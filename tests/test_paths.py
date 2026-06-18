"""User-data location resolution (issue #8).

Tilth keeps per-user state under a single home dir (~/.tilth by default), with
env overrides for power users and contributors. These pin the resolution rules
and the .env search order so a refactor can't silently relocate someone's
sessions or pick up an unrelated repo's .env.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tilth import paths


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    for name in ("TILTH_HOME", "TILTH_SESSIONS_DIR", "TILTH_ENV_FILE"):
        monkeypatch.delenv(name, raising=False)


# --- home + sessions dir ----------------------------------------------------

def test_home_defaults_to_dot_tilth():
    assert paths.tilth_home() == Path.home() / ".tilth"


def test_home_override(monkeypatch, tmp_path):
    monkeypatch.setenv("TILTH_HOME", str(tmp_path / "h"))
    assert paths.tilth_home() == tmp_path / "h"


def test_home_override_expands_user(monkeypatch):
    monkeypatch.setenv("TILTH_HOME", "~/somewhere")
    assert paths.tilth_home() == Path.home() / "somewhere"


def test_home_blank_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("TILTH_HOME", "   ")
    assert paths.tilth_home() == Path.home() / ".tilth"


def test_sessions_dir_defaults_under_home():
    assert paths.sessions_dir() == Path.home() / ".tilth" / "sessions"


def test_sessions_dir_follows_home_override(monkeypatch, tmp_path):
    monkeypatch.setenv("TILTH_HOME", str(tmp_path))
    assert paths.sessions_dir() == tmp_path / "sessions"


def test_sessions_dir_explicit_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("TILTH_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TILTH_SESSIONS_DIR", str(tmp_path / "elsewhere"))
    assert paths.sessions_dir() == tmp_path / "elsewhere"


# --- .env search order (first existing wins) --------------------------------

def test_resolve_env_file_explicit_override(monkeypatch, tmp_path):
    custom = tmp_path / "custom.env"
    custom.write_text("X=1\n")
    monkeypatch.setenv("TILTH_ENV_FILE", str(custom))
    assert paths.resolve_env_file() == custom


def test_resolve_env_file_home_beats_cwd(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text("FROM=home\n")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / ".env").write_text("FROM=cwd\n")
    monkeypatch.setenv("TILTH_HOME", str(home))
    monkeypatch.chdir(cwd)
    assert paths.resolve_env_file() == home / ".env"


def test_resolve_env_file_cwd_fallback(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()  # deliberately no .env here
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / ".env").write_text("FROM=cwd\n")
    monkeypatch.setenv("TILTH_HOME", str(home))
    monkeypatch.chdir(cwd)
    assert paths.resolve_env_file() == cwd / ".env"


def test_resolve_env_file_none_when_nothing_exists(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.setenv("TILTH_HOME", str(home))
    monkeypatch.chdir(cwd)
    assert paths.resolve_env_file() is None


def test_explicit_override_missing_file_falls_through(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text("FROM=home\n")
    monkeypatch.setenv("TILTH_ENV_FILE", str(tmp_path / "nope.env"))
    monkeypatch.setenv("TILTH_HOME", str(home))
    assert paths.resolve_env_file() == home / ".env"


def test_env_file_write_target_default(monkeypatch, tmp_path):
    monkeypatch.setenv("TILTH_HOME", str(tmp_path))
    assert paths.env_file_write_target() == tmp_path / ".env"


def test_env_file_write_target_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("TILTH_ENV_FILE", str(tmp_path / "c.env"))
    assert paths.env_file_write_target() == tmp_path / "c.env"


# --- cli._load_env loads the resolved file ----------------------------------

def test_load_env_loads_resolved_file(monkeypatch, tmp_path):
    from tilth import cli

    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text("TILTH_SMOKE_VAR=hello\n")
    monkeypatch.setenv("TILTH_HOME", str(home))
    monkeypatch.delenv("TILTH_SMOKE_VAR", raising=False)
    try:
        cli._load_env()
        assert os.environ.get("TILTH_SMOKE_VAR") == "hello"
    finally:
        os.environ.pop("TILTH_SMOKE_VAR", None)
