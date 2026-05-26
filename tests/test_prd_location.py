"""prd.json is a harness-owned artifact under sessions/<id>/, not in the workspace.

Mutating it during a run must never touch the user's workspace, and reading it
must never read from there either — the load helpers key on the session dir.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tilth import loop


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    sd = tmp_path / "sessions" / "20260525-120000-abc123"
    sd.mkdir(parents=True)
    return sd


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def _seed() -> list[dict]:
    return [
        {"id": "T-001", "title": "first", "status": "pending"},
        {"id": "T-002", "title": "second", "status": "pending"},
    ]


def test_load_prd_reads_from_session_dir(session_dir):
    (session_dir / "prd.json").write_text(json.dumps(_seed()))
    prd = loop._load_prd(session_dir)
    assert [t["id"] for t in prd] == ["T-001", "T-002"]


def test_load_prd_raises_when_session_dir_has_no_prd(session_dir):
    with pytest.raises(FileNotFoundError) as exc:
        loop._load_prd(session_dir)
    assert "prd.json" in str(exc.value)


def test_load_prd_ignores_stray_prd_in_workspace(session_dir, workspace):
    """A stray prd.json in the workspace must never be picked up — the load
    function takes a session_dir and reads only from there."""
    (workspace / "prd.json").write_text(json.dumps(_seed()))
    with pytest.raises(FileNotFoundError):
        loop._load_prd(session_dir)


def test_save_prd_writes_to_session_dir(session_dir, workspace):
    loop._save_prd(session_dir, _seed())
    assert (session_dir / "prd.json").is_file()
    assert not (workspace / "prd.json").is_file()
    parsed = json.loads((session_dir / "prd.json").read_text())
    assert [t["id"] for t in parsed] == ["T-001", "T-002"]


def test_save_prd_round_trips_via_load(session_dir):
    loop._save_prd(session_dir, _seed())
    prd = loop._load_prd(session_dir)
    assert prd == _seed()
