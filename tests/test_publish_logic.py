"""Assignment-decision logic (public vs class vs keep-draft) and CLI finish."""

from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from wqu import publish as P
from wqu.output import FileResult
from wqu.publish import PipelineOptions
from wqu.upload import QuizData

LINK = "https://wayground.com/join?gc=24923411&source=liveDashboard"
QUIZ_URL = "https://wayground.com/activity/admin/quiz/quiz123"


@pytest.fixture()
def mocked_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Patch everything around _process_one except the decision under test."""
    data = QuizData(
        path=tmp_path / "f.xlsx", title="T", label="T", is_split=False,
        part=None, questions=(), issues=(),
    )
    calls: dict = {}

    monkeypatch.setattr(P, "load_xlsx", lambda file, title_override=None: data)
    monkeypatch.setattr(P, "find_by_title", lambda state_path, title: [])
    monkeypatch.setattr(P, "record_created", lambda *a, **k: None)
    monkeypatch.setattr(P, "create_quiz", lambda page, on_progress=None: "quiz123")
    monkeypatch.setattr(P, "upload_xlsx", lambda *a, **k: None)
    monkeypatch.setattr(P, "publish_quiz", lambda *a, **k: QUIZ_URL)

    def fake_assign(page, *, class_name, assignment_title, no_deadline=True, on_progress=None):  # noqa: ANN001
        calls["class_name"] = class_name
        calls["no_deadline"] = no_deadline
        return SimpleNamespace(class_name=class_name, public=class_name is None)

    monkeypatch.setattr(P, "assign_one", fake_assign)
    monkeypatch.setattr(P, "extract_join_link", lambda page, on_progress=None: LINK)
    monkeypatch.setattr(P, "_scan_dom_for_join", lambda page: None)
    calls["handle"] = SimpleNamespace(page=SimpleNamespace(url=QUIZ_URL))
    calls["options"] = PipelineOptions(state_path=tmp_path / "state.json", logs_dir=tmp_path / "logs")
    calls["file"] = tmp_path / "f.xlsx"
    return calls


def _run(mocked: dict, decide=None, options: PipelineOptions | None = None) -> FileResult:
    return P._process_one(
        mocked["handle"], mocked["file"], options or mocked["options"],
        decide, None, "runid", None,
    )


def test_decision_none_assigns_publicly(mocked_pipeline: dict) -> None:
    result = _run(mocked_pipeline, decide=lambda view: None)
    assert mocked_pipeline["class_name"] is None  # public flow
    assert result.assigned is True
    assert result.join_link == LINK
    assert result.error is None


def test_decision_class_uses_class_flow(mocked_pipeline: dict) -> None:
    result = _run(mocked_pipeline, decide=lambda view: "Naic")
    assert mocked_pipeline["class_name"] == "Naic"
    assert mocked_pipeline["no_deadline"] is True
    assert result.assigned is True and result.join_link == LINK


def test_assign_class_option_beats_callback(mocked_pipeline: dict) -> None:
    options = mocked_pipeline["options"]
    options = PipelineOptions(state_path=options.state_path, logs_dir=options.logs_dir,
                              assign_class="OtherClass")
    called = {"n": 0}

    def decide(view):  # noqa: ANN001
        called["n"] += 1
        return "Naic"

    result = _run(mocked_pipeline, decide=decide, options=options)
    assert called["n"] == 0  # --assign bypasses the callback entirely
    assert mocked_pipeline["class_name"] == "OtherClass"
    assert result.assigned is True


def test_keep_draft_skips_assignment_entirely(mocked_pipeline: dict) -> None:
    options = mocked_pipeline["options"]
    options = PipelineOptions(state_path=options.state_path, logs_dir=options.logs_dir,
                              keep_draft=True)
    result = _run(mocked_pipeline, decide=None, options=options)
    assert "class_name" not in mocked_pipeline  # assign_one never called
    assert result.assigned is False
    assert result.join_link is None  # DOM scan found nothing → marker path
    assert result.error is None
    assert result.line().endswith("(NO JOIN LINK — published but not assigned)")


def test_public_mode_never_sets_no_deadline(mocked_pipeline: dict) -> None:
    _run(mocked_pipeline, decide=lambda view: None)
    assert mocked_pipeline["no_deadline"] is False  # platform default 1-day deadline


# --- CLI finish: panel + clipboard ordering ---------------------------------


def test_finish_publish_panel_and_clipboard_first(capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    from wqu import cli

    copied: dict = {}
    monkeypatch.setattr(cli, "copy_to_clipboard",
                        lambda text: copied.setdefault("text", text) or True)
    results = [FileResult(file="a.xlsx", title="A", quiz_url=QUIZ_URL, join_link=LINK, assigned=True)]
    rc = cli.finish_publish(Namespace(json=False, quiet=False), results)
    captured = capsys.readouterr()
    assert rc == 0
    assert copied["text"] == f"A: {LINK}"          # clipboard got the block
    assert "wqu results" in captured.out           # the panel is on stdout
    assert f"A: {LINK}" in captured.out
    assert "Copied the result block to the clipboard." in captured.err
    assert "Processed 1 files: 1 success, 0 failure" in captured.err
    assert "OK" in captured.err                    # summary table row on stderr


def test_finish_publish_failure_counts_exit_one(capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    from wqu import cli

    monkeypatch.setattr(cli, "copy_to_clipboard", lambda text: True)
    results = [FileResult(file="a.xlsx", title="A", error="boom")]
    rc = cli.finish_publish(Namespace(json=False, quiet=False), results)
    captured = capsys.readouterr()
    assert rc == 1
    assert "boom" in captured.out                  # red failure line inside the panel
    assert "boom" in captured.err                  # and in the summary table
    assert "Processed 1 files: 0 success, 1 failure" in captured.err


def test_finish_publish_quiet_is_plain(capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    from wqu import cli

    monkeypatch.setattr(cli, "copy_to_clipboard", lambda text: True)
    results = [FileResult(file="a.xlsx", title="A", quiz_url=QUIZ_URL, join_link=LINK, assigned=True)]
    rc = cli.finish_publish(Namespace(json=False, quiet=True), results)
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == f"A: {LINK}\n"          # no panel, no decoration
    assert "wqu results" not in captured.out


# --- clipboard ---------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="clip.exe is Windows-only")
def test_copy_to_clipboard_uses_clip_exe(monkeypatch: pytest.MonkeyPatch) -> None:
    from wqu import output as O

    recorded: dict = {}

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        recorded["cmd"] = cmd
        recorded["input"] = kwargs.get("input")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(O.subprocess, "run", fake_run)
    assert O.copy_to_clipboard("hello") is True
    assert recorded["cmd"] == ["clip"]
    assert recorded["input"] == "hello".encode("utf-16-le")  # unicode-safe first attempt
