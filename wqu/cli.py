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
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

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
from wqu.logging_setup import setup_logging
from wqu.publish import publish_many
from wqu.store import default_state_path

DEFAULT_CLASS = "Naic"  # the interactive branch answer "y" assigns to this class

console = Console(file=sys.stderr, soft_wrap=True)
out = Console(file=sys.stdout, soft_wrap=True)


def err(message: str = "") -> None:
    print(message, file=sys.stderr)


def progress(message: str) -> None:
    err(message)


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
    if args.keep_draft or args.no_assign:
        return None
    if args.assign:
        return lambda view: args.assign
    if args.yes:
        err(f"--yes: defaulting to no assignment (use --assign {DEFAULT_CLASS} to assign).")
        return lambda view: None
    if not _is_tty():
        err("stdin is not interactive; defaulting to no assignment "
            f"(pass --assign {DEFAULT_CLASS} or --no-assign to silence this).")
        return None

    def decide(view: FileView) -> str | None:
        if args.interactive_review and view.quiz_url:
            err(f"      Published: {view.quiz_url}")
            _prompt_line("      Press Enter to continue to the assignment step... ")
        note = " (split part — default N)" if view.is_split else ""
        answer = _prompt_line(f'      Assign to class "{DEFAULT_CLASS}"? [y/N]:{note} ')
        if answer is not None and answer.strip().lower() in ("y", "yes"):
            return DEFAULT_CLASS
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
        state_path=default_state_path(),
        logs_dir=logs_dir,
    )


def cmd_publish(args: argparse.Namespace) -> int:
    log_path = setup_logging("publish", args.logs_dir)
    err(f"Log: {log_path}")
    try:
        files = collect_files(args.paths or [], args.from_toolkit_root)
    except WquError as exc:
        err(str(exc))
        return 2
    if not files:
        err("no .xlsx files found to process.")
        return 2
    if args.title and len(files) > 1:
        err("--title can only be used with exactly one input file.")
        return 2

    options = build_options(args, Path(args.logs_dir))

    if args.dry_run:
        return run_dry_run(files, options)

    session = Session(state_path=options.state_path, logs_dir=options.logs_dir)
    if not session.exists():
        err(f"No saved session at {options.state_path}. Run 'wqu login' first.")
        return 2

    err(f"Processing {len(files)} file(s){' (headless)' if options.headless else ''}...")
    try:
        results = publish_many(
            files,
            options,
            session=session,
            on_progress=progress,
            decide_assign=make_decide_assign(args),
            resolve_duplicate=make_resolve_duplicate(args),
        )
    except SessionExpiredError as exc:
        err(str(exc))
        return 2
    return finish_publish(args, results)


def finish_publish(args: argparse.Namespace, results: list[FileResult]) -> int:
    any_error = any(r.error is not None for r in results)
    if args.json:
        print(format_json(results))
    else:
        block = format_links(results)
        if block:
            print(block)
        if not args.quiet:
            err(format_summary(results))
        elif any_error:
            err(format_summary(results))  # --quiet still surfaces failures
        if not args.quiet and block:
            if copy_to_clipboard(block):
                err("Copied the result block to the clipboard.")
            else:
                err("WARNING: could not copy to the clipboard.")
    if any(r.error_kind == "session-expired" for r in results):
        err("Run 'wqu login' again.")
        return 2
    if any(r.error_kind == "class-not-found" for r in results):
        return 2
    return 1 if any_error else 0


# ---------------------------------------------------------------------------
# other commands
# ---------------------------------------------------------------------------


def cmd_login(args: argparse.Namespace) -> int:
    log_path = setup_logging("login", args.logs_dir)
    err(f"Log: {log_path}")
    session = Session(logs_dir=args.logs_dir)

    def wait_for_login() -> None:
        err("Sign in to Wayground in the opened browser window.")
        _prompt_line("Press Enter here when you are done... ")

    result = session.login(
        headless=False,
        wait_for_login=wait_for_login if _is_tty() else None,
        on_progress=progress,
    )
    if result.ok:
        err(f"[OK] {result.detail}")
        return 0
    err(f"[FAIL] {result.detail}")
    return 2


def cmd_check(args: argparse.Namespace) -> int:
    log_path = setup_logging("check", args.logs_dir)
    err(f"Log: {log_path}")
    session = Session(logs_dir=args.logs_dir)
    result = session.check(headless=True, on_progress=progress)
    if result.ok:
        err(f"[OK] {result.detail}")
        return 0
    err(f"[EXPIRED] {result.detail}")
    return 2


def cmd_doctor(args: argparse.Namespace) -> int:
    checks = Session(logs_dir=args.logs_dir).doctor()
    failed = 0
    for check in checks:
        if check.ok:
            err(f"[OK]   {check.name} — {check.detail}")
        else:
            failed += 1
            hint = f" (hint: {check.hint})" if check.hint else ""
            err(f"[FAIL] {check.name} — {check.detail}{hint}")
    if failed:
        err(f"{failed} of {len(checks)} checks failed.")
        return 1
    err(f"All {len(checks)} checks passed.")
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    log_path = setup_logging("discover", args.logs_dir)
    err(f"Log: {log_path}")
    session = Session(logs_dir=args.logs_dir)

    def wait_for_user() -> None:
        try:
            input("Interact now. Press Ctrl+C when you are done.\n")
        except (KeyboardInterrupt, EOFError):
            err("")

    if not session.exists():
        err(f"No saved session at {session.state_path}. Run 'wqu login' first.")
        return 2

    from wqu.session import discover  # local import keeps --version startup fast

    result = discover(args.url, session=session, snaps_dir=Path("snaps"),
                      wait_for_user=wait_for_user, on_progress=progress)
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

    p_pub = sub.add_parser("publish", help="files or folders; full pipeline")
    p_pub.add_argument("paths", nargs="*", help="one or more .xlsx files and/or folders")
    p_pub.add_argument("--subject", default="Information Technology (IT)",
                       help='subject for the publish modal (default: "Information Technology (IT)")')
    p_pub.add_argument("--grade", default="University", help="grade for the publish modal")
    p_pub.add_argument("--title", default=None, help="quiz title override (single file only)")
    p_pub.add_argument("--assign", metavar="CLASS", default=None,
                       help="force-assign to this class name (no prompt)")
    p_pub.add_argument("--no-assign", action="store_true",
                       help="never assign; also suppresses the interactive prompt")
    p_pub.add_argument("--no-deadline", dest="no_deadline", action="store_true", default=True,
                       help="set 'No deadline' on the assignment (default; accepted for symmetry)")
    p_pub.add_argument("--deadline", dest="no_deadline", action="store_false",
                       help="reserved for future use: keep the platform's default deadline")
    p_pub.add_argument("--no-headless", action="store_true", help="show the browser window")
    p_pub.add_argument("--dry-run", action="store_true", help="validate only; no browser")
    p_pub.add_argument("--json", action="store_true", help="machine-readable output on stdout")
    p_pub.add_argument("--quiet", action="store_true", help="only the Label: URL lines (stdout)")
    p_pub.add_argument("--keep-draft", action="store_true",
                       help="publish but skip assignment entirely")
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
