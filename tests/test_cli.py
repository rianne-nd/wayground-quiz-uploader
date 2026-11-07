"""CLI behavior without a browser: dry-run, flag validation, exit codes."""

from __future__ import annotations

from pathlib import Path

from wqu.cli import main


def test_dry_run_valid_file_exits_zero(fixtures_dir: Path, tmp_path: Path, capsys) -> None:  # noqa: ANN001
    rc = main([
        "publish", str(fixtures_dir / "sample_single.xlsx"), "--dry-run",
        "--logs-dir", str(tmp_path / "logs"),
    ])
    captured = capsys.readouterr()
    assert rc == 0
    assert "sample_single" in captured.out
    assert "look good" in captured.err


def test_dry_run_broken_file_exits_one(fixtures_dir: Path, tmp_path: Path, capsys) -> None:  # noqa: ANN001
    rc = main([
        "publish", str(fixtures_dir / "sample_broken.xlsx"), "--dry-run",
        "--logs-dir", str(tmp_path / "logs"),
    ])
    captured = capsys.readouterr()
    assert rc == 1
    assert "ERROR" in captured.out or "ERROR" in captured.err


def test_dry_run_never_touches_a_session(fixtures_dir: Path, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # no ~/.wayground/state.json here
    rc = main([
        "publish", str(fixtures_dir / "sample_single.xlsx"), "--dry-run",
        "--logs-dir", str(tmp_path / "logs"),
    ])
    assert rc == 0


def test_title_with_multiple_files_is_rejected(fixtures_dir: Path, tmp_path: Path) -> None:
    rc = main([
        "publish",
        str(fixtures_dir / "DataComs_Quiz2_pt1.xlsx"),
        str(fixtures_dir / "DataComs_Quiz2_pt2.xlsx"),
        "--title", "One", "--dry-run", "--logs-dir", str(tmp_path / "logs"),
    ])
    assert rc == 2


def test_missing_path_is_rejected(tmp_path: Path) -> None:
    rc = main(["publish", str(tmp_path / "nope.xlsx"), "--logs-dir", str(tmp_path / "logs")])
    assert rc == 2


def test_non_xlsx_file_is_rejected(tmp_path: Path) -> None:
    bogus = tmp_path / "notes.txt"
    bogus.write_text("hi", encoding="utf-8")
    rc = main(["publish", str(bogus), "--logs-dir", str(tmp_path / "logs")])
    assert rc == 2


def test_folder_input_sorted_naturally(fixtures_dir: Path, tmp_path: Path, capsys) -> None:  # noqa: ANN001
    # copy the two split parts into a clean folder and dry-run the FOLDER
    folder = tmp_path / "quizfolder"
    folder.mkdir()
    for name in ("DataComs_Quiz2_pt1.xlsx", "DataComs_Quiz2_pt2.xlsx"):
        (folder / name).write_bytes((fixtures_dir / name).read_bytes())
    rc = main(["publish", str(folder), "--dry-run", "--logs-dir", str(tmp_path / "logs")])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.index("DataComs_Quiz2_pt1") < out.index("DataComs_Quiz2_pt2")


def test_publish_without_session_exits_two(fixtures_dir: Path, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    rc = main([
        "publish", str(fixtures_dir / "sample_single.xlsx"),
        "--logs-dir", str(tmp_path / "logs"),
    ])
    assert rc == 2  # no ~/.wayground/state.json → "run wqu login first"


def test_help_and_version() -> None:
    import pytest

    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert main([]) == 2
