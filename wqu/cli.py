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
import json
import sys
from pathlib import Path

from rich import box
from rich.box import SIMPLE as BOX_SIMPLE
from rich.markup import escape
from rich.panel import Panel
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
from wqu.ui import LiveTreeUI, err_console, out_console, print_banner
from wqu.ui import classify as _classify

DEFAULT_CLASS = "Naic"  # the interactive branch answer "y" assigns to this class


def err(message: str = "") -> None:
    print(message, file=sys.stderr)


def progress(message: str) -> None:
    err(message)


# The UI helpers (err_console, print_banner, classify, LiveTreeUI) live in
# wqu/ui.py — imported at the top of this module.  Keeping them there keeps
# cli.py's own text to prompts/commands; core never imports wqu.ui.


def _is_tty() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        return False


def _prompt_line(prompt: str, ui: LiveTreeUI | None = None) -> str | None:
    """input() that never explodes or hangs a non-interactive shell.

    When a Live UI is running it is paused for the prompt and resumed after —
    otherwise rich's redraw mangles the prompt line (the v0.1.2 [r]esume bug).
    """
    if not _is_tty():
        return None
    if ui is not None:
        ui.pause()
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return None
    finally:
        if ui is not None:
            ui.resume()


def _confirm(prompt: str, console=None, *, default: bool = True) -> bool:  # noqa: ANN001
    """Confirm.ask that treats a closed stdin as "no" instead of crashing."""
    try:
        return Confirm.ask(prompt, console=console or err_console(), default=default)
    except (EOFError, KeyboardInterrupt):
        return False


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
    out = out_console()  # per call so tests/capsys capture correctly
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


def _say(ui: LiveTreeUI | None, message: str, *, style: str | None = None) -> None:
    """Print a line above/outside the Live region (plain stderr as fallback)."""
    if ui is not None:
        console = ui.console
        console.print(f"[{style}]{escape(message)}[/]" if style else escape(message))
    else:
        err(message)


def make_decide_assign(args: argparse.Namespace, ui: LiveTreeUI | None = None):
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
        _say(ui, f"--yes: defaulting to a PUBLIC assignment, no class "
                 f"(use --assign {DEFAULT_CLASS} to assign to it).")
        return lambda view: None
    if not _is_tty():
        _say(ui, "stdin is not interactive; defaulting to a PUBLIC assignment, no class "
                 f"(pass --assign {DEFAULT_CLASS}, --keep-draft, or --no-assign to silence this).")
        return None

    def decide(view: FileView) -> str | None:
        if args.interactive_review and view.quiz_url:
            _say(ui, f"      Published: {view.quiz_url}")
            _prompt_line("      Press Enter to continue to the assignment step... ", ui)
        note = " (split part — default N)" if view.is_split else ""
        answer = _prompt_line(f'      Assign to class "{DEFAULT_CLASS}"? [y/N]:{note} ', ui)
        if answer is not None and answer.strip().lower() in ("y", "yes"):
            return DEFAULT_CLASS
        _say(ui, "      → public assignment (no class; 1-day deadline)")
        return None

    return decide


def make_resolve_duplicate(args: argparse.Namespace, ui: LiveTreeUI | None = None):
    """Build the duplicate-title callback ([r]esume / [s]kip / [n]ew)."""
    if args.force:
        return None  # publish_many skips the check entirely when force=True
    if args.yes:
        def decide(info: DuplicateInfo) -> str:
            _say(ui, f"WARNING: quiz titled {info.title!r} already exists "
                     f"({', '.join(info.prior_quiz_ids)}); creating a NEW one (--yes default).")
            return "new"
        return decide
    if not _is_tty():
        def decide_noninteractive(info: DuplicateInfo) -> str:
            _say(ui, f"WARNING: quiz titled {info.title!r} already exists; "
                     "non-interactive run defaults to creating a NEW quiz.")
            return "new"
        return decide_noninteractive

    def decide_interactive(info: DuplicateInfo) -> str:
        # LiveTreeUI.pause() inside _prompt_line keeps the [r]esume prompt
        # from being mangled by the Live redraw (the v0.1.2 bug).
        _say(ui, f"      A quiz titled {info.title!r} was created before "
                 f"({', '.join(info.prior_quiz_ids)}).")
        answer = _prompt_line("      [r]esume / [s]kip / [n]ew? [r]: ", ui)
        answer = (answer or "").strip().lower()
        if answer in ("s", "skip"):
            return "skip"
        if answer in ("n", "new"):
            return "new"
        return "resume"

    return decide_interactive


