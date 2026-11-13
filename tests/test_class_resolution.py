"""Dynamic class resolution in the assign flow (no browser).

Covers `_resolve_and_select_class`: existing class, resolver-chosen class,
create-then-select round-trip, public fallback, auto-create via
`create_if_missing`, and the ClassNotFound error listing the scraped classes.
"""

from __future__ import annotations

import pytest

from wqu import assign as A
from wqu import classes as C
from wqu.errors import ClassNotFoundError

HOMEWORK_URL = "https://wayground.com/activity/admin/quiz/homework/hw1"


class FakePage:
    def __init__(self, url: str = HOMEWORK_URL) -> None:
        self.url = url
        self.goto_calls: list[str] = []

    def goto(self, url: str, **kwargs) -> None:  # noqa: ANN003
        self.goto_calls.append(url)
        self.url = url


@pytest.fixture()
def env(monkeypatch):  # noqa: ANN001
    """Patch everything _resolve_and_select_class touches except decisions."""
    state: dict = {
        "scrapes": [], "selected": [], "created": [], "cancelled": 0,
        "clicks": [], "page": FakePage(),
    }
    monkeypatch.setattr(A, "_open_class_dialog", lambda page, on_progress=None: None)
    monkeypatch.setattr(A.S, "find_dialog", lambda *a, **k: object())
    monkeypatch.setattr(A, "_available_class_names",
                        lambda dialog: state["scrapes"].pop(0))
    monkeypatch.setattr(A, "_select_class_row",
                        lambda page, dialog, name, on_progress: state["selected"].append(name))
    monkeypatch.setattr(A, "_uncheck_accommodations", lambda *a, **k: None)
    monkeypatch.setattr(A, "_cancel_class_dialog",
                        lambda page, dialog: state.__setitem__("cancelled", state["cancelled"] + 1))
    monkeypatch.setattr(A, "_dismiss_coachmarks", lambda *a, **k: None)
    monkeypatch.setattr(A.S, "click", lambda scope, sel, **k: state["clicks"].append(sel.key))

    def fake_create(page, name, *, require_guardian_email=False, on_progress=None):  # noqa: ANN001
        state["created"].append((name, require_guardian_email))
        return C.CreatedClass(name=name, guardian_email_required=require_guardian_email)

    monkeypatch.setattr(C, "create_class", fake_create)
    yield state


def test_requested_class_present_selects_without_prompting(env: dict) -> None:
    env["scrapes"].append(("Naic", "Other"))
    prompted: list = []
    result = A._resolve_and_select_class(
        env["page"], "Naic", create_if_missing=False,
        resolve_class=lambda available: prompted.append(available),  # type: ignore[func-returns-value]
        on_progress=None,
    )
    assert result == "Naic"
    assert env["selected"] == ["Naic"]
    assert prompted == []  # no prompt when the requested class exists
    assert "next_button" in env["clicks"]


def test_missing_class_resolver_selects_existing(env: dict) -> None:
    env["scrapes"].append(("Alpha", "Beta"))
    result = A._resolve_and_select_class(
        env["page"], "Naic", create_if_missing=False,
        resolve_class=lambda available: C.ClassChoice("select", "Beta"),
        on_progress=None,
    )
    assert result == "Beta"
    assert env["selected"] == ["Beta"]


def test_missing_class_resolver_creates_then_selects(env: dict) -> None:
    env["scrapes"].extend([("Alpha",), ("Alpha", "Brand New")])
    prompted: list = []

    def resolve(available: tuple[str, ...]) -> C.ClassChoice:
        prompted.append(available)
        return C.ClassChoice("create", "Brand New", True)

    result = A._resolve_and_select_class(
        env["page"], "Naic", create_if_missing=False, resolve_class=resolve, on_progress=None,
    )
    assert result == "Brand New"
    assert env["created"] == [("Brand New", True)]  # guardian email honoured
    assert prompted == [("Alpha",)]                 # not re-prompted after creation
    assert env["cancelled"] == 1                    # modal closed before navigating
    assert env["page"].goto_calls == [HOMEWORK_URL]  # returned to the assignment page
    assert env["selected"] == ["Brand New"]


def test_missing_class_resolver_public_falls_back(env: dict) -> None:
    env["scrapes"].append(("Alpha",))
    result = A._resolve_and_select_class(
        env["page"], "Naic", create_if_missing=False,
        resolve_class=lambda available: C.ClassChoice("public"),
        on_progress=None,
    )
    assert result is None
    assert env["cancelled"] == 1
    assert env["selected"] == []


def test_missing_class_without_resolver_raises_with_available(env: dict) -> None:
    env["scrapes"].append(("Alpha", "Beta"))
    with pytest.raises(ClassNotFoundError) as excinfo:
        A._resolve_and_select_class(
            env["page"], "Naic", create_if_missing=False, resolve_class=None, on_progress=None,
        )
    message = str(excinfo.value)
    assert "Naic" in message and "Alpha" in message and "Beta" in message


def test_missing_class_create_if_missing_auto_creates(env: dict) -> None:
    env["scrapes"].extend([(), ("Naic",)])
    result = A._resolve_and_select_class(
        env["page"], "Naic", create_if_missing=True, resolve_class=None, on_progress=None,
    )
    assert result == "Naic"
    assert env["created"] == [("Naic", False)]


def test_resolver_selecting_unknown_name_raises(env: dict) -> None:
    env["scrapes"].append(("Alpha",))
    with pytest.raises(ClassNotFoundError):
        A._resolve_and_select_class(
            env["page"], "Naic", create_if_missing=False,
            resolve_class=lambda available: C.ClassChoice("select", "Ghost"),
            on_progress=None,
        )


def test_empty_account_offers_create_or_public(env: dict) -> None:
    env["scrapes"].extend([(), ("Fresh",)])
    result = A._resolve_and_select_class(
        env["page"], "Naic", create_if_missing=False,
        resolve_class=lambda available: C.ClassChoice("create", "Fresh"),
        on_progress=None,
    )
    assert result == "Fresh"
    assert env["created"] == [("Fresh", False)]


# --- assign_one wiring ---------------------------------------------------------


def test_assign_one_public_result_reports_no_deadline_false(monkeypatch) -> None:  # noqa: ANN001
    page = FakePage("https://wayground.com/activity/admin/quiz/quiz1")
    monkeypatch.setattr(A, "_enter_homework_page", lambda page, on_progress=None: "hw1")
    monkeypatch.setattr(A, "_dismiss_coachmarks", lambda *a, **k: None)
    monkeypatch.setattr(A, "recover_accommodations_page", lambda *a, **k: False)
    monkeypatch.setattr(A.S, "resolve", lambda *a, **k: object())
    monkeypatch.setattr(A, "_click_final_assign", lambda page, on_progress=None: "act1")

    result = A.assign_one(page, class_name=None, assignment_title="T", on_progress=None)
    assert result.public is True
    assert result.class_name is None
    assert result.no_deadline is False
    assert result.activity_id == "act1"
