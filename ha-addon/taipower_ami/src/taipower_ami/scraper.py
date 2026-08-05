"""Dashboard scraping helpers."""
import json
import re
import time
from pathlib import Path
from typing import Optional

from taipower_ami.auth import PWTimeout

# The dashboard URL embeds an account-specific key that rotates, so it is
# always discovered from the logged-in navigation (see fetcher.discover_enkey).
AMI_BASE = "https://service.taipower.com.tw/ebpps2/amichart/amidashball/"


def ami_url(page) -> str:
    """Current AMI dashboard URL for the logged-in account."""
    from taipower_ami.fetcher import discover_enkey

    return AMI_BASE + discover_enkey(page)

AMI_TABS = ["每15分鐘", "每小時", "每日", "每月", "比較"]


def logged_in(page) -> bool:
    """Probe the AMI dashboard to see if the session is authenticated."""
    try:
        resp = page.goto(ami_url(page), wait_until="domcontentloaded")
    except Exception:
        return False
    return bool(resp and resp.status == 200 and "/login" not in page.url)


def settle(page, seconds: float = 3.0) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except PWTimeout:
        pass
    time.sleep(seconds)


def sanitize(url: str) -> str:
    name = url.split("/ebpps2/", 1)[-1]
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120] or "response"


def scrape(page, out_dir: Path) -> tuple[dict, dict]:
    """Visit the dashboard and every AMI tab, capturing JSON and page HTML."""
    from taipower_ami.fetcher import discover_enkey

    captured: dict[str, object] = {}
    pages_html: dict[str, str] = {}

    def on_response(resp):
        if "json" in resp.headers.get("content-type", "") and "/ebpps2/" in resp.url:
            try:
                captured[resp.url] = resp.json()
            except Exception:
                pass

    page.on("response", on_response)
    page.goto(AMI_BASE + discover_enkey(page), wait_until="domcontentloaded")
    settle(page)

    out_dir.mkdir(parents=True, exist_ok=True)
    pages_html["dashboard"] = page.content()
    page.screenshot(path=str(out_dir / "ami_dashboard.png"), full_page=True)

    for tab in AMI_TABS:
        try:
            el = page.get_by_text(tab, exact=True).first
            if not el.count():
                print(f"  tab {tab}: not found, skipping")
                continue
            el.click(timeout=10000)
            settle(page, 2.0)
            pages_html[tab] = page.content()
            page.screenshot(path=str(out_dir / f"ami_{tab}.png"), full_page=True)
            print(f"  tab {tab}: captured")
        except Exception as exc:
            print(f"  tab {tab}: {type(exc).__name__}, skipping")

    return captured, pages_html


def extract_chart_series(html: str) -> list[dict]:
    """Pull the amCharts dataProvider array out of the inline page script."""
    for m in re.finditer(r'"dataProvider"\s*:\s*(\[.*?\])\s*[,}]', html, re.S):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data
    return []


def save_results(out_dir: Path, captured: dict, pages_html: dict) -> list[Path]:
    """Write captured JSON responses, HTML pages, and extracted chart series."""
    saved: list[Path] = []
    ami = {u: d for u, d in captured.items() if "ami" in u.lower()} or captured
    for url, data in ami.items():
        f = out_dir / f"{sanitize(url)}.json"
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        saved.append(f)

    for label, html in pages_html.items():
        f = out_dir / f"page_{re.sub(r'[^A-Za-z0-9]+', '_', label)}.html"
        f.write_text(html, encoding="utf-8")
        series = extract_chart_series(html)
        if series:
            sf = out_dir / f"series_{re.sub(r'[^A-Za-z0-9]+', '_', label)}.json"
            sf.write_text(json.dumps(series, ensure_ascii=False, indent=2), encoding="utf-8")
            saved.append(sf)
            print(f"  {label}: {len(series)} data points, e.g. {series[0]}")

    print(f"\nCaptured {len(captured)} JSON responses across "
          f"{len(pages_html)} views. Wrote to {out_dir}/:")
    for f in saved:
        print(f"  {f}")
    return saved


__all__ = [
    "AMI_BASE",
    "ami_url",
    "AMI_TABS",
    "logged_in",
    "settle",
    "sanitize",
    "scrape",
    "extract_chart_series",
    "save_results",
]