def _archive_table(scan, *, title: str | None = None) -> Table:  # noqa: ANN001 - library.LibraryScan
    """Numbered quiz table shared by the capacity prompt and `wqu archive`."""
    table = Table(title=title, box=BOX_SIMPLE, pad_edge=False)
    table.add_column("#", justify="right")
    table.add_column("Title", overflow="fold")
    table.add_column("Age", style="dim")
    for item in scan.items[:30]:
        table.add_row(str(item.row_index), escape(item.title), escape(item.age_text or "?"))
    if len(scan.items) > 30:
        table.add_row("…", f"[dim]+{len(scan.items) - 30} more[/dim]", "")
    return table


def _ask_archive_selection(console, scan, *, needed: int = 0, title: str | None = None) -> tuple[str, ...]:  # noqa: ANN001
    """Print the numbered table and ask which quizzes to archive."""
    from wqu.library import select_for_archive

    console.print("")
    console.print(_archive_table(scan, title=title))
    try:
        spec = Prompt.ask(
            "Archive which? [#,# | o<N>=oldest N | a=all | Enter=skip]",
            console=console, default="",
        )
    except (EOFError, KeyboardInterrupt):
        # isatty() can lie (captured shells); EOF means no answers are coming.
        console.print("[yellow]No input available; nothing selected.[/yellow]")
        return ()
    picks = select_for_archive(spec, scan, needed)
    if not picks:
        console.print("[yellow]Nothing selected.[/yellow]")
        return ()
    try:
        confirmed = Confirm.ask(f"Archive {len(picks)} quiz(zes)?", console=console, default=False)
    except (EOFError, KeyboardInterrupt):
        confirmed = False
    return picks if confirmed else ()


def make_choose_archive(args: argparse.Namespace, ui: LiveTreeUI | None = None):
    """Interactive library-cap chooser (rich table + prompts).

    Returns None when --auto-archive-oldest (handled inside the pipeline) or
    when not interactive: a non-TTY run must never hang on a prompt.
    """
    if args.auto_archive_oldest is not None:
        return None
    if args.quiet or not _is_tty():
        return None

    def choose(scan, needed: int):  # noqa: ANN001 - library.LibraryScan
        console = ui.console if ui is not None else err_console()
        if ui is not None:
            ui.pause()  # keep the live tree from fighting the table/prompts
        try:
            picks = _ask_archive_selection(
                console, scan, needed=needed,
                title=f"Library cap: {scan.total}/{scan.limit} active quizzes — "
                      f"this batch needs {needed} more slot(s)",
            )
            if not picks:
                console.print("[yellow]The run stops if the cap would be exceeded.[/yellow]")
            return picks
        finally:
            if ui is not None:
                ui.resume()

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


def _can_login_interactively(args: argparse.Namespace) -> bool:
    """Auto-opening a login browser only makes sense for a human at a terminal."""
    if getattr(args, "yes", False) or getattr(args, "quiet", False) or getattr(args, "json", False):
        return False
    return _is_tty()


def _login_interactively(session: Session, ui: LiveTreeUI | None = None) -> bool:
    """Tell the user a login is needed, open a headed browser, save the state.

    Returns True when a session was saved. The Live region is paused for the
    whole flow so prompts and progress lines stay readable.
    """
    console = ui.console if ui is not None else err_console()
    if ui is not None:
        ui.pause()
    try:
        console.print("")
        console.print("[bold yellow]You are not signed in to Wayground.[/bold yellow]")
        console.print("Opening a browser window so you can sign in. "
                      "Come back here when you're done.")
        console.print("")

        def wait_for_login() -> None:
            console.print("[cyan]Sign in to Wayground in the browser window.[/cyan]")
            _prompt_line("Press Enter here when you are signed in... ")

        for attempt in range(1, 4):
            result = session.login(
                headless=False,
                wait_for_login=wait_for_login if _is_tty() else None,
                on_progress=_styled_progress(console),
            )
            if result.ok:
                console.print(f"[bold green][OK][/bold green] {escape(result.detail)}")
                return True
            console.print(f"[bold red][FAIL][/bold red] {escape(result.detail)}")
            if not _is_tty():
                return False
            if attempt == 3 or not _confirm(
                "Still not signed in. Open the login browser again?",
                console, default=True,
            ):
                return False
        return False
    finally:
        if ui is not None:
            ui.resume()


