"""Workbook loading and validation (the input contract — brief Part 1.5).

Every input workbook: exactly one sheet named "Create a Quiz", header row 1
with 11 columns in a fixed order, 1-based "Correct Answer" indices, and an
optional literal "Optional" in Image Link / Answer explanation.

This module never prints; it returns ``QuizData`` with attached ``Issue``
records so the CLI (or a future GUI) decides how to present them.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from openpyxl import load_workbook

from wqu.errors import ValidationError

log = logging.getLogger(__name__)

SHEET_NAME = "Create a Quiz"
HEADERS: tuple[str, ...] = (
    "Question Text",
    "Question Type",
    "Option 1",
    "Option 2",
    "Option 3",
    "Option 4",
    "Option 5",
    "Correct Answer",
    "Time in seconds",
    "Image Link",
    "Answer explanation",
)

# Filename marks: "Quizizz_Foo.xlsx" -> "Foo"; "Foo_pt3.xlsx" -> split part 3.
_QUIZIZZ_PREFIX = re.compile(r"^Quizizz_", re.IGNORECASE)
_SPLIT_RE = re.compile(r"^(?P<base>.+?)_pt(?P<n>\d+)\.xlsx$", re.IGNORECASE)

_OPTIONAL_LITERAL = "optional"


class QType(str, Enum):
    MULTIPLE_CHOICE = "Multiple Choice"
    CHECKBOX = "Checkbox"
    FILL_IN_BLANK = "Fill-in-the-Blank"


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" | "warning"
    row: int | None
    message: str

    def __str__(self) -> str:
        location = f"row {self.row}: " if self.row else ""
        return f"{self.severity.upper()}: {location}{self.message}"


@dataclass(frozen=True)
class Question:
    row: int  # spreadsheet row (row 1 is the header)
    text: str
    qtype: QType
    options: tuple[str, ...]  # non-empty option cells, in column order
    correct: tuple[int, ...]  # 1-based indices into the option *columns*
    time_s: int | None
    image_link: str | None
    explanation: str | None


@dataclass(frozen=True)
class QuizData:
    path: Path
    title: str  # quiz title (normalized, Quizizz_ stripped, _PtN capitalized)
    label: str  # output label — identical to title; kept for clarity
    is_split: bool
    part: int | None
    questions: tuple[Question, ...]
    issues: tuple[Issue, ...]

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity == "error")

    @property
    def warnings(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity == "warning")

    @property
    def is_valid(self) -> bool:
        return not self.errors


def title_from_filename(filename: str) -> tuple[str, str, bool, int | None]:
    """Derive (title, label, is_split, part) from a workbook filename.

    ``Quizizz_DataComs_Mod14.xlsx``            -> DataComs_Mod14 (whole)
    ``Quizizz_DataComs_Quiz2_pt1.xlsx``        -> DataComs_Quiz2_Pt1 (split 1)
    """
    name = Path(filename).name
    split = _SPLIT_RE.match(name)
    if split:
        base = _QUIZIZZ_PREFIX.sub("", split.group("base"))
        part = int(split.group("n"))
        label = f"{base}_Pt{part}"
        return label, label, True, part
    stem = Path(name).stem
    title = _QUIZIZZ_PREFIX.sub("", stem)
    return title, title, False, None


def _cell_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value).strip()


def normalize_qtype(raw: str) -> QType | None:
    """Map the spelling variants of question types onto the three types."""
    text = re.sub(r"[\s_-]+", " ", raw.strip().lower())
    if not text:
        return None
    if "fill" in text or "blank" in text:
        return QType.FILL_IN_BLANK
    if "check" in text or "select" in text:  # "Checkbox", "Multiple Select"
        return QType.CHECKBOX
    if "choice" in text or "multiple" in text or text == "mcq":
        return QType.MULTIPLE_CHOICE
    return None


def _parse_correct(raw: str, qtype: QType, row: int) -> tuple[tuple[int, ...], list[Issue]]:
    issues: list[Issue] = []
    raw = raw.strip()
    if qtype is QType.FILL_IN_BLANK:
        if raw:
            issues.append(Issue("warning", row, "Correct Answer is ignored for fill-in-the-blank "
                                               "(accepted answers live in the Option columns)"))
        return (), issues
    if not raw:
        issues.append(Issue("error", row, "missing Correct Answer (expected 1-based option index)"))
        return (), issues
    indices: list[int] = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            index = int(float(piece))
        except ValueError:
            issues.append(Issue("error", row, f"Correct Answer {piece!r} is not a number"))
            continue
        if index < 1 or index > 5:
            issues.append(Issue("error", row, f"Correct Answer {index} is outside the 1-5 option range"))
            continue
        if index not in indices:
            indices.append(index)
    return tuple(sorted(indices)), issues


def _parse_row(row_number: int, values: list[str]) -> tuple[Question | None, list[Issue]]:
    issues: list[Issue] = []
    (
        text, qtype_raw, *option_cells, correct_raw, time_raw, image_raw, explanation_raw,
    ) = values
    options = tuple(_cell_str(c) for c in option_cells)
    if not text:
        issues.append(Issue("error", row_number, "empty Question Text"))
    qtype = normalize_qtype(qtype_raw)
    if qtype is None:
        issues.append(Issue("error", row_number, f"unrecognized Question Type {qtype_raw!r}"))
        return None, issues
    option_columns = [i for i, opt in enumerate(options) if opt]
    if qtype is QType.FILL_IN_BLANK:
        if not options or all(not opt for opt in options):
            issues.append(Issue("error", row_number, "fill-in-the-blank row has no accepted answers "
                                                     "in the Option columns"))
    elif len(option_columns) < 2:
        issues.append(Issue("error", row_number, "needs at least two options for its question type"))
    correct, correct_issues = _parse_correct(correct_raw, qtype, row_number)
    issues.extend(correct_issues)
    for index in correct:
        if not options[index - 1]:
            issues.append(Issue("error", row_number,
                                f"Correct Answer {index} points at empty Option {index}"))
    time_s: int | None = None
    if time_raw:
        try:
            time_s = int(float(time_raw))
            if time_s <= 0:
                issues.append(Issue("warning", row_number, f"non-positive time {time_raw!r} ignored"))
                time_s = None
        except ValueError:
            issues.append(Issue("warning", row_number, f"unparseable time {time_raw!r} ignored"))
    image = None if image_raw.strip().lower() == _OPTIONAL_LITERAL else (image_raw or None)
    explanation = None if explanation_raw.strip().lower() == _OPTIONAL_LITERAL else (explanation_raw or None)
    question = Question(
        row=row_number,
        text=text,
        qtype=qtype,
        options=tuple(opt for opt in options if opt),
        correct=correct,
        time_s=time_s,
        image_link=image,
        explanation=explanation,
    )
    return question, issues


def load_xlsx(path: Path | str, *, title_override: str | None = None) -> QuizData:
    """Load and validate one workbook. Content problems are returned as issues."""
    path = Path(path)
    if not path.is_file():
        raise ValidationError(f"file not found: {path}")
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise ValidationError(f"could not open {path.name}: {exc}") from exc

    title, label, is_split, part = title_from_filename(path.name)
    if title_override:
        title = title_override
        label = title_override

    issues: list[Issue] = []
    try:
        if SHEET_NAME not in workbook.sheetnames:
            found = ", ".join(workbook.sheetnames) or "(none)"
            raise ValidationError(f"{path.name}: sheet {SHEET_NAME!r} not found (found: {found})")
        extras = [name for name in workbook.sheetnames if name != SHEET_NAME]
        if extras:
            issues.append(Issue("warning", None, f"extra sheets ignored: {', '.join(extras)}"))
        sheet = workbook[SHEET_NAME]

        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            raise ValidationError(f"{path.name}: sheet {SHEET_NAME!r} is empty")
        header = [_cell_str(v) for v in rows[0][: len(HEADERS)]]
        if [h.lower() for h in header] != [h.lower() for h in HEADERS]:
            detail = "; ".join(
                f"col {i + 1}: expected {HEADERS[i]!r}, found {(header[i] if i < len(header) else '')!r}"
                for i in range(len(HEADERS))
                if i >= len(header) or header[i].lower() != HEADERS[i].lower()
            )
            raise ValidationError(f"{path.name}: header row does not match the expected format — {detail}")

        questions: list[Question] = []
        for offset, row_values in enumerate(rows[1:], start=2):
            values = [_cell_str(v) for v in row_values[: len(HEADERS)]]
            values += [""] * (len(HEADERS) - len(values))
            if not any(values):
                continue  # fully blank spacer row
            question, row_issues = _parse_row(offset, values)
            issues.extend(row_issues)
            if question is not None:
                questions.append(question)
        if not questions:
            raise ValidationError(f"{path.name}: no question rows found under the header")
        return QuizData(
            path=path,
            title=title,
            label=label,
            is_split=is_split,
            part=part,
            questions=tuple(questions),
            issues=tuple(issues),
        )
    finally:
        workbook.close()


def preview_errors(path: Path | str) -> list[Issue]:
    """Convenience wrapper used by dry-run: every issue for one workbook."""
    try:
        return list(load_xlsx(path).issues)
    except ValidationError as exc:
        return [Issue("error", None, str(exc))]
