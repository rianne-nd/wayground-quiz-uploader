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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from wqu import classes as classlib
from wqu import library as lib
from wqu import selectors as S
from wqu.assign import assign_one
from wqu.errors import (
    AssignError,
    ClassCreateError,
    ClassNotFoundError,
    LibraryLimitError,
    PublishError,
    SelectorNotFoundError,
    SessionExpiredError,
    UploadError,
    ValidationError,
    WquError,
)
from wqu.logging_setup import run_stamp
from wqu.output import FileResult, validate_join_link
from wqu.session import BrowserHandle, Session, looks_like_login
from wqu.store import default_state_path, find_by_title, record_created
from wqu.upload import QuizData, load_xlsx, title_from_filename

log = logging.getLogger(__name__)

WAYGROUND = "https://wayground.com"
QUIZ_EDIT_URL = WAYGROUND + "/activity/admin/quiz/{quiz_id}/edit"
QUIZ_URL = WAYGROUND + "/activity/admin/quiz/{quiz_id}"

_EDIT_URL_RE = re.compile(r"/activity/admin/quiz/(?P<quiz_id>[\w-]+)/edit")
# Quiz page (no /edit) — also excludes /quiz/homework/... because [\w-]+ must end the path.
_QUIZ_PAGE_RE = re.compile(r"/activity/admin/quiz/[\w-]+(?:[?#].*)?$")
_HOMEWORK_URL_RE = re.compile(r"/activity/admin/quiz/homework/(?P<homework_id>[\w-]+)")
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
    class_name: str | None = None          # canonical: class to assign to
    assign_class: str | None = None        # legacy alias for class_name
    create_class_if_missing: bool = False  # create class_name when it is absent
    keep_draft: bool = False
    no_deadline: bool = True
    title: str | None = None
    force: bool = False
    force_import: bool = False
    fail_fast: bool = False
    headless: bool = True
    auto_archive_oldest: int | None = None  # free-tier cap: archive N oldest first
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
        dialog = S.find_dialog(page, S.PREMIUM_POPUP_MARKER, timeout_ms=4_000)
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
    if looks_like_login(page.url):
        raise SessionExpiredError("Session expired mid-flow. Run 'wqu login' again.")


def _wait_for_either(page: Page | S.Locator, first: S.Sel, second: S.Sel, *, timeout_ms: int) -> str:
    """Wait until one of two markers appears; return 'first' or 'second'."""
    waited = 0
    step = 500
    while waited < timeout_ms:
        if S.visible(page, first, timeout_ms=step):
            return "first"
        if S.visible(page, second, timeout_ms=step):
            return "second"
        waited += step
    raise SelectorNotFoundError(f"banner:{first.key}|{second.key}")


def _snapshot(page: Page, logs_dir: Path, run_id: str, file: Path, on_progress: ProgFn) -> str | None:
    """Full-page screenshot for failure triage; returns the path."""
    target = Path(logs_dir) / f"{run_id}-{file.stem}.png"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target), full_page=True)
        _log(on_progress, f"Screenshot saved: {target}")
        return str(target)
    except Exception as exc:
        log.warning("could not save screenshot: %s", exc)
        return None


_ERROR_ROW_JS = r"""
() => {
  const rows = Array.from(document.querySelectorAll('tr, [role="row"]'));
  const found = [];
  for (const r of rows) {
    const bg = getComputedStyle(r).backgroundColor || '';
    const m = bg.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
    if (!m) continue;
    const [rr, gg, bb] = [Number(m[1]), Number(m[2]), Number(m[3])];
    const whitish = rr > 250 && gg > 250 && bb > 250;
    const pinkish = rr > 235 && rr - gg > 8 && (rr - bb) > -10 && !whitish;
    if (!pinkish) continue;
    const cell = r.querySelector('td, [role="cell"], [role="gridcell"]');
    const n = cell ? parseInt((cell.textContent || '').trim(), 10) : NaN;
    if (Number.isFinite(n)) found.push(n);
  }
  return found;
}
"""


def scrape_error_rows(scope: Page | S.Locator) -> list[int]:
    """Heuristic: spreadsheet row numbers of rows highlighted pink in the preview.

    Clicks 'Next error' are done by the caller; this only reads the DOM.
    """
    try:
        rows = scope.evaluate(_ERROR_ROW_JS)
        return sorted({int(n) for n in rows if n})
    except Exception as exc:
        log.debug("error-row scrape failed: %s", exc)
        return []


