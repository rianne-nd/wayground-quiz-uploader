"""Logging configuration for wqu.

Every run writes ``<logs_dir>/<timestamp>-<command>.log`` (default
``./wqu-logs/``) and old logs are rotated to a maximum of ``MAX_LOG_FILES``.

This module only configures the stdlib logging tree; it never prints. All
user-facing output stays in ``wqu.cli``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

MAX_LOG_FILES = 50


def run_stamp() -> str:
    """Timestamp used in log/artifact filenames (Windows-safe, no colons)."""
    return datetime.now().strftime("%Y%m%dT%H%M%S")


def setup_logging(command: str, logs_dir: Path | str = Path("wqu-logs")) -> Path:
    """Configure file logging for one run; return the log file path.

    Replaces any handlers previously installed by this function so repeated
    calls in one process (tests, REPL) do not duplicate log lines.
    """
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / f"{run_stamp()}-{command}.log"

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(logging.DEBUG)

    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root.addHandler(file_handler)

    _rotate(logs_dir)
    return path


def _rotate(logs_dir: Path) -> None:
    """Keep only the newest MAX_LOG_FILES log files."""
    logs = sorted(logs_dir.glob("*.log"), key=lambda p: p.name)
    for stale in logs[:-MAX_LOG_FILES]:
        try:
            stale.unlink()
        except OSError:
            # A locked/undeletable old log must never fail the run.
            pass
