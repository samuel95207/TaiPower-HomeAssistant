"""Scrape and parse the Taipower e-bill pages (myebill-overview and friends).

The bill pages are server-rendered HTML, not XHR-driven, so we navigate with
the logged-in browser page and parse tables out of the markup. The links to
the per-bill detail page and the history page carry session-specific encrypted
keys, so they are re-extracted from the overview page on every fetch.
"""
import re
from datetime import date
from typing import Optional

from taipower_ami import BASE

OVERVIEW_URL = f"{BASE}/ebpps2/bill/myebill-overview"


# ---------------------------------------------------------------- HTML utils

def _tables(html: str) -> list[list[str]]:
    """Return each <table> as a flat list of cleaned, non-empty cell texts."""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
    out = []
    for t in re.findall(r"<table.*?</table>", text, flags=re.S):
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", t, flags=re.S)
        clean = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip() for c in cells]
        out.append([c for c in clean if c])
    return out


def _roc_date(value: str) -> Optional[str]:
    """'115年08月10日' -> '2026-08-10'."""
    m = re.search(r"(\d{2,3})年(\d{1,2})月(\d{1,2})日", value)
    if not m:
        return None
    return date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3))).isoformat()


def _roc_month(value: str) -> Optional[str]:
    """'115年07月' -> '2026-07'."""
    m = re.search(r"(\d{2,3})年(\d{1,2})月", value)
    if not m:
        return None
    return f"{int(m.group(1)) + 1911}-{int(m.group(2)):02d}"


def _number(value: str) -> Optional[float]:
    m = re.search(r"-?[\d,]+(?:\.\d+)?", value)
    if not m:
        return None
    return float(m.group(0).replace(",", ""))


def _int(value: str) -> Optional[int]:
    n = _number(value)
    return int(n) if n is not None else None


def _labeled(cells: list[str]) -> dict[str, str]:
    """Pair '標籤：'/'標籤' cells with the following value cell."""
    pairs: dict[str, str] = {}
    for i, cell in enumerate(cells[:-1]):
        label = cell.rstrip(":：").strip()
        if cell.endswith((":", "：")) and label:
            pairs[label] = cells[i + 1]
    return pairs


# ------------------------------------------------------------------- parsers

def parse_bill_rows(html: str) -> list[dict]:
    """Parse bill listing rows (overview and history pages share the shape).

    Rows look like: 電號, 帳單月份, 應繳總金額('10,167 明細'), 繳費狀況,
    <notice cell(s)>, 繳費期限.
    """
    rows: list[dict] = []
    seen: set[tuple] = set()
    for cells in _tables(html):
        i = 0
        while i < len(cells):
            if re.fullmatch(r"\d{10,11}", cells[i]) and i + 3 < len(cells):
                month = _roc_month(cells[i + 1])
                if month:
                    due = None
                    for j in range(i + 4, min(i + 8, len(cells))):
                        due = _roc_date(cells[j])
                        if due:
                            break
                    row = {
                        "customer_number": cells[i],
                        "bill_month": month,
                        "amount_due": _int(cells[i + 2]),
                        "payment_status": cells[i + 3],
                        "due_date": due,
                    }
                    key = (row["customer_number"], row["bill_month"])
                    if key not in seen:
                        seen.add(key)
                        rows.append(row)
                    i += 4
                    continue
            i += 1
    return rows


