"""Checkpoint round-trips the `source` field added in Phase 2.

The new source field is what `_find_blocking_sessions` and
`_find_prepared_sessions` key on. Old checkpoints (pre-Phase-2) don't have
the field; `Session.wake` must default it to None rather than raising.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tilth.session import Session


@pytest.fixture
def sessions_root(tmp_path: Path) -> Path:
    return tmp_path


def test_source_round_trips_through_wake(sessions_root, tmp_path):
    src = tmp_path / "my-project"
    src.mkdir()
    s = Session.new(sessions_root)
    s.source = src
    s.save_checkpoint()

    woken = Session.wake(sessions_root, s.session_id)
    assert woken.source == src


def test_source_in_checkpoint_json(sessions_root, tmp_path):
    src = tmp_path / "my-project"
    src.mkdir()
    s = Session.new(sessions_root)
    s.source = src
    s.save_checkpoint()
    cp = json.loads(s.checkpoint_path.read_text())
    assert cp["source"] == str(src)


def test_wake_defaults_missing_source_to_none(sessions_root):
    """Pre-Phase-2 checkpoints have no source field; wake must not raise."""
    s = Session.new(sessions_root)
    cp = json.loads(s.checkpoint_path.read_text())
    cp.pop("source", None)
    s.checkpoint_path.write_text(json.dumps(cp))
    woken = Session.wake(sessions_root, s.session_id)
    assert woken.source is None


def test_new_session_has_no_source_by_default(sessions_root):
    s = Session.new(sessions_root)
    assert s.source is None
    cp = json.loads(s.checkpoint_path.read_text())
    assert cp["source"] is None
