"""Filename semantics: Quizizz_ stripping, split labels, natural ordering."""

from __future__ import annotations

from wqu.output import natural_sort_key
from wqu.upload import title_from_filename


def test_strips_quizizz_prefix() -> None:
    title, label, is_split, part = title_from_filename("Quizizz_DataComs_Mod14.xlsx")
    assert title == "DataComs_Mod14"
    assert label == title
    assert is_split is False
    assert part is None


def test_no_prefix_is_kept_as_is() -> None:
    assert title_from_filename("DataComs_Mod14.xlsx")[0] == "DataComs_Mod14"


def test_prefix_case_insensitive() -> None:
    assert title_from_filename("QUIZIZZ_Foo.xlsx")[0] == "Foo"


def test_split_part_label_normalization() -> None:
    title, label, is_split, part = title_from_filename("DataComs_Quiz2_pt1.xlsx")
    assert (title, label, is_split, part) == ("DataComs_Quiz2_Pt1", "DataComs_Quiz2_Pt1", True, 1)


def test_split_part_uppercase_and_double_digit() -> None:
    title, _, is_split, part = title_from_filename("DATACOMS_QUIZ2_PT10.XLSX")
    assert title == "DATACOMS_QUIZ2_Pt10"
    assert is_split is True
    assert part == 10


def test_split_with_quizizz_prefix() -> None:
    assert title_from_filename("Quizizz_DataComs_Quiz2_pt2.xlsx")[0] == "DataComs_Quiz2_Pt2"


def test_natural_sort_split_parts() -> None:
    files = [
        "DataComs_Quiz2_pt10.xlsx",
        "DataComs_Quiz2_pt2.xlsx",
        "DataComs_Quiz2_pt1.xlsx",
        "DataComs_Quiz2.xlsx",
    ]
    ordered = sorted(files, key=natural_sort_key)
    assert ordered == [
        "DataComs_Quiz2.xlsx",
        "DataComs_Quiz2_pt1.xlsx",
        "DataComs_Quiz2_pt2.xlsx",
        "DataComs_Quiz2_pt10.xlsx",
    ]


def test_natural_sort_module_numbers() -> None:
    files = ["Mod10.xlsx", "Mod2.xlsx", "Mod1.xlsx"]
    assert sorted(files, key=natural_sort_key) == ["Mod1.xlsx", "Mod2.xlsx", "Mod10.xlsx"]


def test_natural_sort_accepts_paths() -> None:
    from pathlib import Path

    files = [Path("d/Quiz2_pt2.xlsx"), Path("d/Quiz2_pt1.xlsx")]
    assert sorted(files, key=natural_sort_key) == [Path("d/Quiz2_pt1.xlsx"), Path("d/Quiz2_pt2.xlsx")]