def _publish_with_login(
    args: argparse.Namespace,
    files: list[Path],
    options: PipelineOptions,
    session: Session,
    ui: LiveTreeUI,
) -> list[FileResult]:
    """Run publish_many, offering a browser login when the session is unusable.

    The pipeline probes the login before touching anything; if that (or a
    mid-run bounce) reports an expired session, the user is told and a headed
    browser is opened. Once the new state is saved the same batch is retried.
    """
    for attempt in range(1, 4):
        try:
            return publish_many(
                files,
                options,
                session=session,
                on_progress=None if args.quiet else ui.on_progress,
                decide_assign=make_decide_assign(args, ui),
                resolve_duplicate=make_resolve_duplicate(args, ui),
                choose_archive=make_choose_archive(args, ui),
                on_result=None if args.quiet else ui.on_file_result,
            )
        except SessionExpiredError as exc:
            if attempt == 3 or not _can_login_interactively(args):
                raise
            ui.info(str(exc))
            if not _login_interactively(session, ui):
                raise SessionExpiredError("Login was not completed; nothing was uploaded.") from exc
            ui.info("Login saved. Continuing with the upload...")
    return []  # unreachable: the loop returns or raises


def cmd_publish(args: argparse.Namespace) -> int:
    log_path = setup_logging("publish", args.logs_dir)
    mode = "headed" if args.no_headless else "headless"
    ui = LiveTreeUI(subtitle=f"publish ({mode})")
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

    # Login pre-flight (before any upload work): no saved state at all.
    if not session.exists():
        ui.info(f"No saved Wayground session at {options.state_path}.")
        if not _can_login_interactively(args):
            ui.info("Run 'wqu login' first, then re-run this command.")
            return 2
        if not _login_interactively(session, ui):
            ui.info("Login was not completed; nothing was uploaded.")
            return 2

    ui.console.print("")
    ui.console.print(f"[bold]Processing {len(files)} file(s)...[/bold]")
    ui.console.print("")
    if not args.quiet:
        ui.start(total=len(files))
    try:
        # Prompts (assign / duplicate / archive) pause the Live region via
        # _prompt_line / make_choose_archive, so the tree can stay on.
        results = _publish_with_login(args, files, options, session, ui)
    except SessionExpiredError as exc:
        ui.info(str(exc))
        return 2
    except LibraryLimitError as exc:
        ui.info(str(exc))
        return 2
    finally:
        ui.stop()
    return finish_publish(args, results, ui)


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


def _summary_table(results: list[FileResult], ui: LiveTreeUI | None = None) -> Table:
    """Per-file batch summary for the terminal (stderr), with timings."""
    table = Table(box=BOX_SIMPLE, pad_edge=False, show_edge=False)
    table.add_column("File", style="bold", overflow="fold", max_width=40)
    table.add_column("Status", no_wrap=True)
    table.add_column("Time", justify="right", no_wrap=True)
    table.add_column("Join link / detail", overflow="fold")
    times = list(ui.file_times) if ui is not None else []
    for index, result in enumerate(results):
        name = Path(result.file).name
        seconds = f"{times[index]:.1f}s" if index < len(times) else "-"
        if result.skipped:
            table.add_row(name, "[yellow]SKIP[/yellow]", seconds, "[dim]skipped by user[/dim]")
        elif result.error is not None:
            table.add_row(name, "[red]FAIL[/red]", seconds, f"[red]{escape(result.error)}[/red]")
        elif result.join_link:
            table.add_row(name, "[green]OK[/green]", seconds,
                          f"[green]{escape(result.join_link)}[/green]")
        else:
            detail = escape(str(result.quiz_url or "(no quiz created)"))
            table.add_row(name, "[green]OK[/green]", seconds,
                          f"{detail} [yellow](NO JOIN LINK)[/yellow]")
    return table


