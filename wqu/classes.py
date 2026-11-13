"""Class management: scrape available classes and create new ones.

Used by the assign flow (dynamic class detection) and the CLI prompts.

LIVE DOM notes (2026-11-13, user screenshots + earlier probes):
  * classes page: https://wayground.com/manage/admin/classes ("My classes"),
    a pink "Create a class" button in the top right;
  * the create modal ("Create a new class") has an "Enter class name" input,
    a "Require students to enter a guardian's email address" checkbox and a
    pink "Create class" footer button;
  * class rows inside the "Assign to a class" modal are checkbox inputs named
    "choose-class" (``data-testid=choose-class-N``); the first text line of the
    row container is the class name, the second the roster summary.

Library-safe: returns dataclasses, prints nothing; prompts live in cli.py
behind the ``resolve_class`` callback.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from wqu import selectors as S
from wqu.errors import ClassCreateError, SessionExpiredError
from wqu.session import looks_like_login

log = logging.getLogger(__name__)

CLASSES_URL = "https://wayground.com/manage/admin/classes"

ProgFn = Callable[[str], None] | None


@dataclass(frozen=True)
class CreatedClass:
    name: str
    guardian_email_required: bool = False


@dataclass(frozen=True)
class ClassChoice:
    """What to do when the requested class is missing (returned by resolvers).

    ``action``: "select" (use ``name`` from the available list) | "create"
    (create ``name`` first, then select it) | "public" (no class at all).
    """

    action: str
    name: str | None = None
    guardian_email: bool = False


ResolveClassFn = Callable[[tuple[str, ...]], "ClassChoice | str | None"]


def normalize_class_choice(value: ClassChoice | str | None) -> ClassChoice:
    """Accept the convenient forms callers use and return a ClassChoice."""
    if value is None:
        return ClassChoice("public")
    if isinstance(value, ClassChoice):
        return value
    text = str(value).strip()
    if not text:
        return ClassChoice("public")
    return ClassChoice("select", text)


def _log(on_progress: ProgFn, message: str) -> None:
    log.info(message)
    if on_progress is not None:
        on_progress(message)


def _dismiss_tip(page: Page) -> None:
    """The 'Got it' onboarding bubble intercepts clicks on fresh pages."""
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


# Scrape the "Assign to a class" modal: checkbox rows whose first text line is
# the class name (second line is "N students · M completed sessions").
_SCRAPE_CLASSES_JS = r"""
() => {
  const names = [];
  const boxes = document.querySelectorAll(
    'input[name="choose-class"], input[data-testid^="choose-class"]');
  const scan = (box) => {
    const row = box.closest('label, li, [role="row"], [role="listitem"], div');
    const text = (row && row.innerText) || box.getAttribute('aria-label') || '';
    return text.split('\n').map(s => s.trim()).filter(Boolean)
      .find(s => !/select all|set up accommodations|choose-class/i.test(s));
  };
  for (const box of boxes) {
    const name = scan(box);
    if (name) names.push(name);
  }
  if (names.length === 0) {  // fallback: any checkbox row with a name
    for (const box of document.querySelectorAll('[role="checkbox"], input[type="checkbox"]')) {
      const name = scan(box);
      if (name) names.push(name);
    }
  }
  return [...new Set(names)];
}
"""


def scrape_classes_from_modal(dialog: Locator) -> tuple[str, ...]:
    """Class names currently offered in the open 'Assign to a class' modal."""
    try:
        names = dialog.evaluate(_SCRAPE_CLASSES_JS)
    except Exception as exc:
        log.debug("class scrape failed: %s", exc)
        return ()
    cleaned: list[str] = []
    for raw in names or []:
        name = str(raw).strip()
        if name and name not in cleaned:
            cleaned.append(name)
    return tuple(cleaned)


def create_class(
    page: Page,
    name: str,
    *,
    require_guardian_email: bool = False,
    on_progress: ProgFn = None,
) -> CreatedClass:
    """Create a class on the classes page and wait until it is listed.

    Flow (user screenshots, 2026-11-13): /manage/admin/classes → "Create a
    class" → "Create a new class" modal → name → optional guardian checkbox →
    "Create class" → modal closes and the class appears in the list.
    """
    name = name.strip()
    if not name:
        raise ClassCreateError("a class name is required")
    _log(on_progress, f"Creating a new class {name!r}...")
    page.goto(CLASSES_URL, wait_until="domcontentloaded", timeout=60_000)
    if looks_like_login(page.url):
        raise SessionExpiredError("Session expired. Run 'wqu login' again.")
    _dismiss_tip(page)

    S.click(page, S.CREATE_CLASS_BUTTON, timeout_ms=20_000)
    dialog = S.find_dialog(page, S.CREATE_CLASS_DIALOG_TITLE, timeout_ms=15_000)
    field = S.resolve(dialog, S.CLASS_NAME_INPUT, timeout_ms=8_000)
    field.fill(name)

    if require_guardian_email:
        try:
            box = S.resolve(dialog, S.GUARDIAN_EMAIL_CHECKBOX, timeout_ms=4_000)
        except Exception:
            box = None
        if box is None:
            log.warning("guardian-email checkbox not found; creating without it")
        else:
            state = S.read_checked(page, box)
            if state is False:
                S.click_control(box, timeout_ms=2_500)
            elif state is None:
                log.warning("could not read the guardian-email checkbox state; leaving it as-is")

    S.click(dialog, S.CREATE_CLASS_CONFIRM, timeout_ms=8_000)
    try:
        page.get_by_text("Create a new class", exact=False).first.wait_for(state="hidden", timeout=15_000)
    except PlaywrightTimeoutError:
        log.info("create-class modal still mounted (stacked); continuing")
    try:
        page.get_by_text(name, exact=True).first.wait_for(state="visible", timeout=15_000)
    except PlaywrightTimeoutError as exc:
        raise ClassCreateError(f"class {name!r} was not listed after creation") from exc
    _log(on_progress, f"Class {name!r} created.")
    return CreatedClass(name=name, guardian_email_required=require_guardian_email)
