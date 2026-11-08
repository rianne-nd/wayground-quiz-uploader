"""Library management: active-quiz counting and archiving (free-tier cap).

Wayground free accounts cap *active* quizzes at 20 ("11 / 20 activities
created"). Before a batch is uploaded, ``ensure_capacity`` checks the count
and — when the batch would exceed the cap — archives quizzes that the caller
selects (``--auto-archive-oldest N`` or an interactive chooser in the CLI).

LIVE 2026-11-09 DOM notes (probes; see docs/selectors.md):
  * counter:  [data-testid=item-count-created-by-me-pill] -> "11 / 20"
  * row:      [data-testid=ui-table-row-{quiz_id}]
  * title:    [data-testid=activity-card-title]
  * age:      relative text in the row ("47 minutes ago", "3 days ago")
  * checkbox: input[data-testid=ui-table-row-checkbox-{quiz_id}]
              (the input is styled-covered — toggle via a JS click)
  * bulk bar: "N activity selected" + a button whose aria-label is "Archive";
              a confirmation dialog may follow (handled defensively).

Library-safe contract: returns dataclasses, prints nothing; prompts live in
cli.py behind the ``choose_archive`` callback.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from wqu import selectors as S
from wqu.errors import LibraryError, LibraryLimitError, SelectorNotFoundError, SessionExpiredError

log = logging.getLogger(__name__)

LIBRARY_URL = "https://wayground.com/my-library/admin/my-library/createdByMe"
FREE_LIMIT = 20

_ROW_SEL = (
    '[data-testid^="ui-table-row-"]'
    ':not([data-testid*="anchor-link"]):not([data-testid*="checkbox"])'
)
_AGE_RE = re.compile(
    r"(\d+)\s*(minute|hour|day|week|month|year)s?\s*ago", re.IGNORECASE
)
_AGE_FACTORS = {"minute": 1, "hour": 60, "day": 1440, "week": 10_080,
                "month": 43_200, "year": 525_600}

ProgFn = Callable[[str], None] | None
ChooseArchiveFn = Callable[["LibraryScan", int], Sequence[str]]


@dataclass(frozen=True)
class LibraryItem:
    quiz_id: str
    title: str
    age_text: str
    age_minutes: int | None  # parsed from age_text; None when unparseable
    row_index: int           # 1-based, in displayed order (newest first)


@dataclass(frozen=True)
class LibraryScan:
    total: int               # active quizzes (counter when available)
    limit: int               # the account cap (20 for free accounts)
    items: tuple[LibraryItem, ...]
    counter_text: str | None


def _log(on_progress: ProgFn, message: str) -> None:
    log.info(message)
    if on_progress is not None:
        on_progress(message)


def parse_age_minutes(text: str | None) -> int | None:
    """Parse Wayground's relative ages like '47 minutes ago' / '3 days ago'."""
    if not text:
        return None
    lowered = text.strip().lower()
    if "just now" in lowered:
        return 0
    if "yesterday" in lowered:
        return 1440
    match = _AGE_RE.search(lowered)
    if not match:
        return None
    return int(match.group(1)) * _AGE_FACTORS[match.group(2).lower()]


def needs_archiving(total: int, new_count: int, limit: int = FREE_LIMIT) -> int:
    """How many slots must be freed before uploading ``new_count`` quizzes."""
    return max(0, total + new_count - limit)


def oldest_ids(items: Sequence[LibraryItem], count: int) -> tuple[str, ...]:
    """Quiz ids of the ``count`` oldest items (known ages first, then tail order)."""
    if count <= 0:
        return ()
    known = sorted((i for i in items if i.age_minutes is not None),
                   key=lambda i: i.age_minutes or 0, reverse=True)
    unknown = [i for i in items if i.age_minutes is None]
    ordered = known + list(reversed(unknown))
    return tuple(i.quiz_id for i in ordered[:count])


def select_for_archive(spec: str, scan: LibraryScan, needed: int) -> tuple[str, ...]:
    """Parse an interactive selection string into quiz ids (pure, testable).

    Supported: "" (skip) | "a"/"all" | "o<N>"/"oldest <N>" | "1,4,7" (row #s).
    """
    text = (spec or "").strip().lower()
    if not text:
        return ()
    if text in ("a", "all"):
        return tuple(i.quiz_id for i in scan.items)
    match = re.fullmatch(r"(?:o|oldest)\s*(\d+)", text)
    if match:
        return oldest_ids(scan.items, int(match.group(1)))
    wanted = {int(token) for token in re.findall(r"\d+", text)}
    return tuple(i.quiz_id for i in scan.items if i.row_index in wanted)


