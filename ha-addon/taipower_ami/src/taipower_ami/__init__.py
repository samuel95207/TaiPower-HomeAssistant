"""Taipower AMI scraper package.

Provides reusable Python functions, a CLI, and an HTTP API for logging into the
Taipower e-service portal and fetching AMI electricity usage data.
"""

from pathlib import Path

__version__ = "0.1.0"

ROOT = Path(__file__).resolve().parents[2]
BASE = "https://service.taipower.com.tw"
LOGIN_URL = f"{BASE}/ebpps2/login"