def _attach_workbook(page: Page, dialog: S.Locator, path: Path, on_progress: ProgFn) -> None:
    """Set the hidden file input; fall back to intercepting the file chooser."""
    if S.set_files_via_inputs(page, dialog, path):
        _log(on_progress, f"Workbook attached: {path.name}")
        return
    try:
        with page.expect_file_chooser(timeout=5_000) as chooser_info:
            S.click(dialog, S.UPLOAD_FROM_DEVICE)
        chooser_info.value.set_files(str(path))
        _log(on_progress, f"Workbook attached via chooser: {path.name}")
        return
    except PlaywrightTimeoutError as exc:
        if S.set_files_via_inputs(page, dialog, path):  # input may have rendered late
            return
        raise UploadError("could not attach the workbook: no file input and no file chooser") from exc


def upload_xlsx(
    page: Page,
    data: QuizData,
    *,
    options: PipelineOptions,
    on_progress: ProgFn = None,
) -> None:
    """Editor step: Import existing files -> Spreadsheet -> attach -> banner."""
    section = _scroll_to_import_section(page, on_progress)
    section.scroll_into_view_if_needed()
    _log(on_progress, "Opening the spreadsheet import modal...")
    S.click(page, S.SPREADSHEET_BUTTON)
    dialog = S.find_dialog(page, S.IMPORT_DIALOG_TITLE)
    _attach_workbook(page, dialog, data.path, on_progress)
    _log(on_progress, "Waiting for the import preview...")
    outcome = _wait_for_either(dialog, S.BANNER_OK, S.BANNER_BAD, timeout_ms=120_000)
    if outcome == "second":
        rows = scrape_error_rows(dialog)
        for _ in range(5):  # click through "Next error" to surface more highlighted rows
            if not S.visible(dialog, S.NEXT_ERROR_BUTTON, timeout_ms=300):
                break
            try:
                S.click(dialog, S.NEXT_ERROR_BUTTON, timeout_ms=2_000)
                page.wait_for_timeout(250)
                rows = sorted(set(rows) | set(scrape_error_rows(dialog)))
            except Exception:
                break
        where = f"rows {', '.join(str(r) for r in rows)}" if rows else "row numbers not exposed in the DOM"
        if not options.force_import:
            try:
                S.click(dialog, S.CANCEL_BUTTON, timeout_ms=3_000)
                dialog.wait_for(state="hidden", timeout=5_000)
            except Exception:
                pass
            raise UploadError(f"Import preview errors (Invalid correct option) — {where}", rows=rows,
                              banner="Invalid correct option")
        _log(on_progress, f"WARNING: --force-import set; clicking Import despite errors ({where})")
    else:
        _log(on_progress, "Preview looks good.")
    _log(on_progress, "Importing questions...")
    try:
        S.click(dialog, S.IMPORT_CONFIRM_BUTTON, timeout_ms=10_000)
    except Exception as exc:
        if not options.force_import:
            raise UploadError("could not click Import in the preview modal") from exc
        try:  # forced path: the button may be disabled; try a forced click anyway
            S.resolve(dialog, S.IMPORT_CONFIRM_BUTTON).click(force=True, timeout=5_000)
        except Exception:
            raise UploadError("--force-import set but the Import button could not be clicked") from exc
    try:
        dialog.wait_for(state="hidden", timeout=15_000)
    except PlaywrightTimeoutError:
        log.info("import dialog still visible after Import; continuing once questions appear")


def _wait_questions_loaded(page: Page, *, timeout_ms: int = 45_000) -> None:
    """Editor shows the count 'N Questions' / question cards once import lands."""
    waited = 0
    step = 500
    while waited < timeout_ms:
        try:
            cards = page.locator('[data-testid^="question-details-card-"]')
            if cards.count() > 0:
                return
        except Exception:
            pass
        if S.visible(page, S.QUESTIONS_COUNT_MARKER, timeout_ms=step):
            return
        waited += step
    raise SelectorNotFoundError("questions_loaded")


