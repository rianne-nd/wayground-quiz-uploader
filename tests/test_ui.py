"""Live step-tree logic: catalog matching, states, rendering (no terminal)."""

from __future__ import annotations

from wqu.ui import STEP_CATALOG, FileTree, bar, classify, fmt_duration


def _tree() -> FileTree:
    tree = FileTree(1, 1, "sample.xlsx")
    tree.begin()
    return tree


def _states(tree: FileTree) -> dict[str, str]:
    return {step.label: step.state for step in tree.steps}


def test_begin_marks_validation_done_and_activates_navigation() -> None:
    tree = _tree()
    # Local validation already passed before the pipeline starts this file.
    assert tree.steps[0].state == "done"
    assert "Validating workbook" in _states(tree)
    assert tree.steps[1].state == "active"  # next expected step spins
    assert tree.pointer == 1


def test_messages_match_catalog_in_order() -> None:
    tree = _tree()
    tree.accept("Navigating to the assessment page...")
    tree.accept("Creating a new quiz (Start from scratch)...")
    tree.accept("Detected the accommodations page; clicking the back arrow...")  # skips ahead
    states = _states(tree)
    assert states["Validating workbook"] == "done"
    assert states["Navigating to the assessment page"] == "done"
    assert states["Creating new quiz (Start from scratch)"] == "done"
    # slots that were jumped over become skipped, never done
    assert states["Dismissing premium popup"] == "skipped"
    assert states["Editor ready"] == "skipped"
    assert states["Recovering accommodations glitch"] == "done"


def test_unmatched_message_becomes_detail() -> None:
    tree = _tree()
    tree.accept("Workbook attached: sample.xlsx")
    tree.accept("WARNING: --force-import set; clicking Import despite errors (rows 3)")
    active = tree.steps[tree.pointer]
    assert active.detail and "force-import" in active.detail


def test_finish_success_marks_done_and_skips_the_rest() -> None:
    tree = _tree()
    tree.accept("Join link captured from the clipboard: https://wayground.com/join?gc=1")
    tree.finish(None)
    states = _states(tree)
    assert states["Join link captured"] == "done"
    assert states["Done!"] == "done"
    assert tree.failed is False
    assert all(s in ("done", "skipped") for s in states.values())


def test_finish_error_marks_active_step_failed() -> None:
    tree = _tree()
    tree.accept("Publishing the quiz...")
    tree.finish("the app did not finish publishing within 60s")
    states = _states(tree)
    assert states["Publishing quiz"] == "done"
    failed = [s for s in tree.steps if s.state == "failed"]
    assert len(failed) == 1
    assert "60s" in (failed[0].error or "")
    assert tree.failed is True


def test_render_smoke_contains_key_pieces() -> None:
    tree = _tree()
    tree.accept("Creating a new quiz (Start from scratch)...")
    panel = tree.render(console_width=100)
    text = err_console_render(panel)
    assert "[1/1] sample.xlsx" in text
    assert "Steps:" in text
    assert "✓" in text
    assert "Creating new quiz" in text


def test_finished_tree_renders_every_step_and_timing() -> None:
    tree = _tree()
    # Resolve enough steps to trigger the running window's history trimming.
    for step in tree.steps[1:20]:
        step.state = "done"
        step.started = step.ended = 0.0

    running = err_console_render(tree.render(console_width=100))
    assert "earlier steps" in running

    tree.finish(None)
    final = err_console_render(tree.render(console_width=100))
    assert "earlier steps" not in final
    assert "Validating workbook" in final
    assert "No deadline confirmed" in final
    assert "Join link captured" in final
    assert "Done!" in final
    assert final.count("├─") == len(tree.steps) - 1
    assert "  └─ ✓ Done!" in final


def err_console_render(renderable) -> str:  # noqa: ANN001
    from io import StringIO

    from rich.console import Console

    buffer = StringIO()
    Console(file=buffer, width=100, legacy_windows=False).print(renderable)
    return buffer.getvalue()


