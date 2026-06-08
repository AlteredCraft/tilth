"""Session checkpoint carries a `status` field (running|all_done|failed).

`set_status` validates against SESSION_STATUSES and persists to the checkpoint;
`wake` round-trips it. (The prompt-driven refactor dropped the `prepared`
status along with prep-feature.)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tilth.session import SESSION_STATUSES, Session


@pytest.fixture
def sessions_root(tmp_path: Path) -> Path:
    return tmp_path


def _cp(session: Session) -> dict:
    return json.loads(session.checkpoint_path.read_text())


def test_new_session_defaults_to_running(sessions_root):
    s = Session.new(sessions_root)
    assert s.status == "running"
    assert _cp(s)["status"] == "running"


def test_set_status_persists_to_checkpoint(sessions_root):
    s = Session.new(sessions_root)
    s.set_status("all_done")
    assert _cp(s)["status"] == "all_done"


def test_set_status_rejects_unknown_value(sessions_root):
    s = Session.new(sessions_root)
    with pytest.raises(ValueError):
        s.set_status("bogus")


def test_wake_preserves_status_from_disk(sessions_root):
    s = Session.new(sessions_root)
    s.set_status("all_done")
    woken = Session.wake(sessions_root, s.session_id)
    assert woken.status == "all_done"


def test_wake_defaults_missing_status_to_running(sessions_root):
    s = Session.new(sessions_root)
    cp = json.loads(s.checkpoint_path.read_text())
    cp.pop("status", None)
    s.checkpoint_path.write_text(json.dumps(cp))
    woken = Session.wake(sessions_root, s.session_id)
    assert woken.status == "running"


def test_status_set_covers_documented_values():
    assert SESSION_STATUSES == frozenset({"running", "all_done", "failed"})