def finish_publish(args: argparse.Namespace, results: list[FileResult],
                   ui: LiveTreeUI | None = None) -> int:
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
            # human mode: clipboard copy FIRST, then a borderless result block,
            # then the summary table with timings.
            if block:
                if copy_to_clipboard(block):
                    console.print("[dim]Copied the result block to the clipboard.[/dim]")
                else:
                    console.print("[yellow]WARNING: could not copy to the clipboard.[/yellow]")
            content = _results_fancy_text(results)
            if content.plain:
                out = out_console()
                out.print("")  # spacing before the block
                out.print(
                    Panel(
                        content,
                        title="wqu results",
                        # HORIZONTALS: no side borders — double-click selection
                        # in PowerShell/Windows Terminal no longer grabs "│".
                        box=box.HORIZONTALS,
                        border_style="red" if any_error else "green",
                        expand=False,
                    )
                )
            console.print("")  # spacing before the summary
            console.print(_summary_table(results, ui))
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


def _styled_progress(console):
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


def library_payload(scan) -> dict:  # noqa: ANN001 - library.LibraryScan
    """Machine-readable form of a library scan (used by `wqu list --json`)."""
    return {
        "total": scan.total,
        "limit": scan.limit,
        "quizzes": [
            {
                "index": item.row_index,
                "title": item.title,
                "age": item.age_text or None,
                "age_minutes": item.age_minutes,
                "quiz_id": item.quiz_id,
            }
            for item in scan.items
        ],
    }


def library_payload_json(scan) -> str:  # noqa: ANN001 - library.LibraryScan
    return json.dumps(library_payload(scan), indent=2, ensure_ascii=False)


