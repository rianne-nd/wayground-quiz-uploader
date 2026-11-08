"""Mechanical enforcement of the core-module discipline (brief Parts 2.5/2.6).

cli.py is the only module that may print/prompt or import rich; vendored code
is exempt (see NOTICE). No asyncio anywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

WQU = Path(__file__).resolve().parents[1] / "wqu"
# cli.py and ui.py form the CLI presentation layer: cli.py owns prompts and
# stdout/stderr, ui.py owns the rich renderables it uses. ui.py is imported
# ONLY by cli.py — enforced below — so core stays GUI-wrappable.
PRESENTATION = {"cli.py", "ui.py", "__init__.py"}
CORE_MODULES = sorted(path for path in WQU.glob("*.py") if path.name not in PRESENTATION)


def _sources() -> list[tuple[str, str]]:
    return [(path.name, path.read_text(encoding="utf-8")) for path in CORE_MODULES]


@pytest.mark.parametrize("name,source", _sources(), ids=[n for n, _ in _sources()])
def test_core_has_no_print_calls(name: str, source: str) -> None:
    assert "print(" not in source, f"{name} must not print (only cli.py does)"


@pytest.mark.parametrize("name,source", _sources(), ids=[n for n, _ in _sources()])
def test_core_never_imports_rich_or_typer(name: str, source: str) -> None:
    assert "import rich" not in source and "from rich" not in source, f"{name} must not import rich"
    assert "import typer" not in source and "from typer" not in source, f"{name} must not import typer"


def test_no_asyncio_anywhere() -> None:
    for path in WQU.rglob("*.py"):
        if "vendor" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        assert "import asyncio" not in source, f"{path.name} must not use asyncio (sync Playwright only)"


def test_only_cli_imports_the_ui_layer() -> None:
    """wqu.ui is presentation; core modules must never depend on it."""
    for path in CORE_MODULES:
        source = path.read_text(encoding="utf-8")
        assert "wqu.ui" not in source, f"{path.name} must not import wqu.ui"
        assert "import ui" not in source, f"{path.name} must not import wqu.ui"
