"""`tilth init` scaffolds the per-user home so the installed tool runs from
anywhere (issue #8). It creates ~/.tilth/{.env,sessions/} from the template and
refuses to clobber an existing .env.
"""

from __future__ import annotations

import pytest

from tilth import loop, paths


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    for name in ("TILTH_HOME", "TILTH_SESSIONS_DIR", "TILTH_ENV_FILE"):
        monkeypatch.delenv(name, raising=False)


def _point_home(monkeypatch, home) -> None:
    monkeypatch.setenv("TILTH_HOME", str(home))
    # mimic cli.main() recomputing the module-level sessions dir after env load
    monkeypatch.setattr(loop, "SESSIONS_DIR", paths.sessions_dir())


def test_init_writes_env_and_creates_sessions(monkeypatch, tmp_path, capsys):
    _point_home(monkeypatch, tmp_path)
    rc = loop.do_init_cmd()
    assert rc == 0
    env_file = tmp_path / ".env"
    assert env_file.is_file()
    body = env_file.read_text()
    assert "TILTH_BASE_URL" in body
    assert "TILTH_WORKER_MODEL" in body
    assert (tmp_path / "sessions").is_dir()
    assert str(env_file) in capsys.readouterr().out


def test_init_does_not_clobber_existing(monkeypatch, tmp_path, capsys):
    _point_home(monkeypatch, tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("SENTINEL=keep\n")
    rc = loop.do_init_cmd()
    assert rc == 0
    assert env_file.read_text() == "SENTINEL=keep\n"  # untouched
    assert "already exists" in capsys.readouterr().out.lower()


def test_init_respects_env_file_override(monkeypatch, tmp_path):
    monkeypatch.setenv("TILTH_HOME", str(tmp_path / "home"))
    custom = tmp_path / "custom" / "my.env"
    monkeypatch.setenv("TILTH_ENV_FILE", str(custom))
    monkeypatch.setattr(loop, "SESSIONS_DIR", paths.sessions_dir())
    rc = loop.do_init_cmd()
    assert rc == 0
    assert custom.is_file()
    assert "TILTH_WORKER_MODEL" in custom.read_text()
