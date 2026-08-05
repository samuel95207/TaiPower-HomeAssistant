"""DataUpdateCoordinator for the Taipower AMI integration."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.components.persistent_notification import (
    async_create as notify_create,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    BILL_REFRESH_CYCLES,
    CONF_BASE_URL,
    DOMAIN,
    REQUEST_TIMEOUT,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")

# How far back to look for the row that anchors a rewritten window's sum.
LOOKBACK_ANCHOR_DAYS = 30

try:  # HA 2025.4+ replaced has_mean with mean_type
    from homeassistant.components.recorder.models import StatisticMeanType

    _MEAN_NONE: dict[str, Any] = {"mean_type": StatisticMeanType.NONE}
    _MEAN_ARITHMETIC: dict[str, Any] = {"mean_type": StatisticMeanType.ARITHMETIC}
except ImportError:
    _MEAN_NONE = {"has_mean": False}
    _MEAN_ARITHMETIC = {"has_mean": True}


def _energy_id(custno: str) -> str:
    return f"{DOMAIN}:{custno}_energy"


def _power_id(custno: str) -> str:
    return f"{DOMAIN}:{custno}_power"


def _cost_id(custno: str) -> str:
    return f"{DOMAIN}:{custno}_cost"


def _bill_id(custno: str) -> str:
    return f"{DOMAIN}:{custno}_bill"


def _price_table(bill: dict) -> list[tuple[date, date, float]]:
    """(period_start, period_end, TWD/kWh) for every bill we have detail for.

    Taiwan's tariff is tiered and changes over time, so each bill's own
    average price is the honest rate to cost its own billing period with.
    """
    table: list[tuple[date, date, float]] = []
    for detail in (bill or {}).get("details", {}).values():
        price = detail.get("avg_price_per_kwh")
        start, end = detail.get("billing_period_start"), detail.get("billing_period_end")
        if price and start and end:
            table.append((date.fromisoformat(start), date.fromisoformat(end), float(price)))
    table.sort(key=lambda row: row[0])
    return table


def _price_for(moment: datetime, table: list[tuple[date, date, float]]) -> float | None:
    """Rate covering `moment`; outside known periods use the nearest one."""
    if not table:
        return None
    day = moment.date()
    for start, end, price in table:
        if start <= day <= end:
            return price
    if day < table[0][0]:
        return table[0][2]
    return table[-1][2]


def _hourly_from_points(
    day_date: str, points: list[dict], require_complete: bool = True
) -> list[dict]:
    """Hourly energy/power buckets of one day.

    The fifteenlist `power` field is energy in kWh per 15-minute slot, so an
    hour's kWh is the plain sum of its slots, the hourly mean power in kW
    equals that same sum (kWh per hour), and per-slot power is the value × 4.

    The live path (require_complete=True) reports only hours with all 4 slots
    so a row never needs correcting after it has been written: the sum stays
    monotonic without rewrites. Backfills pass require_complete=False —
    historical days no longer change, and months with flaky meter reporting
    would otherwise lose all their partially-reported hours.
    """
    buckets: dict[int, list[float]] = {}
    for p in points or []:
        if p.get("isMssingData") or p.get("power") is None:
            continue
        hour = int(p["time"].split(":")[0])
        buckets.setdefault(hour, []).append(float(p["power"]))
    year, month, dom = (int(x) for x in day_date.split("-"))
    return [
        {
            "start": datetime(year, month, dom, hour, tzinfo=TAIPEI),
            "kwh": round(sum(slots), 4),
            "mean": round(sum(slots), 4),
            "min": round(min(slots) * 4, 4),
            "max": round(max(slots) * 4, 4),
        }
        for hour, slots in sorted(buckets.items())
        if len(slots) == 4 or not require_complete
    ]


class TaipowerAmiCoordinator(DataUpdateCoordinator[dict]):
    """Polls the Taipower AMI HTTP API and feeds long-term statistics.

    coordinator.data = {
        "customers": {custno: {latest, today, yesterday, alias}},
        "bills": {custno: {current_bill, detail, history}},
    }
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.base_url: str = entry.data[CONF_BASE_URL].rstrip("/")
        self._session = async_get_clientsession(hass)
        self._cycle = 0
        self.bills: dict[str, dict] = {}
        self.backfill_running = False

    async def _get_json(self, path: str, timeout: int = REQUEST_TIMEOUT) -> dict:
        url = f"{self.base_url}{path}"
        try:
            async with self._session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status != 200:
                    body = (await resp.text())[:200]
                    raise UpdateFailed(f"{url} returned HTTP {resp.status}: {body}")
                return await resp.json()
        except aiohttp.ClientError as exc:
            raise UpdateFailed(f"Error talking to {url}: {exc}") from exc

    async def _async_update_data(self) -> dict:
        latest = await self._get_json("/fetch/latest", timeout=600)
        customers = {c["customer_number"]: c for c in latest.get("customers", [])}
        if not customers:
            raise UpdateFailed("Server returned no electricity numbers")

        if not self.bills or self._cycle % BILL_REFRESH_CYCLES == 0:
            try:
                bill = await self._get_json("/bill/summary")
                self.bills = bill.get("customers", {})
                try:
                    await self._insert_bill_statistics()
                except Exception:
                    _LOGGER.exception("Writing bill statistics failed")
            except UpdateFailed as exc:
                # Bill data is non-critical; keep the previous copy.
                _LOGGER.warning("Bill summary refresh failed: %s", exc)
        self._cycle += 1

        if not self.backfill_running:
            try:
                await self._insert_recent_statistics(customers)
            except Exception:  # statistics must never break sensor updates
                _LOGGER.exception("Inserting long-term statistics failed")

        return {"customers": customers, "bills": self.bills}

    @property
    def customer_numbers(self) -> list[str]:
        if not self.data:
            return []
        return list(self.data["customers"])

    # ------------------------------------------------------------ statistics

    async def _last_stat(
        self, statistic_id: str, types: set[str]
    ) -> tuple[float, datetime | None]:
        last_stats = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, True, types
        )
        if last_stats and statistic_id in last_stats:
            row = last_stats[statistic_id][0]
            return float(row.get("sum") or 0.0), datetime.fromtimestamp(
                row["start"], tz=TAIPEI
            )
        return 0.0, None

    def _energy_metadata(self, custno: str) -> StatisticMetaData:
        return StatisticMetaData(
            has_sum=True,
            name=f"Taipower AMI {custno} Energy",
            source=DOMAIN,
            statistic_id=_energy_id(custno),
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            **_MEAN_NONE,
        )

    def _power_metadata(self, custno: str) -> StatisticMetaData:
        return StatisticMetaData(
            has_sum=False,
            name=f"Taipower AMI {custno} Power",
            source=DOMAIN,
            statistic_id=_power_id(custno),
            unit_of_measurement="kW",
            **_MEAN_ARITHMETIC,
        )

    def _cost_metadata(self, custno: str) -> StatisticMetaData:
        return StatisticMetaData(
            has_sum=True,
            name=f"Taipower AMI {custno} Cost",
            source=DOMAIN,
            statistic_id=_cost_id(custno),
            unit_of_measurement="TWD",
            **_MEAN_NONE,
        )

    def _bill_metadata(self, custno: str) -> StatisticMetaData:
        return StatisticMetaData(
            has_sum=True,
            name=f"Taipower AMI {custno} Bill",
            source=DOMAIN,
            statistic_id=_bill_id(custno),
            unit_of_measurement="TWD",
            **_MEAN_NONE,
        )

    async def _insert_bill_statistics(self) -> None:
        """One row per issued bill, at the end of its billing period.

        These are the amounts Taipower actually charged, so a bar chart of
        this statistic matches the bills exactly — unlike the hourly cost
        series, which is derived from usage and split by calendar month.
        """
        for custno, bill in (self.bills or {}).items():
            details = bill.get("details") or {}
            rows_by_start: dict[datetime, float] = {}
            for row in bill.get("history") or []:
                month, amount = row.get("bill_month"), row.get("amount_due")
                if not month or amount is None:
                    continue
                detail = details.get(month) or {}
                end = detail.get("billing_period_end")
                if end:
                    when = datetime.fromisoformat(end).replace(tzinfo=TAIPEI)
                else:  # no detail: fall back to the middle of the bill month
                    year, mon = (int(x) for x in month.split("-"))
                    when = datetime(year, mon, 15, tzinfo=TAIPEI)
                rows_by_start[when.replace(minute=0, second=0, microsecond=0)] = float(amount)

            if not rows_by_start:
                continue
            running = 0.0
            stats: list[StatisticData] = []
            for when in sorted(rows_by_start):
                running += rows_by_start[when]
                stats.append(
                    StatisticData(start=when, state=rows_by_start[when], sum=running)
                )
            async_add_external_statistics(self.hass, self._bill_metadata(custno), stats)
            _LOGGER.debug("%s: wrote %d bill rows", custno, len(stats))

    def _power_rows(
        self, hours: list[dict], after: datetime | None
    ) -> list[StatisticData]:
        return [
            StatisticData(
                start=h["start"], mean=h["mean"], min=h["min"], max=h["max"]
            )
            for h in hours
            if after is None or h["start"] > after
        ]

    async def _sum_before(self, statistic_id: str, moment: datetime) -> float:
        """Cumulative sum of the last row strictly before `moment` (0 if none).

        Used to anchor a rewritten window so the running total stays a single
        continuous chain no matter how many rows the rewrite replaces.
        """
        rows = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            moment - timedelta(days=LOOKBACK_ANCHOR_DAYS),
            moment,
            {statistic_id},
            "hour",
            None,
            {"sum"},
        )
        series = rows.get(statistic_id) or []
        return float(series[-1].get("sum") or 0.0) if series else 0.0

    async def _fill_gap(self, custno: str, gap_start: date, gap_end: date) -> list[dict]:
        """Re-fetch whole days that the live window no longer covers."""
        hours: list[dict] = []
        current = gap_start
        while current <= gap_end:
            chunk_end = min(current + timedelta(days=29), gap_end)
            resp = await self._get_json(
                "/fetch/history"
                f"?start_date={current.isoformat()}&end_date={chunk_end.isoformat()}"
                f"&electricity_number={custno}",
                timeout=600,
            )
            for day in resp.get("days", []):
                if day.get("points"):
                    hours.extend(
                        _hourly_from_points(day["date"], day["points"],
                                            require_complete=False)
                    )
            current = chunk_end + timedelta(days=1)
        return hours

    async def _insert_recent_statistics(self, customers: dict[str, dict]) -> None:
        """Rewrite the live window on every poll so it self-heals.

        Rather than only appending hours after the last stored row, every
        update recomputes today's and yesterday's hours and writes them all.
        Rows are keyed by their start time, so re-writing is idempotent, and
        an hour that was skipped earlier (Taipower had not published all four
        of its 15-minute slots yet, or a fetch failed) is filled in as soon as
        the data arrives — no backfill needed. If the gap is older than the
        live window, the missing days are re-fetched from /fetch/history.
        """
        for custno, cust in customers.items():
            hours: list[dict] = []
            for day_key in ("yesterday", "today"):
                day = cust.get(day_key) or {}
                if day.get("date"):
                    hours.extend(_hourly_from_points(day["date"], day.get("points")))
            hours.sort(key=lambda item: item["start"])
            if not hours:
                continue

            _, last_start = await self._last_stat(_energy_id(custno), {"sum"})
            window_start = hours[0]["start"]

            # Outage longer than the live window: re-fetch the missing days.
            if last_start is not None and last_start < window_start - timedelta(hours=1):
                gap_start = last_start.date()
                gap_end = window_start.date() - timedelta(days=1)
                if gap_start <= gap_end:
                    _LOGGER.info(
                        "%s: statistics gap %s..%s — re-fetching",
                        custno, gap_start, gap_end,
                    )
                    try:
                        gap_hours = await self._fill_gap(custno, gap_start, gap_end)
                    except Exception:
                        _LOGGER.exception("%s: gap re-fetch failed", custno)
                        gap_hours = []
                    if gap_hours:
                        merged = {h["start"]: h for h in gap_hours}
                        merged.update({h["start"]: h for h in hours})
                        hours = sorted(merged.values(), key=lambda h: h["start"])
                        window_start = hours[0]["start"]

            base_sum = await self._sum_before(_energy_id(custno), window_start)
            base_cost = await self._sum_before(_cost_id(custno), window_start)
            prices = _price_table(self.bills.get(custno, {}))
            running = base_sum
            running_cost = base_cost
            energy_rows: list[StatisticData] = []
            cost_rows: list[StatisticData] = []
            for h in hours:
                running += h["kwh"]
                energy_rows.append(
                    StatisticData(start=h["start"], state=h["kwh"], sum=running)
                )
                price = _price_for(h["start"], prices)
                if price is not None:
                    hour_cost = round(h["kwh"] * price, 4)
                    running_cost += hour_cost
                    cost_rows.append(
                        StatisticData(start=h["start"], state=hour_cost, sum=running_cost)
                    )
            async_add_external_statistics(
                self.hass, self._energy_metadata(custno), energy_rows
            )
            async_add_external_statistics(
                self.hass, self._power_metadata(custno), self._power_rows(hours, None)
            )
            if cost_rows:
                async_add_external_statistics(
                    self.hass, self._cost_metadata(custno), cost_rows
                )
            _LOGGER.debug(
                "%s: wrote %d hourly rows %s..%s (anchor sum %.3f)",
                custno, len(energy_rows), hours[0]["start"], hours[-1]["start"],
                base_sum,
            )

    # -------------------------------------------------------------- backfill

    async def async_backfill(self, start: date) -> None:
        """Rebuild the whole statistic from `start` to now for every number.

        Fetches 15-min history in monthly chunks, clears the statistic, and
        re-inserts every complete hour with a cumulative sum from zero.
        """
        if self.backfill_running:
            _LOGGER.warning("Backfill already running; ignoring new request")
            return
        self.backfill_running = True
        try:
            for custno in self.customer_numbers:
                await self._backfill_one(custno, start)
            # Wait until the recorder has committed the rebuilt rows before
            # regular polling may read get_last_statistics again — otherwise
            # the next cycle reads the pre-backfill last row and writes one
            # row with a stale (tiny) cumulative sum.
            await get_instance(self.hass).async_block_till_done()
            notify_create(
                self.hass,
                f"Taipower AMI backfill from {start} finished for: "
                f"{', '.join(self.customer_numbers)}. Statistics are rebuilt.",
                title="Taipower AMI backfill complete",
            )
        except Exception as exc:
            _LOGGER.exception("Backfill failed")
            notify_create(
                self.hass,
                f"Taipower AMI backfill failed: {exc}",
                title="Taipower AMI backfill failed",
            )
        finally:
            self.backfill_running = False

    async def _backfill_one(self, custno: str, start: date) -> None:
        today = datetime.now(TAIPEI).date()
        hours: list[dict] = []
        current = start
        while current <= today:
            chunk_end = min(current + timedelta(days=29), today)
            _LOGGER.info(
                "Backfill %s: fetching %s .. %s", custno, current, chunk_end
            )
            path = (
                "/fetch/history"
                f"?start_date={current.isoformat()}&end_date={chunk_end.isoformat()}"
                f"&electricity_number={custno}"
            )
            try:
                resp = await self._get_json(path, timeout=600)
            except UpdateFailed as exc:
                _LOGGER.warning("Backfill chunk failed, retrying once: %s", exc)
                resp = await self._get_json(path, timeout=600)
            for day in resp.get("days", []):
                if day.get("points"):
                    hours.extend(
                        _hourly_from_points(
                            day["date"], day["points"], require_complete=False
                        )
                    )
            current = chunk_end + timedelta(days=1)

        hours.sort(key=lambda item: item["start"])
        if not hours:
            _LOGGER.warning("Backfill %s: no data found from %s", custno, start)
            return

        # Clear existing rows, then re-insert everything with a fresh
        # cumulative sum. The recorder queue is FIFO, so the clear is
        # processed before the inserts below.
        get_instance(self.hass).async_clear_statistics(
            [_energy_id(custno), _power_id(custno), _cost_id(custno)]
        )

        prices = _price_table(self.bills.get(custno, {}))
        running = 0.0
        running_cost = 0.0
        energy_rows: list[StatisticData] = []
        cost_rows: list[StatisticData] = []
        for h in hours:
            running += h["kwh"]
            energy_rows.append(
                StatisticData(start=h["start"], state=h["kwh"], sum=running)
            )
            price = _price_for(h["start"], prices)
            if price is not None:
                hour_cost = round(h["kwh"] * price, 4)
                running_cost += hour_cost
                cost_rows.append(
                    StatisticData(start=h["start"], state=hour_cost, sum=running_cost)
                )
        async_add_external_statistics(
            self.hass, self._energy_metadata(custno), energy_rows
        )
        async_add_external_statistics(
            self.hass, self._power_metadata(custno), self._power_rows(hours, None)
        )
        if cost_rows:
            async_add_external_statistics(
                self.hass, self._cost_metadata(custno), cost_rows
            )
        _LOGGER.info(
            "Backfill %s: inserted %d hourly rows (%s .. %s, %.1f kWh total)",
            custno, len(energy_rows), hours[0]["start"], hours[-1]["start"], running,
        )
