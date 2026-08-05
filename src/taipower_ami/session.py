"""Persistent browser session for the API server.

A single dedicated thread owns one Camoufox browser and executes jobs
serially. Playwright objects are not thread-safe and its sync API refuses to
start inside anyio's worker threads, so all browser work is funneled here.

The session stays logged in between requests: `login()` short-circuits when
Taipower redirects away from the login page, so only expired sessions pay the
Turnstile-solving cost again. On any job failure the browser is torn down,
recreated and the job retried once.
"""
import queue
import threading
import traceback
from typing import Any, Callable, Optional

from taipower_ami.auth import CamoufoxSession, is_logged_in, load_credentials, login


class BrowserWorker:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def submit(self, fn: Callable[[Any], Any], timeout: float = 300) -> Any:
        """Run fn(page) on the browser thread and return its result."""
        self._ensure_thread()
        done = threading.Event()
        holder: dict = {}
        self._queue.put((fn, done, holder))
        if not done.wait(timeout):
            raise TimeoutError(f"Browser job timed out after {timeout}s")
        if "error" in holder:
            raise holder["error"]
        return holder["value"]

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

    def _run(self) -> None:
        session: Optional[CamoufoxSession] = None
        page = None

        def teardown() -> None:
            nonlocal session, page
            if session is not None:
                try:
                    session.__exit__(None, None, None)
                except Exception:
                    pass
            session, page = None, None

        def ensure() -> None:
            nonlocal session, page
            fresh = session is None
            if fresh:
                session = CamoufoxSession(headless=self.headless)
                session.__enter__()
                page = session.context.pages[0] if session.context.pages else session.context.new_page()
            # On a warm session, probe the portal home instead of re-visiting
            # the login page: fewer Cloudflare challenges, and the home page
            # conveniently contains the AMI enkey link for discovery.
            if not fresh and is_logged_in(page):
                return
            user, password = load_credentials()
            if not login(page, user, password):
                raise RuntimeError("Login failed")

        while True:
            fn, done, holder = self._queue.get()
            try:
                try:
                    ensure()
                    holder["value"] = fn(page)
                except Exception as first_exc:
                    print(f"Browser job failed ({type(first_exc).__name__}: {first_exc}); "
                          "restarting session and retrying once", flush=True)
                    teardown()
                    ensure()
                    holder["value"] = fn(page)
            except BaseException as exc:
                traceback.print_exc()
                holder["error"] = exc
                teardown()
            finally:
                done.set()


_worker: Optional[BrowserWorker] = None
_worker_lock = threading.Lock()


def get_worker() -> BrowserWorker:
    """Process-wide singleton browser worker."""
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = BrowserWorker()
        return _worker


__all__ = ["BrowserWorker", "get_worker"]