def parse_detail(html: str) -> dict:
    """Parse the myebill-detail page into a structured dict."""
    tables = _tables(html)
    flat = [c for t in tables for c in t]
    pairs = _labeled(flat)

    # 帳單月份 appears without a trailing colon.
    bill_month = None
    for i, c in enumerate(flat[:-1]):
        if c.rstrip(":：") == "帳單月份":
            bill_month = _roc_month(flat[i + 1])
            break

    period = pairs.get("計費期間", "")
    period_dates = re.findall(r"\d{2,3}年\d{1,2}月\d{1,2}日", period)

    detail: dict = {
        "bill_month": bill_month,
        "customer_name": pairs.get("用戶名稱"),
        "customer_number": pairs.get("電號"),
        "supply_address": pairs.get("用電地址"),
        "tariff_type": pairs.get("電價種類"),
        "time_of_use": pairs.get("時間種類"),
        "billing_period_start": _roc_date(period_dates[0]) if period_dates else None,
        "billing_period_end": _roc_date(period_dates[1]) if len(period_dates) > 1 else None,
        "meter_read_date": _roc_date(pairs.get("本次抄表日", "")),
        "next_meter_read_date": _roc_date(pairs.get("下次抄表日", "")),
        "debit_date": _roc_date(pairs.get("本次扣款日期", "")),
        "next_debit_date": _roc_date(pairs.get("下次扣款日期", "")),
        "outage_group": pairs.get("輪流停電組別"),
        "energy_charge": _number(pairs.get("流動電費", "")),
        "public_facility_charge": _number(pairs.get("公共設施電費", "")),
        "ebill_discount": _number(pairs.get("電子帳單優惠減收金額", "")),
        "amount_due": _int(pairs.get("應繳總金額", "")),
        "billed_kwh": _int(pairs.get("經常(尖峰)度數", "")),
        "fuel_cost_per_kwh": None,
        "carbon_kg": None,
        "renewable_fund_per_kwh": None,
        "avg_price_per_kwh": None,
        "comparison": [],
    }

    # Non-colon labelled stats in the comparison table.
    stat_labels = {
        "每度燃料成本": ("fuel_cost_per_kwh", _number),
        "本期碳排量": ("carbon_kg", _number),
        "每度繳交再生基金": ("renewable_fund_per_kwh", _number),
        "當期每度平均電價": ("avg_price_per_kwh", _number),
    }
    for i, c in enumerate(flat[:-1]):
        if c in stat_labels:
            key, conv = stat_labels[c]
            detail[key] = conv(flat[i + 1])

    # 比較項目 table: 本期/去年同期/去年下期 rows of (days, kwh, daily avg).
    for t in tables:
        if "比較項目" in t:
            for i, c in enumerate(t):
                if c in ("本期", "去年同期", "去年下期") and i + 3 < len(t):
                    detail["comparison"].append({
                        "period": c,
                        "days": _int(t[i + 1]),
                        "kwh": _int(t[i + 2]),
                        "daily_avg_kwh": _number(t[i + 3]),
                    })
            m = re.search(r"同棟大樓平均用電度數\s*([\d,]+)\s*度", " ".join(t))
            if m:
                detail["same_building_avg_kwh"] = _int(m.group(1))
    return detail


# ------------------------------------------------------------------ fetchers

def _settle(page, seconds: float = 2.0) -> None:
    import time

    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    time.sleep(seconds)


def _visit_details(page, html: str, into: dict[str, dict], limit: int = 24) -> None:
    """Follow every myebill-detail link in `html`, keyed by 電號 then month."""
    for link in list(dict.fromkeys(
        re.findall(r"/ebpps2/bill/myebill-detail/[^'\"]+", html)
    ))[:limit]:
        try:
            page.goto(BASE + link, wait_until="domcontentloaded")
            _settle(page, 1.0)
            detail = parse_detail(page.content())
        except Exception:
            continue
        custno, month = detail.get("customer_number"), detail.get("bill_month")
        if custno and month:
            into.setdefault(custno, {}).setdefault(month, detail)


def fetch_bill_summary(page, with_history_details: bool = True) -> dict:
    """Navigate overview -> details -> history; group everything by 電號.

    Returns {"customers": {custno: {current_bill, detail, details, history}}}
    where `details` maps each bill month to its full detail (billing period,
    billed kWh, average price actually charged). Past-bill details come from
    the history page, which links each row to its own detail page — that is
    what makes historical cost correct, since rates are tiered and change.
    """
    page.goto(OVERVIEW_URL, wait_until="domcontentloaded")
    _settle(page)
    overview_html = page.content()

    overview_rows = parse_bill_rows(overview_html)

    details: dict[str, dict] = {}
    _visit_details(page, overview_html, details)

    history_rows: list[dict] = []
    history_links = list(dict.fromkeys(
        re.findall(r"/ebpps2/bill/myebill-history/[^'\"]+", overview_html)
    ))
    for link in history_links[:20]:
        page.goto(BASE + link, wait_until="domcontentloaded")
        _settle(page)
        history_html = page.content()
        history_rows.extend(parse_bill_rows(history_html))
        if with_history_details:
            _visit_details(page, history_html, details)

    customers: dict[str, dict] = {}
    numbers = [r["customer_number"] for r in overview_rows]
    numbers += [n for n in details if n not in numbers]
    for custno in numbers:
        current = next(
            (r for r in overview_rows if r["customer_number"] == custno), None
        )
        seen: set[str] = set()
        history = []
        for r in history_rows:
            if r["customer_number"] == custno and r["bill_month"] not in seen:
                seen.add(r["bill_month"])
                history.append(r)
        by_month = details.get(custno, {})
        current_month = (current or {}).get("bill_month")
        customers[custno] = {
            "current_bill": current,
            # `detail` stays the current bill for backwards compatibility.
            "detail": by_month.get(current_month) if current_month else None,
            "details": by_month,
            "history": history,
        }
    return {"customers": customers}


__all__ = [
    "OVERVIEW_URL",
    "parse_bill_rows",
    "parse_detail",
    "fetch_bill_summary",
]