def create_quiz(page: Page, *, on_progress: ProgFn = None) -> str:
    """Assessment chooser -> 'Start from scratch' -> editor; returns quiz_id."""
    _log(on_progress, "Navigating to the assessment page...")
    page.goto(S.URL_ASSESSMENT, wait_until="domcontentloaded", timeout=60_000)
    guard_session(page)
    dismiss_premium_popup(page, on_progress)  # brief: almost always shown on first load
    S.click(page, S.START_FROM_SCRATCH, timeout_ms=20_000)
    page.wait_for_url(_EDIT_URL_RE, timeout=30_000)
    match = _EDIT_URL_RE.search(page.url)
    if not match:
        raise PublishError(f"could not read the quiz id from {page.url}")
    # LIVE 2026-11-08: the "Import existing files" section is lazily rendered;
    # the top-bar publish button is the reliable editor-ready marker.
    S.resolve(page, S.PUBLISH_BUTTON_TOP, timeout_ms=30_000)
    _log(on_progress, f"Editor ready (quiz id {match.group('quiz_id')})")
    return match.group("quiz_id")


def resume_quiz(page: Page, quiz_id: str, *, on_progress: ProgFn = None) -> None:
    """Reopen an existing (draft) quiz editor."""
    _log(on_progress, f"Resuming quiz {quiz_id}...")
    page.goto(QUIZ_EDIT_URL.format(quiz_id=quiz_id), wait_until="domcontentloaded", timeout=60_000)
    guard_session(page)
    S.resolve(page, S.PUBLISH_BUTTON_TOP, timeout_ms=30_000)


def _question_count(page: Page) -> int | None:
    """Number of questions currently in the editor, when the count chip exists."""
    try:
        loc = S.resolve(page, S.QUESTIONS_COUNT_MARKER, timeout_ms=5_000)
        match = re.search(r"(\d+)\s+Questions", loc.inner_text())
        return int(match.group(1)) if match else None
    except SelectorNotFoundError:
        return None


def _scroll_to_import_section(page: Page, on_progress: ProgFn) -> Locator:
    """Scroll the editor until the (lazy-rendered) import section appears."""
    js_bottom = ("() => { const el = document.scrollingElement || document.documentElement; "
                 "el.scrollTop = el.scrollHeight; return el.scrollTop; }")
    for _attempt in range(8):
        if S.visible(page, S.IMPORT_EXISTING_SECTION, timeout_ms=700):
            return S.resolve(page, S.IMPORT_EXISTING_SECTION)
        try:
            page.mouse.move(720, 450)
            page.mouse.wheel(0, 1_200)
            page.evaluate(js_bottom)
        except Exception:
            pass
        page.wait_for_timeout(300)
    _log(on_progress, "Scrolling further to reach 'Import existing files'...")
    return S.resolve(page, S.IMPORT_EXISTING_SECTION, timeout_ms=15_000)


def _verify_dropdown_value(trigger: Locator, value: str, on_progress: ProgFn) -> None:
    """The trigger's text becomes the chosen value; warn (don't fail) if not."""
    try:
        text = (trigger.inner_text() or "").strip()
    except Exception:
        return
    if value.lower() not in text.lower():
        _log(on_progress, f"WARNING: dropdown shows {text!r} instead of {value!r}")


def _dropdown_trigger(dialog: Locator, sel: S.Sel, fallback_index: int) -> Locator:
    """Prefer the label-anchored locator; fall back to the Nth 'Select...'."""
    try:
        return S.resolve(dialog, sel, timeout_ms=3_000)
    except SelectorNotFoundError:
        triggers = dialog.get_by_role("button", name=re.compile(r"select", re.I))
        if triggers.count() == 0:
            triggers = dialog.get_by_text("Select...", exact=True)
        if fallback_index < triggers.count():
            return triggers.nth(fallback_index)
        raise


