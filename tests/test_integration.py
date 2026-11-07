"""Live integration tests — opt-in only.

These publish REAL quizzes to the Wayground account behind the saved session.
Run them with:

    set WQU_INTEGRATION=1        (PowerShell: $env:WQU_INTEGRATION="1")
    pytest -m integration

They are skipped by default and never run in CI. Every artifact they create is
named TEST_WQU_* so it can be found and deleted manually afterwards.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wqu.core import PipelineOptions, Session, publish_one

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("WQU_INTEGRATION") != "1",
        reason="live tests are opt-in: set WQU_INTEGRATION=1 (requires 'wqu login')",
    ),
]


@pytest.fixture(scope="module")
def live_session() -> Session:
    session = Session()
    if not session.exists():
        pytest.skip("no saved session; run 'wqu login' first")
    return session


def test_live_check(fixtures_dir: Path, tmp_path: Path) -> None:
    session = Session()
    if not session.exists():
        pytest.skip("no saved session")
    result = session.check()
    assert result.ok, result.detail


def test_live_publish_no_assign(fixtures_dir: Path, tmp_path: Path, live_session: Session) -> None:
    options = PipelineOptions(
        keep_draft=True,  # publish, skip assignment
        headless=True,
        title="TEST_WQU_NO_ASSIGN",
        logs_dir=tmp_path / "logs",
    )
    result = publish_one(fixtures_dir / "sample_single.xlsx", options, session=live_session)
    assert result.error is None, result.error
    assert result.quiz_url and "/activity/admin/quiz/" in result.quiz_url


def test_live_publish_and_assign(fixtures_dir: Path, tmp_path: Path, live_session: Session) -> None:
    from wqu.output import validate_join_link

    options = PipelineOptions(
        assign_class="Naic",
        headless=True,
        title="TEST_WQU_ASSIGN",
        logs_dir=tmp_path / "logs",
    )
    result = publish_one(fixtures_dir / "sample_single.xlsx", options, session=live_session)
    assert result.error is None, result.error
    assert result.assigned is True
    assert validate_join_link(result.join_link), f"bad join link: {result.join_link!r}"
