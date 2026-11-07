"""Shared test fixtures. Unit tests never touch the network or a browser."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
_REQUIRED = (
    "sample_single.xlsx",
    "sample_broken.xlsx",
    "Quizizz_DataComs_Mod14.xlsx",
    "DataComs_Quiz2_pt1.xlsx",
    "DataComs_Quiz2_pt2.xlsx",
)


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Path to the committed fixtures, regenerating them when missing."""
    if not all((FIXTURES / name).exists() for name in _REQUIRED):
        spec = importlib.util.spec_from_file_location("generate_fixtures", FIXTURES / "generate_fixtures.py")
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        module.main()
    return FIXTURES


@pytest.fixture()
def wk(pytestconfig):  # noqa: ANN001 - small helper for temp workbooks
    """Helper factory writing one-sheet workbooks in tests."""
    from openpyxl import Workbook

    def make(path: Path, rows: list[tuple], sheet_name: str = "Create a Quiz",
             headers: list[str] | None = None) -> Path:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = sheet_name
        sheet.append(headers or [
            "Question Text", "Question Type", "Option 1", "Option 2", "Option 3",
            "Option 4", "Option 5", "Correct Answer", "Time in seconds",
            "Image Link", "Answer explanation",
        ])
        for row in rows:
            sheet.append(list(row))
        workbook.save(path)
        return path

    return make


def pytest_configure(config):  # noqa: ANN001
    # keep sys.path clean; the package is installed editable for tests
    sys.path = [p for p in sys.path if "tests" not in p.lower()]
