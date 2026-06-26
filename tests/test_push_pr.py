"""tilth push / tilth pr — getting a session branch out to a remote.

Exercises the handlers against a real local repo + bare 'origin' (offline), the
gh-present path (via a faked subprocess), the gh-absent URL fallback, and the
post-run "working with this session" summary block.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tilth import loop
from tilth import workspace as ws


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def published(tmp_path: Path, monkeypatch):
    """A wakeable session whose source repo has a bare 'origin' + a session worktree.

    Returns (sid, source, origin). `loop.SESSIONS_DIR` points at a tmp sessions
    dir holding a checkpoint.json the handlers wake from. Push works offline
    against the bare repo.
    """
    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(loop, "SESSIONS_DIR", sdir)

    src = tmp_path / "src"
    src.mkdir()
    _git("init", "-b", "main", cwd=src)
    _git("config", "user.email", "t@t.t", cwd=src)
    _git("config", "user.name", "t", cwd=src)
    (src / "f.txt").write_text("hi\n")
    _git("add", "-A", cwd=src)
    _git("commit", "-m", "initial", cwd=src)

    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git("init", "--bare", "-b", "main", cwd=origin)
    _git("remote", "add", "origin", str(origin), cwd=src)

    sid = "20260606-000000-eee"
    wt = sdir / sid / "workspace"
    (sdir / sid).mkdir()
    _git("worktree", "add", str(wt), "-b", f"session/{sid}", cwd=src)

    (sdir / sid / "checkpoint.json").write_text(json.dumps({
        "status": "all_done",
        "source": str(src),
        "workspace": str(wt),
        "branch": f"session/{sid}",
        "feature_dir": str(src / ".tilth" / "demo"),
    }))
    return sid, src, origin


# --- push -------------------------------------------------------------------


def test_push_pushes_branch_to_bare_origin(published, capsys) -> None:
    sid, src, _ = published
    rc = loop.do_push_cmd(sid)
    assert rc == 0
    assert "pushed" in capsys.readouterr().out
    assert ws.branch_on_remote(src, f"session/{sid}") is True


def test_push_no_remote_is_actionable(published, capsys) -> None:
    sid, src, _ = published
    subprocess.run(["git", "remote", "remove", "origin"], cwd=src, check=True, capture_output=True)
    rc = loop.do_push_cmd(sid)
    assert rc == 2
    out = capsys.readouterr().out
    assert "no 'origin' remote" in out
    assert "remote add origin" in out  # tells the user how to fix it


def test_push_no_sessions(tmp_path, monkeypatch, capsys) -> None:
    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(loop, "SESSIONS_DIR", sdir)
    rc = loop.do_push_cmd(None)
    assert rc == 2
    assert "no sessions found" in capsys.readouterr().out


# --- pr ---------------------------------------------------------------------


def test_pr_web_auto_pushes_then_falls_back(published, capsys) -> None:
    """--web skips gh; a non-GitHub (local) remote yields the host-UI fallback,
    and the branch is pushed first since it wasn't on the remote yet."""
    sid, src, _ = published
    rc = loop.do_pr_cmd(sid, web=True)
    assert rc == 0
    assert ws.branch_on_remote(src, f"session/{sid}") is True
    assert "is on origin" in capsys.readouterr().out


