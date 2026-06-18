"""A global-install user with no provider config should get an actionable
message — not a raw traceback or internal paths (issue #8 + project CLAUDE.md
on user-facing errors). `_load_config` returns None and explains the fix.
"""

from __future__ import annotations

from tilth import loop

REQUIRED = ("TILTH_BASE_URL", "TILTH_API_KEY", "TILTH_WORKER_MODEL")
OPTIONAL = (
    "TILTH_EVALUATOR_MODEL",
    "TILTH_EVALUATOR_BASE_URL",
    "TILTH_EVALUATOR_API_KEY",
    "TILTH_CONTEXT_FILES",
)
LOCATION = ("TILTH_HOME", "TILTH_SESSIONS_DIR", "TILTH_ENV_FILE")


def test_load_config_no_env_suggests_init(monkeypatch, tmp_path, capsys):
    for name in (*REQUIRED, *OPTIONAL, *LOCATION):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TILTH_HOME", str(tmp_path / "home"))  # empty -> no .env
    monkeypatch.chdir(tmp_path)  # no CWD .env either

    cfg = loop._load_config()
    assert cfg is None
    out = capsys.readouterr().out
    assert "tilth init" in out
    # never leak internals / dump a traceback
    assert "Traceback" not in out
    assert ".env.example" not in out
    assert "docs/" not in out


def test_load_config_incomplete_env_points_at_file(monkeypatch, tmp_path, capsys):
    for name in (*REQUIRED, *OPTIONAL, "TILTH_SESSIONS_DIR", "TILTH_ENV_FILE"):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    env_file = home / ".env"
    env_file.write_text("TILTH_BASE_URL=https://x/v1\n")  # missing key + model
    monkeypatch.setenv("TILTH_HOME", str(home))
    monkeypatch.setenv("TILTH_BASE_URL", "https://x/v1")  # partially-filled config

    cfg = loop._load_config()
    assert cfg is None
    assert str(env_file) in capsys.readouterr().out


def test_do_run_returns_2_on_missing_config(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.ws, "ensure_git_repo", lambda p: None)
    monkeypatch.setattr(
        loop.tasks, "load_feature", lambda src: ("overview", [{"id": "T-001"}])
    )

    def _raise():
        raise RuntimeError("Missing required configuration: TILTH_API_KEY")

    monkeypatch.setattr(loop.TilthConfig, "from_env", staticmethod(_raise))
    for name in LOCATION:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TILTH_HOME", str(tmp_path / "home"))

    rc = loop.do_run_cmd(tmp_path / "repo")
    assert rc == 2
