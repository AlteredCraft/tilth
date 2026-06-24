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


def _row(out: str, name: str) -> str:
    """The output line for env var `name` (rows are not wrapped at these widths)."""
    return next(ln for ln in out.splitlines() if ln.strip().startswith(name))


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("TILTH_BASE_URL", "https://x/v1")
    monkeypatch.setenv("TILTH_API_KEY", "sk-secret-tail")
    monkeypatch.setenv("TILTH_WORKER_MODEL", "vendor/model-x")
    for var in ("TILTH_EVALUATOR_MODEL", "TILTH_EVALUATOR_BASE_URL",
                "TILTH_EVALUATOR_API_KEY", "TILTH_MAX_ITERATIONS_PER_TASK",
                "MAX_EVALUATOR_CALLS_PER_TASK", "TILTH_MAX_WALL_CLOCK_MINUTES",
                "TILTH_MAX_TOKEN_DOLLAR_SPEND", "TILTH_CONTEXT_FILES"):
        monkeypatch.delenv(var, raising=False)


def test_config_uses_real_var_names(sessions, configured, capsys):
    assert loop.do_config_cmd() == 0
    out = capsys.readouterr().out
    for name in ("TILTH_BASE_URL", "TILTH_WORKER_MODEL", "TILTH_API_KEY",
                 "TILTH_EVALUATOR_MODEL", "TILTH_MAX_ITERATIONS_PER_TASK",
                 "MAX_EVALUATOR_CALLS_PER_TASK", "TILTH_CONTEXT_FILES"):
        assert name in out


def test_config_source_environment_and_default(sessions, configured, capsys):
    # required vars come from the process env; no .env file exists in tmp home
    assert loop.do_config_cmd() == 0
    out = capsys.readouterr().out
    assert "environment" in _row(out, "TILTH_BASE_URL")
    # an unset cap falls back to its built-in default, sourced as such
    assert "default" in _row(out, "TILTH_MAX_ITERATIONS_PER_TASK")
    assert "32" in _row(out, "TILTH_MAX_ITERATIONS_PER_TASK")


def test_config_source_dotenv_file(sessions, tmp_path, monkeypatch, capsys):
    envfile = tmp_path / "myenv"
    envfile.write_text(
        "TILTH_BASE_URL=https://file/v1\n"
        "TILTH_API_KEY=sk-from-file\n"
        "TILTH_WORKER_MODEL=file/model\n"
    )
    monkeypatch.setenv("TILTH_ENV_FILE", str(envfile))
    # mirror cli._load_env: load_dotenv(override=False) merges the file into env
    monkeypatch.setenv("TILTH_BASE_URL", "https://file/v1")
    monkeypatch.setenv("TILTH_API_KEY", "sk-from-file")
    monkeypatch.setenv("TILTH_WORKER_MODEL", "file/model")
    assert loop.do_config_cmd() == 0
    out = capsys.readouterr().out
    assert ".env" in _row(out, "TILTH_WORKER_MODEL")
    assert "environment" not in _row(out, "TILTH_WORKER_MODEL")


def test_config_source_shell_overrides_file(sessions, tmp_path, monkeypatch, capsys):
    envfile = tmp_path / "myenv"
    envfile.write_text("TILTH_WORKER_MODEL=file/model\n")
    monkeypatch.setenv("TILTH_ENV_FILE", str(envfile))
    monkeypatch.setenv("TILTH_BASE_URL", "https://x/v1")
    monkeypatch.setenv("TILTH_API_KEY", "sk-secret-tail")
    # shell value differs from the file entry -> shell wins (override=False)
    monkeypatch.setenv("TILTH_WORKER_MODEL", "shell/model")
    assert loop.do_config_cmd() == 0
    out = capsys.readouterr().out
    assert "environment" in _row(out, "TILTH_WORKER_MODEL")
    assert "shell/model" in _row(out, "TILTH_WORKER_MODEL")


def test_config_evaluator_inherits_source(sessions, configured, capsys):
    assert loop.do_config_cmd() == 0
    out = capsys.readouterr().out
    assert "inherits TILTH_WORKER_MODEL" in _row(out, "TILTH_EVALUATOR_MODEL")
    assert "inherits TILTH_BASE_URL" in _row(out, "TILTH_EVALUATOR_BASE_URL")
    assert "inherits TILTH_API_KEY" in _row(out, "TILTH_EVALUATOR_API_KEY")
