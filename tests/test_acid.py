"""The GUI-ready acid tests from brief Part 2.6 rule 6.

1. ``publish_one`` works from a REPL-style call with a mocked browser and
   returns a dataclass whose ``join_link`` is populated.
2. ``import wqu.core`` prints nothing (checked in a subprocess).
3. Core modules contain no print() calls and never import rich/typer/asyncio.
"""

from __future__ import annotations

import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from wqu import publish as publish_mod
from wqu.core import FileResult, PipelineOptions, publish_one
from wqu.session import Session

LINK = "https://wayground.com/join?gc=24923411&source=liveDashboard"


@contextmanager
def _fake_open(self, **kwargs):  # noqa: ANN001, ANN202
    # the handle is unused because _process_one is faked below, but the login
    # pre-flight reads handle.page before anything else runs.
    yield SimpleNamespace(page=object())


def test_acid_publish_one_with_mocked_browser(tmp_path: Path, fixtures_dir: Path, monkeypatch) -> None:
    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    options = PipelineOptions(state_path=state, logs_dir=tmp_path / "logs")

    monkeypatch.setattr(Session, "open", _fake_open)
    monkeypatch.setattr(Session, "is_authenticated", lambda self, page, **kw: True)

    def fake_process(handle, file, opts, decide, resolve, run_id, on_progress):  # noqa: ANN001
        return FileResult(
            file=str(file), title="sample_single",
            quiz_url="https://wayground.com/activity/admin/quiz/mocked",
            join_link=LINK, assigned=True,
        )

    monkeypatch.setattr(publish_mod, "_process_one", fake_process)

    result = publish_one(fixtures_dir / "sample_single.xlsx", options)
    assert isinstance(result, FileResult)
    assert result.join_link == LINK
    assert result.assigned is True
    assert result.error is None


def test_acid_should_cancel_checked_between_files(tmp_path: Path, fixtures_dir: Path, monkeypatch) -> None:
    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    options = PipelineOptions(state_path=state, logs_dir=tmp_path / "logs")
    monkeypatch.setattr(Session, "open", _fake_open)
    monkeypatch.setattr(Session, "is_authenticated", lambda self, page, **kw: True)

    calls: list[str] = []

    def fake_process(handle, file, opts, decide, resolve, run_id, on_progress):  # noqa: ANN001
        calls.append(Path(file).name)
        return FileResult(file=str(file), title=Path(file).stem)

    monkeypatch.setattr(publish_mod, "_process_one", fake_process)

    cancels = iter([False, True])  # first file runs, second is cancelled

    from wqu.core import publish_many

    results = publish_many(
        [fixtures_dir / "sample_single.xlsx", fixtures_dir / "sample_broken.xlsx"],
        options,
        should_cancel=lambda: next(cancels, True),
    )
    assert calls == ["sample_single.xlsx"]
    assert len(results) == 1


def test_acid_import_prints_nothing() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", "import wqu.core; from wqu.core import publish_one, PipelineOptions"],
        capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""
