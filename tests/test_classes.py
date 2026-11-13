"""classes.py pure logic + the CLI dynamic-class prompts (no browser)."""

from __future__ import annotations

from argparse import Namespace

import pytest

from wqu import classes as C
from wqu.errors import ClassCreateError

# --- pure helpers ---------------------------------------------------------------


def test_normalize_class_choice_forms() -> None:
    assert C.normalize_class_choice(None) == C.ClassChoice("public")
    assert C.normalize_class_choice("") == C.ClassChoice("public")
    assert C.normalize_class_choice("  ") == C.ClassChoice("public")
    assert C.normalize_class_choice("Naic") == C.ClassChoice("select", "Naic")
    choice = C.ClassChoice("create", "X", True)
    assert C.normalize_class_choice(choice) is choice


class FakeDialog:
    def __init__(self, names=None, *, error: bool = False) -> None:
        self.names = names or []
        self.error = error

    def evaluate(self, js: str) -> list:  # noqa: ARG002
        if self.error:
            raise RuntimeError("boom")
        return self.names


def test_scrape_classes_cleans_and_dedupes() -> None:
    dialog = FakeDialog([" Naic ", "", "Naic", "Other", "  "])
    assert C.scrape_classes_from_modal(dialog) == ("Naic", "Other")  # type: ignore[arg-type]


def test_scrape_classes_survives_evaluate_error() -> None:
    assert C.scrape_classes_from_modal(FakeDialog(error=True)) == ()  # type: ignore[arg-type]


def test_create_class_requires_a_name() -> None:
    with pytest.raises(ClassCreateError):
        C.create_class(None, "   ")  # type: ignore[arg-type]


# --- CLI resolver prompts --------------------------------------------------------


def _args(**overrides) -> Namespace:  # noqa: ANN003
    base = dict(keep_draft=False, assign=None, create_class_if_missing=False)
    base.update(overrides)
    return Namespace(**base)


@pytest.fixture()
def interactive(monkeypatch):  # noqa: ANN001
    from wqu import cli

    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    yield


def test_make_resolve_class_returns_none_when_not_interactive(monkeypatch) -> None:  # noqa: ANN001
    from wqu import cli

    monkeypatch.setattr(cli, "_is_tty", lambda: False)
    assert cli.make_resolve_class(_args()) is None
    assert cli.make_resolve_class(_args(keep_draft=True)) is None


def test_resolver_selects_existing_by_number(interactive, monkeypatch) -> None:  # noqa: ANN001
    from wqu import cli

    answers = iter(["2"])
    monkeypatch.setattr(cli.Prompt, "ask", lambda *a, **k: next(answers))
    resolve = cli.make_resolve_class(_args())
    assert resolve(("Alpha", "Beta")) == C.ClassChoice("select", "Beta")


def test_resolver_creates_new_class_with_guardian(interactive, monkeypatch) -> None:  # noqa: ANN001
    from wqu import cli

    answers = iter(["c", "My Class"])
    monkeypatch.setattr(cli.Prompt, "ask", lambda *a, **k: next(answers))
    monkeypatch.setattr(cli.Confirm, "ask", lambda *a, **k: True)
    resolve = cli.make_resolve_class(_args())
    assert resolve(("Alpha",)) == C.ClassChoice("create", "My Class", True)


def test_resolver_public_choice(interactive, monkeypatch) -> None:  # noqa: ANN001
    from wqu import cli

    answers = iter(["p"])
    monkeypatch.setattr(cli.Prompt, "ask", lambda *a, **k: next(answers))
    resolve = cli.make_resolve_class(_args())
    assert resolve(("Alpha",)) == C.ClassChoice("public")


def test_resolver_empty_account_can_create(interactive, monkeypatch) -> None:  # noqa: ANN001
    from wqu import cli

    answers = iter(["c", "Fresh"])
    monkeypatch.setattr(cli.Prompt, "ask", lambda *a, **k: next(answers))
    monkeypatch.setattr(cli.Confirm, "ask", lambda *a, **k: False)
    resolve = cli.make_resolve_class(_args())
    assert resolve(()) == C.ClassChoice("create", "Fresh", False)


def test_resolver_flag_auto_creates_without_prompting(interactive, monkeypatch) -> None:  # noqa: ANN001
    from wqu import cli

    def boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("must not prompt when --create-class is set")

    monkeypatch.setattr(cli.Prompt, "ask", boom)
    resolve = cli.make_resolve_class(_args(assign="MyClass", create_class_if_missing=True))
    assert resolve(("Alpha",)) == C.ClassChoice("create", "MyClass")


def test_resolver_eof_falls_back_to_public(interactive, monkeypatch) -> None:  # noqa: ANN001
    from wqu import cli

    def eof(*args, **kwargs):  # noqa: ANN002, ANN003
        raise EOFError

    monkeypatch.setattr(cli.Prompt, "ask", eof)
    resolve = cli.make_resolve_class(_args())
    assert resolve(("Alpha",)) == C.ClassChoice("public")
