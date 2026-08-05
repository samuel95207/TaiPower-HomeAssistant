"""Authentication helpers: Camoufox/Playwright sessions and Taipower login."""
import os
import sys
import time
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values

try:
    from patchright.sync_api import TimeoutError as PWTimeout
    from patchright.sync_api import sync_playwright
except Exception:
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright

from taipower_ami import BASE, LOGIN_URL, ROOT

PROFILE_DIR = ROOT / "browser-profile"


def load_credentials(env_path: Optional[Path] = None) -> tuple[str, str]:
    """Load credentials from TAIPOWER_* environment variables or .env.

    Env vars take precedence so the HA add-on can inject options without a
    file. Electricity numbers are not configured — they are discovered from
    the account (see taipower_ami.fetcher.discover_customers).
    """
    env = dotenv_values(env_path or ROOT / ".env")
    user = os.getenv("TAIPOWER_USER") or env.get("USER") or ""
    password = os.getenv("TAIPOWER_PASSWORD") or env.get("PASSWORD") or ""
    if not user or not password:
        raise RuntimeError("USER / PASSWORD missing (.env or TAIPOWER_USER/TAIPOWER_PASSWORD)")
    return user, password


class CamoufoxSession:
    """Thin wrapper so Camoufox can be used with the same `with` shape as Playwright."""

    def __init__(self, headless: bool = True):
        from camoufox.sync_api import Camoufox

        self.camoufox = Camoufox(headless=headless)
        self.browser = None
        self.context = None

    def __enter__(self):
        self.browser = self.camoufox.start()
        self.context = self.browser.new_context()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        # Stop the underlying Playwright loop too. Without this the event loop
        # keeps running in this thread and any later sync-Playwright start in
        # the same thread fails with "Sync API inside the asyncio loop".
        try:
            self.camoufox.__exit__(exc_type, exc, tb)
        except Exception:
            pass
        return False


def open_context(pw, headless: bool, channel: Optional[str] = None, persistent: bool = True):
    """Open a browser context. Persistent context keeps cookies between runs;
    non-persistent avoids profile-lock issues when running automated flows."""
    kwargs: dict = {
        "headless": headless,
        "locale": "zh-TW",
    }
    if channel:
        kwargs["channel"] = channel

    if persistent:
        PROFILE_DIR.mkdir(exist_ok=True)
        return pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            viewport={"width": 1440, "height": 900},
            **kwargs,
        )

    browser = pw.chromium.launch(**kwargs)
    return browser.new_context(
        viewport={"width": 1440, "height": 900},
        locale="zh-TW",
    )


