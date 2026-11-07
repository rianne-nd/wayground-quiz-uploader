"""Central selector registry and Playwright interaction helpers.

Selector discipline (project brief Part 5):
  1. data-testid when available
  2. role + accessible name
  3. placeholder / aria-label
  4. visible text
  5. structural position inside [role=dialog] / [role=listbox]

Nothing here selects by CSS *class* (Wayground uses hashed class names). The
one CSS selector used is ``input[type=file]`` / attribute-contains lookups,
which are structural, not class-based.

Every constant carries ``status`` + ``evidence`` notes. Selectors were derived
from the user's screenshots (screenshots/step_*.png, 2026-11-08) and the
manual-flow description; they are *visually* verified but their exact DOM
attributes still need a live pass. ``wqu discover`` and the trace files will
confirm or correct them; see docs/selectors.md.

Interaction helpers here operate on a Playwright ``Page``/``Locator`` and
never print (core rule: only cli.py writes to stdout/stderr).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from wqu.errors import SelectorNotFoundError

log = logging.getLogger(__name__)

# Candidate encoding: tuples of ("role", role[, name[, exact]]) | ("testid", id)
# | ("placeholder", text) | ("text", text[, exact]) | ("label", text)
# | ("css", selector) | ("xpath", expression)


@dataclass(frozen=True)
class Sel:
    """A logical UI element with ordered fallback candidates."""

    key: str
    candidates: tuple[tuple[str, ...], ...]
    status: str = "unverified-dom"
    evidence: str = ""


def _build(root: Page | Locator, cand: tuple[str, ...]) -> Locator:
    kind = cand[0]
    if kind == "role":
        role, name = cand[1], cand[2]
        if isinstance(name, str) and name.startswith("re:"):
            return root.get_by_role(role, name=re.compile(name[3:], re.I))  # type: ignore[arg-type]
        exact = bool(cand[3]) if len(cand) > 3 else False
        return root.get_by_role(role, name=name, exact=exact)  # type: ignore[arg-type]
    if kind == "testid":
        return root.locator(f'[data-testid="{cand[1]}"]')
    if kind == "placeholder":
        return root.get_by_placeholder(cand[1])
    if kind == "text":
        exact = bool(cand[2]) if len(cand) > 2 else False
        return root.get_by_text(cand[1], exact=exact)
    if kind == "label":
        return root.get_by_label(cand[1])
    if kind == "regex":
        return root.get_by_text(re.compile(cand[1]))
    if kind == "css":
        return root.locator(cand[1])
    if kind == "xpath":
        return root.locator(f"xpath={cand[1]}")
    raise ValueError(f"unknown candidate kind: {kind!r}")


_MARK_DIALOG_JS = r"""
(el) => {
  // Re-mark each time so scoping cannot go stale after a modal closes.
  document.querySelectorAll('[data-wqu-dialog]').forEach(
    (n) => n.removeAttribute('data-wqu-dialog'));
  let node = el.parentElement;
  let fallback = null;
  while (node && node !== document.body) {
    const st = getComputedStyle(node);
    if (st.position === 'fixed' || st.position === 'absolute') {
      if (node.querySelector('button')) {
        node.setAttribute('data-wqu-dialog', '1');
        return true;
      }
      if (!fallback) fallback = node;
    }
    node = node.parentElement;
  }
  if (fallback) {
    fallback.setAttribute('data-wqu-dialog', '1');
    return true;
  }
  return false;
}
"""


def find_dialog(root: Page, title_sel: Sel, *, timeout_ms: int = 15_000) -> Locator:
    """Return a Locator scoped to the modal identified by ``title_sel``.

    Wayground modals do NOT reliably carry role=dialog, so two strategies
    are tried in order:
      1. a [role=dialog] whose subtree contains the title text;
      2. find the title text element, walk up to the nearest fixed or
         absolute overlay that contains a button, and tag it with
         ``data-wqu-dialog`` (a marker we add ourselves — not a CSS class).
    """
    js = _MARK_DIALOG_JS
    deadline = time.monotonic() + timeout_ms / 1000.0
    while True:
        dialogs = root.get_by_role("dialog")
        for i in range(dialogs.count()):
            dialog = dialogs.nth(i)
            try:
                if dialog.is_visible() and visible(dialog, title_sel, timeout_ms=400):
                    return dialog
            except Exception:
                continue
        # Fallback: tag the modal container ourselves.
        try:
            title = resolve(root, title_sel, timeout_ms=400)
            if title.evaluate(js):
                marked = root.locator('[data-wqu-dialog="1"]').first
                if marked.count() and marked.is_visible():
                    return marked
        except SelectorNotFoundError:
            pass
        except Exception as exc:
            log.debug("dialog marking failed: %s", exc)
        if time.monotonic() >= deadline:
            raise SelectorNotFoundError(f"dialog:{title_sel.key}", title_sel.evidence)
        root.wait_for_timeout(250)


def resolve(
    root: Page | Locator,
    sel: Sel,
    *,
    timeout_ms: int = 10_000,
    state: str = "visible",
) -> Locator:
    """Return the first candidate that reaches ``state`` before the deadline.

    Each candidate gets a short probe window, then the loop repeats until the
    overall deadline. Raises SelectorNotFoundError when nothing matches.
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    probe_ms = 800
    last_error: Exception | None = None
    while True:
        for cand in sel.candidates:
            try:
                loc = _build(root, cand).first
                loc.wait_for(state=state, timeout=probe_ms)
                return loc
            except PlaywrightTimeoutError as exc:
                last_error = exc
            except Exception as exc:  # malformed locator for this candidate
                last_error = exc
                log.debug("selector %s candidate %r failed: %s", sel.key, cand, exc)
        if time.monotonic() >= deadline:
            break
    note = sel.evidence or sel.status
    raise SelectorNotFoundError(sel.key, note) from last_error