def publish_quiz(
    page: Page,
    *,
    title: str,
    options: PipelineOptions,
    quiz_id: str,
    on_progress: ProgFn = None,
) -> str:
    """Fill the 'Great, you're almost done' modal and publish.

    Returns the quiz page URL (no /edit), per screenshots/step_12-14.png.
    """
    _log(on_progress, "Opening the publish modal...")
    # LIVE 2026-11-08: the top-right button's accessible name is "Publish quiz"
    # (aria-label override); the old "^Publish$" pattern matched nothing.
    S.click_outside_dialogs(page, r"publish( quiz)?$", timeout_ms=15_000)

    # LIVE 2026-11-08 (second finding): clicking Publish on an already-published
    # quiz with no changes skips the modal and navigates straight to the quiz
    # page. Race the modal against that navigation.
    state: str | None = None
    waited = 0
    while waited < 20_000:
        if S.visible(page, S.PUBLISH_DIALOG_TITLE, timeout_ms=400):
            state = "dialog"
            break
        if _EDIT_URL_RE.search(page.url) is None:
            state = "published"
            break
        page.wait_for_timeout(400)
        waited += 400
    if state == "published":
        _log(on_progress, "Quiz was already published; the app skipped the modal (no changes).")
        return f"{WAYGROUND}/activity/admin/quiz/{quiz_id}"
    if state is None:
        raise PublishError("timed out waiting for the publish modal to appear")
    dialog = S.find_dialog(page, S.PUBLISH_DIALOG_TITLE)

    name = title.strip() or "Untitled Quiz"
    if len(name) > MAX_TITLE_LEN:
        _log(on_progress, f"Title trimmed to {MAX_TITLE_LEN} characters (platform limit).")
        name = name[:MAX_TITLE_LEN]
    S.fill(dialog, S.NAME_INPUT, name)

    _log(on_progress, f"Choosing Subject: {options.subject}")
    subject_trigger = _dropdown_trigger(dialog, S.SUBJECT_SELECT, 0)
    if not S.choose_dropdown_option(
        page, subject_trigger, options.subject,
        known_options=("Mathematics", "English", "Portuguese"),
    ):
        raise PublishError(f"could not choose Subject {options.subject!r} in the publish modal")
    _verify_dropdown_value(subject_trigger, options.subject, on_progress)

    _log(on_progress, f"Choosing Grade: {options.grade}")
    grade_trigger = _dropdown_trigger(dialog, S.GRADE_SELECT, 1)
    if not S.choose_dropdown_option(
        page, grade_trigger, options.grade,
        known_options=("Kindergarten", "Elementary (1st - 5th)", "1st Grade", "2nd Grade"),
    ):
        raise PublishError(f"could not choose Grade {options.grade!r} in the publish modal")
    _verify_dropdown_value(grade_trigger, options.grade, on_progress)

    _log(on_progress, "Publishing the quiz...")
    S.click(dialog, S.PUBLISH_MODAL_PUBLISH, timeout_ms=10_000)

    waited = 0
    outcome: str | None = None
    while waited < 60_000:
        if S.visible(dialog, S.PUBLISH_ERROR_HINTS, timeout_ms=400):
            outcome = "errors"
            break
        try:
            if not dialog.is_visible():
                outcome = "closed"
                break
        except Exception:
            outcome = "closed"
            break
        page.wait_for_timeout(500)
        waited += 500
    if outcome == "errors":
        hints = []
        for hint in S.PUBLISH_ERROR_HINTS.candidates:
            loc = dialog.get_by_text(hint[1], exact=False)
            for i in range(loc.count()):
                text = (loc.nth(i).inner_text() or "").strip()
                if text and text not in hints:
                    hints.append(text)
        raise PublishError("the publish modal rejected the form: " + ("; ".join(hints) or "required fields missing"))
    if outcome is None:
        # LIVE 2026-11-08: observed once — the modal button entered its loading
        # state but no publish request left the browser (network/server stall).
        raise PublishError(
            "the app did not finish publishing within 60s (modal button still loading). "
            "The quiz remains a draft; re-running this file resumes it."
        )

    # LIVE 2026-11-09 (headed run): the "Great work!" popup appears while the
    # URL is STILL /edit — dismiss it first, then wait for the URL to drop
    # /edit. Waiting for the URL first caused a visible stall.
    _log(on_progress, "Quiz published; closing the success modal...")
    try:
        saved_dialog = S.find_dialog(page, S.SAVED_DIALOG_MARKER, timeout_ms=15_000)
        try:
            S.click(saved_dialog, S.CLOSE_BUTTON, timeout_ms=5_000)
            saved_dialog.wait_for(state="hidden", timeout=5_000)
        except Exception:
            page.keyboard.press("Escape")
    except SelectorNotFoundError:
        log.info("saved-success modal not found; continuing on the quiz page")

    try:
        page.wait_for_url(_QUIZ_PAGE_RE, timeout=20_000)
    except PlaywrightTimeoutError:
        # Deterministic fallback: the quiz page URL is always valid.
        log.info("URL did not drop /edit after publish; navigating to the quiz page directly")
        page.goto(QUIZ_URL.format(quiz_id=quiz_id), wait_until="domcontentloaded", timeout=45_000)
        guard_session(page)
    return f"{WAYGROUND}/activity/admin/quiz/{quiz_id}"


