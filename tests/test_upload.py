"""Workbook validation: the Part 1.5 input contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from wqu.errors import ValidationError
from wqu.upload import QType, load_xlsx, preview_errors


def test_sample_single_is_valid(fixtures_dir: Path) -> None:
    data = load_xlsx(fixtures_dir / "sample_single.xlsx")
    assert data.is_valid, [str(i) for i in data.issues]
    assert len(data.questions) == 4
    types = [q.qtype for q in data.questions]
    assert QType.MULTIPLE_CHOICE in types
    assert QType.CHECKBOX in types
    assert QType.FILL_IN_BLANK in types
    checkbox = next(q for q in data.questions if q.qtype is QType.CHECKBOX)
    assert checkbox.correct == (1, 3)
    fib = next(q for q in data.questions if q.qtype is QType.FILL_IN_BLANK)
    assert fib.correct == ()  # answers live in the option columns
    assert fib.options == ("10", "ten")
    # literal "Optional" is treated as blank
    optional_rows = [q for q in data.questions if q.image_link is None and q.explanation is None]
    assert optional_rows


def test_sample_broken_reports_row_and_range(fixtures_dir: Path) -> None:
    data = load_xlsx(fixtures_dir / "sample_broken.xlsx")
    assert not data.is_valid
    messages = " ".join(str(i) for i in data.errors)
    assert "row 3" in messages
    assert "outside the 1-5 option range" in messages


def test_missing_sheet_raises(wk, tmp_path: Path) -> None:
    path = wk(tmp_path / "x.xlsx", [("Q?", "Multiple Choice", "A", "B", "", "", "", "1", 30, "", "")],
              sheet_name="Other")
    with pytest.raises(ValidationError, match="Create a Quiz"):
        load_xlsx(path)


def test_header_mismatch_raises(wk, tmp_path: Path) -> None:
    path = wk(tmp_path / "x.xlsx", [("Q?", "Multiple Choice", "A", "B", "", "", "", "1", 30, "", "")],
              headers=["Question", "Type", "A", "B", "C", "D", "E", "Correct", "Time", "Image", "Why"])
    with pytest.raises(ValidationError, match="header row"):
        load_xlsx(path)


def test_correct_answer_pointing_at_empty_option(wk, tmp_path: Path) -> None:
    path = wk(tmp_path / "x.xlsx", [("Q?", "Multiple Choice", "A", "", "", "", "", "3", 30, "", "")])
    data = load_xlsx(path)
    assert not data.is_valid
    assert any("empty Option 3" in str(i) for i in data.errors)


def test_fill_in_the_blank_needs_answers(wk, tmp_path: Path) -> None:
    path = wk(tmp_path / "x.xlsx", [("Q?", "Fill-in-the-Blank", "", "", "", "", "", "", 30, "", "")])
    data = load_xlsx(path)
    assert not data.is_valid
    assert any("accepted answers" in str(i) for i in data.errors)


def test_unparseable_time_is_warning_only(wk, tmp_path: Path) -> None:
    path = wk(tmp_path / "x.xlsx", [("Q?", "Multiple Choice", "A", "B", "", "", "", "1", "soon", "", "")])
    data = load_xlsx(path)
    assert data.is_valid
    assert any("unparseable time" in str(i) for i in data.warnings)


def test_blank_spacer_rows_ignored(wk, tmp_path: Path) -> None:
    path = wk(tmp_path / "x.xlsx", [
        ("Q1?", "Multiple Choice", "A", "B", "", "", "", "2", 30, "", ""),
        ("", "", "", "", "", "", "", "", "", "", ""),
        ("Q2?", "Multiple Choice", "A", "B", "", "", "", "1", 30, "", ""),
    ])
    data = load_xlsx(path)
    assert data.is_valid
    assert [q.row for q in data.questions] == [2, 4]


def test_preview_errors_for_missing_file(tmp_path: Path) -> None:
    issues = preview_errors(tmp_path / "nope.xlsx")
    assert len(issues) == 1
    assert "file not found" in str(issues[0])


def test_title_override_applies_to_label(fixtures_dir: Path) -> None:
    data = load_xlsx(fixtures_dir / "Quizizz_DataComs_Mod14.xlsx", title_override="Custom")
    assert data.title == "Custom"
    assert data.label == "Custom"
