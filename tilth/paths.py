"""User-data location resolution.

Tilth keeps all per-user runtime state under a single home directory so the
installed tool (`uv tool install`) runs from anywhere, not just a clone. Each
location has an environment override for power users and contributors:

    $TILTH_HOME          the root dir       (default: ~/.tilth)
    $TILTH_SESSIONS_DIR  the sessions dir   (default: <home>/sessions)
    $TILTH_ENV_FILE      explicit .env path (default: the search order below)

The .env search order — first existing file wins, loaded on its own so we never
slurp an unrelated repo's .env just because it sits in the CWD:

    1. $TILTH_ENV_FILE
    2. <home>/.env
    3. ./.env   (CWD — contributor convenience before ~/.tilth is set up)

`$TILTH_HOME` (and `$TILTH_SESSIONS_DIR`) must be real shell variables to move
the *whole* tree, since they decide where the .env is read from. A `.env` may
still set `$TILTH_SESSIONS_DIR` to relocate just the sessions onto a bigger
disk — the CLI re-resolves the sessions dir after loading the .env.
"""

from __future__ import annotations

import os
from pathlib import Path


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else None


def tilth_home() -> Path:
    return _env_path("TILTH_HOME") or (Path.home() / ".tilth")


def sessions_dir() -> Path:
    return _env_path("TILTH_SESSIONS_DIR") or (tilth_home() / "sessions")


def env_file_write_target() -> Path:
    """Where `tilth init` writes the .env. Never the CWD fallback — only the
    explicit override or the home location."""
    return _env_path("TILTH_ENV_FILE") or (tilth_home() / ".env")


def resolve_env_file() -> Path | None:
    """First existing .env in the search order, or None if there is none."""
    candidates: list[Path] = []
    explicit = _env_path("TILTH_ENV_FILE")
    if explicit is not None:
        candidates.append(explicit)
    candidates.append(tilth_home() / ".env")
    candidates.append(Path.cwd() / ".env")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None
