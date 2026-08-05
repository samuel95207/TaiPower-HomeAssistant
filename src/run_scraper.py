#!/usr/bin/env python3
"""CLI entry point for the Taipower AMI dashboard scraper."""
import argparse
import os
import sys
from pathlib import Path

# Allow running from src/ without installing the package.
sys.path.insert(0, str(Path(__file__).parent))

from taipower_ami.auth import (
    CamoufoxSession,
    interactive_setup,
    load_credentials,
    login,
    open_context,
    try_auto_login_in_context,
)
from taipower_ami.scraper import logged_in, save_results, scrape


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true", help="force a visible browser login flow")
    ap.add_argument("--no-interactive-fallback", action="store_true", help="fail instead of opening a visible browser")
    ap.add_argument("--browser-channel", default=os.getenv("BROWSER_CHANNEL", ""))
    ap.add_argument(
        "--browser-type",
        default=os.getenv("BROWSER_TYPE", ""),
        choices=["", "chromium", "chrome", "msedge", "camoufox"],
    )
    ap.add_argument("--no-persistent", action="store_true")
    ap.add_argument("--setup-timeout", type=int, default=300)
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    user, password = load_credentials()
    out_dir = Path(args.out)
    channel = args.browser_channel or None
    browser_type = args.browser_type or ""

    def run_camoufox(headless: bool, allow_visible_retry: bool = True):
        with CamoufoxSession(headless=headless) as cf:
            page = cf.context.pages[0] if cf.context.pages else cf.context.new_page()

            if args.setup:
                interactive_setup(page, user, password, timeout_seconds=args.setup_timeout)
            elif not logged_in(page):
                print("Saved session is invalid. Trying in-context Turnstile solve...")
                if try_auto_login_in_context(page, user, password):
                    print("  In-context auto-login succeeded.")
                else:
                    print("  In-context solve failed.")
                if "/ebpps2/login" in page.url or not logged_in(page):
                    if args.no_interactive_fallback:
                        sys.exit("No valid session and interactive fallback disabled.")
                    if headless and allow_visible_retry:
                        print("Opening visible browser for manual verification...")
                        return run_camoufox(headless=False, allow_visible_retry=False)
                    sys.exit("Camoufox could not log in automatically.")

            print("Opening AMI dashboard...")
            return scrape(page, out_dir)

    if browser_type == "camoufox":
        captured, pages_html = run_camoufox(headless=not args.setup)
    else:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = None
            if args.no_persistent:
                browser = pw.chromium.launch(headless=not args.setup, channel=channel)
                ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-TW")
            else:
                ctx = open_context(pw, headless=not args.setup, channel=channel)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            if args.setup:
                interactive_setup(page, user, password, timeout_seconds=args.setup_timeout)
            elif not logged_in(page):
                print("Saved session is invalid. Trying in-context Turnstile solve...")
                if try_auto_login_in_context(page, user, password):
                    print("  In-context auto-login succeeded.")
                else:
                    print("  In-context solve failed.")
                if "/ebpps2/login" in page.url or not logged_in(page):
                    if args.no_interactive_fallback:
                        sys.exit("No valid session and interactive fallback disabled.")
                    print("Opening visible browser for manual verification...")
                    ctx.close()
                    if browser:
                        browser.close()
                    if args.no_persistent:
                        browser = pw.chromium.launch(headless=False, channel=channel)
                        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-TW")
                    else:
                        ctx = open_context(pw, headless=False, channel=channel)
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    interactive_setup(page, user, password, timeout_seconds=args.setup_timeout)
                    if not logged_in(page):
                        ctx.close()
                        browser.close()
                        sys.exit("Login verification did not produce a valid session.")

            print("Opening AMI dashboard...")
            captured, pages_html = scrape(page, out_dir)
            ctx.close()
            if browser:
                browser.close()

    save_results(out_dir, captured, pages_html)


if __name__ == "__main__":
    main()
