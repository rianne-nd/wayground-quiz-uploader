"""Wayground session management: login, check, doctor, discover, browser life.

A ``Session`` object owns the storage-state path and the log directory. It is
passed explicitly to the pipeline (no module-level state, no globals), so a
GUI can hold one Session and reuse it across runs.

Playwright is used synchronously (project rule: no asyncio). All browser
contexts use locale="en-US", timezone "Asia/Manila" and a 1440x900 viewport:
the publish modal's React form does not lay out correctly at smaller sizes
(brief Part 5).
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from wqu.errors import SessionExpiredError
from wqu.logging_setup import run_stamp
from wqu.store import default_state_path

log = logging.getLogger(__name__)

LOGIN_URL = "https://wayground.com/login"
HOME_URL = "https://wayground.com"
PROBE_URL = "https://wayground.com/activity/admin/assessment"
REACHABILITY_URL = "https://wayground.com"
# URL fragments that prove the app bounced us to a sign-in page.
LOGIN_URL_TOKENS = ("/login", "/signin", "/signup", "/auth")
# The authenticated assessment chooser's heading (see screenshots/step_01.png).
LOGGED_IN_MARKER = "How would you like to get started?"
# Admin URLs that only render for a signed-in user. Used as a fast-path
# fallback: the heading copy is not contractual, so we must not hang waiting
# for it when the URL already proves we are past the login wall.
PROTECTED_URL_MARKERS = ("/activity/", "/explore/", "/my-library/", "/library/")
# Accessible names that only appear when the browser is signed out.
SIGNED_OUT_CTA = re.compile(r"\b(log ?in|sign ?in|sign ?up)\b", re.IGNORECASE)

CONTEXT_KWARGS: dict = {
    "locale": "en-US",
    "timezone_id": "Asia/Manila",
    "viewport": {"width": 1440, "height": 900},
}

ProgFn = Callable[[str], None] | None
WaitFn = Callable[[], None] | None


@dataclass(frozen=True)
class LoginResult:
    ok: bool
    state_path: str
    detail: str


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    detail: str


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str
    hint: str = ""


@dataclass
class BrowserHandle:
    """Live browser objects for one run; created via ``Session.open``."""

    playwright: Playwright
    browser: Browser
    context: BrowserContext
    page: Page
    tracing: bool = False
    _trace_saved: str | None = field(default=None, repr=False)

    def save_trace(self, path: Path) -> str | None:
        """Stop tracing and write the zip; returns the path (or None)."""
        if not self.tracing:
            return None
        try:
            self.context.tracing.stop(path=str(path))
            self.tracing = False
            self._trace_saved = str(path)
            return str(path)
        except Exception as exc:  # trace must never mask the real error
            log.warning("could not save trace: %s", exc)
            self.tracing = False
            return None

    def discard_trace(self) -> None:
        if self.tracing:
            try:
                self.context.tracing.stop()
            except Exception:
                pass
            self.tracing = False

    def close(self) -> None:
        for closer in (self.context.close, self.browser.close, self.playwright.stop):
            try:
                closer()
            except Exception:
                pass


def _log_progress(on_progress: ProgFn, message: str) -> None:
    log.info(message)
    if on_progress is not None:
        on_progress(message)


def looks_like_login(url: str) -> bool:
    """True when ``url`` is a Wayground sign-in page (i.e. the session bounced)."""
    lowered = (url or "").lower()
    return any(token in lowered for token in LOGIN_URL_TOKENS)


def looks_like_protected_page(url: str) -> bool:
    """True when ``url`` is an admin page that only renders when signed in."""
    lowered = (url or "").lower()
    if looks_like_login(lowered):
        return False
    return any(token in lowered for token in PROTECTED_URL_MARKERS)


class Session:
    """Owns the storage state and log directory for one user."""

    def __init__(
        self,
        state_path: Path | str | None = None,
        logs_dir: Path | str = Path("wqu-logs"),
    ) -> None:
        self.state_path = Path(state_path) if state_path else default_state_path()
        self.logs_dir = Path(logs_dir)

    def exists(self) -> bool:
        return self.state_path.is_file()

    # -- browser lifetime ---------------------------------------------------

    @contextmanager
    def open(
        self,
        *,
        headless: bool = True,
        trace: bool = False,
        require_state: bool = True,
    ) -> Iterator[BrowserHandle]:
        """Launch Chromium with the stored session and yield a BrowserHandle."""
        if require_state and not self.exists():
            raise SessionExpiredError(
                f"No saved session at {self.state_path}. Run 'wqu login' again."
            )
        playwright = sync_playwright().start()
        browser = None
        try:
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(
                storage_state=str(self.state_path) if self.exists() else None,
                **CONTEXT_KWARGS,
            )
            try:
                context.grant_permissions(
                    ["clipboard-read", "clipboard-write"],
                    origin="https://wayground.com",
                )
            except Exception as exc:  # clipboard is best-effort; DOM scan is primary
                log.info("clipboard permissions not granted: %s", exc)
            handle = BrowserHandle(playwright=playwright, browser=browser, context=context, page=context.new_page())
            if trace:
                try:
                    context.tracing.start(screenshots=True, snapshots=True, sources=True)
                    handle.tracing = True
                except Exception as exc:
                    log.warning("could not start tracing: %s", exc)
            try:
                yield handle
            finally:
                handle.discard_trace()  # no-op if already saved
                handle.close()
        except Exception:
            if browser is None:
                try:
                    playwright.stop()
                except Exception:
                    pass
            raise

    # -- login / check --------------------------------------------------------

    def login(
        self,
        *,
        headless: bool = False,
        timeout_s: int = 300,
        wait_for_login: WaitFn = None,
        on_progress: ProgFn = None,
    ) -> LoginResult:
        """Open a browser for manual sign-in, then persist the storage state.

        ``wait_for_login`` is how the CLI blocks until the user pressed Enter;
        when None (library/GUI use) this polls the assessment page until the
        user is authenticated or ``timeout_s`` elapses.
        """
        with self.open(headless=headless, require_state=False) as handle:
            page = handle.page
            _log_progress(on_progress, "Opening Wayground login page...")
            try:
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45_000)
            except Exception:
                try:
                    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45_000)
                except Exception as exc:
                    log.info("could not open the Wayground login page: %s", exc)
            had_state = self.exists()
            if wait_for_login is not None:
                _log_progress(on_progress, "Sign in to Wayground in the opened browser, then confirm here.")
                wait_for_login()
                # Persist BEFORE probing. A slow or wrong probe must never
                # discard a sign-in the user just completed (that is exactly
                # how a successful login used to be lost: the probe waited on
                # a heading that had been reworded and the user closed the
                # terminal first).
                self._save_state(handle)
                _log_progress(on_progress, "Verifying the sign-in...")
                ok = self._probe(page, timeout_ms=15_000, on_progress=on_progress)
            else:
                _log_progress(on_progress, f"Waiting up to {timeout_s}s for sign-in to complete...")
                ok = self._poll_authenticated(page, timeout_s=timeout_s, on_progress=on_progress)
                if ok:
                    self._save_state(handle)
            if not ok:
                if not had_state:  # never leave an empty state file behind
                    try:
                        self.state_path.unlink()
                    except OSError:
                        pass
                return LoginResult(False, str(self.state_path), "Sign-in was not detected in time.")
            return LoginResult(True, str(self.state_path), f"Session saved to {self.state_path}")

    def _save_state(self, handle: BrowserHandle) -> None:
        """Write the context's cookies to ``state_path`` (0600 where supported)."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        handle.context.storage_state(path=str(self.state_path))
        try:
            os.chmod(self.state_path, 0o600)  # no-op/ignored on Windows ACLs
        except OSError:
            pass

    def check(self, *, headless: bool = True, on_progress: ProgFn = None) -> CheckResult:
        """Lightweight probe of the stored session."""
        if not self.exists():
            return CheckResult(False, f"No saved session at {self.state_path}. Run 'wqu login'.")
        with self.open(headless=headless) as handle:
            if self.is_authenticated(handle.page, timeout_ms=25_000, on_progress=on_progress):
                return CheckResult(True, "Session is valid.")
            return CheckResult(False, "Session looks expired. Run 'wqu login' again.")

    def is_authenticated(
        self,
        page: Page,
        *,
        timeout_ms: int = 15_000,
        on_progress: ProgFn = None,
    ) -> bool:
        """Probe an already-open page for a usable signed-in session.

        The pipeline calls this as a pre-flight: an expired session is then
        detected in a couple of seconds instead of walking the whole
        create/import flow and timing out (~40 s).
        """
        return self._probe(page, timeout_ms=timeout_ms, on_progress=on_progress)

    def _poll_authenticated(self, page: Page, *, timeout_s: int, on_progress: ProgFn) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._probe(page, timeout_ms=10_000, on_progress=None):
                return True
            page.wait_for_timeout(2_000)
        return False

    def _probe(self, page: Page, *, timeout_ms: int, on_progress: ProgFn) -> bool:
        """Navigate to a protected page and decide whether we are signed in.

        Observable signals (screenshots/step_01.png): the assessment chooser
        heading proves an authenticated admin session; a /login URL or a
        visible "Log in / Sign in" call-to-action proves the opposite.

        Detection is deliberately eager:
          * a login URL or a visible "Log in" CTA means signed out;
          * the exact heading copy is *not* contractual — after a short settle
            (~1.5 s) a protected admin URL with no login CTA counts as signed
            in. Waiting the full timeout for a heading that may have been
            reworded is what made sign-in look like a hang (and, if the user
            closed the terminal first, the state was never saved).
        """
        try:
            page.goto(PROBE_URL, wait_until="domcontentloaded", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            log.info("probe navigation timed out")
        except Exception as exc:  # redirects can abort navigation (net::ERR_ABORTED)
            log.info("probe navigation failed: %s", exc)

        marker = page.get_by_text(LOGGED_IN_MARKER, exact=False).first
        login_cta = page.get_by_role("link", name=SIGNED_OUT_CTA).or_(
            page.get_by_role("button", name=SIGNED_OUT_CTA)
        ).first

        waited = 0
        step = 250
        settle_ms = 1_500
        while True:
            url = page.url
            if looks_like_login(url):
                return False
            try:
                if marker.is_visible():
                    return True
            except Exception:
                pass
            try:
                if login_cta.is_visible():
                    return False
            except Exception:
                pass
            if waited >= settle_ms and looks_like_protected_page(url):
                return True
            if waited >= timeout_ms:
                break
            page.wait_for_timeout(step)
            waited += step

        # Unknown state: treat any admin URL that did not bounce to login as ok.
        authenticated = looks_like_protected_page(page.url)
        _log_progress(on_progress, f"Session probe inconclusive at {page.url}; assuming "
                      + ("valid" if authenticated else "expired"))
        return authenticated

    # -- doctor ---------------------------------------------------------------

    def doctor(self) -> list[DoctorCheck]:
        """Environment diagnostics (brief Part 4)."""
        return [
            self._check_python(),
            self._check_playwright(),
            self._check_state(),
            self._check_network(),
            self._check_logs_dir(),
            self._check_openpyxl(),
        ]

    def _check_python(self) -> DoctorCheck:
        version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ok = sys.version_info >= (3, 11)
        return DoctorCheck("Python >= 3.11", ok, f"found {version}", "Install Python 3.11 or newer.")

    def _check_playwright(self) -> DoctorCheck:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return DoctorCheck("playwright importable", False, "module not found",
                               'pip install -e ".[dev]" (or pip install playwright)')
        try:
            with sync_playwright_context() as p:
                exe = Path(p.chromium.executable_path)
            if exe.is_file():
                return DoctorCheck("Chromium installed", True, str(exe))
            return DoctorCheck("Chromium installed", False, f"expected at {exe}",
                               "python -m playwright install chromium")
        except Exception as exc:
            return DoctorCheck("Chromium installed", False, f"launch probe failed: {exc}",
                               "python -m playwright install chromium")

    def _check_state(self) -> DoctorCheck:
        if not self.exists():
            return DoctorCheck("stored session", False, f"missing {self.state_path}",
                               "wqu login")
        age_days = (time.time() - self.state_path.stat().st_mtime) / 86_400
        if age_days > 30:
            return DoctorCheck("stored session", False,
                               f"exists but {age_days:.0f} days old (looks stale)",
                               "wqu login")
        return DoctorCheck("stored session", True, f"{self.state_path} ({age_days:.1f} days old)")

    def _check_network(self) -> DoctorCheck:
        try:
            request = urllib.request.Request(REACHABILITY_URL, method="HEAD")
            with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310
                return DoctorCheck("wayground.com reachable", True, f"HTTP {response.status}")
        except urllib.error.HTTPError as exc:  # reached the server (4xx/5xx still proves network)
            return DoctorCheck("wayground.com reachable", True, f"HTTP {exc.code}")
        except Exception as exc:
            return DoctorCheck("wayground.com reachable", False, str(exc), "Check your internet connection.")

    def _check_logs_dir(self) -> DoctorCheck:
        probe = self.logs_dir / ".write-probe"
        try:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return DoctorCheck("wqu-logs writable", True, str(self.logs_dir.resolve()))
        except OSError as exc:
            return DoctorCheck("wqu-logs writable", False, str(exc), "Check folder permissions.")

    def _check_openpyxl(self) -> DoctorCheck:
        try:
            import openpyxl

            return DoctorCheck("openpyxl importable", True, f"version {openpyxl.__version__}")
        except ImportError:
            return DoctorCheck("openpyxl importable", False, "module not found", "pip install openpyxl")


@contextmanager
def sync_playwright_context():
    """Thin wrapper so doctor can reuse the same Playwright lifecycle idiom."""
    playwright = sync_playwright().start()
    try:
        yield playwright
    finally:
        try:
            playwright.stop()
        except Exception:
            pass


@dataclass(frozen=True)
class DiscoverResult:
    html: str
    aria: str
    screenshot: str
    site: str


def discover(
    url: str,
    *,
    session: Session,
    snaps_dir: Path | str = Path("snaps"),
    wait_for_user: WaitFn = None,
    on_progress: ProgFn = None,
) -> DiscoverResult:
    """Open ``url`` headed, let the user interact, dump DOM/aria/screenshot.

    On Ctrl+C (or when ``wait_for_user`` returns), saves
    ``<ts>-page.html``, ``<ts>-aria.txt`` and ``<ts>-shot.png``.
    """
    snaps = Path(snaps_dir)
    snaps.mkdir(parents=True, exist_ok=True)
    stamp = run_stamp()
    with session.open(headless=False) as handle:
        page = handle.page
        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        _log_progress(on_progress, "Interact now. Press Ctrl+C when you are done.")
        try:
            if wait_for_user is not None:
                wait_for_user()
            else:
                idle = threading.Event()
                while True:
                    idle.wait(0.25)
        except KeyboardInterrupt:
            pass
        html_path = snaps / f"{stamp}-page.html"
        aria_path = snaps / f"{stamp}-aria.txt"
        shot_path = snaps / f"{stamp}-shot.png"
        html_path.write_text(page.content(), encoding="utf-8")
        try:
            aria = page.locator("body").aria_snapshot()
        except Exception as exc:
            aria = f"(aria snapshot unavailable: {exc})"
        aria_path.write_text(aria, encoding="utf-8")
        page.screenshot(path=str(shot_path), full_page=True)
        _log_progress(on_progress, f"Snapshot saved: {shot_path}")
        return DiscoverResult(html=str(html_path), aria=str(aria_path), screenshot=str(shot_path), site=page.url)
