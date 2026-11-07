"""Result formatting: human ``Label: URL`` block, JSON payload, summary, clipboard.

Output rules (brief Part 4, strictly enforced by the CLI):
  * everything here returns *strings*; only cli.py prints them;
  * the human block goes to stdout, the summary to stderr;
  * the human block is copied to the clipboard — never in --json/--dry-run/--quiet.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from dataclasses import asdict, dataclass

log = logging.getLogger(__name__)

NO_JOIN_LINK_MARKER = "(NO JOIN LINK — published but not assigned)"

JOIN_LINK_RE = re.compile(r"^https?://wayground\.com/join\?gc=\d+(?:&[\w%.~-]+=[\w%.~*-]*)*$")


@dataclass(frozen=True)
class FileResult:
    """Outcome for one workbook. ``error=None`` means the file succeeded."""

    file: str
    title: str
    quiz_url: str | None = None
    join_link: str | None = None
    assigned: bool = False
    error: str | None = None
    skipped: bool = False
    screenshot: str | None = None
    trace: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.skipped

    @property
    def has_join_link(self) -> bool:
        return bool(self.join_link)

    def line(self) -> str:
        """The stdout line for this file (human format)."""
        if self.skipped:
            return f"{self.title}: (skipped)"
        if self.join_link:
            return f"{self.title}: {self.join_link}"
        if self.quiz_url:
            return f"{self.title}: {self.quiz_url} {NO_JOIN_LINK_MARKER}"
        return f"{self.title}: (no quiz created)"

    def to_json(self) -> dict:
        return {
            "file": self.file,
            "title": self.title,
            "quiz_url": self.quiz_url,
            "join_link": self.join_link,
            "assigned": self.assigned,
            "error": self.error,
        }


def format_links(results: list[FileResult]) -> str:
    """The human stdout block: one line per succeeded (non-skipped) file."""
    return "\n".join(r.line() for r in results if r.ok)


def format_json(results: list[FileResult]) -> str:
    return json.dumps([r.to_json() for r in results], indent=2, ensure_ascii=False)


def format_summary(results: list[FileResult]) -> str:
    """The stderr per-file summary (brief Part 8)."""
    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok and not r.skipped]
    skipped = [r for r in results if r.skipped]
    head = f"Processed {len(results)} files: {len(ok)} success, {len(failed)} failure"
    if skipped:
        head += f", {len(skipped)} skipped"
    lines = [head]
    for r in results:
        if r.skipped:
            lines.append(f"  SKIP {r.title}  (skipped by user choice)")
        elif r.error is None:
            value = r.join_link or r.quiz_url or ""
            lines.append(f"  OK   {r.title}  {value}".rstrip())
        else:
            detail = r.error
            if r.screenshot:
                detail += f" — screenshot {r.screenshot}"
            lines.append(f"  FAIL {r.title}  {detail}")
    return "\n".join(lines)


def validate_join_link(url: str | None) -> str | None:
    """Return the link when it matches the Wayground join shape, else None."""
    if url and JOIN_LINK_RE.match(url.strip()):
        return url.strip()
    return None


def copy_to_clipboard(text: str) -> bool:
    """Copy ``text`` to the system clipboard; returns success. Never prints.

    Tries tkinter (unicode-safe on Windows), then platform tools. A GUI would
    replace this with its own clipboard handling.
    """
    if not text:
        return False
    try:
        import tkinter

        root = tkinter.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        root.destroy()
        return True
    except Exception as exc:
        log.debug("tkinter clipboard failed: %s", exc)
    try:
        if sys.platform == "win32":
            subprocess.run("clip", input=text.encode("utf-16le"), check=True, shell=False)
            return True
        if sys.platform == "darwin":
            subprocess.run("pbcopy", input=text.encode("utf-8"), check=True, shell=False)
            return True
        for tool in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard"]):
            try:
                subprocess.run(tool, input=text.encode("utf-8"), check=True, shell=False)
                return True
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
    except Exception as exc:
        log.debug("clipboard tool failed: %s", exc)
    return False