def _dismiss_library_tip(page: Page) -> None:
    tip = S.first_visible(page, S.GOT_IT_BUTTON)
    if tip is None:
        return
    try:
        tip.click(timeout=2_000)
    except Exception:
        try:
            tip.evaluate("el => el.click()")
        except Exception:
            pass


def scan_library(page: Page, *, on_progress: ProgFn = None) -> LibraryScan:
    """Open the library page and return the active-quiz count and rows."""
    _log(on_progress, "Checking the library (free-tier cap)...")
    page.goto(LIBRARY_URL, wait_until="domcontentloaded", timeout=60_000)
    if any(token in page.url.lower() for token in ("/login", "/signin", "/signup")):
        raise SessionExpiredError("Session expired. Run 'wqu login' again.")
    _dismiss_library_tip(page)

    total: int | None = None
    limit: int | None = None
    counter_text: str | None = None
    try:
        counter_text = (S.resolve(page, S.LIBRARY_COUNTER, timeout_ms=20_000).inner_text() or "").strip()
        match = re.search(r"(\d+)\s*/\s*(\d+)", counter_text)
        if match:
            total, limit = int(match.group(1)), int(match.group(2))
    except SelectorNotFoundError:
        log.info("library counter not found; falling back to the row count")

    rows = page.locator(_ROW_SEL)
    if rows.count() == 0:
        try:
            rows.first.wait_for(state="visible", timeout=8_000)
        except PlaywrightTimeoutError:
            pass
    items: list[LibraryItem] = []
    for index in range(rows.count()):
        row = rows.nth(index)
        testid = row.get_attribute("data-testid") or ""
        quiz_id = testid.replace("ui-table-row-", "")
        if not quiz_id:
            continue
        try:
            title = (row.locator('[data-testid="activity-card-title"]').first.inner_text() or "").strip()
        except Exception:
            title = "(untitled)"
        try:
            row_text = row.inner_text()
        except Exception:
            row_text = ""
        found = _AGE_RE.search(row_text) or re.search(r"just now|yesterday", row_text, re.IGNORECASE)
        age_text = found.group(0).replace("\xa0", " ").strip() if found else ""
        items.append(LibraryItem(
            quiz_id=quiz_id,
            title=title or "(untitled)",
            age_text=age_text,
            age_minutes=parse_age_minutes(age_text),
            row_index=index + 1,
        ))
    if total is None:
        total = len(items)
    if not limit:
        limit = FREE_LIMIT
    _log(on_progress, f"Library: {total}/{limit} active quizzes "
                      f"({len(items)} row(s) on the page).")
    return LibraryScan(total=total, limit=limit, items=tuple(items), counter_text=counter_text)


def _maybe_confirm_archive(page: Page) -> bool:
    """Accept the '2 things to consider before you archive' confirmation.

    LIVE 2026-11-09: the modal's primary button is
    ``[data-testid=archive-activity-modal-primary-button]`` with the label
    "Archive (1)" — the earlier ^archive$ regex missed the counter suffix.
    """
    waited = 0
    while waited < 4_000:
        button = page.locator('[data-testid="archive-activity-modal-primary-button"]')
        for i in range(button.count()):
            element = button.nth(i)
            try:
                if not element.is_visible():
                    continue
                element.click(timeout=3_000)
                return True
            except Exception:
                try:
                    element.evaluate("el => el.click()")
                    return True
                except Exception:
                    continue
        # Fallback: any dialog-scoped confirm-looking button.
        dialogs = page.get_by_role("dialog")
        for i in range(dialogs.count()):
            dialog = dialogs.nth(i)
            try:
                if not dialog.is_visible():
                    continue
            except Exception:
                continue
            for name in (r"^archive(\s*\(\d+\))?$", r"^confirm$", r"^yes"):
                candidate = dialog.get_by_role("button", name=re.compile(name, re.IGNORECASE))
                if candidate.count():
                    try:
                        candidate.first.click(timeout=3_000)
                        return True
                    except Exception:
                        continue
        page.wait_for_timeout(300)
        waited += 300
    return False


