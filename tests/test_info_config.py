"""tilth info / tilth config — the read-only discovery surface.

These exercise the handlers directly (not just routing): the session table, the
single-session dossier with its worktree↔.git mapping, and the config view in
both the fully-configured and degraded states.
"""

from __future__ import annotations

import json

import pytest

from tilth import loop


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    """Point loop.SESSIONS_DIR at an empty tmp sessions dir, with a clean env."""
    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(loop, "SESSIONS_DIR", sdir)
    monkeypatch.setenv("TILTH_HOME", str(tmp_path))
    for var in ("TILTH_BASE_URL", "TILTH_API_KEY", "TILTH_WORKER_MODEL",
                "TILTH_EVALUATOR_MODEL", "TILTH_EVALUATOR_BASE_URL",
                "TILTH_EVALUATOR_API_KEY", "TILTH_ENV_FILE"):
        monkeypatch.delenv(var, raising=False)
    return sdir


def _write_session(sdir, sid, *, checkpoint=None, summary=None):
    d = sdir / sid
    d.mkdir()
    if checkpoint is not None:
        (d / "checkpoint.json").write_text(json.dumps(checkpoint))
    if summary is not None:
        (d / "summary.json").write_text(json.dumps(summary))
    return d


def test_info_list_empty(sessions, capsys):
    rc = loop.do_info_cmd(None)
    assert rc == 0
    out = capsys.readouterr().out
    assert "no sessions" in out


def test_info_list_shows_sessions_newest_first(sessions, capsys):
    _write_session(sessions, "20260101-000000-aaa",
                   checkpoint={"status": "all_done", "tokens_used": 100})
    _write_session(sessions, "20260202-000000-bbb",
                   checkpoint={"status": "running", "tokens_used": 200})
    rc = loop.do_info_cmd(None)
    assert rc == 0
    out = capsys.readouterr().out
    assert out.index("20260202-000000-bbb") < out.index("20260101-000000-aaa")
    assert "(latest)" in out
    # the latest tag sits on the newest id
    assert "20260202-000000-bbb (latest)" in out


def test_info_detail_missing_session(sessions, capsys):
    rc = loop.do_info_cmd("nope-123")
    assert rc == 2
    assert "no session at" in capsys.readouterr().out


def test_info_detail_shows_worktree_and_git_mapping(sessions, tmp_path, capsys):
    """A real linked worktree resolves its gitdir and reads as registered."""
    import subprocess

    src = tmp_path / "src"
    src.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=src, check=True,
                       capture_output=True, text=True)

    git("init", "-b", "main")
    git("config", "user.email", "t@t.t")
    git("config", "user.name", "t")
    (src / "f.txt").write_text("hi")
    git("add", "-A")
    git("commit", "-m", "initial")

    sid = "20260303-000000-ccc"
    wt = sessions / sid / "workspace"
    (sessions / sid).mkdir()
    subprocess.run(["git", "worktree", "add", str(wt), "-b", f"session/{sid}"],
                   cwd=src, check=True, capture_output=True, text=True)

    (sessions / sid / "checkpoint.json").write_text(json.dumps({
        "status": "running",
        "source": str(src),
        "workspace": str(wt),
        "branch": f"session/{sid}",
        "tokens_used": 42,
    }))

    rc = loop.do_info_cmd(sid)
    assert rc == 0
    out = capsys.readouterr().out
    assert "workspace" in out
    assert "gitdir" in out
    assert ".git/worktrees" in out
    assert "registered" in out
    assert f"session/{sid}" in out


def test_info_detail_flags_missing_worktree(sessions, capsys):
    sid = "20260404-000000-ddd"
    _write_session(sessions, sid, checkpoint={
        "status": "all_done",
        "workspace": str(sessions / sid / "workspace"),  # never created
        "branch": f"session/{sid}",
    })
    rc = loop.do_info_cmd(sid)
    assert rc == 0
    assert "missing" in capsys.readouterr().out


def test_config_degraded_flags_missing(sessions, capsys):
    rc = loop.do_config_cmd()
    assert rc == 1
    out = capsys.readouterr().out
    assert "Missing required configuration" in out
    assert "TILTH_API_KEY" in out


def test_config_full_masks_key(sessions, monkeypatch, capsys):
    monkeypatch.setenv("TILTH_BASE_URL", "https://x/v1")
    monkeypatch.setenv("TILTH_API_KEY", "sk-secret-tail")
    monkeypatch.setenv("TILTH_WORKER_MODEL", "vendor/model-x")
    rc = loop.do_config_cmd()
    assert rc == 0
    out = capsys.readouterr().out
    assert "vendor/model-x" in out
    assert "sk-secret-tail" not in out  # masked
    assert "tail" in out                # last-4 shown
    assert "limits" in out