def test_pr_web_prints_github_compare_url(published, capsys, monkeypatch) -> None:
    """A GitHub remote + branch already on it → the compare URL, no push."""
    sid, _, _ = published
    monkeypatch.setattr(ws, "remote_url", lambda *a, **k: "git@github.com:AC/tilth.git")
    monkeypatch.setattr(ws, "branch_on_remote", lambda *a, **k: True)
    rc = loop.do_pr_cmd(sid, web=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert f"https://github.com/AC/tilth/compare/main...session/{sid}?expand=1" in out


def test_pr_falls_back_to_url_when_gh_missing(published, capsys, monkeypatch) -> None:
    """No --web, but gh is absent → still produce the compare URL."""
    sid, _, _ = published
    monkeypatch.setattr(ws, "remote_url", lambda *a, **k: "https://github.com/AC/tilth.git")
    monkeypatch.setattr(ws, "branch_on_remote", lambda *a, **k: True)
    monkeypatch.setattr(loop.shutil, "which", lambda _name: None)
    rc = loop.do_pr_cmd(sid)
    assert rc == 0
    assert f"compare/main...session/{sid}?expand=1" in capsys.readouterr().out


def test_pr_uses_gh_when_present(published, capsys, monkeypatch) -> None:
    """gh present → create the PR and surface its URL."""
    sid, _, _ = published
    monkeypatch.setattr(ws, "remote_url", lambda *a, **k: "git@github.com:AC/tilth.git")
    monkeypatch.setattr(ws, "branch_on_remote", lambda *a, **k: True)
    monkeypatch.setattr(loop.shutil, "which", lambda _name: "/usr/bin/gh")

    real_run = subprocess.run

    def fake_run(cmd, **kw):
        if cmd[:3] == ["gh", "pr", "view"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="no pull requests found")
        if cmd[:3] == ["gh", "pr", "create"]:
            return SimpleNamespace(
                returncode=0, stdout="https://github.com/AC/tilth/pull/7\n", stderr=""
            )
        return real_run(cmd, **kw)  # let real git calls (e.g. default-branch lookup) run

    monkeypatch.setattr(loop.subprocess, "run", fake_run)
    rc = loop.do_pr_cmd(sid)
    assert rc == 0
    assert "https://github.com/AC/tilth/pull/7" in capsys.readouterr().out


def test_pr_reports_existing_pr(published, capsys, monkeypatch) -> None:
    sid, _, _ = published
    monkeypatch.setattr(ws, "remote_url", lambda *a, **k: "git@github.com:AC/tilth.git")
    monkeypatch.setattr(ws, "branch_on_remote", lambda *a, **k: True)
    monkeypatch.setattr(loop.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(
        loop.subprocess, "run",
        lambda cmd, **kw: SimpleNamespace(
            returncode=0, stdout="https://github.com/AC/tilth/pull/3\n", stderr=""
        ),
    )
    rc = loop.do_pr_cmd(sid)
    assert rc == 0
    out = capsys.readouterr().out
    assert "already open" in out
    assert "pull/3" in out


# --- summary block ("working with this session") ----------------------------


def test_working_with_session_all_done_with_remote(capsys, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ws, "remote_url", lambda *a, **k: "git@github.com:AC/tilth.git")
    sess = SimpleNamespace(
        session_id="sid1",
        workspace=tmp_path / "workspace",
        branch="session/sid1",
        source=tmp_path,
    )
    loop._print_working_with_session(sess, {"done": 3, "pending": 0, "failed": 0})
    out = capsys.readouterr().out
    assert "working with this session" in out
    assert "git switch session/sid1" in out  # names the gotcha
    assert "cd " in out
    assert "tilth push sid1" in out
    assert "tilth pr sid1" in out


def test_working_with_session_no_remote_note(capsys, tmp_path) -> None:
    sess = SimpleNamespace(
        session_id="sid3",
        workspace=tmp_path / "workspace",
        branch="session/sid3",
        source=None,
    )
    loop._print_working_with_session(sess, {"done": 2, "pending": 0, "failed": 0})
    out = capsys.readouterr().out
    assert "no `origin` remote" in out
    assert "get the work to origin" not in out  # no command lines, just the note
    assert "tilth push sid3" not in out


def test_working_with_session_incomplete_points_to_resume(capsys, tmp_path) -> None:
    sess = SimpleNamespace(
        session_id="sid2",
        workspace=tmp_path / "workspace",
        branch="session/sid2",
        source=tmp_path,
    )
    loop._print_working_with_session(sess, {"done": 1, "pending": 1, "failed": 0})
    out = capsys.readouterr().out
    assert "tilth resume sid2" in out
    assert "tilth push" not in out
    assert "tilth pr" not in out