def archive_items(page: Page, items: Sequence[LibraryItem], *, on_progress: ProgFn = None) -> int:
    """Tick the rows' checkboxes and click the bulk-bar Archive button."""
    if not items:
        return 0
    _log(on_progress, f"Archiving {len(items)} quiz(zes) on the library page...")
    if "my-library" not in page.url:
        page.goto(LIBRARY_URL, wait_until="domcontentloaded", timeout=60_000)
        _dismiss_library_tip(page)

    selected = 0
    for item in items:
        box = page.locator(f'[data-testid="ui-table-row-checkbox-{item.quiz_id}"]')
        try:
            if box.count() == 0:  # possibly scrolled out of the (virtualized) table
                row = page.locator(f'[data-testid="ui-table-row-{item.quiz_id}"]')
                row.first.scroll_into_view_if_needed(timeout=3_000)
            if not box.first.is_checked():
                box.first.evaluate("el => el.click()")  # styled-covered input
            if box.first.is_checked():
                selected += 1
        except Exception as exc:
            log.warning("could not select %r for archiving: %s", item.title, exc)
    if selected == 0:
        raise LibraryError("could not select any library rows for archiving")

    try:
        text = S.resolve(page, S.LIBRARY_SELECTION_TEXT, timeout_ms=4_000).inner_text()
        count = re.search(r"(\d+)", text or "")
        if count and int(count.group(1)) != selected:
            log.warning("bulk bar reports %s selected but %d were ticked", count.group(1), selected)
    except SelectorNotFoundError:
        pass

    try:
        S.resolve(page, S.LIBRARY_ARCHIVE_BUTTON, timeout_ms=5_000).click(timeout=5_000)
    except Exception as exc:
        raise LibraryError("could not click the library 'Archive' button") from exc
    _maybe_confirm_archive(page)

    missing = 0
    for item in items:
        try:
            page.locator(f'[data-testid="ui-table-row-{item.quiz_id}"]').first.wait_for(
                state="detached", timeout=15_000
            )
        except PlaywrightTimeoutError:
            missing += 1
            log.warning("row for %r is still listed after archiving", item.title)
    archived = selected - missing
    _log(on_progress, f"Archived {archived} quiz(zes).")
    return archived


def ensure_capacity(
    page: Page,
    *,
    new_count: int,
    auto_archive_oldest: int | None = None,
    choose_archive: ChooseArchiveFn | None = None,
    on_progress: ProgFn = None,
) -> tuple[int, int]:
    """Make room for ``new_count`` quizzes; returns (archived, active total).

    Raises LibraryLimitError when the cap still cannot be respected (nothing
    was archived and no chooser was supplied, or the selection was too small).
    """
    scan = scan_library(page, on_progress=on_progress)
    needed = needs_archiving(scan.total, new_count, scan.limit)
    if needed <= 0:
        return 0, scan.total

    _log(on_progress, f"Library near its cap: {scan.total}/{scan.limit} active; "
                      f"this batch needs {needed} more slot(s).")
    picks: tuple[str, ...] = ()
    if auto_archive_oldest is not None:
        picks = oldest_ids(scan.items, auto_archive_oldest)
        _log(on_progress, f"--auto-archive-oldest: archiving {len(picks)} of the oldest quiz(zes)...")
    elif choose_archive is not None:
        picks = tuple(choose_archive(scan, needed))

    if picks:
        by_id = {item.quiz_id: item for item in scan.items}
        chosen = [by_id[quiz_id] for quiz_id in picks if quiz_id in by_id]
        archive_items(page, chosen, on_progress=on_progress)
        scan = scan_library(page, on_progress=on_progress)
        needed = needs_archiving(scan.total, new_count, scan.limit)

    if needed > 0:
        raise LibraryLimitError(
            f"library still full: {scan.total}/{scan.limit} active and this batch needs "
            f"{needed} more slot(s). Archive quizzes with 'wqu library' or pass "
            f"--auto-archive-oldest N."
        )
    return len(picks), scan.total


def open_ui(
    *,
    session,
    url: str = LIBRARY_URL,
    wait_for_user: Callable[[], None] | None = None,
    on_progress: ProgFn = None,
) -> None:
    """Open the library in a headed browser for manual management."""
    with session.open(headless=False) as handle:
        handle.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        _log(on_progress, "Library is open. Archive/manage quizzes in the browser; "
                          "close it (Ctrl+C / Enter) when done.")
        if wait_for_user is not None:
            wait_for_user()
        else:
            idle = threading.Event()
            try:
                while True:
                    idle.wait(0.25)
            except KeyboardInterrupt:
                pass
