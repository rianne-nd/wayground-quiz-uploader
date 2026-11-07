"""Command-line interface for wqu.

This is the ONLY module allowed to write to stdout/stderr directly or to
prompt the user. Everything in the rest of the package is library-safe
(no ``print``, no prompts) so a GUI can wrap it later without touching the
automation code.
"""

from __future__ import annotations

import argparse
import sys

from wqu import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wqu",
        description="Publish .xlsx quizzes to Wayground: upload, publish, assign, join link.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # Commands are registered as they are implemented (login/check/doctor are
    # added in step 3, publish in step 4, discover in step 3/5).
    sub.add_parser("help", help="show this help message")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "help"):
        parser.print_help(sys.stderr)
        return 0 if args.command == "help" else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
