"""HTTP API server exposing Taipower scraper features.

Run inside the Docker container:
    uvicorn taipower_ami.api:app --host 0.0.0.0 --port 8000

All browser work runs on a single persistent-session worker thread (see
session.py): the Camoufox browser and the Taipower login are reused across
requests, so only the first request (or an expired session) pays the
Turnstile-solving cost.

Every AMI electricity number (電號) registered on the account is discovered
automatically from the AMI dashboard, so nothing has to be configured.
"""
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from taipower_ami.bills import fetch_bill_summary
from taipower_ami.fetcher import discover_customers, fetch_15min_api
from taipower_ami.scraper import scrape
from taipower_ami.session import get_worker

app = FastAPI(title="Taipower AMI API", version="0.3.0")

OUT_DIR = Path(os.getenv("TAIPOWER_OUT_DIR", "/app/data"))
BILL_CACHE_TTL = float(os.getenv("TAIPOWER_BILL_CACHE_TTL", str(6 * 3600)))
TAIPEI = ZoneInfo("Asia/Taipei")

MAX_HISTORY_DAYS = 45

_bill_cache: dict = {"data": None, "fetched_at": 0.0}
_bill_lock = threading.Lock()


class Fetch15MinRequest(BaseModel):
    date: Optional[str] = None  # YYYY-MM-DD; default yesterday
    electricity_number: Optional[str] = None


class DateRangeRequest(BaseModel):
    start_date: str  # YYYY-MM-DD
    end_date: str  # YYYY-MM-DD
    electricity_number: Optional[str] = None


def _resolve_date(value: Optional[str]) -> date:
    if value:
        return date.fromisoformat(value)
    return datetime.now(TAIPEI).date() - timedelta(days=1)


def _submit(fn, timeout: float = 300):
    """Run a browser job, mapping failures to HTTP errors."""
    try:
        return get_worker().submit(fn, timeout=timeout)
    except HTTPException:
        raise
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc))
    except RuntimeError as exc:
        if "Login failed" in str(exc):
            raise HTTPException(status_code=401, detail="Login failed")
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}")


def _pick_customer(customers: list[dict], electricity_number: Optional[str]) -> dict:
    if electricity_number:
        for c in customers:
            if c["customer_number"] == electricity_number:
                return c
        raise HTTPException(
            status_code=404,
            detail=f"Electricity number {electricity_number} not found on this account",
        )
    return customers[0]


