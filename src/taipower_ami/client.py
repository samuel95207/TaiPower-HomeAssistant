"""Python client for calling the Taipower AMI HTTP API from outside Docker."""
from datetime import date, timedelta
from typing import Optional

import requests


class TaipowerClient:
    """Client for the Taipower AMI scraper API."""

    def __init__(self, base_url: str = "http://localhost:8000", timeout: int = 300):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, json: Optional[dict] = None) -> dict:
        resp = requests.post(
            f"{self.base_url}{path}",
            json=json,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str, params: Optional[dict] = None, timeout: Optional[int] = None) -> dict:
        resp = requests.get(
            f"{self.base_url}{path}",
            params=params,
            timeout=timeout or self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def health(self) -> dict:
        """Check API health."""
        return self._get("/health", timeout=10)

    def customers(self) -> dict:
        """List all AMI electricity numbers on the account."""
        return self._get("/customers")

    def fetch_latest(self) -> dict:
        """Today's + yesterday's 15-min data for every electricity number."""
        return self._get("/fetch/latest")

    def fetch_history(
        self,
        start_date: date | str,
        end_date: date | str,
        electricity_number: Optional[str] = None,
    ) -> dict:
        """15-min data for a date range (max 45 days per call)."""
        if isinstance(start_date, date):
            start_date = start_date.isoformat()
        if isinstance(end_date, date):
            end_date = end_date.isoformat()
        params = {"start_date": start_date, "end_date": end_date}
        if electricity_number:
            params["electricity_number"] = electricity_number
        return self._get("/fetch/history", params=params, timeout=600)

    def bill_summary(self, refresh: bool = False) -> dict:
        """Bills for every electricity number (cached 6h)."""
        return self._get("/bill/summary", params={"refresh": refresh})

    def scrape_dashboard(self) -> dict:
        """Trigger dashboard scrape."""
        return self._post("/scrape/dashboard")

    def fetch_15min(self, target_date: Optional[date | str] = None) -> dict:
        """Fetch 15-minute data for a single day (default: yesterday)."""
        if isinstance(target_date, date):
            target_date = target_date.isoformat()
        return self._post("/fetch/15min", {"date": target_date})

    def fetch_15min_range(self, start_date: date | str, end_date: date | str) -> dict:
        """Fetch 15-minute data across a date range."""
        if isinstance(start_date, date):
            start_date = start_date.isoformat()
        if isinstance(end_date, date):
            end_date = end_date.isoformat()
        return self._post("/fetch/15min/range", {
            "start_date": start_date,
            "end_date": end_date,
        })


__all__ = ["TaipowerClient"]