_CLIPBOARD_POLL_JS = (
    r"() => navigator.clipboard.readText()"
    r".then(t => /wayground\.com\/join\?gc=\d+/.test(t)).catch(() => false)"
)


def _scan_dom_for_join(page: Page) -> str | None:
    try:
        values = page.evaluate(_JOIN_IN_DOM_JS)
    except Exception as exc:
        log.debug("join-link DOM scan failed: %s", exc)
        return None
    for value in values or []:
        validated = validate_join_link(value)
        if validated:
            return validated
    return None


def extract_join_link(page: Page, *, on_progress: ProgFn = None) -> str | None:
    """DOM first, then 'Copy Join link' + clipboard read (brief step 7).

    Never fabricates: the clipboard is poisoned with a sentinel before the
    click so a stale link from a previous run cannot be mistaken for this one.
    """
    link = _scan_dom_for_join(page)
    if link:
        _log(on_progress, f"Join link found in the DOM: {link}")
        return link
    if not S.visible(page, S.COPY_JOIN_LINK_BUTTON, timeout_ms=8_000):
        _log(on_progress, "No 'Copy Join link' button found on this page.")
        return _scan_dom_for_join(page)
    try:
        page.evaluate("() => navigator.clipboard.writeText('wqu-pending-clipboard')")
    except Exception as exc:
        log.debug("could not pre-clear clipboard: %s", exc)
    _log(on_progress, "Clicking 'Copy Join link'...")
    S.click(page, S.COPY_JOIN_LINK_BUTTON, timeout_ms=10_000)
    try:
        page.wait_for_function(_CLIPBOARD_POLL_JS, timeout=10_000)
    except PlaywrightTimeoutError:
        log.info("clipboard did not receive a join link in time")
    clip = None
    try:
        clip = page.evaluate("() => navigator.clipboard.readText()")
    except Exception as exc:
        _log(on_progress, f"Clipboard read failed: {exc}")
    validated = validate_join_link(clip)
    if validated:
        _log(on_progress, f"Join link captured from the clipboard: {validated}")
        return validated
    link = _scan_dom_for_join(page)  # the click may have revealed the URL in the DOM
    if link:
        return link
    _log(on_progress, "Join link not found (DOM and clipboard both empty).")
    return None


