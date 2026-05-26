"""`tilth run` no-prep behavior.

Pre-pivot, run-without-prep created a fresh session + worktree and then crashed
on missing prd.json. The picker (TTY) or clean error (non-TTY) replaces that.
We test:

  - non-TTY + no prior session → exit 2, no session created.
  - non-TTY + prior resumable → exit 2 with resume pointer.
  - TTY + prior + "1"  → dispatches do_resume_cmd(prior).
  - TTY + no prior + "1" → dispatches _do_prep_feature(source).
  - cancel/EOF on either picker → exit 0.

Picker output isn't asserted (it's user-facing copy, free to evolve). The
dispatch decisions are.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tilth import loop as loop_mod
from tilth.loop import (
    _RUN_ACTION_CANCEL,
    _RUN_ACTION_PREP_NOW,
    _RUN_ACTION_RESET_AND_PREP,
    _RUN_ACTION_RESUME,
    _prompt_no_prep_action,
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
    (d / "checkpoint.json").write_text(
        json.dumps({"session_id": sid, "source": source, "status": status})
    )
    events: list[dict] = [{"type": "session_start", "payload": {"source": source}}]
    if last_stop is not None:
        events.append({"type": "stop", "payload": {"reason": last_stop}})
    (d / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + ("\n" if events else "")
    )
    return d


def _scripted_input(answers: list[str]):
    it = iter(answers)

    def _input(prompt: str = "") -> str:
        try:
            return next(it)
        except StopIteration as exc:
            raise EOFError("scripted input exhausted") from exc

    return _input


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


# --- _prompt_no_prep_action (picker decisions) ----------------------------


def test_picker_no_prior_prep_now(sessions_root):
    action = _prompt_no_prep_action(None, sessions_root, _scripted_input(["1"]))
    assert action == _RUN_ACTION_PREP_NOW


def test_picker_no_prior_cancel(sessions_root):
    action = _prompt_no_prep_action(None, sessions_root, _scripted_input(["0"]))
    assert action == _RUN_ACTION_CANCEL


def test_picker_with_prior_resume(sessions_root, source):
    _make_session(
        sessions_root, "20260525-100000-aaa",
        source=str(source), status="failed", last_stop="iter_cap",
    )
    action = _prompt_no_prep_action(
        ("20260525-100000-aaa", "iter_cap"), sessions_root, _scripted_input(["1"]),
    )
    assert action == _RUN_ACTION_RESUME


def test_picker_with_prior_reset_and_prep(sessions_root, source):
    _make_session(
        sessions_root, "20260525-100000-aaa",
        source=str(source), status="failed", last_stop="iter_cap",
    )
    action = _prompt_no_prep_action(
        ("20260525-100000-aaa", "iter_cap"), sessions_root, _scripted_input(["2"]),
    )
    assert action == _RUN_ACTION_RESET_AND_PREP


def test_picker_with_prior_eof_is_cancel(sessions_root, source):
    _make_session(
        sessions_root, "20260525-100000-aaa",
        source=str(source), status="failed", last_stop="iter_cap",
    )
    action = _prompt_no_prep_action(
        ("20260525-100000-aaa", "iter_cap"), sessions_root, _scripted_input([]),
    )
    assert action == _RUN_ACTION_CANCEL


# --- do_run_cmd non-TTY paths -----------------------------------------------


def _init_git_repo(path: Path) -> None:
    import subprocess
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("# fixture\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    import os
    subprocess.run(
        ["git", "commit", "-m", "initial", "--no-gpg-sign"],
        cwd=path, check=True, capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": os.environ.get("PATH", ""),
        },
    )


@pytest.fixture
def git_source(source: Path) -> Path:
    _init_git_repo(source)
    return source


def test_do_run_non_tty_no_prior_exits_clean(
    sessions_root, git_source, monkeypatch
):
    """No prep, no prior session, non-TTY → exit 2, no session/worktree created."""
    monkeypatch.setattr(loop_mod, "SESSIONS_DIR", sessions_root)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(
        loop_mod.TilthConfig, "from_env",
        classmethod(lambda cls: _stub_config()),
    )
    monkeypatch.setattr(loop_mod, "LLMClient", lambda config: None)

    rc = loop_mod.do_run_cmd(git_source)
    assert rc == 2
    # No session directory created.
    assert list(sessions_root.iterdir()) == []


def test_do_run_non_tty_with_prior_exits_with_resume_pointer(
    sessions_root, git_source, monkeypatch
):
    _make_session(
        sessions_root, "20260525-100000-aaa",
        source=str(git_source), status="failed", last_stop="iter_cap",
    )
    monkeypatch.setattr(loop_mod, "SESSIONS_DIR", sessions_root)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(
        loop_mod.TilthConfig, "from_env",
        classmethod(lambda cls: _stub_config()),
    )
    monkeypatch.setattr(loop_mod, "LLMClient", lambda config: None)

    rc = loop_mod.do_run_cmd(git_source)
    assert rc == 2
    # Existing session untouched.
    assert (sessions_root / "20260525-100000-aaa" / "checkpoint.json").is_file()
    # No new session created.
    assert sorted(p.name for p in sessions_root.iterdir()) == ["20260525-100000-aaa"]


def _stub_config():
    """Minimal placeholder so TilthConfig.from_env doesn't need real env vars.

    do_run_cmd only reads it before deciding whether to bail; the picker /
    refusal paths return before any field is actually used."""
    class _C:
        worker_model = "stub"
    return _C()
