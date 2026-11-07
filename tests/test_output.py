"""Output formatting: the stdout block, JSON schema, summary, join-link validation."""

from __future__ import annotations

import json

from wqu.output import (
    NO_JOIN_LINK_MARKER,
    FileResult,
    format_json,
    format_links,
    format_summary,
    validate_join_link,
)

LINK = "https://wayground.com/join?gc=24923411&source=liveDashboard"


def _ok(title: str = "DataComs_Mod14", **kwargs) -> FileResult:
    defaults = dict(file="f.xlsx", title=title, quiz_url="https://wayground.com/activity/admin/quiz/abc",
                    join_link=LINK, assigned=True)
    defaults.update(kwargs)
    return FileResult(**defaults)


def test_line_with_join_link() -> None:
    assert _ok().line() == f"DataComs_Mod14: {LINK}"


def test_line_without_join_link_has_marker() -> None:
    result = _ok(join_link=None, assigned=False)
    assert result.line().endswith(NO_JOIN_LINK_MARKER)
    assert result.line().startswith("DataComs_Mod14: https://wayground.com/activity/admin/quiz/abc")


def test_line_skipped() -> None:
    assert FileResult(file="f", title="T", skipped=True).line() == "T: (skipped)"


def test_format_links_excludes_errors_and_skipped() -> None:
    results = [
        _ok("A"),
        FileResult(file="b", title="B", error="boom"),
        FileResult(file="c", title="C", skipped=True),
        _ok("D"),
    ]
    assert format_links(results) == f"A: {LINK}\nD: {LINK}"


def test_json_schema_is_exact() -> None:
    payload = json.loads(format_json([_ok()]))
    assert isinstance(payload, list) and len(payload) == 1
    assert set(payload[0].keys()) == {"file", "title", "quiz_url", "join_link", "assigned", "error"}
    assert payload[0]["join_link"] == LINK
    assert payload[0]["assigned"] is True
    assert payload[0]["error"] is None


def test_json_join_link_null_for_failure() -> None:
    payload = json.loads(format_json([FileResult(file="b", title="B", error="boom")]))
    assert payload[0]["join_link"] is None
    assert payload[0]["error"] == "boom"


def test_summary_counts_and_lines() -> None:
    results = [
        _ok("A"),
        _ok("B", join_link=None, assigned=False, quiz_url="https://wayground.com/activity/admin/quiz/xyz"),
        FileResult(file="c", title="C", error="Import preview errors (rows 12, 34)",
                   screenshot="wqu-logs/20261108T000000-c.png"),
        FileResult(file="d", title="D", skipped=True),
    ]
    summary = format_summary(results)
    assert summary.startswith("Processed 4 files: 2 success, 1 failure, 1 skipped")
    assert "  OK   A  " in summary
    assert "  OK   B  https://wayground.com/activity/admin/quiz/xyz" in summary
    assert "  FAIL C  Import preview errors (rows 12, 34) — screenshot wqu-logs/" in summary
    assert "  SKIP D" in summary


def test_validate_join_link_accepts_real_shape() -> None:
    assert validate_join_link(LINK) == LINK
    assert validate_join_link("https://wayground.com/join?gc=02211091&source=liveDashboard") is not None
    assert validate_join_link("https://wayground.com/join?gc=123") is not None


def test_validate_join_link_rejects_junk() -> None:
    assert validate_join_link(None) is None
    assert validate_join_link("") is None
    assert validate_join_link("https://wayground.com/activity/admin/quiz/abc") is None
    assert validate_join_link("https://evil.example.com/join?gc=123") is None
    assert validate_join_link("https://wayground.com/join?gc=abc") is None