def wait_for_turnstile_token(page, timeout: int = 120) -> Optional[str]:
    """Wait until Cloudflare has populated the hidden response input."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = page.input_value('input[name="cf-turnstile-response"]')
        if value:
            return value
        time.sleep(0.5)
    return None


def click_turnstile_widget(page, retries: int = 3) -> bool:
    """Click the Turnstile checkbox to trigger solving.

    The checkbox sits at the left edge of the widget, so click by bounding box
    rather than the element centre — the container can be far wider than the
    checkbox (it stretches to the form width on this site), and a centre click
    misses it entirely.
    """
    for _ in range(retries):
        try:
            widget = page.locator(".cf-turnstile").first
            if widget.is_visible(timeout=5000):
                box = widget.bounding_box()
                if box:
                    page.mouse.click(box["x"] + 22, box["y"] + box["height"] / 2)
                    return True
                widget.click(timeout=5000)
                return True
        except Exception:
            pass
        try:
            iframe = page.locator("iframe[src*='challenges.cloudflare']").first
            if iframe.is_visible(timeout=5000):
                iframe.click(timeout=5000)
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def is_logged_in(page) -> bool:
    """Cheap probe: the portal home shows the AMI dashboard link when authed."""
    try:
        page.goto(f"{BASE}/ebpps2/", wait_until="domcontentloaded")
        if "/ebpps2/login" in page.url:
            return False
        return "amidashball" in page.content()
    except Exception:
        return False


def login(page, user: str, password: str, attempts: int = 3) -> bool:
    """Log in to Taipower by solving Turnstile in the browser doing the login.

    Measured over fresh sessions this succeeds ~7 of 8 times (arm64 4/4,
    amd64 3/4, 7-14s each), and retrying is what recovers the rest.

    Solving the challenge in a *separate* browser (the Turnstile-Solver API
    approach) does not work here: it mints a valid token in ~4s, but Taipower
    rejects the submit because Cloudflare binds the challenge to the context
    that solved it. That path was removed rather than left as a false safety
    net — retries are the real recovery mechanism.
    """
    for attempt in range(attempts):
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            if "/ebpps2/login" not in page.url:
                return True
            page.fill("#username", user)
            page.fill("#password", password)

            # The Turnstile widget is injected asynchronously; clicking before
            # it has rendered silently does nothing (seen on slower hosts and
            # on arm64), so wait for it and let it settle first.
            try:
                page.wait_for_selector(".cf-turnstile", state="visible", timeout=30000)
            except Exception:
                pass
            time.sleep(3)

            click_turnstile_widget(page)
            token = wait_for_turnstile_token(page, timeout=90)

            if not token:
                print(f"Turnstile produced no token (attempt {attempt + 1}/{attempts})",
                      file=sys.stderr)
                continue

            page.click("form[action='/ebpps2/login'] button[type='submit']")
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            if "/ebpps2/login" not in page.url:
                return True
        except Exception as exc:
            print(f"Login attempt {attempt + 1} failed: {exc}", file=sys.stderr)
    return False


def interactive_setup(page, user: str, password: str, timeout_seconds: int = 300) -> None:
    """Interactive first-time login. You solve Turnstile; the script waits."""
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    page.fill("#username", user)
    page.fill("#password", password)
    print(
        "\n  A browser window is open with your account and password already filled in.\n"
        '  Tick the Cloudflare "驗證您是人類" box, then click 登入.\n'
        f"  Waiting up to {timeout_seconds // 60 if timeout_seconds >= 60 else timeout_seconds}"
        f" {'minutes' if timeout_seconds >= 60 else 'seconds'}...\n"
    )
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if "/ebpps2/login" not in page.url:
            print(f"  Login detected ({page.url}). Session saved to {PROFILE_DIR.name}/.")
            return
        time.sleep(1)
    raise RuntimeError("Timed out waiting for a manual login.")


def get_turnstile_sitekey(page) -> Optional[str]:
    """Extract the Turnstile sitekey from the rendered login page."""
    import re

    el = page.locator(".cf-turnstile").first
    if el.count():
        sitekey = el.get_attribute("data-sitekey")
        if sitekey:
            return sitekey
    for script in page.locator("script").all_inner_texts():
        if "sitekey" in script:
            m = re.search(r"['\"]sitekey['\"]\s*:\s*['\"]([^'\"]+)", script)
            if m:
                return m.group(1)
    return None


def try_auto_login_in_context(page, user: str, password: str) -> bool:
    """Solve Turnstile in the same browser context, then log in."""
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        if not page.locator(".cf-turnstile").count():
            print("  No Turnstile widget found; cannot auto-login in context.")
            return False
        page.fill("#username", user)
        page.fill("#password", password)
        print("  Solving Turnstile in-context...")
        click_turnstile_widget(page)
        token = wait_for_turnstile_token(page, timeout=120)
        if not token:
            print("  Turnstile did not produce a token in context.")
            return False
        print(f"  Got Turnstile token ({len(token)} chars).")
        with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
            page.click("form[action='/ebpps2/login'] button[type='submit']")
        return "/ebpps2/login" not in page.url
    except Exception as exc:
        print(f"  In-context auto-login failed: {exc}")
        return False


__all__ = [
    "CamoufoxSession",
    "is_logged_in",
    "PROFILE_DIR",
    "load_credentials",
    "open_context",
    "wait_for_turnstile_token",
    "click_turnstile_widget",
    "login",
    "interactive_setup",
    "get_turnstile_sitekey",
    "try_auto_login_in_context",
]
