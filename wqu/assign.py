"""Assignment flow (DOM path), in the order of the definitive guide:

    quiz page --Assign--> Assignment Details
      1. Select a class (or Modify) -> [accommodations glitch recovery]
      2. tick the class row -> uncheck "Set up Accommodations" -> Next
      3. "Name this assignment (Optional)" -> title -> [uncheck] -> Next
      4. "Change" (Start - End time) -> "No deadline" ON -> "Save and close"
      5. verify the details row shows "No deadline"
      6. top-right Assign -> /gameplay/admin/activity/classic/{id}

Evidence: screenshots/step_14..24.png + screenshots/screenshot-guide.md
(2026-11-08). Library-safe: returns ``AssignResult``; never prints.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from wqu import selectors as S
from wqu.errors import AssignError, ClassNotFoundError, SelectorNotFoundError

log = logging.getLogger(__name__)

ProgFn = Callable[[str], None] | None

_GAMEPLAY_URL_RE = re.compile(r"/gameplay/admin/activity/classic/(?P<activity_id>[\w-]+)")
_HOMEWORK_URL_RE = re.compile(r"/activity/admin/quiz/homework/(?P<homework_id>[\w-]+)")

MAX_ASSIGNMENT_TITLE = 200  # not documented by Wayground; generous clip only


@dataclass(frozen=True)
class AssignResult:
    class_name: str
    homework_id: str
    activity_id: str
    activity_url: str
    no_deadline: bool


def _log(on_progress: ProgFn, message: str) -> None:
    log.info(message)
    if on_progress is not None:
        on_progress(message)


def recover_accommodations_page(page: Page, on_progress: ProgFn = None) -> bool:
    """The known glitch (guide rule 2): full-page 'Set up learning supports'.

    When detected, click the back-arrow in the top-left to return. Returns
    True when a recovery click was performed.
    """
    if not S.visible(page, S.ACCOMMODATIONS_PAGE_MARKER, timeout_ms=1_200):
        return False
    _log(on_progress, "Detected the accommodations page; clicking the back arrow...")
    for attempt in range(3):
        try:
            S.click(page, S.ACCOMMODATIONS_BACK_BUTTON, timeout_ms=5_000)
            break
        except SelectorNotFoundError:
            if attempt == 0:
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
            elif attempt == 1:
                try:
                    page.go_back()
                except Exception:
                    pass
    try:
        page.get_by_text("Set up learning supports", exact=False).first.wait_for(
            state="hidden", timeout=10_000
        )
    except PlaywrightTimeoutError:
        log.warning("accommodations page still visible after recovery attempts")
    return True


def _open_class_dialog(page: Page, on_progress: ProgFn = None) -> None:
    """Click 'Select a class' (or 'Modify') and handle the accommodations glitch.

    The glitch is triggered *by the first click* on 'Select a class'
    (guide rule 2): the full page opens instead of the modal; the back arrow
    returns to the assignment flow, then the modal opens.
    """
    clicked = False
    if S.visible(page, S.SELECT_A_CLASS_BUTTON, timeout_ms=3_000):
        _log(on_progress, "Opening the class picker...")
        S.click(page, S.SELECT_A_CLASS_BUTTON, timeout_ms=10_000)
        clicked = True
    elif S.visible(page, S.MODIFY_CLASS_BUTTON, timeout_ms=3_000):
        _log(on_progress, "A class is already attached; opening Modify...")
        S.click(page, S.MODIFY_CLASS_BUTTON, timeout_ms=10_000)
        clicked = True
    else:
        raise AssignError(
            "could not find 'Select a class' or 'Modify' on the Assignment Details page"
        )

    if not clicked:  # pragma: no cover - defensive
        raise AssignError("class picker click did not happen")

    for _attempt in range(3):
        state = _wait_class_dialog_or_glitch(page, timeout_ms=20_000)
        if state == "dialog":
            return
        if state == "glitch":
            recover_accommodations_page(page, on_progress)
            # After recovery we may already be back in the modal, or back on
            # the details page — check, and re-open the picker if needed.
            if _wait_class_dialog_or_glitch(page, timeout_ms=8_000) == "dialog":
                return
            if S.visible(page, S.SELECT_A_CLASS_BUTTON, timeout_ms=3_000):
                _log(on_progress, "Re-opening the class picker after recovery...")
                S.click(page, S.SELECT_A_CLASS_BUTTON, timeout_ms=10_000)
                continue
            if S.visible(page, S.MODIFY_CLASS_BUTTON, timeout_ms=3_000):
                S.click(page, S.MODIFY_CLASS_BUTTON, timeout_ms=10_000)
                continue
    raise AssignError("the 'Assign to a class' modal never appeared (after glitch recovery)")


def _wait_class_dialog_or_glitch(page: Page, *, timeout_ms: int) -> str:
    """Return 'dialog', 'glitch', or 'timeout'."""
    waited = 0
    step = 500
    while waited < timeout_ms:
        if S.visible(page, S.ACCOMMODATIONS_PAGE_MARKER, timeout_ms=step):
            return "glitch"
        if S.visible(page, S.CLASS_DIALOG_TITLE, timeout_ms=step):
            return "dialog"
        waited += step
    return "timeout"


_AVAILABLE_CLASSES_JS = r"""
() => {
  const names = [];
  for (const box of document.querySelectorAll('[role="checkbox"], input[type="checkbox"]')) {
    const row = box.closest('label, li, [role="row"], [role="listitem"], div');
    const text = ((box.getAttribute('aria-label') || (row && row.innerText) || '')).split('\n')[0].trim();
    if (text && !/select all|set up accommodations/i.test(text)) names.push(text);
  }
  return [...new Set(names)];
}
"""


def _available_class_names(dialog: S.Locator) -> list[str]:
    try:
        return [str(n) for n in dialog.evaluate(_AVAILABLE_CLASSES_JS)]
    except Exception:
        return []


def _select_class_row(page: Page, dialog: S.Locator, class_name: str, on_progress: ProgFn) -> None:
    """Tick the checkbox for ``class_name`` inside the class modal."""
    _log(on_progress, f"Selecting class {class_name!r}...")
    candidates: list[S.Locator] = []
    row = dialog.get_by_role("row").filter(has_text=class_name)
    if row.count() == 0:
        row = dialog.get_by_role("listitem").filter(has_text=class_name)
    if row.count() == 0:
        row = dialog.locator("label").filter(has_text=class_name)
    for container in (row, dialog):
        boxes = container.get_by_role("checkbox")
        for i in range(min(boxes.count(), 10)):
            candidates.append(boxes.nth(i))
    # Last resort: click the class name text itself (row-click usually toggles).
    clicked = False
    for box in candidates:
        try:
            label = (box.get_attribute("aria-label") or box.inner_text() or "")
        except Exception:
            label = ""
        if class_name.lower() in label.lower():
            box.click(timeout=3_000)
            clicked = True
            break
    if not clicked:
        text = dialog.get_by_text(class_name, exact=False).first
        if text.count() == 0:
            available = _available_class_names(dialog)
            raise ClassNotFoundError(class_name, available)
        text.click(timeout=3_000)
        clicked = True

    # Verify: selecting a class reveals the 'Pick specific students' link
    # (screenshots/step_18.png) and/or flips checkbox state.
    if S.visible(dialog, S.CLASS_SELECTED_HINT, timeout_ms=4_000):
        return
    state_ok = False
    for box in candidates:
        if S.read_checked(page, box) is True:
            state_ok = True
            break
    if not state_ok:
        available = _available_class_names(dialog)
        raise ClassNotFoundError(class_name, available)


def _uncheck_accommodations(page: Page, dialog: S.Locator, *, fatal: bool, on_progress: ProgFn) -> None:
    """Ensure the 'Set up Accommodations' checkbox in this modal is OFF.

    It is CHECKED BY DEFAULT once a class is selected (guide rule 3).
    ``fatal=True`` in the class modal (refuse to proceed unchecked-broken);
    warnings only in the name modal.
    """
    try:
        box = S.resolve(dialog, S.ACCOMMODATIONS_CHECKBOX, timeout_ms=4_000)
    except SelectorNotFoundError:
        if fatal:
            raise AssignError("could not find the 'Set up Accommodations' checkbox") from None
        log.info("no accommodations checkbox here; nothing to do")
        return
    if S.ensure_unchecked(page, box, label="Set up Accommodations", on_progress=on_progress):
        _log(on_progress, "'Set up Accommodations' is unchecked.")
        return
    if fatal:
        raise AssignError("could not verify that 'Set up Accommodations' is unchecked; aborting "
                          "rather than assigning with accommodations enabled")
    _log(on_progress, "WARNING: could not verify 'Set up Accommodations' in the name modal.")


def _name_assignment(page: Page, title: str, on_progress: ProgFn, dialog: Locator | None = None) -> None:
    """Fill the 'Name this assignment (Optional)' modal and click Next.

    NOTE (LIVE 2026-11-08): Wayground stacks modals — the previous dialog stays
    mounted underneath, so we never wait for it to *hide*; callers wait for the
    next observable state instead.
    """
    if dialog is None:
        dialog = S.find_dialog(page, S.NAME_ASSIGNMENT_DIALOG_TITLE)
    _log(on_progress, f"Naming the assignment {title!r}...")
    field = S.resolve(dialog, S.ASSIGNMENT_TITLE_INPUT, timeout_ms=5_000)
    field.fill(title[:MAX_ASSIGNMENT_TITLE])
    _uncheck_accommodations(page, dialog, fatal=False, on_progress=on_progress)
    S.click(dialog, S.NEXT_BUTTON, timeout_ms=8_000)
    try:
        page.get_by_text("Name this assignment", exact=False).first.wait_for(
            state="hidden", timeout=10_000
        )
    except PlaywrightTimeoutError:
        log.info("name modal still mounted (stacked); continuing to the details check")


def _set_no_deadline(page: Page, on_progress: ProgFn) -> None:
    """Change -> toggle 'No deadline' ON -> 'Save and close' -> verify."""
    _log(on_progress, "Setting 'No deadline' on the Start - End time...")
    S.click(page, S.CHANGE_TIME_BUTTON, timeout_ms=10_000)
    save_button = S.resolve(page, S.SAVE_AND_CLOSE_BUTTON, timeout_ms=15_000)
    toggle = S.resolve(page, S.NO_DEADLINE_TOGGLE, timeout_ms=8_000)

    state = S.read_checked(page, toggle)
    if state is None:
        raise AssignError(
            "could not read the 'No deadline' toggle state; refusing to click blind "
            "(verify the control markup with 'wqu discover' — see docs/troubleshooting.md)"
        )
    if not state:
        # The switch input is covered by a styled knob → click_control handles it.
        S.click_control(toggle, timeout_ms=3_000)
        after = S.read_checked(page, toggle)
        if after is False:
            S.click_control(toggle, timeout_ms=3_000)  # one retry, JS click path
            after = S.read_checked(page, toggle)
        if after is False:
            raise AssignError("clicked 'No deadline' but the control still reads OFF")
        _log(on_progress, "'No deadline' is now ON.")
    else:
        _log(on_progress, "'No deadline' was already ON.")

    S.click_control(save_button, timeout_ms=5_000)
    try:
        save_button.wait_for(state="hidden", timeout=15_000)
    except PlaywrightTimeoutError:
        log.info("time modal still visible after Save and close; continuing")
    # Observable confirmation (screenshots/step_24.png): the details row now
    # reads "Start immediately – No deadline".
    try:
        page.get_by_text(re.compile(r"No deadline")).first.wait_for(state="visible", timeout=15_000)
    except PlaywrightTimeoutError as exc:
        raise AssignError("the assignment still does not show 'No deadline' after saving") from exc


def _click_final_assign(page: Page, on_progress: ProgFn) -> str:
    """Click the top-right Assign (rightmost match) and wait for gameplay."""
    buttons = page.get_by_role("button", name=re.compile(r"^Assign$", re.I))
    if buttons.count() == 0:
        raise AssignError("final Assign button not found on the Assignment Details page")
    _log(on_progress, "Clicking the final Assign...")
    S.click_rightmost(page, buttons, timeout_ms=15_000)
    try:
        page.wait_for_url(_GAMEPLAY_URL_RE, timeout=45_000)
    except PlaywrightTimeoutError as exc:
        raise AssignError(f"clicked Assign but never reached the gameplay page (now at {page.url})") from exc
    match = _GAMEPLAY_URL_RE.search(page.url)
    return match.group("activity_id") if match else ""


def _wait_homework_or_glitch(page: Page, *, timeout_ms: int) -> str:
    """Race: homework URL reached | accommodations page shown | timeout."""
    waited = 0
    step = 500
    while waited < timeout_ms:
        if _HOMEWORK_URL_RE.search(page.url):
            return "homework"
        if S.visible(page, S.ACCOMMODATIONS_PAGE_MARKER, timeout_ms=step):
            return "glitch"
        waited += step
    return "timeout"


def _enter_homework_page(page: Page, on_progress: ProgFn) -> str:
    """Click Assign on the quiz page; recover from the accommodations glitch.

    LIVE 2026-11-08: this glitch can strike right after clicking Assign (not
    only at 'Select a class'); after the back arrow we either land on the
    homework page or back on the quiz page (then Assign again).
    """
    for _attempt in range(3):
        _dismiss_coachmarks(page, on_progress)
        S.click_outside_dialogs(page, r"^Assign$", timeout_ms=15_000)
        state = _wait_homework_or_glitch(page, timeout_ms=30_000)
        if state == "homework":
            match = _HOMEWORK_URL_RE.search(page.url)
            return match.group("homework_id") if match else ""
        if state == "glitch":
            recover_accommodations_page(page, on_progress)
            page.wait_for_timeout(1_500)
            if _HOMEWORK_URL_RE.search(page.url):
                match = _HOMEWORK_URL_RE.search(page.url)
                return match.group("homework_id") if match else ""
            _log(on_progress, "Back from the accommodations page; trying Assign again...")
            continue
    raise AssignError(f"could not reach the Assignment Details page (now at {page.url})")


def _dismiss_coachmarks(page: Page, on_progress: ProgFn = None) -> None:
    """'Got it' tip bubbles can intercept clicks (LIVE 2026-11-08)."""
    for _ in range(3):
        if not S.visible(page, S.GOT_IT_BUTTON, timeout_ms=800):
            return
        _log(on_progress, "Dismissing a 'Got it' tip...")
        try:
            S.click(page, S.GOT_IT_BUTTON, timeout_ms=2_000)
        except SelectorNotFoundError:
            return


def assign_one(
    page: Page,
    *,
    class_name: str,
    assignment_title: str,
    no_deadline: bool = True,
    on_progress: ProgFn = None,
) -> AssignResult:
    """Run the whole homework-assignment flow. Entry point: the quiz page."""
    _log(on_progress, "Starting the assignment flow (Assign)...")
    recover_accommodations_page(page, on_progress)
    homework_id = _enter_homework_page(page, on_progress)
    S.resolve(page, S.ASSIGNMENT_DETAILS_MARKER, timeout_ms=30_000)
    recover_accommodations_page(page, on_progress)

    _open_class_dialog(page, on_progress)
    dialog = S.find_dialog(page, S.CLASS_DIALOG_TITLE)
    _select_class_row(page, dialog, class_name, on_progress)
    _uncheck_accommodations(page, dialog, fatal=True, on_progress=on_progress)
    S.click(dialog, S.NEXT_BUTTON, timeout_ms=8_000)
    # Modals stack: the class modal stays mounted; wait for the NEXT modal.
    name_dialog = S.find_dialog(page, S.NAME_ASSIGNMENT_DIALOG_TITLE, timeout_ms=15_000)

    _name_assignment(page, assignment_title, on_progress, dialog=name_dialog)
    recover_accommodations_page(page, on_progress)
    try:
        S.resolve(page, S.CLASS_ASSIGNED_MARKER, timeout_ms=15_000)
    except SelectorNotFoundError as exc:
        raise AssignError("the class was not attached (no 'Session will be assigned' marker)") from exc

    if no_deadline:
        _set_no_deadline(page, on_progress)
    activity_id = _click_final_assign(page, on_progress)
    _log(on_progress, "Assignment created; gameplay dashboard reached.")
    return AssignResult(class_name=class_name, homework_id=homework_id,
                        activity_id=activity_id, activity_url=page.url,
                        no_deadline=no_deadline)