def _day_stats(points: list[dict]) -> dict:
    """Summarize one day of 15-min points: latest reading and kWh total.

    The fifteenlist `power` field is energy in kWh per 15-minute slot (the
    monthly sums match Taipower's everyMonthList exactly), so a day's kWh is
    the plain sum and the average power during a slot is the value × 4.
    """
    real = [p for p in points if not p.get("isMssingData") and p.get("power") is not None]
    latest = real[-1] if real else None
    kwh = round(sum(p["power"] for p in real), 4)
    return {
        "points": points,
        "valid_points": len(real),
        "kwh": kwh,
        "latest": {
            "time": latest["time"],
            "power": round(latest["power"] * 4, 4),  # average kW over the slot
            "kwh": latest["power"],
        } if latest else None,
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/customers")
def customers():
    """List all AMI electricity numbers registered on the account."""
    found = _submit(discover_customers)
    return {
        "customers": [
            {"customer_number": c["customer_number"], "alias": c["alias"]}
            for c in found
        ]
    }


@app.get("/fetch/latest")
def fetch_latest():
    """Today's + yesterday's 15-min data for every electricity number.

    Designed for frequent polling (e.g. Home Assistant every 15 minutes).
    Taipower publishes same-day readings with roughly a 1.5-2 hour lag.
    """
    today = datetime.now(TAIPEI).date()
    yesterday = today - timedelta(days=1)

    def work(page):
        out = []
        for c in discover_customers(page):
            out.append({
                "customer_number": c["customer_number"],
                "alias": c["alias"],
                "today_points": fetch_15min_api(page, today, None, enkey=c["enkey"]),
                "yesterday_points": fetch_15min_api(page, yesterday, None, enkey=c["enkey"]),
            })
        return out

    results = []
    for raw in _submit(work, timeout=600):
        today_stats = _day_stats(raw["today_points"])
        yesterday_stats = _day_stats(raw["yesterday_points"])
        latest = None
        if today_stats["latest"]:
            latest = {"date": today.isoformat(), **today_stats["latest"]}
        elif yesterday_stats["latest"]:
            latest = {"date": yesterday.isoformat(), **yesterday_stats["latest"]}
        results.append({
            "customer_number": raw["customer_number"],
            "alias": raw["alias"],
            "latest": latest,
            "today": {"date": today.isoformat(), **today_stats},
            "yesterday": {"date": yesterday.isoformat(), **yesterday_stats},
        })

    return {
        "generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
        "customers": results,
    }


@app.get("/fetch/history")
def fetch_history(
    start_date: str = Query(...),
    end_date: str = Query(...),
    electricity_number: Optional[str] = Query(None),
):
    """15-min data for a date range (max 45 days per call), for backfills.

    Days with no valid readings are returned with points omitted.
    """
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        raise HTTPException(status_code=400, detail="end_date must be >= start_date")
    if (end - start).days + 1 > MAX_HISTORY_DAYS:
        raise HTTPException(
            status_code=400, detail=f"Range too large; max {MAX_HISTORY_DAYS} days per call"
        )

    def work(page):
        import time as _time

        cust = _pick_customer(discover_customers(page), electricity_number)
        days = []
        current = start
        while current <= end:
            _time.sleep(0.3)  # be polite during bulk fetches
            points = fetch_15min_api(page, current, None, enkey=cust["enkey"])
            stats = _day_stats(points)
            entry = {
                "date": current.isoformat(),
                "valid_points": stats["valid_points"],
                "kwh": stats["kwh"],
            }
            if stats["valid_points"]:
                entry["points"] = points
            days.append(entry)
            current += timedelta(days=1)
        return {"customer_number": cust["customer_number"], "days": days}

    result = _submit(work, timeout=600)
    return {
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        **result,
    }


@app.get("/bill/summary")
def bill_summary(refresh: bool = False):
    """Bills for every electricity number: current, detail, and history.

    Scraped from the server-rendered e-bill pages; cached for
    TAIPOWER_BILL_CACHE_TTL seconds (default 6h) since bills change rarely.
    """
    with _bill_lock:
        age = time.time() - _bill_cache["fetched_at"]
        if _bill_cache["data"] is not None and age < BILL_CACHE_TTL and not refresh:
            return {**_bill_cache["data"], "cached": True, "cache_age_seconds": int(age)}

        data = _submit(fetch_bill_summary)
        if not data.get("customers"):
            # Transient bad page load — retry once before giving up; never cache it.
            data = _submit(fetch_bill_summary)
            if not data.get("customers"):
                raise HTTPException(status_code=502, detail="Bill pages returned no data")
        data["fetched_at"] = datetime.now(TAIPEI).isoformat(timespec="seconds")
        _bill_cache["data"] = data
        _bill_cache["fetched_at"] = time.time()
        return {**data, "cached": False, "cache_age_seconds": 0}


@app.post("/scrape/dashboard")
def scrape_dashboard():
    """Log in and scrape the AMI dashboard summary data."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    captured, pages_html = _submit(lambda page: scrape(page, OUT_DIR))
    return {
        "captured_urls": list(captured.keys()),
        "pages": list(pages_html.keys()),
        "out_dir": str(OUT_DIR),
    }


@app.post("/fetch/15min")
def fetch_15min(req: Fetch15MinRequest):
    """Fetch 15-minute usage data for a single day (writes a JSON file)."""
    target = _resolve_date(req.date)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    def work(page):
        cust = _pick_customer(discover_customers(page), req.electricity_number)
        return fetch_15min_api(page, target, OUT_DIR, enkey=cust["enkey"])

    points = _submit(work)
    return {
        "date": target.isoformat(),
        "count": len(points),
        "points": points,
        "out_dir": str(OUT_DIR),
    }


@app.post("/fetch/15min/range")
def fetch_15min_range(req: DateRangeRequest):
    """Fetch 15-minute usage data for a date range (writes JSON files)."""
    start = date.fromisoformat(req.start_date)
    end = date.fromisoformat(req.end_date)
    if end < start:
        raise HTTPException(status_code=400, detail="end_date must be >= start_date")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    def work(page):
        cust = _pick_customer(discover_customers(page), req.electricity_number)
        results = []
        current = start
        while current <= end:
            points = fetch_15min_api(page, current, OUT_DIR, enkey=cust["enkey"])
            results.append({"date": current.isoformat(), "count": len(points)})
            current += timedelta(days=1)
        return results

    results = _submit(work, timeout=600)
    return {
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "days": results,
        "out_dir": str(OUT_DIR),
    }
