"""Publish pipeline (DOM path): create -> upload xlsx -> import -> publish.

Public API: ``publish_one`` / ``publish_many``. Both are library-safe: they
return ``FileResult`` objects, accept ``on_progress`` / ``should_cancel``
callbacks, and never print. ``wqu.cli`` owns all presentation.

Flow evidence: screenshots/step_01..13.png (2026-11-08). All waits are on
observable state (URLs, banner text, modal text) — never time.sleep().
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from wqu import selectors as S
from wqu.assign import AssignResult, assign_one
from wqu.errors import (
    PublishError,
    SessionExpiredError,
    UploadError,
    ValidationError,
    WquError,
)
from wqu.logging_setup import run_stamp
from wqu.output import FileResult, validate_join_link
from wqu.session import BrowserHandle, Session
from wqu.store import default_state_path, find_by_title, record_created
from wqu.upload import QuizData, load_xlsx

log = logging.getLogger(__name__)

WAYGROUND = "https://wayground.com"
QUIZ_EDIT_URL = WAYGROUND + "/activity/admin/quiz/{quiz_id}/edit"
QUIZ_URL = WAYGROUND + "/activity/admin/quiz/{quiz_id}"

_EDIT_URL_RE = re.compile(r"/activity/admin/quiz/(?P<quiz_id>[\w-]+)/edit")
_QUIZ_URL_RE = re.compile(r"/activity/admin/quiz/(?P<quiz_id>[\w-]+)(?:\?|$|#)")
_GAMEPLAY_URL_RE = re.compile(r"/gameplay/admin/activity/classic/(?P<activity_id>[\w-]+)")
_JOIN_IN_DOM_JS = r"""
() => {
  const rx = /https?:\/\/wayground\.com\/join\?gc=\d+[^\s"'<>]*/;
  const values = [];
  for (const el of document.querySelectorAll('input, textarea')) {
    const v = el.value || el.getAttribute('value') || '';
    if (rx.test(v)) values.push(v.match(rx)[0]);
  }
  const bodyText = document.body ? document.body.innerText : '';
  const m = bodyText.match(rx);
  if (m) values.push(m[0]);
  return values;
}
"""

MAX_TITLE_LEN = 64


@dataclass(frozen=True)
class PipelineOptions:
    """Everything the pipeline needs; pass explicitly, never via globals."""

    subject: str = "Information Technology (IT)"
    grade: str = "University"
    assign_class: str | None = None
    keep_draft: bool = False
    no_deadline: bool = True
    title: str | None = None
    force: bool = False
    force_import: bool = False
    fail_fast: bool = False
    headless: bool = True
    state_path: Path = field(default_factory=default_state_path)
    logs_dir: Path = field(default_factory=lambda: Path("wqu-logs"))


@dataclass(frozen=True)
class FileView:
    """Snapshot handed to the per-file assign-decision callback."""

    file: str
    title: str
    quiz_url: str | None
    is_split: bool
    part: int | None


@dataclass(frozen=True)
class DuplicateInfo:
    title: str
    file: str
    prior_quiz_ids: tuple[str, ...]


@dataclass(frozen=True)
class PublishResult:
    file: str
    title: str
    quiz_id: str
    quiz_url: str


ProgFn = Callable[[str], None] | None
DecideAssignFn = Callable[[FileView], str | None]
ResolveDuplicateFn = Callable[[DuplicateInfo], str]


def _log(on_progress: ProgFn, message: str) -> None:
    log.info(message)
    if on_progress is not None:
        on_progress(message)


def dismiss_premium_popup(page: Page, on_progress: ProgFn = None) -> bool:
    """Close the 'Unlock 45+ premium features' upsell if it is on screen."""
    if not S.visible(page, S.PREMIUM_POPUP_MARKER, timeout_ms=1_200):
        return False
    _log(on_progress, "Dismissing premium popup...")
    try:
        dialog = page.get_by_role("dialog").first
        S.click(dialog, S.CLOSE_BUTTON, timeout_ms=3_000)
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            return False
    try:
        page.get_by_text("Unlock 45+ premium features", exact=False).first.wait_for(
            state="hidden", timeout=5_000
        )
    except PlaywrightTimeoutError:
        log.warning("premium popup did not disappear; continuing")
    return True


def guard_session(page: Page) -> None:
    """Raise SessionExpiredError when a nav bounced us to a login page."""
    url = page.url.lower()
    if any(token in url for token in ("/login", "/signin", "/signup")):
        raise SessionExpiredError("Session expired mid-flow. Run 'wqu login' again.")


def _wait_for_either(page: Page, first: S.Sel, second: S.Sel, *, timeout_ms: int) -> str:
    """Wait until one of two markers appears; return 'first' or 'second'."""
    deadline_ms = timeout_ms
    step = 500
    waited = 0
    while waited < deadline_ms:
        if S.visible(page, first, timeout_ms=step):
            return "first"
        if S.visible(page, second, timeout_ms=step):
            return "second"
        waited += step
    raise SelectorNotFoundErrorBoth(first.key, second.key)


class SelectorNotFoundErrorBoth(WquError):
    def __init__(self, first: str, second: str) -> None:
        super().__init__(f"Timed out waiting for either {first!r} or {second!r} to appear")
