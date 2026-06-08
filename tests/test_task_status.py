"""Per-task status is harness-owned state in sessions/<id>/task-status.json.

The authored task markdown is read-only; the loop overlays a {task_id: status}
map onto the static task list to pick the next pending task. A task absent from
the map is `pending`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tilth import loop


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    sd = tmp_path / "session"
    sd.mkdir()
    return sd


def _tasks() -> list[dict]:
    return [
        {"id": "T-001", "title": "first", "description": "a", "acceptance_criteria": []},
        {"id": "T-002", "title": "second", "description": "b", "acceptance_criteria": []},
        {"id": "T-003", "title": "third", "description": "c", "acceptance_criteria": []},
    ]


def test_status_round_trips(session_dir):
    loop._save_status(session_dir, {"T-001": "done"})
    assert (session_dir / "task-status.json").is_file()
    assert loop._load_status(session_dir) == {"T-001": "done"}


def test_load_status_absent_is_empty(session_dir):
    assert loop._load_status(session_dir) == {}


def test_set_task_status_merges(session_dir):
    loop._set_task_status(session_dir, "T-001", "done")
    loop._set_task_status(session_dir, "T-002", "failed")
    assert loop._load_status(session_dir) == {"T-001": "done", "T-002": "failed"}


def test_overlay_status_marks_pending_by_default(session_dir):
    prd = loop._overlay_status(_tasks(), {"T-001": "done"})
    assert [t["status"] for t in prd] == ["done", "pending", "pending"]
    # overlay returns copies — the static task dicts are not mutated
    assert "status" not in _tasks()[0]


def test_next_pending_skips_done(session_dir):
    prd = loop._overlay_status(_tasks(), {"T-001": "done"})
    assert loop._next_pending(prd)["id"] == "T-002"


def test_next_pending_none_when_all_done(session_dir):
    prd = loop._overlay_status(_tasks(), {"T-001": "done", "T-002": "done", "T-003": "done"})
    assert loop._next_pending(prd) is None