def test_classify_colours() -> None:
    assert classify("Join link captured from the clipboard") == "green"
    assert classify("WARNING: could not select rows") == "yellow"
    assert classify("the publish modal rejected the form: error") == "red"
    assert classify("Choosing Subject: Information Technology (IT)") == "cyan"


def test_bar_and_duration_format() -> None:
    assert bar(1.0, width=4).plain == "████"
    assert bar(0.0, width=4).plain == "░░░░"
    assert fmt_duration(0.1, running=False) == "[00.1s]"
    assert fmt_duration(3.2, running=True) == "[ 3.2s...]"


def test_catalog_has_unique_labels_and_patterns() -> None:
    labels = [label for label, _ in STEP_CATALOG]
    assert len(labels) == len(set(labels))
    assert labels[0] == "Validating workbook" and labels[-1] == "Done!"


def test_live_tree_ui_plain_mode_lines(capsys) -> None:  # noqa: ANN001
    from wqu.ui import LiveTreeUI

    ui = LiveTreeUI()
    ui._interactive = False  # force plain mode regardless of the test runner
    ui.on_progress("Checking the library (free-tier cap)...")
    ui.on_progress("[1/1] sample.xlsx")
    ui.on_progress("Preview looks good.")
    ui.on_file_result(type("R", (), {
        "error": None, "skipped": False, "title": "sample_single",
        "join_link": "https://wayground.com/join?gc=1", "quiz_url": None,
    })())
    captured = capsys.readouterr()
    assert "Checking the library" in captured.err
    assert "[1/1] sample.xlsx" in captured.err
    assert "OK" in captured.err and "sample_single" in captured.err
    assert len(ui.file_times) == 1


def test_live_tree_ui_prints_preamble_while_live() -> None:
    """Regression: rich 14 renders a callable renderable as-is.

    ``Live(self._render)`` made ``console.print(...)`` fail with
    NotRenderableError when a preamble message arrived after the live region
    had started (e.g. the library pre-check in ``wqu publish``).
    """
    from io import StringIO

    from rich.console import Console

    from wqu.ui import LiveTreeUI

    buffer = StringIO()
    ui = LiveTreeUI()
    ui.console = Console(file=buffer, force_terminal=True, width=100, legacy_windows=False)
    ui._interactive = True
    ui.start(1)
    try:
        ui.on_progress("Checking the library (free-tier cap)...")
        ui.on_progress("[1/1] sample.xlsx")
        ui.on_progress("Preview looks good.")
    finally:
        ui.stop()
    assert "Checking the library" in buffer.getvalue()


def test_live_tree_ui_freezes_one_panel_per_file() -> None:
    from io import StringIO

    from rich.console import Console

    from wqu.ui import LiveTreeUI

    class Result:
        error = None
        skipped = False
        join_link = "https://wayground.com/join?gc=1"
        quiz_url = None

        def __init__(self, title: str) -> None:
            self.title = title

    buffer = StringIO()
    ui = LiveTreeUI()
    ui.console = Console(file=buffer, force_terminal=True, width=110, legacy_windows=False)
    ui._interactive = True
    ui.start(2)
    try:
        ui.on_progress("[1/2] file1.xlsx")
        ui.on_progress("Preview looks good.")
        ui.on_file_result(Result("file1"))
        assert "[1/2] file1.xlsx" in buffer.getvalue()

        ui.on_progress("[2/2] file2.xlsx")
        ui.on_progress("Preview looks good.")
        ui.on_file_result(Result("file2"))
    finally:
        ui.stop()

    output = buffer.getvalue()
    assert "[1/2] file1.xlsx" in output
    assert "[2/2] file2.xlsx" in output
    assert "earlier steps" not in output


def test_prompt_line_pauses_and_resumes_live(monkeypatch) -> None:  # noqa: ANN001
    from wqu import cli

    class FakeUI:
        def __init__(self):  # noqa: ANN204
            self.calls: list[str] = []

        def pause(self) -> None:
            self.calls.append("pause")

        def resume(self) -> None:
            self.calls.append("resume")

    ui = FakeUI()
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "r")
    assert cli._prompt_line("[r]esume / [s]kip / [n]ew? [r]: ", ui) == "r"
    assert ui.calls == ["pause", "resume"]