def visible(root: Page | Locator, sel: Sel, *, timeout_ms: int = 1_500) -> bool:
    """True when any candidate is visible; never raises."""
    try:
        resolve(root, sel, timeout_ms=timeout_ms)
        return True
    except SelectorNotFoundError:
        return False


def click(root: Page | Locator, sel: Sel, *, timeout_ms: int = 10_000) -> Locator:
    loc = resolve(root, sel, timeout_ms=timeout_ms)
    loc.click()
    return loc


def fill(root: Page | Locator, sel: Sel, value: str, *, timeout_ms: int = 10_000) -> None:
    loc = resolve(root, sel, timeout_ms=timeout_ms)
    loc.fill(value)


def count_visible(page: Page, locator: Locator) -> int:
    n = 0
    for i in range(locator.count()):
        try:
            if locator.nth(i).is_visible():
                n += 1
        except Exception:
            continue
    return n


def click_rightmost(page: Page, locator: Locator, *, timeout_ms: int = 10_000) -> Locator:
    """Click the visible element with the largest x coordinate.

    Some pages show two buttons with the same accessible name (e.g. the
    breadcrumb "Assign" and the top-right "Assign"); the actionable one is on
    the right. Falls back to the first visible match if geometry is unknown.
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        best: tuple[float, Locator] | None = None
        first_visible: Locator | None = None
        for i in range(locator.count()):
            inst = locator.nth(i)
            try:
                if not inst.is_visible():
                    continue
                box = inst.bounding_box()
            except Exception:
                continue
            if first_visible is None:
                first_visible = inst
            if box is not None and (best is None or box["x"] > best[0]):
                best = (box["x"], inst)
        if best is not None:
            best[1].click()
            return best[1]
        if first_visible is not None:
            first_visible.click()
            return first_visible
        page.wait_for_timeout(150)
    raise SelectorNotFoundError("click_rightmost", "no visible candidate found")


def _in_dialog(page: Page, locator: Locator) -> bool:
    return bool(locator.evaluate("el => !!el.closest('[role=dialog]')"))


def click_outside_dialogs(page: Page, name: str, *, timeout_ms: int = 10_000) -> Locator:
    """Click the first visible button with this name that is NOT in a dialog."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    pattern = re.compile(name, re.I)
    while time.monotonic() < deadline:
        buttons = page.get_by_role("button", name=pattern)
        for i in range(buttons.count()):
            inst = buttons.nth(i)
            try:
                if inst.is_visible() and not _in_dialog(page, inst):
                    inst.click()
                    return inst
            except Exception:
                continue
        page.wait_for_timeout(150)
    raise SelectorNotFoundError(f"button-outside-dialogs:{name}")


