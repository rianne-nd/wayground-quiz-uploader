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
from dataclasses import dataclass

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
    error_kind: str | None = None  # e.g. "session-expired", "class-not-found" (not in JSON schema)
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


_SPLIT_RE = re.compile(r"^(?P<base>.+?)_pt(?P<n>\d+)\.xlsx$", re.IGNORECASE)


def natural_sort_key(item: str) -> tuple:
    """Natural ordering for workbooks: _pt1, _pt2, ..., _pt10 (not alphabetic).

    Whole quizzes sort before their split parts; digit runs compare as ints.
    """
    name = str(item).replace("\\", "/").split("/")[-1].lower()
    split = _SPLIT_RE.match(name)
    if split:
        base = split.group("base")
    else:
        base = re.sub(r"\.xlsx$", "", name)
    chunks = re.split(r"(\d+)", base)
    key: list = []
    for i, chunk in enumerate(chunks):
        key.append(int(chunk) if i % 2 == 1 else chunk)
    if split:
        return (tuple(key), 1, int(split.group("n")))
    return (tuple(key), 0, 0)


def _run_clip_tool(cmd: list[str], text: str, *, as_text: bool) -> bool:
    try:
        if as_text:
            subprocess.run(cmd, input=text, check=True, shell=False, text=True, timeout=10)
        else:
            subprocess.run(cmd, input=text.encode("utf-16-le"), check=True, shell=False, timeout=10)
        return True
    except Exception as exc:
        log.debug("clipboard tool %r failed: %s", cmd, exc)
        return False


def copy_to_clipboard(text: str) -> bool:
    """Copy ``text`` to the system clipboard; returns success. Never prints.

    LIVE 2026-11-09: tkinter reported success but the text did not survive
    process exit on Windows, so the native ``clip.exe`` is used first:
    UTF-16LE bytes (unicode-safe, the classic clip.exe recipe) and then the
    plain ``text=True`` form as a fallback. pbcopy/xclip cover the other
    platforms; tkinter remains a last resort only.
    """
    if not text:
        return False
    if sys.platform == "win32":
        if _run_clip_tool(["clip"], text, as_text=False):
            return True
        if _run_clip_tool(["clip"], text, as_text=True):
            return True
    elif sys.platform == "darwin":
        if _run_clip_tool(["pbcopy"], text, as_text=False):
            return True
    else:
        for tool in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard"]):
            if _run_clip_tool(tool, text, as_text=False):
                return True
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
    return False