def cmd_list(args: argparse.Namespace) -> int:
    log_path = setup_logging("list", args.logs_dir)
    print_banner("list")
    console = err_console()
    console.print(f"[dim]Log: {log_path}[/dim]")
    session = Session(logs_dir=args.logs_dir)
    if not session.exists():
        console.print("[red]No saved session[/red] — run [bold]wqu login[/bold] first.")
        return 2

    from wqu.library import scan_library

    try:
        with session.open(headless=not args.no_headless) as handle:
            if not session.is_authenticated(handle.page, on_progress=_styled_progress(console)):
                console.print("[red]Not signed in to Wayground[/red] — run [bold]wqu login[/bold] first.")
                return 2
            scan = scan_library(handle.page, on_progress=_styled_progress(console))
    except SessionExpiredError as exc:
        console.print(f"[red]{escape(str(exc))}[/red] Run [bold]wqu login[/bold] again.")
        return 2

    if args.json:
        print(library_payload_json(scan))
        return 0

    table = Table(title=f"wqu library — {scan.total}/{scan.limit} active quizzes",
                  box=BOX_SIMPLE, pad_edge=False)
    table.add_column("#", justify="right")
    table.add_column("Quiz title")
    table.add_column("Age", style="dim")
    table.add_column("Quiz ID", style="dim")
    for item in scan.items:
        table.add_row(str(item.row_index), escape(item.title), escape(item.age_text or "?"),
                      item.quiz_id[:8] + "…")
    out_console().print(table)
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    modes = [bool(args.all), bool(args.test), bool(args.ids), bool(args.interactive)]
    console = err_console()
    if sum(modes) > 1:
        console.print("[red]Choose only one of --all, --test, --id, --interactive.[/red]")
        return 2
    interactive_mode = args.interactive or not any(modes)
    if interactive_mode and not _is_tty():
        console.print("[yellow]No selection flags and stdin is not interactive.[/yellow] "
                      "Use [bold]--all[/bold], [bold]--test[/bold], or [bold]--id <id>...[/bold] — "
                      "or run in a terminal.")
        return 2

    log_path = setup_logging("archive", args.logs_dir)
    print_banner("archive")
    console.print(f"[dim]Log: {log_path}[/dim]")
    session = Session(logs_dir=args.logs_dir)
    if not session.exists():
        console.print("[red]No saved session[/red] — run [bold]wqu login[/bold] first.")
        return 2

    from wqu.library import (
        archive_items,
        scan_library,
        select_by_ids,
        select_test_artifacts,
    )

    try:
        with session.open(headless=not args.no_headless) as handle:
            page = handle.page
            if not session.is_authenticated(page, on_progress=_styled_progress(console)):
                console.print("[red]Not signed in to Wayground[/red] — run [bold]wqu login[/bold] first.")
                return 2
            scan = scan_library(page, on_progress=_styled_progress(console))

            chosen: list = []
            if args.all:
                chosen = list(scan.items)
                if not chosen:
                    console.print("[yellow]The library has no active quizzes.[/yellow]")
                    return 0
                if _is_tty():
                    try:
                        ok = Confirm.ask(f"Archive ALL {len(chosen)} active quizzes?",
                                         console=console, default=False)
                    except (EOFError, KeyboardInterrupt):
                        ok = False
                    if not ok:
                        console.print("[yellow]Nothing archived.[/yellow]")
                        return 0
            elif args.test:
                chosen = list(select_test_artifacts(scan.items))
                if not chosen:
                    console.print("[yellow]No test artifacts found — nothing to clean up.[/yellow]")
                    return 0
                console.print(f"[cyan]Matched {len(chosen)} test artifact(s) "
                              "(TEST / sample / WQU_V / DELETE_ME).[/cyan]")
            elif args.ids:
                chosen, missing = select_by_ids(scan.items, args.ids)
                if missing:
                    console.print(f"[yellow]Not found in the library: {', '.join(missing)}[/yellow]")
                if not chosen:
                    console.print("[red]No matching quizzes.[/red]")
                    return 2
            else:  # interactive fallback (also reached with --interactive)
                picks = _ask_archive_selection(console, scan,
                                               title=f"wqu library — {scan.total}/{scan.limit} active quizzes")
                by_id = {item.quiz_id: item for item in scan.items}
                chosen = [by_id[quiz_id] for quiz_id in picks]
                if not chosen:
                    console.print("[yellow]Nothing archived.[/yellow]")
                    return 0

            archived_ids = archive_items(page, chosen, on_progress=_styled_progress(console))
            archived_set = set(archived_ids)
            for item in chosen:
                if item.quiz_id in archived_set:
                    console.print(f"[green]✓ Archived: {escape(item.title)}[/green]")
            scan_after = scan_library(page, on_progress=None)
            console.print(f"[bold green]Archived {len(archived_ids)} quiz(zes). "
                          f"Library is now {scan_after.total}/{scan_after.limit}.[/bold green]")
            return 0
    except SessionExpiredError as exc:
        console.print(f"[red]{escape(str(exc))}[/red] Run [bold]wqu login[/bold] again.")
        return 2


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

    p_list = sub.add_parser("list", help="list active quizzes in your library")
    p_list.add_argument("--json", action="store_true",
                        help="machine-readable output (total/limit/quizzes) on stdout")
    p_list.add_argument("--no-headless", action="store_true", help="show the browser window")
    _add_logs_dir(p_list)
    p_list.set_defaults(func=cmd_list)

    p_archive = sub.add_parser("archive", help="archive quizzes in your library")
    p_archive.add_argument("--all", action="store_true", help="archive every active quiz")
    p_archive.add_argument("--test", action="store_true",
                           help="archive development artifacts (title contains TEST, sample, WQU_V, DELETE_ME)")
    p_archive.add_argument("--id", dest="ids", nargs="+", metavar="ID", default=None,
                           help="archive specific quizzes by id (exact, or unique prefix)")
    p_archive.add_argument("--interactive", action="store_true",
                           help="numbered table + prompt (default when no flags are given)")
    p_archive.add_argument("--no-headless", action="store_true", help="show the browser window")
    _add_logs_dir(p_archive)
    p_archive.set_defaults(func=cmd_archive)

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