def set_files_via_inputs(page: Page, root: Locator | Page, path) -> bool:
    """Attach ``path`` to the first file input found under ``root``.

    The import modal parses the workbook client-side (Part 3, DOM path), so
    setting the hidden input directly is enough; no native file-chooser dance
    is required. Returns False when no input exists (caller can fall back to
    expect_file_chooser around "Upload from device").
    """
    inputs = root.locator('input[type="file"]')
    for i in range(inputs.count()):
        try:
            inputs.nth(i).set_input_files(str(path))
            return True
        except Exception as exc:
            log.debug("set_input_files attempt %d failed: %s", i, exc)
    return False


def read_checked(scope: Page | Locator, loc: Locator) -> bool | None:
    """Best-effort read of a checkbox/switch state; None when unreadable."""
    try:
        return loc.is_checked()
    except Exception:
        pass
    for attr in ("aria-checked", "checked"):
        try:
            value = loc.get_attribute(attr, timeout=1_000)
        except Exception:
            continue
        if value is not None:
            return value.lower() == "true"
    try:
        return bool(
            loc.evaluate(
                "(el) => (el.checked !== undefined ? el.checked : el.getAttribute('aria-checked') === 'true')"
            )
        )
    except Exception:
        return None


def click_control(loc: Locator, *, timeout_ms: int = 2_500) -> None:
    """Click a custom control (checkbox/switch) reliably.

    Wayground wraps its inputs in styled divs whose knobs intercept pointer
    events, so Playwright's normal click can time out (LIVE 2026-11-08). Fall
    back to a JS element click, which React handles exactly like a user click.
    """
    try:
        loc.click(timeout=timeout_ms)
        return
    except Exception as exc:
        log.debug("normal click failed (%s); falling back to JS click", exc)
    loc.evaluate("el => el.click()")


def ensure_unchecked(page: Page, loc: Locator, *, label: str, on_progress=None) -> bool:
    """Make sure a checkbox/switch is OFF. Returns True when verified OFF.

    When the state cannot be read, nothing is clicked (clicking blind could
    toggle it ON) and False is returned so the caller can warn.
    """
    state = read_checked(page, loc)
    if state is None:
        log.warning("could not read state of %s", label)
        return False
    if state:
        try:
            click_control(loc)
        except Exception as exc:
            log.warning("clicking %s failed: %s", label, exc)
            return False
        state_after = read_checked(page, loc)
        if state_after is None:
            log.warning("clicked %s but could not re-verify state", label)
            return True  # clicked from a verified-ON state, optimistic
        return not state_after
    return True


def choose_dropdown_option(
    page: Page,
    trigger: Locator,
    option_text: str,
    *,
    known_options: tuple[str, ...] = (),
    timeout_ms: int = 20_000,
) -> bool:
    """Click ``trigger`` then pick ``option_text`` from the opened listbox.

    Long lists (the Grade dropdown) need scrolling: the payload is not always
    rendered until scrolled into view, so we probe, then wheel-scroll over the
    open panel, then probe again.
    """
    trigger.click()
    deadline = time.monotonic() + timeout_ms / 1000.0
    scrolled = False
    while time.monotonic() < deadline:
        for loc in (
            page.get_by_role("option", name=option_text, exact=True),
            page.get_by_text(option_text, exact=True),
        ):
            if loc.count() == 0:
                continue
            inst = loc.last  # portal renders at the end of <body>
            try:
                if inst.is_visible():
                    inst.scroll_into_view_if_needed(timeout=2_000)
                    inst.click(timeout=3_000)
                    return True
            except Exception as exc:
                log.debug("option %r probe failed: %s", option_text, exc)
        # Scroll the open panel: hover a known option, then wheel down.
        anchor = None
        for text in known_options:
            candidate = page.get_by_text(text, exact=True)
            if candidate.count():
                try:
                    if candidate.last.is_visible():
                        anchor = candidate.last
                        break
                except Exception:
                    continue
        try:
            if anchor is not None:
                anchor.hover(timeout=1_500)
            listbox = page.locator('[role="listbox"]')
            if listbox.count():
                listbox.last.hover(timeout=1_500)
            page.mouse.wheel(0, 900)
            scrolled = True
        except Exception:
            pass
        page.wait_for_timeout(120)
    log.warning("dropdown option %r not found (scrolled=%s)", option_text, scrolled)
    return False


