"""Login pre-flight: detect an expired session BEFORE any upload work.

Regression for the reported bug: with an expired stored session the run used
to spend ~40 s walking the create/import flow (the library pre-check swallowed
``SessionExpiredError``) before failing. Now the pipeline probes the login
first, and the CLI offers a browser sign-in and retries the batch.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from wqu import cli as cli_mod
from wqu import publish as P
from wqu.errors import SessionExpiredError
from wqu.output import FileResult
from wqu.publish import PipelineOptions
from wqu.session import Session

LINK = "https://wayground.com/join?gc=24923411&source=liveDashboard"


# ---------------------------------------------------------------------------
# Session probe
# ---------------------------------------------------------------------------


class _FakeLocator:
    def __init__(self, visible: bool) -> None:
        self._visible = visible

    @property
    def first(self) -> _FakeLocator:
        return self

    def or_(self, other: _FakeLocator) -> _FakeLocator:
        return self

    def is_visible(self) -> bool:
        return self._visible


class _FakePage:
    """Just enough of a Page for Session._probe()."""

    def __init__(self, url: str, *, marker: bool = False, login_cta: bool = False) -> None:
        self.url = url
        self._marker = _FakeLocator(marker)
        self._login = _FakeLocator(login_cta)
        self.waits = 0

    def goto(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        return None

    def get_by_text(self, *args, **kwargs) -> _FakeLocator:  # noqa: ANN002, ANN003
        return self._marker

    def get_by_role(self, *args, **kwargs) -> _FakeLocator:  # noqa: ANN002, ANN003
        return self._login

    def wait_for_timeout(self, ms: int) -> None:
        self.waits += 1


def test_probe_rejects_login_redirect_immediately(tmp_path: Path) -> None:
    page = _FakePage("https://wayground.com/login?redirect=%2Factivity")
    session = Session(state_path=tmp_path / "state.json")
    assert session.is_authenticated(page, timeout_ms=15_000) is False
    assert page.waits == 0  # detected from the URL, no polling/timeout burned


def test_probe_accepts_authenticated_marker(tmp_path: Path) -> None:
    page = _FakePage("https://wayground.com/activity/admin/assessment", marker=True)
    session = Session(state_path=tmp_path / "state.json")
    assert session.is_authenticated(page, timeout_ms=15_000) is True
    assert page.waits == 0


def test_probe_rejects_visible_login_call_to_action(tmp_path: Path) -> None:
    page = _FakePage("https://wayground.com/activity/admin/assessment", login_cta=True)
    session = Session(state_path=tmp_path / "state.json")
    assert session.is_authenticated(page, timeout_ms=1_000) is False


def test_probe_falls_back_to_admin_url_after_timeout(tmp_path: Path) -> None:
    page = _FakePage("https://wayground.com/activity/admin/assessment")
    session = Session(state_path=tmp_path / "state.json")
    assert session.is_authenticated(page, timeout_ms=1_000) is True
    assert page.waits >= 1  # it did poll before falling back


def test_probe_fast_path_does_not_wait_for_the_heading(tmp_path: Path) -> None:
    """The heading copy is not contractual: a protected URL is enough.

    Regression for the reported hang: after signing in, the browser reached
    /activity/admin/assessment but the exact heading was missing, so the probe
    silently polled for the full 30 s and the state was never saved.
    """
    page = _FakePage("https://wayground.com/activity/admin/assessment")
    session = Session(state_path=tmp_path / "state.json")
    assert session.is_authenticated(page, timeout_ms=30_000) is True
    assert page.waits <= 8  # ~1.5 s settle, not 30 s


def test_probe_treats_sign_in_call_to_action_as_signed_out(tmp_path: Path) -> None:
    page = _FakePage("https://wayground.com/activity/admin/assessment", login_cta=True)
    session = Session(state_path=tmp_path / "state.json")
    assert session.is_authenticated(page, timeout_ms=5_000) is False


# ---------------------------------------------------------------------------
# login() persists the state even when the probe is slow/flaky
# ---------------------------------------------------------------------------


class _FakeContext:
    def __init__(self, state_path: Path) -> None:
        self._state_path = state_path
        self.saved: list[str] = []

    def storage_state(self, path: str) -> None:
        self.saved.append(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("{}", encoding="utf-8")


class _FakeHandle:
    def __init__(self, page: _FakePage, state_path: Path) -> None:
        self.page = page
        self.context = _FakeContext(state_path)


def _fake_login_open(handle: _FakeHandle):  # noqa: ANN202
    @contextmanager
    def _open(self, **kwargs):  # noqa: ANN001, ANN202
        yield handle

    return _open


def test_login_saves_state_before_probing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state.json"
    page = _FakePage("https://wayground.com/explore/admin")
    handle = _FakeHandle(page, state)

    monkeypatch.setattr(Session, "open", _fake_login_open(handle))
    monkeypatch.setattr(Session, "_probe", lambda self, page, **kw: False)

    result = Session(state_path=state).login(wait_for_login=lambda: None)

    assert result.ok is False
    assert handle.context.saved == [str(state)]  # persisted despite the failed probe
    assert not state.exists()  # but an empty first-time state is cleaned up


def test_login_keeps_state_when_probe_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state.json"
    page = _FakePage("https://wayground.com/explore/admin")
    handle = _FakeHandle(page, state)

    monkeypatch.setattr(Session, "open", _fake_login_open(handle))
    monkeypatch.setattr(Session, "_probe", lambda self, page, **kw: True)

    result = Session(state_path=state).login(wait_for_login=lambda: None)

    assert result.ok is True
    assert state.is_file()


# ---------------------------------------------------------------------------
# publish_many pre-flight
# ---------------------------------------------------------------------------


@contextmanager
def _fake_open(self, **kwargs):  # noqa: ANN001, ANN202
    yield SimpleNamespace(page=object())


def test_publish_many_aborts_before_any_upload_when_logged_out(
    tmp_path: Path, fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    options = PipelineOptions(state_path=state, logs_dir=tmp_path / "logs")

    monkeypatch.setattr(Session, "open", _fake_open)
    monkeypatch.setattr(Session, "is_authenticated", lambda self, page, **kw: False)

    touched: list[str] = []
    monkeypatch.setattr(P.lib, "ensure_capacity",
                        lambda *a, **k: touched.append("library") or (0, 0))
    monkeypatch.setattr(P, "_process_one",
                        lambda *a, **k: touched.append("process") or None)

    with pytest.raises(SessionExpiredError):
        P.publish_many([fixtures_dir / "sample_single.xlsx"], options)

    assert touched == []  # no library scan, no create/upload attempt


def test_publish_many_does_not_swallow_library_session_expiry(
    tmp_path: Path, fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    options = PipelineOptions(state_path=state, logs_dir=tmp_path / "logs")

    monkeypatch.setattr(Session, "open", _fake_open)
    monkeypatch.setattr(Session, "is_authenticated", lambda self, page, **kw: True)

    def expired(*args, **kwargs):  # noqa: ANN002, ANN003
        raise SessionExpiredError("Session expired. Run 'wqu login' again.")

    monkeypatch.setattr(P.lib, "ensure_capacity", expired)
    monkeypatch.setattr(P, "_process_one",
                        lambda *a, **k: pytest.fail("must not process files"))

    with pytest.raises(SessionExpiredError):
        P.publish_many([fixtures_dir / "sample_single.xlsx"], options)


# ---------------------------------------------------------------------------
# CLI behaviour
# ---------------------------------------------------------------------------


def _save_state(tmp_path: Path) -> None:
    state = tmp_path / ".wayground" / "state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{}", encoding="utf-8")


def test_publish_with_expired_session_exits_two_without_prompting(
    tmp_path: Path, fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    _save_state(tmp_path)

    def expired(*args, **kwargs):  # noqa: ANN002, ANN003
        raise SessionExpiredError("Not signed in to Wayground — session expired.")

    monkeypatch.setattr(cli_mod, "publish_many", expired)
    monkeypatch.setattr(cli_mod, "_login_interactively",
                        lambda *a, **k: pytest.fail("must not open a browser without a TTY"))

    rc = cli_mod.main([
        "publish", str(fixtures_dir / "sample_single.xlsx"),
        "--logs-dir", str(tmp_path / "logs"),
    ])

    assert rc == 2
    err = capsys.readouterr().err
    assert "Not signed in to Wayground" in err


def test_publish_opens_login_and_retries_when_interactive(
    tmp_path: Path, fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    _save_state(tmp_path)

    monkeypatch.setattr(cli_mod, "_is_tty", lambda: True)
    logged_in: list[bool] = []

    def fake_login(session, ui=None):  # noqa: ANN001
        logged_in.append(True)
        return True

    monkeypatch.setattr(cli_mod, "_login_interactively", fake_login)

    attempts: list[int] = []

    def flaky_publish(*args, **kwargs):  # noqa: ANN002, ANN003
        attempts.append(1)
        if len(attempts) == 1:
            raise SessionExpiredError("Not signed in to Wayground — session expired.")
        return [FileResult(
            file=str(fixtures_dir / "sample_single.xlsx"), title="sample_single",
            quiz_url="https://wayground.com/activity/admin/quiz/abc",
            join_link=LINK, assigned=True,
        )]

    monkeypatch.setattr(cli_mod, "publish_many", flaky_publish)

    rc = cli_mod.main([
        "publish", str(fixtures_dir / "sample_single.xlsx"),
        "--logs-dir", str(tmp_path / "logs"),
    ])

    assert rc == 0
    assert logged_in == [True]
    assert len(attempts) == 2  # the batch was retried after signing in
    err = capsys.readouterr().err
    assert "Login saved" in err


def test_publish_without_any_state_offers_login_when_interactive(
    tmp_path: Path, fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    monkeypatch.setattr(cli_mod, "_is_tty", lambda: True)
    logged_in: list[bool] = []

    def fake_login(session, ui=None):  # noqa: ANN001
        logged_in.append(True)
        # emulate the login saving the state file
        session.state_path.parent.mkdir(parents=True, exist_ok=True)
        session.state_path.write_text("{}", encoding="utf-8")
        return True

    monkeypatch.setattr(cli_mod, "_login_interactively", fake_login)
    monkeypatch.setattr(cli_mod, "publish_many", lambda *a, **k: [])

    rc = cli_mod.main([
        "publish", str(fixtures_dir / "sample_single.xlsx"),
        "--logs-dir", str(tmp_path / "logs"),
    ])

    assert rc == 0
    assert logged_in == [True]
    err = capsys.readouterr().err
    assert "No saved Wayground session" in err
