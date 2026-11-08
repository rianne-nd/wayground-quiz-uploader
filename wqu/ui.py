"""Terminal presentation for the CLI: live step tree, bars, banners, colours.

This module is part of the **CLI presentation layer** — it is imported only by
``wqu/cli.py`` and never by core modules (enforced by tests). It hosts the
rich-based ``LiveTreeUI`` that renders the per-file step tree live.

Design (v0.1.3, from the user's spec):
  * one Live region on stderr re-rendering continuously;
  * an overall bar (files) plus a per-file panel titled ``[i/n] name``;
  * a step catalog (~30 slots) matched against ``on_progress`` messages;
  * ✓ green done, ⠸ cyan active spinner, - dim pending, – skipped, ✗ red error;
  * prompts MUST call ``pause()`` / ``resume()`` around input();
  * falling back to plain lines when stderr is not a TTY (pipes, tests).
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

# ---------------------------------------------------------------------------
# colours + small formatting helpers (used by cli.py too)
# ---------------------------------------------------------------------------

_STYLE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(error|failed|failure|FAIL)\b", re.I), "red"),
    (re.compile(r"\b(warning|skipped|skipping|could not|no join link|still)\b", re.I), "yellow"),
    (
        re.compile(
            r"\b(published|looks good|ready|unchecked|captured|already|archived|"
            r"room for|now at|is now ON|OK)\b",
            re.I,
        ),
        "green",
    ),
)


def classify(message: str) -> str:
    """Pick a colour for a progress message (cyan = neutral info)."""
    for pattern, style in _STYLE_RULES:
        if pattern.search(message):
            return style
    return "cyan"


def styled(message: str) -> Text:
    return Text.from_markup(f"[{classify(message)}]{escape(message)}[/]")


def err_console() -> Console:
    """Fresh stderr console per call (so tests/capsys capture correctly).

    legacy_windows=False forces the nice unicode box glyphs; main() already
    reconfigures the streams to UTF-8 on Windows consoles.
    """
    return Console(file=sys.stderr, soft_wrap=True, highlight=False, legacy_windows=False)


def out_console() -> Console:
    return Console(file=sys.stdout, soft_wrap=True, highlight=False, legacy_windows=False)


def banner_text(subtitle: str = "") -> Panel:
    from wqu import __version__

    line = Text.assemble(
        ("wqu ", "bold white"),
        (f"v{__version__}", "bold cyan"),
        ("  •  Wayground Quiz Uploader", "white"),
    )
    if subtitle:
        line.append(f"  •  {subtitle}", style="dim")
    return Panel(line, border_style="blue", padding=(0, 2))


def print_banner(subtitle: str = "") -> None:
    console = err_console()
    console.print(banner_text(subtitle))
    console.print("")


def bar(fraction: float, width: int = 24) -> Text:
    """`████░░░░` progress bar as a Text with green/dim spans."""
    filled = max(0, min(width, round(fraction * width)))
    return Text.assemble(("█" * filled, "green"), ("░" * (width - filled), "dim"))


def fmt_duration(seconds: float, *, running: bool) -> str:
    if running:
        return f"[{seconds:>4.1f}s...]"
    return f"[{seconds:04.1f}s]"


_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


# ---------------------------------------------------------------------------
# step catalog: (label, message pattern | None for automatic slots)
# ---------------------------------------------------------------------------

STEP_CATALOG: tuple[tuple[str, str | None], ...] = (
    ("Validating workbook", None),  # auto: becomes active when the file starts
    ("Navigating to the assessment page", r"Navigating to the assessment page"),
    ("Dismissing premium popup", r"Dismissing premium popup"),
    ("Creating new quiz (Start from scratch)", r"Creating a new quiz"),
    ("Opening existing draft", r"Resuming quiz|Resuming existing quiz"),
    ("Editor ready", r"Editor ready"),
    ("Skipping import (draft already has questions)", r"skipping the import step"),
    ("Opening import modal", r"Opening the spreadsheet import modal"),
    ("Attaching workbook", r"Workbook attached"),
    ("Waiting for the import preview", r"Waiting for the import preview"),
    ("Preview validated (Looks good!)", r"Preview looks good"),
    ("Importing questions", r"Importing questions"),
    ("Opening publish modal", r"Opening the publish modal"),
    ("Choosing Subject", r"Choosing Subject"),
    ("Choosing Grade", r"Choosing Grade"),
    ("Publishing quiz", r"Publishing the quiz"),
    ("Handling success modal", r"closing the success modal|app skipped the modal"),
    ("Assignment decision", r"Assigning to class|Assigning publicly"),
    ("Dismissing coachmark ('Got it')", r"Dismissing the 'Got it' tip"),
    ("Recovering accommodations glitch", r"Detected the accommodations page|trying Assign again"),
    ("Opening assignment flow (Assign)", r"Starting the assignment flow"),
    ("Opening class picker", r"Opening the class picker|class is already attached"),
    ("Selecting class", r"Selecting class"),
    ("Unchecking accommodations", r"'Set up Accommodations' is unchecked"),
    ("Naming assignment", r"Naming the assignment"),
    ("Setting No deadline", r"Setting 'No deadline'"),
    ("No deadline confirmed", r"'No deadline' is (now ON|already ON)"),
    ("Public assignment flow (skipping class)", r"Public assignment \(no class\)"),
    ("Clicking final Assign", r"Clicking the final Assign"),
    ("Gameplay dashboard reached", r"gameplay dashboard reached"),
    ("Copying join link", r"Clicking 'Copy Join link'|Join link found in the DOM"),
    ("Join link captured", r"Join link captured"),
    ("Done!", None),  # auto: completed when the file result arrives
)

_COMPILED_CATALOG: tuple[tuple[str, re.Pattern[str] | None], ...] = tuple(
    (label, re.compile(pattern, re.I) if pattern else None)
    for label, pattern in STEP_CATALOG
)


@dataclass
class Step:
    label: str
    state: str = "pending"  # pending | active | done | skipped | failed
    started: float | None = None
    ended: float | None = None
    detail: str | None = None
    error: str | None = None

    @property
    def duration(self) -> float | None:
        if self.started is None:
            return None
        return (self.ended if self.ended is not None else time.perf_counter()) - self.started


_KEEP_DONE = 6       # completed steps kept in the window before trimming
_KEEP_PENDING = 3    # pending steps shown after the active one


class FileTree:
    """Per-file step tracker: matches on_progress messages to catalog slots."""

    def __init__(self, index: int, total: int, name: str) -> None:
        self.index = index
        self.total = total
        self.name = name
        self.steps: list[Step] = [Step(label) for label, _ in _COMPILED_CATALOG]
        self.pointer = 0
        self.started = time.perf_counter()
        self.ended: float | None = None
        self.finished = False
        self.failed = False

    # -- state machine ------------------------------------------------------

    def begin(self) -> None:
        """File starts: validation is already passed locally, mark it done."""
        first = self.steps[0]
        first.state = "done"
        first.started = first.ended = time.perf_counter()
        self.pointer = 1
        self._activate(1)

    def accept(self, message: str) -> None:
        """Match one progress message against the catalog (or keep as detail)."""
        if self.finished:
            return
        for idx in range(self.pointer, len(self.steps)):
            pattern = _COMPILED_CATALOG[idx][1]
            if pattern is not None and pattern.search(message):
                for earlier in range(self.pointer, idx):
                    step = self.steps[earlier]
                    # Slots between the last match and this one never fired:
                    # they are optional/branch steps -> skipped (dim dash).
                    if step.state in ("active", "pending"):
                        step.state = "skipped"
                        step.ended = time.perf_counter()
                step = self.steps[idx]
                if step.state != "done":
                    step.started = step.started or time.perf_counter()
                    step.ended = time.perf_counter()
                    step.state = "done"
                self.pointer = idx + 1
                if self.pointer < len(self.steps):
                    self._activate(self.pointer)
                return
        # Unmatched: attach as a detail line to the current active step.
        target = self.steps[self.pointer] if self.pointer < len(self.steps) else self.steps[-1]
        target.detail = message

    def finish(self, error: str | None = None) -> None:
        """The file result arrived (success or failure)."""
        if self.finished:
            return
        self.finished = True
        self.ended = time.perf_counter()
        if error:
            self.failed = True
            active = None
            for step in self.steps:
                if step.state == "active":
                    active = step
                    break
            if active is None and self.pointer > 0:
                active = self.steps[self.pointer - 1]
                active.state = "active"
            if active is not None:
                active.state = "failed"
                active.ended = time.perf_counter()
                active.error = error
        else:
            for step in self.steps:
                if step.state in ("pending", "active"):
                    step.state = "skipped"
                    step.ended = time.perf_counter()
            done = self.steps[-1]
            done.state = "done"
            done.started = done.started or time.perf_counter()
            done.ended = time.perf_counter()

    def _activate(self, idx: int) -> None:
        step = self.steps[idx]
        if step.state == "pending":
            step.state = "active"
            step.started = time.perf_counter()

    # -- rendering ----------------------------------------------------------

    def resolved_count(self) -> int:
        return sum(1 for s in self.steps if s.state in ("done", "skipped", "failed"))

    def render(self, console_width: int, *, active_label: str | None = None) -> Panel:
        width = max(56, console_width - 2)
        inner = width - 6  # panel borders + padding
        # line = 2 indent + 2 connector + 1 + icon(1) + 1 + label + 1 + time(11)
        label_w = max(28, inner - 19)
        time_w = 11

        elapsed = (self.ended or time.perf_counter()) - self.started
        resolved = self.resolved_count()
        head = Text.assemble(
            "  Steps: ",
            bar(resolved / len(self.steps)),
            (f" {resolved}/{len(self.steps)}", "bold"),
            (f" ({elapsed:.1f}s elapsed)", "dim"),
        )
        lines: list[Text] = [head, Text("")]

        window = self._windowed_steps()
        for step, connector in window:
            lines.append(self._render_step(step, connector, label_w, time_w, active_label))
            if step.error:
                lines.append(Text.assemble(
                    "  │  └─ ", ("ERROR: ", "bold red"), (step.error, "red"),
                    overflow="fold",
                ))
            elif step.detail and step.state in ("active", "failed"):
                lines.append(Text(f"  │  └─ {step.detail}", style="dim", overflow="fold"))

        title = f"[{self.index}/{self.total}] {self.name}"
        border = "red" if self.failed else "blue"
        return Panel(
            Group(*lines),
            title=title,
            title_align="left",
            box=box.SQUARE,
            border_style=border,
            padding=(0, 1),
            width=width,
        )

    def _windowed_steps(self) -> list[tuple[Step, str]]:
        """Chronological window: trimmed history + active + a few pending."""
        resolved: list[Step] = []
        pending: list[Step] = []
        seen_pending = False
        for step in self.steps:
            if step.state in ("pending",):
                seen_pending = True
            if seen_pending:
                pending.append(step)
            else:
                resolved.append(step)
        keep_resolved = resolved[-(_KEEP_DONE + 1):] if len(resolved) > _KEEP_DONE + 1 else resolved
        trimmed = len(resolved) - len(keep_resolved)
        keep_pending = pending[:_KEEP_PENDING]
        extra_pending = len(pending) - len(keep_pending)

        window: list[tuple[Step, str]] = []
        if trimmed > 0:
            marker = Step(label=f"... ({trimmed} earlier steps)")
            window.append((marker, "├─"))
        for step in keep_resolved:
            window.append((step, "├─"))
        for step in keep_pending:
            window.append((step, "├─"))
        if extra_pending > 0:
            marker = Step(label=f"... ({extra_pending} more steps)")
            window.append((marker, "├─"))
        if window:
            window[-1] = (window[-1][0], "└─")
        return window

    def _render_step(self, step: Step, connector: str, label_w: int,
                     time_w: int, active_label: str | None) -> Text:
        label = step.label
        if label.startswith("... ("):  # trimmed-history marker: no icon/time cell
            return Text(f"  {connector} {label}", style="dim")
        if len(label) > label_w:
            label = label[: label_w - 1] + "…"
        padded = f"{label:<{label_w}}"

        if step.state == "done":
            icon, style = "✓", "green"
            time_s = fmt_duration(step.duration or 0.0, running=False)
        elif step.state == "active":
            icon = _SPINNER_FRAMES[int(time.perf_counter() * 10) % len(_SPINNER_FRAMES)]
            style = "cyan"
            time_s = fmt_duration(step.duration or 0.0, running=True)
        elif step.state == "failed":
            icon, style = "✗", "red"
            time_s = fmt_duration(step.duration or 0.0, running=False)
        elif step.state == "skipped":
            icon, style = "–", "dim"
            time_s = "[ skipped ]"
        else:
            icon, style = "-", "dim"
            time_s = "[ waiting ]"

        line = Text.assemble(
            f"  {connector} ",
            (icon, style),
            " ",
            (padded, "" if step.state in ("done", "active") else "dim"),
            " ",
            (f"{time_s:>{time_w}}", "dim" if step.state != "active" else "cyan"),
        )
        return line


_FILE_RE = re.compile(r"^\[(\d+)/(\d+)\]\s*(.*)$")


class LiveTreeUI:
    """Live step-tree UI for `wqu publish` (stderr only; stdout stays clean).

    In a real terminal this renders continuously via ``rich.live.Live``; when
    stderr is not a TTY (pipes, captured test runs) it degrades to plain
    timestamped lines so nothing breaks and tests stay deterministic.
    Prompts must wrap themselves in ``pause()`` / ``resume()``.
    """

    def __init__(self, subtitle: str = "publish") -> None:
        self.subtitle = subtitle
        self.console = err_console()
        self._interactive = False
        try:
            self._interactive = bool(sys.stderr.isatty())
        except Exception:
            self._interactive = False
        self._live: Live | None = None
        self._live_active = False
        self.tree: FileTree | None = None
        self.files_total = 0
        self.file_times: list[float] = []
        self.run_start = time.perf_counter()
        self._file_start: float | None = None

    # -- lifecycle ----------------------------------------------------------

    def banner(self) -> None:
        print_banner(self.subtitle)

    def info(self, message: str) -> None:
        """Persistent styled line (shown above the live region when active)."""
        self.console.print(styled(message))

    def start(self, total: int) -> None:
        self.files_total = total
        if not self._interactive:
            return
        self._live = Live(
            self._render,
            console=self.console,
            refresh_per_second=10,
            vertical_overflow="visible",
        )
        self._live.start()
        self._live_active = True

    def stop(self) -> None:
        if self._live is not None and self._live_active:
            try:
                self._live.update(self._render())
            except Exception:
                pass
            self._live.stop()
        self._live_active = False

    # -- prompt coordination (the [r]esume / assign prompts) ----------------

    def pause(self) -> None:
        """Stop the Live region so input()/prompts render cleanly."""
        if self._live is not None and self._live_active:
            try:
                self._live.stop()
            except Exception:
                pass
            self._live_active = False

    def resume(self) -> None:
        """Restart the Live region after a prompt."""
        if self._live is not None and not self._live_active and self._interactive:
            try:
                self._live.start()
                self._live_active = True
            except Exception:
                self._live_active = False

    # -- core callbacks -----------------------------------------------------

    def on_progress(self, message: str) -> None:
        match = _FILE_RE.match(message)
        if not self._interactive:
            if match:  # still time each file for the summary table's Time column
                self._file_start = time.perf_counter()
            self.console.print(styled(message))
            return
        if match:
            self._begin_file(int(match.group(1)), int(match.group(2)), match.group(3))
        elif self.tree is None:
            # Preamble (library pre-check etc.) — persists above the live region.
            self.console.print(styled(message))
        else:
            self.tree.accept(message)
        self._refresh()

    def on_file_result(self, result) -> None:  # noqa: ANN001 - output.FileResult
        if self._file_start is not None:
            self.file_times.append(time.perf_counter() - self._file_start)
            self._file_start = None
        if self.tree is not None:
            self.tree.finish(result.error)
        if not self._interactive:
            status = "FAIL" if result.error else ("SKIP" if result.skipped else "OK")
            self.console.print(Text.assemble(
                (f"  {status:<4} ", "red" if result.error else "green"),
                (result.title, "bold"),
                ("  " + (result.join_link or result.error or result.quiz_url or ""), "dim"),
            ))
        self._refresh()

    def _begin_file(self, index: int, total: int, name: str) -> None:
        self.tree = FileTree(index, total, name)
        self.tree.begin()
        self.files_total = total
        self._file_start = time.perf_counter()

    def _refresh(self) -> None:
        if self._live is not None and self._live_active:
            try:
                self._live.update(self._render())
            except Exception:
                pass

    # -- rendering ----------------------------------------------------------

    def _render(self) -> Group:
        elapsed = time.perf_counter() - self.run_start
        done = len(self.file_times)
        total = self.files_total or 0
        overall = Text.assemble(
            ("Overall: ", "bold"),
            bar(done / total if total else 0.0),
            (f" {done}/{total} files", "bold"),
            (f" ({elapsed:.1f}s elapsed)", "dim"),
        )
        blocks: list = [overall, Text("")]
        if self.tree is not None:
            blocks.append(self.tree.render(self.console.width))
        else:
            blocks.append(Text("  Waiting for the first file...", style="dim"))
        return Group(*blocks)