# --------------------------------------------------------------------------
# Selector constants
# Status legend: "visual" = confirmed in user screenshots (2026-11-08);
# "unverified-dom" = described in the manual flow but DOM attributes unknown;
# "upstream" = seen in the vendored upstream automation.
# --------------------------------------------------------------------------

URL_ASSESSMENT = "https://wayground.com/activity/admin/assessment"

# --- create flow -----------------------------------------------------------
START_FROM_SCRATCH = Sel(
    "start_from_scratch",
    (
        ("role", "button", "Start from scratch"),
        ("role", "link", "Start from scratch"),
        ("text", "Start from scratch", False),
    ),
    status="visual",
    evidence="screenshots/step_01.png — bottom-right tile with chevron",
)
GET_STARTED_MARKER = Sel(
    "get_started_marker",
    (
        ("text", "How would you like to get started?", False),
        ("text", "Start from scratch", False),
    ),
    status="visual",
    evidence="screenshots/step_01.png — page heading",
)

# --- editor / import -------------------------------------------------------
IMPORT_EXISTING_SECTION = Sel(
    "import_existing_section",
    (
        ("text", "Import existing files", False),
        ("text", "Spreadsheet", False),
    ),
    status="visual",
    evidence="screenshots/step_02.png — section bottom of editor",
)
SPREADSHEET_BUTTON = Sel(
    "spreadsheet_button",
    (
        ("role", "button", "Spreadsheet"),
        ("role", "button", "Spreadsheet", False),
        ("text", "Spreadsheet", True),
    ),
    status="visual",
    evidence='screenshots/step_02.png — "Spreadsheet" button under "Import existing files"',
)
QUESTIONS_COUNT_MARKER = Sel(
    "questions_count_marker",
    (
        ("regex", r"\d+\s+Questions"),
    ),
    status="visual",
    evidence="screenshots/step_06.png — '29 Questions · 29 Points · 29 mins' after import",
)
IMPORT_DIALOG_TITLE = Sel(
    "import_dialog_title",
    (
        ("text", "Import questions from spreadsheet", False),
        ("role", "heading", "Import questions from spreadsheet", False),
    ),
    status="visual",
    evidence="screenshots/step_03.png — modal heading",
)
UPLOAD_FROM_DEVICE = Sel(
    "upload_from_device",
    (
        ("role", "button", "Upload from device"),
        ("role", "button", "Upload from device", False),
        ("text", "Upload from device", False),
    ),
    status="visual",
    evidence="screenshots/step_03.png — purple button in drop zone",
)
BANNER_OK = Sel(
    "banner_ok",
    (
        ("text", "Looks good!", False),
        ("text", "looks good", False),
    ),
    status="visual",
    evidence="screenshots/step_04.png — green banner",
)
BANNER_BAD = Sel(
    "banner_bad",
    (
        ("text", "Invalid correct option", False),
        ("text", "invalid correct option", False),
    ),
    status="visual",
    evidence="screenshots/step_05.png — red banner",
)
NEXT_ERROR_BUTTON = Sel(
    "next_error_button",
    (
        ("role", "button", "Next error"),
        ("role", "button", "Next error", False),
        ("text", "Next error", True),
    ),
    status="visual",
    evidence='screenshots/step_05.png — "Next error" inside red banner',
)
IMPORT_CONFIRM_BUTTON = Sel(
    "import_confirm_button",
    (
        ("role", "button", "Import"),
        ("text", "Import", True),
    ),
    status="visual",
    evidence="screenshots/step_04.png — purple Import bottom-right (dialog-scoped by caller)",
)
CANCEL_BUTTON = Sel(
    "cancel_button",
    (
        ("role", "button", "Cancel"),
        ("text", "Cancel", True),
    ),
    status="visual",
    evidence="screenshots/step_03/04/05.png — gray Cancel bottom-right",
)

