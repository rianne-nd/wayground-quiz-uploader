"""Library manager pure logic + CLI wiring (no browser)."""

from __future__ import annotations

from argparse import Namespace

from wqu.library import (
    LibraryItem,
    LibraryScan,
    is_test_artifact,
    needs_archiving,
    oldest_ids,
    parse_age_minutes,
    select_by_ids,
    select_for_archive,
    select_test_artifacts,
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


# --- wqu archive --test filter -------------------------------------------------


def test_is_test_artifact_matches_dev_titles() -> None:
    # the two cases the user named, plus the other markers
    assert is_test_artifact("sample_single")
    assert is_test_artifact("WQU_V012_TEST")
    assert is_test_artifact("WQU_V013_TEST")
    assert is_test_artifact("WQU_TEST_DELETE_ME")
    assert is_test_artifact("delete_me (copy)")
    assert is_test_artifact("Sample Single 2")  # case-insensitive


def test_is_test_artifact_keeps_real_quizzes() -> None:
    assert not is_test_artifact("DataComs_Mod14")
    assert not is_test_artifact("DataComs_Quiz2_Pt1")
    assert not is_test_artifact("AltOS_FinalsLT1_pt3")
    assert not is_test_artifact("")


def test_select_test_artifacts_filters_scan_items() -> None:
    items = (
        LibraryItem("id1", "sample_single", "2 hours ago", 120, 1),
        LibraryItem("id2", "DataComs_Mod14", "3 hours ago", 180, 2),
        LibraryItem("id3", "WQU_V012_TEST", "1 day ago", 1440, 3),
        LibraryItem("id4", "AltOS_FinalsLT1_pt1", "2 days ago", 2880, 4),
    )
    picked = select_test_artifacts(items)
    assert [item.quiz_id for item in picked] == ["id1", "id3"]


def test_select_by_ids_exact_prefix_and_missing() -> None:
    items = (
        LibraryItem("6aef80c1b5860fbbdcffa5ff", "A", "", None, 1),
        LibraryItem("6af01babaf64e5b6c4061644", "B", "", None, 2),
    )
    exact, missing = select_by_ids(items, ["6aef80c1b5860fbbdcffa5ff"])
    assert [i.title for i in exact] == ["A"] and missing == []

    prefixed, missing = select_by_ids(items, ["6af01bab", "deadbeef"])
    assert [i.title for i in prefixed] == ["B"]
    assert missing == ["deadbeef"]


def test_library_payload_json_shape() -> None:
    import json

    from wqu.cli import library_payload

    scan = _scan()
    payload = library_payload(scan)
    assert payload["total"] == 20 and payload["limit"] == 20
    first = payload["quizzes"][0]
    assert set(first) == {"index", "title", "age", "age_minutes", "quiz_id"}
    assert json.loads(json.dumps(payload))["quizzes"][1]["quiz_id"] == "id2"


# --- wqu archive CLI validation (no browser) -----------------------------------


def _archive_args(**overrides):  # noqa: ANN003, ANN202
    from argparse import Namespace

    base = dict(all=False, test=False, ids=None, interactive=False,
                logs_dir="wqu-logs", no_headless=False)
    base.update(overrides)
    return Namespace(**base)


def test_cmd_archive_rejects_multiple_modes() -> None:
    from wqu.cli import cmd_archive

    assert cmd_archive(_archive_args(all=True, test=True)) == 2
    assert cmd_archive(_archive_args(test=True, ids=["x"])) == 2


def test_cmd_archive_requires_flags_when_not_interactive(monkeypatch) -> None:  # noqa: ANN001
    from wqu import cli

    monkeypatch.setattr(cli, "_is_tty", lambda: False)
    assert cli.cmd_archive(_archive_args()) == 2  # no flags + no TTY -> guidance, exit 2


def test_ask_archive_selection_is_eof_safe(monkeypatch) -> None:  # noqa: ANN001
    from wqu import cli

    def raise_eof(*args, **kwargs):  # noqa: ANN002, ANN003
        raise EOFError

    monkeypatch.setattr(cli.Prompt, "ask", raise_eof)
    assert cli._ask_archive_selection(cli.err_console(), _scan()) == ()


# --- expired-session guards (the v0.1.5 commands use is_authenticated) --------


def _fake_session_cls(authenticated: bool):  # noqa: ANN202
    from contextlib import contextmanager
    from types import SimpleNamespace

    class FakeSession:
        state_path = "fake/state.json"

        def __init__(self, **kwargs):  # noqa: ANN003
            pass

        def exists(self) -> bool:
            return True

        @contextmanager
        def open(self, **kwargs):  # noqa: ANN003, ANN202
            yield SimpleNamespace(page=object())

        def is_authenticated(self, page, *, timeout_ms=15_000, on_progress=None):  # noqa: ANN001
            return authenticated

    return FakeSession


def test_cmd_list_exits_two_when_session_expired(monkeypatch, capsys) -> None:  # noqa: ANN001
    from wqu import cli

    monkeypatch.setattr(cli, "Session", _fake_session_cls(authenticated=False))
    args = _archive_args(json=False)  # same shape: json/no_headless/logs_dir
    assert cli.cmd_list(args) == 2
    assert "wqu login" in capsys.readouterr().err


def test_cmd_archive_exits_two_when_session_expired(monkeypatch, capsys) -> None:  # noqa: ANN001
    from wqu import cli

    monkeypatch.setattr(cli, "Session", _fake_session_cls(authenticated=False))
    assert cli.cmd_archive(_archive_args(test=True)) == 2
    assert "wqu login" in capsys.readouterr().err
