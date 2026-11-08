"""Library manager pure logic + CLI wiring (no browser)."""

from __future__ import annotations

from argparse import Namespace

from wqu.library import (
    LibraryItem,
    LibraryScan,
    needs_archiving,
    oldest_ids,
    parse_age_minutes,
    select_for_archive,
)


def _item(i: int, age: int | None) -> LibraryItem:
    return LibraryItem(quiz_id=f"id{i}", title=f"Quiz {i}",
                       age_text=f"{age} minutes ago" if age is not None else "",
                       age_minutes=age, row_index=i)


def test_parse_age_minutes_units() -> None:
    assert parse_age_minutes("47 minutes ago") == 47
    assert parse_age_minutes("1 hour ago") == 60
    assert parse_age_minutes("3 days ago") == 3 * 1440
    assert parse_age_minutes("2 weeks ago") == 2 * 10080
    assert parse_age_minutes("5 months ago") == 5 * 43200
    assert parse_age_minutes("1 year ago") == 525600


def test_parse_age_minutes_edge_cases() -> None:
    assert parse_age_minutes("just now") == 0
    assert parse_age_minutes("yesterday") == 1440
    assert parse_age_minutes("47 minutes\xa0ago") == 47  # nbsp between number and unit
    assert parse_age_minutes("") is None
    assert parse_age_minutes(None) is None
    assert parse_age_minutes("ages ago, unspecified") is None


def test_needs_archiving() -> None:
    assert needs_archiving(11, 1) == 0
    assert needs_archiving(19, 2) == 1
    assert needs_archiving(20, 5) == 5
    assert needs_archiving(20, 5, limit=50) == 0


def test_oldest_ids_prefers_known_ages_then_tail_order() -> None:
    items = [_item(1, 47), _item(2, 60), _item(3, None), _item(4, 60 * 24), _item(5, None)]
    assert oldest_ids(items, 2) == ("id4", "id2")      # 1 day, then 1 hour
    assert oldest_ids(items, 3) == ("id4", "id2", "id1")
    assert oldest_ids(items, 5) == ("id4", "id2", "id1", "id5", "id3")  # unknowns: tail order
    assert oldest_ids(items, 0) == ()


def _scan() -> LibraryScan:
    return LibraryScan(total=20, limit=20,
                       items=(_item(1, 10), _item(2, 999), _item(3, 500)),
                       counter_text="20 / 20")


def test_select_for_archive_specs() -> None:
    scan = _scan()
    assert select_for_archive("", scan, 1) == ()
    assert select_for_archive("a", scan, 1) == ("id1", "id2", "id3")
    assert select_for_archive("o2", scan, 1) == ("id2", "id3")
    assert select_for_archive("oldest 1", scan, 1) == ("id2",)
    assert select_for_archive("1,3", scan, 1) == ("id1", "id3")
    assert select_for_archive("#1 #3", scan, 1) == ("id1", "id3")
    assert select_for_archive("99", scan, 1) == ()


def test_make_choose_archive_non_interactive(monkeypatch) -> None:  # noqa: ANN001
    from wqu import cli

    args = Namespace(auto_archive_oldest=None, quiet=False)
    monkeypatch.setattr(cli, "_is_tty", lambda: False)
    assert cli.make_choose_archive(args) is None

    args_auto = Namespace(auto_archive_oldest=3, quiet=False)
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    assert cli.make_choose_archive(args_auto) is None  # handled by the pipeline

    args_quiet = Namespace(auto_archive_oldest=None, quiet=True)
    assert cli.make_choose_archive(args_quiet) is None


def test_auto_archive_flag_parses() -> None:
    from wqu.cli import build_parser

    args = build_parser().parse_args(["publish", "x.xlsx", "--auto-archive-oldest", "3"])
    assert args.auto_archive_oldest == 3
    args2 = build_parser().parse_args(["publish", "x.xlsx"])
    assert args2.auto_archive_oldest is None
