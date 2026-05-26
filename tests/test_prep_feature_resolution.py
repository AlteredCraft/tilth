"""Session-resolution helpers used by `tilth --prep-feature` and bare `tilth <ws>`.

These two helpers gate the prep-feature flow:
  - _find_blocking_sessions     — refuse re-prep when an in-flight session exists
  - _find_prepared_sessions     — pick up a prepared session on bare invocation
plus _find_resumable_session, which must now skip prepared sessions so the
"heads up: resumable" warning doesn't fire for them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tilth.loop import (
    _PREP_ACTION_CANCEL,
    _PREP_ACTION_PREP_FRESH,
    _PREP_ACTION_RESET_AND_PREP,
    _PREP_ACTION_RESUME,
    _PREP_ACTION_RUN,
    _find_blocking_sessions,
    _find_prepared_sessions,
    _find_resumable_session,
    _prompt_blocking_action,
)


def _make_session(
    sessions_root: Path,
    sid: str,
    *,
    source: str,
    status: str,
    last_stop: str | None = None,
) -> Path:
    d = sessions_root / sid
    d.mkdir(parents=True)
    cp = {"session_id": sid, "source": source, "status": status}
    (d / "checkpoint.json").write_text(json.dumps(cp))
    events: list[dict] = [{"type": "session_start", "payload": {"source": source}}]
    if last_stop is not None:
        events.append({"type": "stop", "payload": {"reason": last_stop}})
    (d / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + ("\n" if events else "")
    )
    return d


@pytest.fixture
def sessions_root(tmp_path: Path) -> Path:
    r = tmp_path / "sessions"
    r.mkdir()
    return r


@pytest.fixture
def source(tmp_path: Path) -> Path:
    s = tmp_path / "project"
    s.mkdir()
    return s


# --- _find_prepared_sessions ------------------------------------------------

def test_prepared_sessions_empty_when_none_match(sessions_root, source):
    assert _find_prepared_sessions(sessions_root, source) == []


def test_prepared_sessions_returns_single_match(sessions_root, source):
    _make_session(sessions_root, "20260525-100000-aaa", source=str(source), status="prepared")
    assert _find_prepared_sessions(sessions_root, source) == ["20260525-100000-aaa"]


def test_prepared_sessions_returns_all_matches_sorted(sessions_root, source):
    _make_session(sessions_root, "20260525-100000-bbb", source=str(source), status="prepared")
    _make_session(sessions_root, "20260525-090000-aaa", source=str(source), status="prepared")
    assert _find_prepared_sessions(sessions_root, source) == [
        "20260525-090000-aaa",
        "20260525-100000-bbb",
    ]


def test_prepared_sessions_ignores_other_workspaces(sessions_root, source, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    _make_session(sessions_root, "20260525-100000-aaa", source=str(other), status="prepared")
    assert _find_prepared_sessions(sessions_root, source) == []


@pytest.mark.parametrize("status", ["running", "all_done", "failed"])
def test_prepared_sessions_ignores_non_prepared_statuses(sessions_root, source, status):
    _make_session(sessions_root, "20260525-100000-aaa", source=str(source), status=status)
    assert _find_prepared_sessions(sessions_root, source) == []


# --- _find_blocking_sessions ------------------------------------------------

def test_blocking_empty_when_no_sessions(sessions_root, source):
    assert _find_blocking_sessions(sessions_root, source) == []


@pytest.mark.parametrize("status", ["prepared", "running", "failed"])
def test_blocking_includes_each_in_flight_status(sessions_root, source, status):
    _make_session(sessions_root, f"20260525-100000-{status[:3]}", source=str(source), status=status)
    out = _find_blocking_sessions(sessions_root, source)
    assert [s[1] for s in out] == [status]


def test_blocking_excludes_all_done(sessions_root, source):
    _make_session(sessions_root, "20260525-100000-aaa", source=str(source), status="all_done")
    assert _find_blocking_sessions(sessions_root, source) == []


def test_blocking_excludes_other_workspaces(sessions_root, source, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    _make_session(sessions_root, "20260525-100000-aaa", source=str(other), status="prepared")
    assert _find_blocking_sessions(sessions_root, source) == []


# --- _find_resumable_session interaction ------------------------------------

def test_resumable_skips_prepared_sessions(sessions_root, source):
    """Prepared sessions are picked up by the bare-workspace flow, not by
    --resume. The 'heads up: resumable' warning must NOT fire for them."""
    _make_session(sessions_root, "20260525-100000-aaa", source=str(source), status="prepared")
    assert _find_resumable_session(sessions_root, source) is None


def test_resumable_still_finds_running_with_failure_stop(sessions_root, source):
    _make_session(
        sessions_root,
        "20260525-100000-aaa",
        source=str(source),
        status="failed",
        last_stop="iter_cap",
    )
    out = _find_resumable_session(sessions_root, source)
    assert out == ("20260525-100000-aaa", "iter_cap")


# --- _prompt_blocking_action (picker) --------------------------------------


def _scripted_input(answers: list[str]):
    """Returns a prompt_func that yields each canned answer in turn."""
    it = iter(answers)

    def _input(prompt: str = "") -> str:
        try:
            return next(it)
        except StopIteration as exc:
            raise EOFError("scripted input exhausted") from exc

    return _input


def test_picker_one_prepared_choose_run(sessions_root, source):
    _make_session(
        sessions_root, "20260525-100000-aaa", source=str(source), status="prepared"
    )
    blocking = _find_blocking_sessions(sessions_root, source)
    action = _prompt_blocking_action(blocking, sessions_root, _scripted_input(["1"]))
    assert action == _PREP_ACTION_RUN


def test_picker_one_prepared_choose_reset_and_prep(sessions_root, source):
    _make_session(
        sessions_root, "20260525-100000-aaa", source=str(source), status="prepared"
    )
    blocking = _find_blocking_sessions(sessions_root, source)
    action = _prompt_blocking_action(blocking, sessions_root, _scripted_input(["2"]))
    assert action == _PREP_ACTION_RESET_AND_PREP


def test_picker_one_failed_choose_resume(sessions_root, source):
    _make_session(
        sessions_root, "20260525-100000-aaa", source=str(source), status="failed"
    )
    blocking = _find_blocking_sessions(sessions_root, source)
    action = _prompt_blocking_action(blocking, sessions_root, _scripted_input(["1"]))
    assert action == _PREP_ACTION_RESUME


def test_picker_cancel(sessions_root, source):
    _make_session(
        sessions_root, "20260525-100000-aaa", source=str(source), status="prepared"
    )
    blocking = _find_blocking_sessions(sessions_root, source)
    action = _prompt_blocking_action(blocking, sessions_root, _scripted_input(["0"]))
    assert action == _PREP_ACTION_CANCEL


def test_picker_eof_treated_as_cancel(sessions_root, source):
    _make_session(
        sessions_root, "20260525-100000-aaa", source=str(source), status="prepared"
    )
    blocking = _find_blocking_sessions(sessions_root, source)
    action = _prompt_blocking_action(blocking, sessions_root, _scripted_input([]))
    assert action == _PREP_ACTION_CANCEL


def test_picker_invalid_then_valid(sessions_root, source):
    _make_session(
        sessions_root, "20260525-100000-aaa", source=str(source), status="prepared"
    )
    blocking = _find_blocking_sessions(sessions_root, source)
    action = _prompt_blocking_action(
        blocking, sessions_root, _scripted_input(["7", "abc", "2"])
    )
    assert action == _PREP_ACTION_RESET_AND_PREP


def test_picker_multi_blocking_only_offers_reset(sessions_root, source):
    _make_session(
        sessions_root, "20260525-100000-aaa", source=str(source), status="prepared"
    )
    _make_session(
        sessions_root, "20260525-100000-bbb", source=str(source), status="failed"
    )
    blocking = _find_blocking_sessions(sessions_root, source)
    assert len(blocking) == 2
    action = _prompt_blocking_action(blocking, sessions_root, _scripted_input(["1"]))
    assert action == _PREP_ACTION_RESET_AND_PREP


# --- _do_prep_feature non-TTY refusal (regression guard) ------------------


def test_do_prep_feature_non_tty_with_blocking_refuses(
    sessions_root, source, monkeypatch
):
    """CI / non-interactive callers must still see the refuse-and-hint message
    and exit 2, not get a TTY prompt or have state quietly mutated."""
    from tilth import loop as loop_mod

    _make_session(
        sessions_root, "20260525-100000-aaa", source=str(source), status="prepared"
    )
    monkeypatch.setattr(loop_mod, "SESSIONS_DIR", sessions_root)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    rc = loop_mod._do_prep_feature(source, brief="x")
    assert rc == 2
    # Session is untouched.
    assert (sessions_root / "20260525-100000-aaa" / "checkpoint.json").is_file()


def test_do_prep_feature_force_resets_blocking(
    sessions_root, source, monkeypatch
):
    """--force discards blocking sessions and proceeds without prompting."""
    from tilth import loop as loop_mod

    _make_session(
        sessions_root, "20260525-100000-aaa", source=str(source), status="prepared"
    )
    monkeypatch.setattr(loop_mod, "SESSIONS_DIR", sessions_root)

    reset_calls: list[tuple[str, bool]] = []

    def fake_reset(sid: str, *, assume_yes: bool) -> int:
        reset_calls.append((sid, assume_yes))
        return 0

    monkeypatch.setattr(loop_mod, "_do_reset", fake_reset)
    # Short-circuit before LLM config: raise from TilthConfig.from_env so the
    # test doesn't need real env vars.
    monkeypatch.setattr(
        loop_mod.TilthConfig, "from_env",
        classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("stop here"))),
    )

    with pytest.raises(RuntimeError, match="stop here"):
        loop_mod._do_prep_feature(source, brief="x", force=True)

    assert reset_calls == [("20260525-100000-aaa", True)]


# --- prep-fresh option (start a new session, keep existing) ----------------


def test_picker_single_prepared_choose_prep_fresh(sessions_root, source):
    _make_session(
        sessions_root, "20260525-100000-aaa", source=str(source), status="prepared"
    )
    blocking = _find_blocking_sessions(sessions_root, source)
    action = _prompt_blocking_action(blocking, sessions_root, _scripted_input(["3"]))
    assert action == _PREP_ACTION_PREP_FRESH


def test_picker_single_failed_choose_prep_fresh(sessions_root, source):
    _make_session(
        sessions_root, "20260525-100000-aaa", source=str(source), status="failed"
    )
    blocking = _find_blocking_sessions(sessions_root, source)
    action = _prompt_blocking_action(blocking, sessions_root, _scripted_input(["3"]))
    assert action == _PREP_ACTION_PREP_FRESH


def test_picker_multi_blocking_choose_prep_fresh(sessions_root, source):
    _make_session(
        sessions_root, "20260525-100000-aaa", source=str(source), status="prepared"
    )
    _make_session(
        sessions_root, "20260525-100000-bbb", source=str(source), status="failed"
    )
    blocking = _find_blocking_sessions(sessions_root, source)
    assert len(blocking) == 2
    action = _prompt_blocking_action(blocking, sessions_root, _scripted_input(["2"]))
    assert action == _PREP_ACTION_PREP_FRESH


def test_do_prep_feature_keep_existing_skips_reset(
    sessions_root, source, monkeypatch
):
    """--keep-existing leaves blockers alone and proceeds with a fresh prep."""
    from tilth import loop as loop_mod

    _make_session(
        sessions_root, "20260525-100000-aaa", source=str(source), status="prepared"
    )
    monkeypatch.setattr(loop_mod, "SESSIONS_DIR", sessions_root)

    reset_calls: list[str] = []
    monkeypatch.setattr(
        loop_mod, "_do_reset",
        lambda sid, *, assume_yes=False: reset_calls.append(sid) or 0,
    )
    monkeypatch.setattr(
        loop_mod.TilthConfig, "from_env",
        classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("stop here"))),
    )

    with pytest.raises(RuntimeError, match="stop here"):
        loop_mod._do_prep_feature(source, brief="x", keep_existing=True)

    # Existing session was NOT reset.
    assert reset_calls == []
    assert (sessions_root / "20260525-100000-aaa" / "checkpoint.json").is_file()


def test_do_prep_feature_force_and_keep_existing_rejected(
    sessions_root, source, monkeypatch
):
    """The two flags are mutually exclusive."""
    from tilth import loop as loop_mod
    monkeypatch.setattr(loop_mod, "SESSIONS_DIR", sessions_root)
    rc = loop_mod._do_prep_feature(
        source, brief="x", force=True, keep_existing=True
    )
    assert rc == 2