# --- publish modal ----------------------------------------------------------
PUBLISH_DIALOG_TITLE = Sel(
    "publish_dialog_title",
    (
        ("text", "Great, you're almost done", False),
        ("role", "heading", "Great", False),
    ),
    status="visual",
    evidence="screenshots/step_07.png — modal heading",
)
NAME_INPUT = Sel(
    "name_input",
    (
        ("placeholder", "Untitled Quiz"),
        ("role", "textbox", "Untitled Quiz", False),
    ),
    status="visual",
    evidence='screenshots/step_07.png — Name field, placeholder "Untitled Quiz", 0/64',
)
SUBJECT_SELECT = Sel(
    "subject_select",
    (
        # LIVE 2026-11-08: real testid + aria-haspopup=listbox trigger; text is
        # "Select relevant subject" until a value is chosen.
        ("testid", "quiz-subject-select-box"),
        ("role", "button", "Select relevant subject"),
        ("role", "button", "re:select relevant subject"),
        ("xpath", "//p[normalize-space()='Subject']/following::*[@aria-haspopup][1]"),
    ),
    status="verified-live",
    evidence="data-testid=quiz-subject-select-box; options are UL[role=listbox] > LI[role=option]",
)
GRADE_SELECT = Sel(
    "grade_select",
    (
        ("testid", "publish-modal-grade-input-select-box"),
        ("role", "button", "Select relevant grade"),
        ("role", "button", "re:select relevant grade"),
        ("xpath", "//p[normalize-space()='Grade']/following::*[@aria-haspopup][1]"),
    ),
    status="verified-live",
    evidence="data-testid=publish-modal-grade-input-select-box",
)
SELECT_PLACEHOLDER_TRIGGER = Sel(
    "select_placeholder_trigger",
    (
        ("role", "button", "Select..."),
        ("role", "button", "Select", False),
        ("text", "Select...", True),
    ),
    status="visual",
    evidence='screenshots/step_07.png — both empty dropdowns read "Select..."',
)
PUBLISH_MODAL_PUBLISH = Sel(
    "publish_modal_publish",
    (
        ("role", "button", "Publish"),
        ("text", "Publish", True),
    ),
    status="visual",
    evidence="screenshots/step_07/10.png — pink Publish in modal footer (dialog-scoped)",
)
PUBLISH_BUTTON_TOP = Sel(
    "publish_button_top",
    (
        # LIVE 2026-11-08: aria-label overrides the visible text ("Publish quiz").
        ("role", "button", "Publish quiz"),
        ("role", "button", "re:^publish( quiz)?$"),
        ("text", "Publish", True),
    ),
    status="verified-live",
    evidence="aria-label='Publish quiz' observed on the editor; visible text is 'Publish'",
)
SAVED_DIALOG_MARKER = Sel(
    "saved_dialog_marker",
    (
        ("text", "Your quiz is saved", False),
        ("text", "Great work!", False),
        ("text", "Great work", False),
    ),
    status="visual",
    evidence="screenshots/step_13.png — 'Great work! Your quiz is saved and ready to play with students.'",
)
CLOSE_BUTTON = Sel(
    "close_button",
    (
        # LIVE 2026-11-08: the modal X is a button with aria-label="xmark".
        ("role", "button", "xmark"),
        ("label", "xmark"),
        ("role", "button", "Close"),
        ("label", "Close"),
        ("css", '[aria-label="Close"]'),
    ),
    status="verified-live",
    evidence='aria-label="xmark" observed on the publish modal X; screenshots step_07/13',
)
PUBLISH_ERROR_HINTS = Sel(
    "publish_error_hints",
    (
        ("text", "Please select the subject", False),
        ("text", "Please select the grade", False),
        ("text", "Name should be at least", False),
    ),
    status="visual",
    evidence="screenshots/step_10.png — red validation hints under Name/Subject/Grade",
)

