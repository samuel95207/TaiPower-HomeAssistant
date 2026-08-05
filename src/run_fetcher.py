#!/usr/bin/env python3
"""CLI entry point for fetching 15-minute AMI data."""
import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

# Allow running from src/ without installing the package.
sys.path.insert(0, str(Path(__file__).parent))

from taipower_ami.auth import CamoufoxSession, load_credentials, login
from taipower_ami.fetcher import fetch_15min_api


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="Date in YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--out", default="data", help="output directory")
    args = ap.parse_args()

    if args.date:
        target = date.fromisoformat(args.date)
    else:
        target = date.today() - timedelta(days=1)

    user, password = load_credentials()
    out_dir = Path(args.out)

    with CamoufoxSession(headless=True) as cf:
        page = cf.context.pages[0] if cf.context.pages else cf.context.new_page()

        if not login(page, user, password):
            sys.exit("Login failed")
        print("Login succeeded")

        points = fetch_15min_api(page, target, out_dir)
        print(json.dumps({"date": target.isoformat(), "count": len(points)}))


if __name__ == "__main__":
    main()
