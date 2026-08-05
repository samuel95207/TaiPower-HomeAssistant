"""Fetch 15-minute AMI data via the internal Taipower API."""
import base64
import json
import re
import urllib.parse
from datetime import date
from pathlib import Path
from typing import Optional

from taipower_ami import BASE

API_URL = f"{BASE}/ebpps2/amichart/api/fifteenlist"

# The AMI dashboard link carries an encrypted key that the fifteenlist API
# requires. The key rotates (per session/day), so it must be discovered from
# the logged-in pages rather than hardcoded.
ENKEY_RE = re.compile(r"/ebpps2/amichart/amidashball/([^'\"]+)")


def discover_enkey(page) -> str:
    """Extract the current AMI enkey from the logged-in page's navigation."""
    m = ENKEY_RE.search(page.content())
    for fallback in (f"{BASE}/ebpps2/", f"{BASE}/ebpps2/bill/myebill-overview"):
        if m:
            break
        page.goto(fallback, wait_until="domcontentloaded")
        m = ENKEY_RE.search(page.content())
    if not m:
        raise RuntimeError("Could not find the AMI dashboard enkey link; not logged in?")
    return m.group(1)


def discover_customers(page) -> list[dict]:
    """List all AMI electricity numbers on the account with their enkeys.

    The AMI dashboard embeds `var mycustNoList = [{txtno, txtkey, txtalias}]`
    (all values base64) and switches numbers by navigating to
    /amichart/amidashball/<decoded txtkey>. The enkeys are session-specific.
    """
    enkey = discover_enkey(page)
    page.goto(f"{BASE}/ebpps2/amichart/amidashball/{enkey}", wait_until="domcontentloaded")
    html = page.content()

    def b64(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        try:
            return base64.b64decode(value).decode("utf-8")
        except Exception:
            return None

    best: list = []
    for m in re.finditer(r"mycustNoList\s*=\s*(\[.*?\])\s*;", html, re.S):
        try:
            arr = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(arr, list) and len(arr) > len(best):
            best = arr

    customers = []
    for item in best:
        number = b64(item.get("txtno"))
        key = b64(item.get("txtkey"))
        if number and key:
            alias = b64(item.get("txtalias"))
            customers.append({
                "customer_number": number,
                "alias": alias if alias and alias != number else None,
                "enkey": key,
            })
    if not customers:
        # Fall back to the account-level dashboard key.
        customers = [{"customer_number": None, "alias": None, "enkey": enkey}]
    return customers


def fetch_15min_api(
    page, target_date: date, out_dir: Optional[Path] = None, enkey: Optional[str] = None
) -> list[dict]:
    """Call the internal fifteenlist API using the page's cookies/context.

    Returns the list of 15-minute data points. Optionally writes the result to
    out_dir/15min_YYYY-MM-DD.json.
    """
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    if enkey is None:
        enkey = discover_enkey(page)
    query = urllib.parse.urlencode({"enkey": enkey, "day": target_date.strftime("%Y-%m-%d")})
    url = f"{API_URL}?{query}"

    # Occasional responses are non-200 or non-JSON (transient backend errors
    # and rate-limit hiccups, seen especially during bulk history fetches);
    # retry a couple of times with a pause before giving up.
    import time

    data = None
    last_error = ""
    for attempt in range(3):
        if attempt:
            time.sleep(2 * attempt)
        result = page.evaluate("""async (url) => {
            const resp = await fetch(url, { credentials: 'same-origin' });
            const text = await resp.text();
            return { status: resp.status, body: text };
        }""", url)
        if result["status"] != 200:
            last_error = f"HTTP {result['status']}: {result['body'][:200]}"
            continue
        try:
            data = json.loads(result["body"])
            break
        except json.JSONDecodeError:
            last_error = f"non-JSON body ({len(result['body'])} bytes): {result['body'][:120]!r}"
    if data is None:
        raise RuntimeError(f"fifteenlist failed for {target_date}: {last_error}")

    points = data.get("listAMIBase15MinData")
    if not isinstance(points, list):
        raise RuntimeError(f"Unexpected API response: {data}")

    if out_dir:
        out_file = out_dir / f"15min_{target_date.isoformat()}.json"
        out_file.write_text(json.dumps(points, ensure_ascii=False, indent=2), encoding="utf-8")

    return points


__all__ = ["fetch_15min_api", "discover_enkey", "discover_customers", "ENKEY_RE"]