# --- quiz page / assign flow -------------------------------------------------
ASSIGN_BUTTON = Sel(
    "assign_button",
    (
        ("role", "button", "Assign"),
        ("role", "link", "Assign"),
        ("text", "Assign", True),
    ),
    status="visual",
    evidence="screenshots/step_14.png — pink outline Assign top-right of quiz page; "
    "also final Assign on homework page (step_20)",
)
CHANGE_TIME_BUTTON = Sel(
    "change_time_button",
    (
        ("testid", "toggle-calendar-button"),
        ("role", "button", "Change"),
    ),
    status="verified-live",
    evidence='data-testid=toggle-calendar-button; screenshots/step_15.png ("Change")',
)
NO_DEADLINE_TOGGLE = Sel(
    "no_deadline_toggle",
    (
        # LIVE 2026-11-08: the End-time switch in the timeline modal.
        ("testid", "date-time-selector-input"),
        ("role", "switch", "No deadline", False),
        (
            "xpath",
            "//*[normalize-space(text())='No deadline']"
            "/ancestor::*[.//input[@type='checkbox'] or @role='switch'][1]"
            "//*[@role='switch' or @role='checkbox' or @type='checkbox'][1]",
        ),
    ),
    status="verified-live",
    evidence="input[type=checkbox][role=switch][data-testid=date-time-selector-input], aria-checked readable",
)
SAVE_AND_CLOSE_BUTTON = Sel(
    "save_and_close_button",
    (
        ("testid", "timeline-selector-continue-button"),
        ("role", "button", "Save and close"),
        ("text", "Save and close", True),
    ),
    status="verified-live",
    evidence="data-testid=timeline-selector-continue-button in the timeline-selector-modal",
)
DEADLINE_APPLIED_TEXT = Sel(
    "deadline_applied_text",
    (
        ("regex", r"No deadline"),
    ),
    status="visual",
    evidence="screenshots/step_24.png — details row then reads 'Start immediately – No deadline'",
)
CLASS_SELECTED_HINT = Sel(
    "class_selected_hint",
    (
        ("text", "Pick specific students", False),
    ),
    status="visual",
    evidence="screenshots/step_18.png — link appears only once a class row is checked",
)
CLASS_ASSIGNED_MARKER = Sel(
    "class_assigned_marker",
    (
        ("text", "Session will be assigned", False),
    ),
    status="visual",
    evidence="screenshots/step_22/24.png — 'Session will be assigned to 1 class' + class chip",
)
SELECT_A_CLASS_BUTTON = Sel(
    "select_a_class_button",
    (
        ("testid", "select-class-button"),
        ("role", "button", "Select a class"),
        ("text", "Select a class", True),
    ),
    status="verified-live",
    evidence='data-testid=select-class-button; screenshots/step_15.png ("Not assigned to a class" row)',
)
MODIFY_CLASS_BUTTON = Sel(  # pre-selected class from an earlier run
    "modify_class_button",
    (
        ("role", "button", "Modify"),
        ("role", "button", "Change class", False),
        ("text", "Modify", True),
    ),
    status="visual",
    evidence="screenshots/step_24.png — 'Session will be assigned to 1 class' + Modify (pencil) button",
)
CLASS_DIALOG_TITLE = Sel(
    "class_dialog_title",
    (
        ("text", "Assign to a class", False),
        ("role", "heading", "Assign to a class", False),
    ),
    status="visual",
    evidence="screenshots/step_18/19/20.png — modal heading",
)
CLASS_SEARCH_INPUT = Sel(
    "class_search_input",
    (
        ("placeholder", "Search for a class"),
        ("role", "textbox", "Search for a class", False),
    ),
    status="visual",
    evidence='screenshots/step_18.png — search field top of modal',
)
ACCOMMODATIONS_CHECKBOX = Sel(
    "accommodations_checkbox",
    (
        # LIVE 2026-11-08: real input, CHECKED as soon as a class is selected.
        ("testid", "assignment-setup-accommodations-toggle"),
        ("role", "checkbox", "re:setup accommodations|set up accommodations", False),
        (
            "xpath",
            "//*[contains(normalize-space(text()), 'Set up Accommodations')]"
            "/ancestor-or-self::*[.//input[@type='checkbox']][1]"
            "//input[@type='checkbox']",
        ),
    ),
    status="verified-live",
    evidence="input[type=checkbox][data-testid=assignment-setup-accommodations-toggle]; "
    "checked=True after ticking a class; the input is covered by a styled wrapper → use click_control",
)
NEXT_BUTTON = Sel(
    "next_button",
    (
        ("role", "button", "Next"),
        ("text", "Next", True),
    ),
    status="visual",
    evidence="screenshots/step_18-21.png — pink Next in modal footers",
)
NAME_ASSIGNMENT_DIALOG_TITLE = Sel(
    "name_assignment_dialog_title",
    (
        ("text", "Name this assignment (Optional)", False),
        ("text", "Name this assignment", False),
    ),
    status="visual",
    evidence="screenshots/step_21.png — modal heading",
)
ASSIGNMENT_TITLE_INPUT = Sel(
    "assignment_title_input",
    (
        ("placeholder", "Name this assignment for your students"),
        ("xpath", "//*[normalize-space(text())='Assignment title (optional)']/following::input[1]"),
    ),
    status="visual",
    evidence='screenshots/step_21.png — "Assignment title (optional)" field',
)
ASSIGNMENT_DETAILS_MARKER = Sel(
    "assignment_details_marker",
    (
        ("text", "Assignment Details", False),
    ),
    status="visual",
    evidence='screenshots/step_15/17.png — page heading',
)

