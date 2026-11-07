"""Generate the committed test fixtures with openpyxl (reproducible, not opaque).

Run from the repo root:

    python tests/fixtures/generate_fixtures.py

Produces, in this directory:
    sample_single.xlsx          valid: 2 MC + 1 Checkbox + 1 Fill-in-the-Blank
    sample_broken.xlsx          invalid: row 3 has Correct Answer 9 (out of range)
    Quizizz_DataComs_Mod14.xlsx valid, title-stripping fixture
    DataComs_Quiz2_pt1.xlsx     valid split part 1
    DataComs_Quiz2_pt2.xlsx     valid split part 2
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

HERE = Path(__file__).resolve().parent
SHEET = "Create a Quiz"
HEADERS = (
    "Question Text", "Question Type", "Option 1", "Option 2", "Option 3",
    "Option 4", "Option 5", "Correct Answer", "Time in seconds",
    "Image Link", "Answer explanation",
)

SAMPLE_SINGLE = [
    ("What does HTTP stand for?", "Multiple Choice",
     "HyperText Transfer Protocol", "HyperText Transmission Process",
     "Home Tool Transfer Protocol", "", "", "1", 30, "",
     "HyperText Transfer Protocol"),
    ("Which of these are version control systems?", "Checkbox",
     "Git", "Word", "Mercurial", "Excel", "", "1,3", 45, "Optional",
     "Git and Mercurial are version control systems."),
    ("The binary number 1010 equals ___ in decimal.", "Fill-in-the-Blank",
     "10", "ten", "", "", "", "", 60, "", "1010 in binary is 10 in decimal."),
    ("Which CSS property changes the text color?", "Multiple Choice",
     "color", "font-style", "background", "text-decoration", "", "1", 30,
     "Optional", "Optional"),
]

SAMPLE_BROKEN = [
    ("What does HTTP stand for?", "Multiple Choice",
     "HyperText Transfer Protocol", "HyperText Transmission Process",
     "Home Tool Transfer Protocol", "", "", "1", 30, "", ""),
    ("Which tool compiles C# code?", "Multiple Choice",
     "dotnet build", "python -m build", "make", "", "", "9", 30, "", ""),
]

SMALL_TWO = [
    ("Part question one?", "Multiple Choice", "Alpha", "Beta", "", "", "", "2", 30, "", ""),
    ("Part question two?", "Multiple Choice", "Gamma", "Delta", "", "", "", "1", 30, "", ""),
]


def write_workbook(path: Path, rows: list[tuple]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET
    sheet.append(HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"
    for row in rows:
        sheet.append(list(row))
    workbook.save(path)
    print(f"wrote {path.name}")


def main() -> None:
    write_workbook(HERE / "sample_single.xlsx", SAMPLE_SINGLE)
    write_workbook(HERE / "sample_broken.xlsx", SAMPLE_BROKEN)
    write_workbook(HERE / "Quizizz_DataComs_Mod14.xlsx", SMALL_TWO)
    write_workbook(HERE / "DataComs_Quiz2_pt1.xlsx", SMALL_TWO)
    write_workbook(HERE / "DataComs_Quiz2_pt2.xlsx", SMALL_TWO)


if __name__ == "__main__":
    main()
