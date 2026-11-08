"""Command-line interface for wqu.

This is the ONLY module allowed to write to stdout/stderr directly or to
prompt the user. Everything in the rest of the package is library-safe
(no ``print``, no prompts) so a GUI can wrap it later without touching the
automation code.

Output contract (brief Part 4):
  * status/progress/log  -> stderr
  * final ``Label: URL`` block or --json payload -> stdout
  * clipboard copy only in the default human mode (not --json/--dry-run/--quiet)
Exit codes: 0 all good; 1 at least one file failed; 2 config/session problem.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from rich.box import SIMPLE as BOX_SIMPLE
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from wqu import __version__
from wqu.core import (
    DuplicateInfo,
    FileResult,
    FileView,
    PipelineOptions,
    Session,
    SessionExpiredError,
    ValidationError,
    WquError,
    copy_to_clipboard,
    format_json,
    format_links,
    format_summary,
    load_xlsx,
    natural_sort_key,
)
from wqu.errors import LibraryLimitError
from wqu.logging_setup import setup_logging
from wqu.publish import publish_many
from wqu.store import default_state_path

DEFAULT_CLASS = "Naic"  # the interactive branch answer "y" assigns to this class


def err(message: str = "") -> None:
    print(message, file=sys.stderr)


def progress(message: str) -> None:
    err(message)


def err_console() -> Console:
    """Fresh stderr console per call (so tests/capsys capture correctly).

    legacy_windows=False forces the nice unicode box glyphs; main() already
    reconfigures the streams to UTF-8 on Windows consoles.
    """
    return Console(file=sys.stderr, soft_wrap=True, highlight=False, legacy_windows=False)


def print_banner(subtitle: str = "") -> None:
    line = Text.assemble(
        ("wqu ", "bold white"),
        (f"v{__version__}", "bold cyan"),
        ("  •  Wayground quiz uploader", "white"),
    )
    if subtitle:
        line.append(f"  •  {subtitle}", style="dim")
    console = err_console()
    console.print(Panel(line, border_style="blue", padding=(0, 2)))
    console.print("")


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


def _classify(message: str) -> str:
    """Pick a colour for a progress message (cyan = neutral info)."""
    for pattern, style in _STYLE_RULES:
        if pattern.search(message):
            return style
    return "cyan"


class ProgressUI:
    """Banner + live progress/spinner UI for `wqu publish` (stderr only).

    stdout stays reserved for the final result block / JSON; everything here
    is presentation for humans. ``on_progress`` routes core callbacks into a
    single live line: a bar with ``[i/n] files`` plus the current step.
    """

    _FILE_RE = re.compile(r"^\[(\d+)/(\d+)\]\s*(.*)$")

    def __init__(self, subtitle: str = "publish") -> None:
        self.console = err_console()
        self.subtitle = subtitle
        self.progress: Progress | None = None
        self.task_id: TaskID | None = None

    def banner(self) -> None:
        print_banner(self.subtitle)

    def info(self, message: str) -> None:
        self.console.print(f"[{_classify(message)}]{escape(message)}[/]")

    def start(self, total: int) -> None:
        self.progress = Progress(
            SpinnerColumn(style="cyan"),
            BarColumn(bar_width=24, complete_style="cyan", finished_style="green"),
            TextColumn("[bold]{task.completed}/{task.total} files[/bold]"),
            TextColumn("{task.fields[step]}"),
            console=self.console,
        )
        self.progress.start()
        self.task_id = self.progress.add_task("publish", total=total,
                                              step="[cyan]starting...[/cyan]")

    def on_progress(self, message: str) -> None:
        if self.progress is None or self.task_id is None:
            self.info(message)
            return
        match = self._FILE_RE.match(message)
        if match:
            index = int(match.group(1))
            if index > 1:
                self.console.print("")  # spacing between files (major phases)
            self.progress.update(
                self.task_id,
                total=int(match.group(2)),
                completed=index - 1,
                step=f"[bold cyan]{escape(match.group(3))}[/bold cyan]",
            )
        else:
            style = _classify(message)
            self.progress.update(self.task_id, step=f"[{style}]{escape(message)}[/{style}]")

    def stop(self) -> None:
        if self.progress is None or self.task_id is None:
            return
        task = self.progress.tasks[0]
        done = task.completed >= (task.total or 0)
        self.progress.update(
            self.task_id,
            step="[green]done[/green]" if done else "[yellow]stopped[/yellow]",
        )
        self.progress.stop()
        self.progress = None


def _is_tty() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        return False


def _prompt_line(prompt: str) -> str | None:
    """input() that never explodes or hangs a non-interactive shell."""
    if not _is_tty():
        return None
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return None


def collect_files(paths: list[str], toolkit_root: str | None) -> list[Path]:
    """Expand files/directories into a naturally sorted workbook list."""
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            raise WquError(f"path not found: {path}")
        if path.is_dir():
            files.extend(sorted(
                (p for p in path.glob("*.xlsx") if not p.name.startswith("~$")),
                key=natural_sort_key,
            ))
        else:
            if path.suffix.lower() != ".xlsx":
                raise WquError(f"not an .xlsx workbook: {path}")
            files.append(path)
    if toolkit_root:
        root = Path(toolkit_root)
        if not root.is_dir():
            raise WquError(f"--from-toolkit-root is not a directory: {root}")
        discovered = list(root.glob("Quizzes/*/_QuizizzOutput/*.xlsx"))
        if not discovered:
            discovered = list(root.glob("quizzes/*/_quizizzoutput/*.xlsx"))
        files.extend(sorted((p for p in discovered if not p.name.startswith("~$")),
                            key=natural_sort_key))
    # de-duplicate, keep natural order
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in files:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return sorted(unique, key=natural_sort_key)


def run_dry_run(files: list[Path], options: PipelineOptions) -> int:
    """Validate every workbook; print a plan table to stdout. No browser."""
    out = Console(file=sys.stdout, soft_wrap=True, legacy_windows=False)  # per call for capturability
    table = Table(title="wqu publish --dry-run", show_lines=False)
    table.add_column("File")
    table.add_column("Label / title")
    table.add_column("Questions", justify="right")
    table.add_column("Split")
    table.add_column("Issues", overflow="fold")
    failures = 0
    for path in files:
        try:
            data = load_xlsx(path, title_override=options.title)
        except ValidationError as exc:
            failures += 1
            table.add_row(path.name, "-", "-", "-", f"[red]ERROR: {exc}[/red]")
            continue
        issues = "; ".join(str(i) for i in data.issues) or "[green]none[/green]"
        style = "red" if not data.is_valid else ("yellow" if data.warnings else "green")
        if not data.is_valid:
            failures += 1
        table.add_row(
            path.name,
            data.label + (f"  ({path.name})" if data.title != path.stem else ""),
            str(len(data.questions)),
            f"Pt{data.part}" if data.is_split else "whole",
            f"[{style}]{issues}[/{style}]",
        )
    out.print(table)
    if failures:
        err(f"{failures} of {len(files)} file(s) failed validation.")
        return 1
    err(f"All {len(files)} file(s) look good. No browser was launched.")
    return 0


def make_decide_assign(args: argparse.Namespace):
    """Build the per-file 'assign to which class?' callback for publish_many.

    Returns a callable (FileView) -> class name | None. When it is None the
    pipeline never asks and never assigns.
    """
    if args.keep_draft:
        return None  # options.keep_draft skips assignment entirely
    if args.no_assign:
        # Public assignment (no class, platform defaults) — still yields a
        # join link; see LIVE 2026-11-09 note in ARCHITECTURE.md.
        return lambda view: None
    if args.assign:
        return lambda view: args.assign
    if args.yes:
        err(f"--yes: defaulting to a PUBLIC assignment, no class (use --assign {DEFAULT_CLASS} to assign to it).")
        return lambda view: None
    if not _is_tty():
        err("stdin is not interactive; defaulting to a PUBLIC assignment, no class "
            f"(pass --assign {DEFAULT_CLASS}, --keep-draft, or --no-assign to silence this).")
        return None

    def decide(view: FileView) -> str | None:
        if args.interactive_review and view.quiz_url:
            err(f"      Published: {view.quiz_url}")
            _prompt_line("      Press Enter to continue to the assignment step... ")
        note = " (split part — default N)" if view.is_split else ""
        answer = _prompt_line(f'      Assign to class "{DEFAULT_CLASS}"? [y/N]:{note} ')
        if answer is not None and answer.strip().lower() in ("y", "yes"):
            return DEFAULT_CLASS
        err("      → public assignment (no class; 1-day deadline)")
        return None

    return decide


def make_resolve_duplicate(args: argparse.Namespace):
    """Build the duplicate-title callback ([r]esume / [s]kip / [n]ew)."""
    if args.force:
        return None  # publish_many skips the check entirely when force=True
    if args.yes:
        def decide(info: DuplicateInfo) -> str:
            err(f"WARNING: quiz titled {info.title!r} already exists "
                f"({', '.join(info.prior_quiz_ids)}); creating a NEW one (--yes default).")
            return "new"
        return decide
    if not _is_tty():
        def decide_noninteractive(info: DuplicateInfo) -> str:
            err(f"WARNING: quiz titled {info.title!r} already exists; "
                "non-interactive run defaults to creating a NEW quiz.")
            return "new"
        return decide_noninteractive

    def decide_interactive(info: DuplicateInfo) -> str:
        err(f"      A quiz titled {info.title!r} was created before "
            f"({', '.join(info.prior_quiz_ids)}).")
        answer = _prompt_line("      [r]esume / [s]kip / [n]ew? [r]: ")
        answer = (answer or "").strip().lower()
        if answer in ("s", "skip"):
            return "skip"
        if answer in ("n", "new"):
            return "new"
        return "resume"

    return decide_interactive


def make_choose_archive(args: argparse.Namespace):
    """Interactive library-cap chooser (rich table + prompts).

    Returns None when --auto-archive-oldest (handled inside the pipeline) or
    when not interactive: a non-TTY run must never hang on a prompt.
    """
    if args.auto_archive_oldest is not None:
        return None
    if args.quiet or not _is_tty():
        return None

    def choose(scan, needed: int):  # noqa: ANN001 - library.LibraryScan
        from wqu.library import select_for_archive

        console = err_console()
        console.print("")
        table = Table(
            title=f"Library cap: {scan.total}/{scan.limit} active quizzes — "
                  f"this batch needs {needed} more slot(s)",
            box=BOX_SIMPLE,
        )
        table.add_column("#", justify="right")
        table.add_column("Title", overflow="fold")
        table.add_column("Age", style="dim")
        for item in scan.items[:30]:
            table.add_row(str(item.row_index), escape(item.title), escape(item.age_text or "?"))
        if len(scan.items) > 30:
            table.add_row("…", f"[dim]+{len(scan.items) - 30} more[/dim]", "")
        console.print(table)
        spec = Prompt.ask(
            "Archive which? [#,# | o<N>=oldest N | a=all | Enter=skip]",
            console=console, default="",
        )
        picks = select_for_archive(spec, scan, needed)
        if not picks:
            console.print("[yellow]Nothing selected; the run stops if the cap would be exceeded.[/yellow]")
            return ()
        if Confirm.ask(f"Archive {len(picks)} quiz(zes)?", console=console, default=False):
            return picks
        return ()

    return choose


def build_options(args: argparse.Namespace, logs_dir: Path) -> PipelineOptions:
    return PipelineOptions(
        subject=args.subject,
        grade=args.grade,
        assign_class=args.assign,
        keep_draft=args.keep_draft,
        no_deadline=args.no_deadline,
        title=args.title,
        force=args.force,
        force_import=args.force_import,
        fail_fast=args.fail_fast,
        headless=not args.no_headless,
        auto_archive_oldest=getattr(args, "auto_archive_oldest", None),
        state_path=default_state_path(),
        logs_dir=logs_dir,
    )


def cmd_publish(args: argparse.Namespace) -> int:
    log_path = setup_logging("publish", args.logs_dir)
    ui = ProgressUI()
    ui.banner()
    ui.console.print(f"[dim]Log: {log_path}[/dim]")
    try:
        files = collect_files(args.paths or [], args.from_toolkit_root)
    except WquError as exc:
        ui.info(str(exc))
        return 2
    if not files:
        ui.info("no .xlsx files found to process.")
        return 2
    if args.title and len(files) > 1:
        ui.info("--title can only be used with exactly one input file.")
        return 2

    options = build_options(args, Path(args.logs_dir))

    if args.dry_run:
        return run_dry_run(files, options)

    session = Session(state_path=options.state_path, logs_dir=options.logs_dir)
    if not session.exists():
        ui.info(f"No saved session at {options.state_path}. Run 'wqu login' first.")
        return 2

    # Interactive prompting (assign decisions / duplicate handling) needs a
    # plain terminal: keep the live bar off in that mode so prompts stay clean.
    interactive_mode = _is_tty() and not args.yes and not any(
        (args.assign, args.no_assign, args.keep_draft)
    )
    ui.console.print("")
    ui.info(f"Processing {len(files)} file(s){' (headless)' if options.headless else ' (headed)'}...")
    use_bar = not args.quiet and not interactive_mode
    if use_bar:
        ui.start(total=len(files))
    try:
        results = publish_many(
            files,
            options,
            session=session,
            on_progress=None if args.quiet else ui.on_progress,
            decide_assign=make_decide_assign(args),
            resolve_duplicate=make_resolve_duplicate(args),
            choose_archive=make_choose_archive(args),
        )
    except SessionExpiredError as exc:
        ui.info(str(exc))
        return 2
    except LibraryLimitError as exc:
        ui.info(str(exc))
        return 2
    finally:
        ui.stop()
    return finish_publish(args, results)


def _results_fancy_text(results: list[FileResult]) -> Text:
    """One styled line per file: green success, red failure, dim skip."""
    text = Text()
    for index, result in enumerate(results):
        if index:
            text.append("\n")
        if result.skipped:
            text.append(f"{result.title}: (skipped)", style="dim")
        elif result.error is None:
            text.append(result.line(), style="green")
        else:
            text.append(f"{result.title}: {result.error}", style="red")
    return text


def _summary_table(results: list[FileResult]) -> Table:
    """Per-file batch summary for the terminal (stderr)."""
    table = Table(box=BOX_SIMPLE, pad_edge=False, show_edge=False)
    table.add_column("File", style="bold", overflow="fold", max_width=40)
    table.add_column("Status", no_wrap=True)
    table.add_column("Join link / detail", overflow="fold")
    for result in results:
        name = Path(result.file).name
        if result.skipped:
            table.add_row(name, "[yellow]SKIP[/yellow]", "[dim]skipped by user[/dim]")
        elif result.error is not None:
            table.add_row(name, "[red]FAIL[/red]", f"[red]{escape(result.error)}[/red]")
        elif result.join_link:
            table.add_row(name, "[green]OK[/green]", f"[green]{escape(result.join_link)}[/green]")
        else:
            detail = escape(str(result.quiz_url or "(no quiz created)"))
            table.add_row(name, "[green]OK[/green]", f"{detail} [yellow](NO JOIN LINK)[/yellow]")
    return table


def finish_publish(args: argparse.Namespace, results: list[FileResult]) -> int:
    any_error = any(r.error is not None for r in results)
    if args.json:
        print(format_json(results))
    else:
        block = format_links(results)
        if args.quiet:
            # script mode: plain lines only, no clipboard, no decoration
            if block:
                print(block)
            if any_error:
                err(format_summary(results))
        else:
            console = err_console()
            # human mode: clipboard copy FIRST, then a pretty panel (headed-run
            # feedback 2026-11-09), then the summary table.
            if block:
                if copy_to_clipboard(block):
                    console.print("[dim]Copied the result block to the clipboard.[/dim]")
                else:
                    console.print("[yellow]WARNING: could not copy to the clipboard.[/yellow]")
            content = _results_fancy_text(results)
            if content.plain:
                out = Console(file=sys.stdout, soft_wrap=True, legacy_windows=False)
                out.print("")  # spacing before the box
                out.print(
                    Panel(
                        content,
                        title="wqu results",
                        border_style="red" if any_error else "green",
                        expand=False,
                    )
                )
            console.print("")  # spacing before the summary
            console.print(_summary_table(results))
            caption = format_summary(results).splitlines()[0]
            console.print(f"[dim]{escape(caption)}[/dim]")
    if any(r.error_kind == "session-expired" for r in results):
        err("Run 'wqu login' again.")
        return 2
    if any(r.error_kind == "class-not-found" for r in results):
        return 2
    return 1 if any_error else 0


# ---------------------------------------------------------------------------
# other commands
# ---------------------------------------------------------------------------


def _styled_progress(console: Console):
    def report(message: str) -> None:
        console.print(f"[{_classify(message)}]{escape(message)}[/]")

    return report


def cmd_login(args: argparse.Namespace) -> int:
    log_path = setup_logging("login", args.logs_dir)
    print_banner("login")
    console = err_console()
    console.print(f"[dim]Log: {log_path}[/dim]")
    session = Session(logs_dir=args.logs_dir)

    def wait_for_login() -> None:
        console.print("[cyan]Sign in to Wayground in the opened browser window.[/cyan]")
        _prompt_line("Press Enter here when you are done... ")

    result = session.login(
        headless=False,
        wait_for_login=wait_for_login if _is_tty() else None,
        on_progress=_styled_progress(console),
    )
    if result.ok:
        console.print(f"[bold green][OK][/bold green] {escape(result.detail)}")
        return 0
    console.print(f"[bold red][FAIL][/bold red] {escape(result.detail)}")
    return 2


def cmd_check(args: argparse.Namespace) -> int:
    log_path = setup_logging("check", args.logs_dir)
    print_banner("check")
    console = err_console()
    console.print(f"[dim]Log: {log_path}[/dim]")
    session = Session(logs_dir=args.logs_dir)
    result = session.check(headless=True, on_progress=_styled_progress(console))
    if result.ok:
        console.print(f"[bold green][OK][/bold green] {escape(result.detail)}")
        return 0
    console.print(f"[bold red][EXPIRED][/bold red] {escape(result.detail)}")
    return 2


def cmd_doctor(args: argparse.Namespace) -> int:
    print_banner("doctor")
    console = err_console()
    checks = Session(logs_dir=args.logs_dir).doctor()
    failed = 0
    console.print("")
    for check in checks:
        if check.ok:
            console.print(f"[green][OK][/green]   [bold]{escape(check.name)}[/bold] — {escape(check.detail)}")
        else:
            failed += 1
            hint = f" [yellow](hint: {escape(check.hint)})[/yellow]" if check.hint else ""
            console.print(f"[red][FAIL][/red] [bold]{escape(check.name)}[/bold] — {escape(check.detail)}{hint}")
    console.print("")
    if failed:
        console.print(f"[red]{failed} of {len(checks)} checks failed.[/red]")
        return 1
    console.print(f"[green]All {len(checks)} checks passed.[/green]")
    return 0


def cmd_library(args: argparse.Namespace) -> int:
    log_path = setup_logging("library", args.logs_dir)
    print_banner("library")
    console = err_console()
    console.print(f"[dim]Log: {log_path}[/dim]")
    session = Session(logs_dir=args.logs_dir)
    if not session.exists():
        console.print("[red]No saved session[/red] — run [bold]wqu login[/bold] first.")
        return 2

    def wait_for_user() -> None:
        try:
            input("Press Enter when you are done managing the library... ")
        except (KeyboardInterrupt, EOFError):
            pass

    from wqu.library import open_ui  # local import keeps --version startup fast

    open_ui(session=session, wait_for_user=wait_for_user,
            on_progress=_styled_progress(console))
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    log_path = setup_logging("discover", args.logs_dir)
    print_banner("discover")
    console = err_console()
    console.print(f"[dim]Log: {log_path}[/dim]")
    session = Session(logs_dir=args.logs_dir)

    def wait_for_user() -> None:
        try:
            input("Interact now. Press Ctrl+C when you are done.\n")
        except (KeyboardInterrupt, EOFError):
            console.print("")

    if not session.exists():
        console.print(f"[red]No saved session at {escape(str(session.state_path))}[/red] — "
                      "run [bold]wqu login[/bold] first.")
        return 2

    from wqu.session import discover  # local import keeps --version startup fast

    result = discover(args.url, session=session, snaps_dir=Path("snaps"),
                      wait_for_user=wait_for_user, on_progress=_styled_progress(console))
    print(result.screenshot)
    print(result.aria)
    print(result.html)
    return 0


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------


def _add_logs_dir(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--logs-dir", default="wqu-logs", metavar="DIR",
                        help="directory for logs/traces/screenshots (default: wqu-logs)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wqu",
        description="Publish .xlsx quizzes to Wayground: upload, publish, assign, join link.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p_login = sub.add_parser("login", help="interactive login; saves ~/.wayground/state.json")
    p_login.add_argument("--timeout", type=int, default=300, help=argparse.SUPPRESS)
    _add_logs_dir(p_login)
    p_login.set_defaults(func=cmd_login)

    p_check = sub.add_parser("check", help="verify the stored session still works")
    _add_logs_dir(p_check)
    p_check.set_defaults(func=cmd_check)

    p_doctor = sub.add_parser("doctor", help="diagnose the environment")
    _add_logs_dir(p_doctor)
    p_doctor.set_defaults(func=cmd_doctor)

    p_discover = sub.add_parser("discover", help="open a URL headed and dump DOM/aria/screenshot")
    p_discover.add_argument("url", help="Wayground URL to inspect")
    _add_logs_dir(p_discover)
    p_discover.set_defaults(func=cmd_discover)

    p_library = sub.add_parser("library",
                               help="open the Wayground library to manage/archive quizzes")
    _add_logs_dir(p_library)
    p_library.set_defaults(func=cmd_library)

    p_pub = sub.add_parser("publish", help="files or folders; full pipeline")
    p_pub.add_argument("paths", nargs="*", help="one or more .xlsx files and/or folders")
    p_pub.add_argument("--subject", default="Information Technology (IT)",
                       help='subject for the publish modal (default: "Information Technology (IT)")')
    p_pub.add_argument("--grade", default="University", help="grade for the publish modal")
    p_pub.add_argument("--title", default=None, help="quiz title override (single file only)")
    p_pub.add_argument("--assign", metavar="CLASS", default=None,
                       help="force-assign to this class name (no prompt)")
    p_pub.add_argument("--no-assign", action="store_true",
                       help="skip the class picker; still assigns PUBLICLY (platform default 1-day "
                            "deadline) so a join link is produced; also suppresses the prompt")
    p_pub.add_argument("--no-deadline", dest="no_deadline", action="store_true", default=True,
                       help="set 'No deadline' on the assignment (default; accepted for symmetry)")
    p_pub.add_argument("--deadline", dest="no_deadline", action="store_false",
                       help="reserved for future use: keep the platform's default deadline")
    p_pub.add_argument("--no-headless", action="store_true", help="show the browser window")
    p_pub.add_argument("--dry-run", action="store_true", help="validate only; no browser")
    p_pub.add_argument("--json", action="store_true", help="machine-readable output on stdout")
    p_pub.add_argument("--quiet", action="store_true", help="only the Label: URL lines (stdout)")
    p_pub.add_argument("--keep-draft", action="store_true",
                       help="publish but skip assignment ENTIRELY (no class, no public assignment; "
                            "a join link may be unavailable)")
    p_pub.add_argument("--force", action="store_true", help="skip duplicate-title detection")
    p_pub.add_argument("--fail-fast", action="store_true",
                       help="stop at the first failed file (default: skip and continue)")
    p_pub.add_argument("--force-import", action="store_true",
                       help="DANGEROUS: click Import even when the preview shows errors")
    p_pub.add_argument("--interactive-review", action="store_true",
                       help="pause after each publish; show the URL; wait for Enter before assigning")
    p_pub.add_argument("--yes", action="store_true",
                       help="non-interactive: take defaults for all prompts")
    p_pub.add_argument("--from-toolkit-root", metavar="PATH", default=None,
                       help=r"auto-discover Quizzes\*\_QuizizzOutput\*.xlsx under this root")
    p_pub.add_argument("--auto-archive-oldest", type=int, metavar="N", default=None,
                       help="when the 20-quiz library cap would be exceeded, archive the N oldest "
                            "quizzes automatically (no prompt)")
    _add_logs_dir(p_pub)
    p_pub.set_defaults(func=cmd_publish)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to a legacy codepage; em-dashes and the NO JOIN
    # LINK marker must survive. (errors="replace" keeps piped output safe.)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "command", None) is None:
        parser.print_help(sys.stderr)
        return 2
    try:
        return int(args.func(args))
    except WquError as exc:
        err(f"ERROR: {exc}")
        return 2
    except KeyboardInterrupt:
        err("Interrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