# --- gameplay / join link ----------------------------------------------------
GOT_IT_BUTTON = Sel(
    "got_it_button",
    (
        ("role", "button", "Got it"),
        ("text", "Got it", True),
    ),
    status="verified-live",
    evidence="coachmark/tip bubble on the quiz page; intercepts clicks until dismissed",
)
COPY_JOIN_LINK_BUTTON = Sel(
    "copy_join_link_button",
    (
        ("role", "button", "Copy Join link", False),
        ("role", "button", "Copy Join", False),
        ("text", "Copy Join link", False),
    ),
    status="visual",
    evidence="screenshots/step_22.png — pink button under the join-code panel",
)

# --- incidental screens (defensive recovery) ---------------------------------
PREMIUM_POPUP_MARKER = Sel(
    "premium_popup_marker",
    (
        ("text", "Unlock 45+ premium features", False),
        ("text", "Wayground Premium", False),
    ),
    status="visual",
    evidence="screenshots/step_15.png — premium upsell modal with X top-right",
)
ACCOMMODATIONS_PAGE_MARKER = Sel(
    "accommodations_page_marker",
    (
        ("text", "Set up learning supports", False),
        ("text", "Add Accommodations", False),
        ("text", "Preview Accommodations", False),
    ),
    status="visual",
    evidence="screenshots/step_16.png — full-page accommodations screen (the known glitch)",
)
ACCOMMODATIONS_BACK_BUTTON = Sel(
    "accommodations_back_button",
    (
        # The homework page's own back button is aria='chevron-left'; the round
        # back arrow on the accommodations page almost certainly shares it.
        ("role", "button", "chevron-left"),
        ("role", "button", "Back"),
        ("role", "button", "Go back", False),
        ("label", "Back"),
        ("xpath", "//*[normalize-space()='Set up Accommodations']/preceding::button[1]"),
        ("xpath", "//*[normalize-space()='Set up Accommodations']/ancestor::*[1]//button[1]"),
    ),
    status="visual-partial",
    evidence="screenshots/step_16.png — round back arrow; this app's icon aria for back is 'chevron-left' "
    "(as on the homework page back-button)",
)