def _process_one(
    handle: BrowserHandle,
    file: Path,
    options: PipelineOptions,
    decide_assign: DecideAssignFn | None,
    resolve_duplicate: ResolveDuplicateFn | None,
    run_id: str,
    on_progress: ProgFn,
    resolve_class: classlib.ResolveClassFn | None = None,
) -> FileResult:
    """Publish -> (decide) -> assign -> join link, for one workbook."""
    page = handle.page
    data = load_xlsx(file, title_override=options.title)
    if not data.is_valid:
        detail = "; ".join(str(i) for i in data.errors[:4])
        if len(data.errors) > 4:
            detail += f"; (+{len(data.errors) - 4} more)"
        raise ValidationError(f"local validation failed — {detail}",
                              issues=[str(i) for i in data.issues])

    resume_id: str | None = None
    if not options.force:
        prior = find_by_title(options.state_path, data.title)
        if prior:
            info = DuplicateInfo(title=data.title, file=str(file),
                                 prior_quiz_ids=tuple(e.quiz_id for e in prior))
            action = resolve_duplicate(info) if resolve_duplicate is not None else "new"
            if action == "skip":
                _log(on_progress, f"Skipping {data.title!r}: duplicate title (user chose skip).")
                return FileResult(file=str(file), title=data.title, skipped=True)
            if action == "resume":
                resume_id = prior[0].quiz_id
                _log(on_progress, f"Resuming existing quiz {resume_id} "
                                  "(the workbook will be re-imported into it).")
            else:
                _log(on_progress, f"WARNING: a quiz titled {data.title!r} already exists "
                                  f"({prior[0].quiz_id}); creating a new one (--force skips this check).")

    if resume_id is not None:
        resume_quiz(page, resume_id, on_progress=on_progress)
        quiz_id = resume_id
    else:
        quiz_id = create_quiz(page, on_progress=on_progress)
    quiz_url = QUIZ_URL.format(quiz_id=quiz_id)
    record_created(options.state_path, title=data.title, quiz_id=quiz_id, quiz_url=quiz_url,
                   file=str(file), status="created")

    # LIVE 2026-11-08: the editor hides "Import existing files" once a quiz has
    # questions, and re-importing would DUPLICATE them — so a resumed draft
    # that already contains questions skips the import step.
    skip_import = False
    if resume_id is not None:
        count = _question_count(page)
        if count:
            skip_import = True
            _log(on_progress, f"Resume: the draft already contains {count} question(s); "
                              "skipping the import step to avoid duplicates.")
    if not skip_import:
        upload_xlsx(page, data, options=options, on_progress=on_progress)
    quiz_url = publish_quiz(page, title=data.title, options=options, quiz_id=quiz_id,
                            on_progress=on_progress)
    record_created(options.state_path, title=data.title, quiz_id=quiz_id, quiz_url=quiz_url,
                   file=str(file), status="published")

    # Assignment decision (LIVE 2026-11-09 correction): a quiz can be assigned
    # PUBLICLY without a class — that is the standard "no class" path and it
    # still yields a join link. Only --keep-draft skips assignment entirely.
    assign_class: str | None = None
    if not options.keep_draft:
        # class_name is the canonical option; assign_class is the legacy alias.
        assign_class = options.class_name or options.assign_class
        if assign_class is None and decide_assign is not None:
            view = FileView(file=str(file), title=data.title, quiz_url=quiz_url,
                            is_split=data.is_split, part=data.part)
            decision = decide_assign(view)
            assign_class = decision.strip() if isinstance(decision, str) and decision.strip() else None
        if assign_class:
            _log(on_progress, f"Assigning to class {assign_class!r}...")
        else:
            _log(on_progress, "Assigning publicly (no class, platform default 1-day deadline)...")
        # no_deadline applies to the class flow only; the public path ignores it
        # and assign_one reports the truth on AssignResult.
        assign_one(page, class_name=assign_class, assignment_title=data.title,
                   no_deadline=options.no_deadline,
                   create_if_missing=options.create_class_if_missing,
                   resolve_class=resolve_class,
                   on_progress=on_progress)
        join_link = extract_join_link(page, on_progress=on_progress)
        record_created(options.state_path, title=data.title, quiz_id=quiz_id, quiz_url=quiz_url,
                       file=str(file), status="assigned", join_link=join_link)
        if join_link is None:
            _snapshot(page, options.logs_dir, run_id, file, on_progress)
            return FileResult(
                file=str(file), title=data.title, quiz_url=quiz_url, assigned=True,
                error=f"assigned, but no join link could be captured (quiz: {quiz_url})",
                error_kind="join-link",
            )
        return FileResult(file=str(file), title=data.title, quiz_url=quiz_url,
                          join_link=join_link, assigned=True)

    # --keep-draft: published but deliberately not assigned. DOM scan only,
    # never fabricate a link.
    join_link = _scan_dom_for_join(page)
    if join_link:
        record_created(options.state_path, title=data.title, quiz_id=quiz_id, quiz_url=quiz_url,
                       file=str(file), status="published", join_link=join_link)
    else:
        _log(on_progress, "Published without assignment (--keep-draft); no join link exposed — "
                          "the output will carry the NO JOIN LINK marker.")
    return FileResult(file=str(file), title=data.title, quiz_url=quiz_url,
                      join_link=join_link, assigned=False)


