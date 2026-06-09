"""`tilth run` fails fast (no session/worktree created) when the feature is
missing or malformed, and prints the templates so the user can author one.

This replaces the old "no prepared session" picker flow — there is no prep step
anymore; `run` reads <workspace>/.tilth/tasks/ directly.
"""

from __future__ import annotations

import pytest

from tilth import loop


@pytest.fixture(autouse=True)
def _no_git(monkeypatch):
    """The fail-fast happens after the git check; stub it so the test doesn't
    need a real repo."""
    monkeypatch.setattr(loop.ws, "ensure_git_repo", lambda *_a, **_k: None)


@pytest.fixture(autouse=True)
def _isolate_sessions(monkeypatch, tmp_path):
    """Guard: if the code ever got past fail-fast it would create a session —
    point SESSIONS_DIR at a tmp dir so a regression can't litter the repo."""
    monkeypatch.setattr(loop, "SESSIONS_DIR", tmp_path / "sessions")


def test_run_fails_fast_when_no_tasks_dir(tmp_path, capsys):
    ws = tmp_path / "repo"
    ws.mkdir()
    rc = loop.do_run_cmd(ws)
    assert rc == 2
    out = capsys.readouterr().out
    assert "overview.md" in out  # the required-file message + template
    # no session was created
    assert not (loop.SESSIONS_DIR).exists() or not any(loop.SESSIONS_DIR.iterdir())


def test_run_fails_fast_when_overview_missing(tmp_path, capsys):
    ws = tmp_path / "repo"
    (ws / ".tilth" / "tasks").mkdir(parents=True)
    (ws / ".tilth" / "tasks" / "T-001-x.md").write_text(
        "---\nid: T-001\ntitle: x\n---\n\n## Description\nbuild x\n"
    )
    rc = loop.do_run_cmd(ws)
    assert rc == 2
    out = capsys.readouterr().out
    assert "overview.md" in out
