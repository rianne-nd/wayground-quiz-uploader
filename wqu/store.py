"""Persistent record of quizzes created by wqu.

Stored beside the session at ``~/.wayground/created.json`` so a later run can
detect duplicates or reconnect to a draft (Part 9 of the brief). Plain JSON,
read/write per call, no module-level state.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)


def default_state_path() -> Path:
    """Where the browser storage state lives (~/.wayground/state.json)."""
    return Path.home() / ".wayground" / "state.json"


def created_path(state_path: Path) -> Path:
    return Path(state_path).parent / "created.json"


@dataclass(frozen=True)
class CreatedEntry:
    """One quiz this tool has seen. ``status``: created | published | assigned."""

    title: str
    quiz_id: str
    quiz_url: str
    file: str
    status: str
    join_link: str | None
    updated_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def load_created(state_path: Path) -> list[CreatedEntry]:
    path = created_path(state_path)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("could not read %s; treating as empty", path)
        return []
    entries: list[CreatedEntry] = []
    for item in raw.get("entries", []):
        try:
            entries.append(CreatedEntry(**item))
        except TypeError:
            log.warning("skipping malformed created.json entry: %r", item)
    return entries


def find_by_title(state_path: Path, title: str) -> list[CreatedEntry]:
    """Newest first."""
    matches = [e for e in load_created(state_path) if e.title.lower() == title.lower()]
    return sorted(matches, key=lambda e: e.updated_at, reverse=True)


def record_created(
    state_path: Path,
    *,
    title: str,
    quiz_id: str,
    quiz_url: str,
    file: str,
    status: str,
    join_link: str | None = None,
) -> None:
    """Insert or update the entry for ``quiz_id``."""
    state_path = Path(state_path)
    entries = {e.quiz_id: e for e in load_created(state_path)}
    entries[quiz_id] = CreatedEntry(
        title=title,
        quiz_id=quiz_id,
        quiz_url=quiz_url,
        file=file,
        status=status,
        join_link=join_link,
        updated_at=_now(),
    )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"entries": [asdict(e) for e in entries.values()]}
    try:
        state_path.parent.joinpath("created.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        log.warning("could not write created.json next to %s", state_path)