def publish_many(
    files: Sequence[Path | str],
    options: PipelineOptions | None = None,
    *,
    session: Session | None = None,
    on_progress: ProgFn = None,
    should_cancel: Callable[[], bool] | None = None,
    decide_assign: DecideAssignFn | None = None,
    resolve_duplicate: ResolveDuplicateFn | None = None,
    choose_archive: lib.ChooseArchiveFn | None = None,
    resolve_class: classlib.ResolveClassFn | None = None,
    on_result: Callable[[FileResult], None] | None = None,
) -> list[FileResult]:
    """Run the pipeline over many workbooks with ONE browser session.

    ``should_cancel`` is checked at the top of every file iteration.
    ``decide_assign`` lets the caller (CLI prompt / future GUI) decide per
    file whether to assign and to which class, after the quiz is published.
    ``choose_archive`` is asked (interactive CLI / GUI) when the free-tier
    library cap would be exceeded; ``options.auto_archive_oldest`` archives
    automatically without prompting. ``resolve_class`` is asked (interactive
    CLI / GUI) when the requested class is missing from the picker: it may
    select an existing class, create a new one, or fall back to a public
    assignment. ``on_result`` fires after each file's ``FileResult`` is
    appended (the CLI uses it to close the live step tree).
    """
    options = options or PipelineOptions()
    session = session or Session(state_path=options.state_path, logs_dir=options.logs_dir)
    paths = [Path(f) for f in files]
    run_id = run_stamp()
    results: list[FileResult] = []

    with session.open(headless=options.headless, trace=True) as handle:
        # Login pre-flight: verify the stored session BEFORE the library check
        # or any create/import work. Without this, an expired session used to
        # be swallowed by the library pre-check below and the run then spent
        # ~40 s walking the create/upload flow before timing out.
        _log(on_progress, "Checking your Wayground login...")
        if not session.is_authenticated(handle.page, on_progress=on_progress):
            raise SessionExpiredError(
                f"Not signed in to Wayground — the saved session at {session.state_path} "
                "is missing or has expired."
            )
        _log(on_progress, "Login OK.")

        # Pre-flight: free-tier cap (20 active quizzes). A failure to read the
        # library must never block the run — only a *real* cap problem does.
        try:
            archived, total = lib.ensure_capacity(
                handle.page,
                new_count=len(paths),
                auto_archive_oldest=options.auto_archive_oldest,
                choose_archive=choose_archive,
                on_progress=on_progress,
            )
            if archived:
                _log(on_progress, f"Archived {archived} quiz(zes); library now at {total} active.")
        except (LibraryLimitError, SessionExpiredError):
            raise
        except Exception as exc:
            _log(on_progress, f"Library pre-check skipped ({exc.__class__.__name__}: {exc})")
        for index, file in enumerate(paths):
            if should_cancel is not None and should_cancel():
                _log(on_progress, "Cancelled by the caller; stopping.")
                break
            _log(on_progress, f"[{index + 1}/{len(paths)}] {file.name}")
            try:
                result = _process_one(handle, file, options, decide_assign,
                                      resolve_duplicate, run_id, on_progress,
                                      resolve_class=resolve_class)
            except WquError as exc:
                result = _failure_result(handle, file, options, run_id,
                                         str(exc) or exc.__class__.__name__,
                                         on_progress, kind=_error_kind(exc))
                log.warning("file %s failed: %s", file.name, exc)
            except Exception as exc:  # unexpected bug: keep going, but with a trace
                log.exception("unexpected error on %s", file)
                result = _failure_result(handle, file, options, run_id,
                                         f"{exc.__class__.__name__}: {exc}", on_progress)
            results.append(result)
            if on_result is not None:
                try:
                    on_result(result)
                except Exception:
                    log.debug("on_result callback failed", exc_info=True)
            if result.error_kind == "session-expired":
                _log(on_progress, "Session expired; stopping the run.")
                break
            if result.error is not None and options.fail_fast:
                _log(on_progress, "--fail-fast is set; stopping after the first failure.")
                break
    return results


_KIND_BY_ERROR = {
    SessionExpiredError: "session-expired",
    ClassCreateError: "class-create",
    ClassNotFoundError: "class-not-found",
    ValidationError: "validation",
    UploadError: "upload",
    PublishError: "publish",
    AssignError: "assign",
    SelectorNotFoundError: "selector",
}


def _error_kind(exc: BaseException) -> str | None:
    for cls, kind in _KIND_BY_ERROR.items():
        if isinstance(exc, cls):
            return kind
    return None


def _failure_result(
    handle: BrowserHandle,
    file: Path,
    options: PipelineOptions,
    run_id: str,
    message: str,
    on_progress: ProgFn,
    kind: str | None = None,
) -> FileResult:
    screenshot = _snapshot(handle.page, options.logs_dir, run_id, file, on_progress)
    trace_path = Path(options.logs_dir) / f"{run_id}.trace.zip"
    trace = handle.save_trace(trace_path)
    if trace:
        _log(on_progress, f"Playwright trace saved: {trace}")
    title = title_from_filename(Path(file).name)[0]
    return FileResult(file=str(file), title=title, error=message, error_kind=kind,
                      screenshot=screenshot, trace=trace)


def publish_one(
    path: Path | str,
    options: PipelineOptions | None = None,
    *,
    session: Session | None = None,
    on_progress: ProgFn = None,
    should_cancel: Callable[[], bool] | None = None,
) -> FileResult:
    """One-file API (the Part 2.6 acid test): no CLI, no prompts, no globals."""
    results = publish_many([Path(path)], options, session=session, on_progress=on_progress,
                           should_cancel=should_cancel)
    return results[0]
