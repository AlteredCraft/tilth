"""Phase 4 visibility: the worker's prompt now carries the full feature plan.

The worker sees every task (collapsed) so it understands the whole and doesn't
build ahead of its own task (the F9 friction). It is context, not a worklist —
the prompt says so, and the current task is marked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tilth import memory


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    sd = tmp_path / "session"
    sd.mkdir()
    return sd


def _prd() -> list[dict]:
    return [
        {"id": "T-001", "title": "Scaffold", "description": "set up the package",
         "acceptance_criteria": ["pyproject.toml exists"], "status": "done"},
        {"id": "T-002", "title": "Add core fn", "description": "implement add_todo",
         "acceptance_criteria": ["add_todo appends a line"], "status": "pending"},
        {"id": "T-003", "title": "Wire CLI", "description": "todo add wires in",
         "acceptance_criteria": ["cli calls add_todo"], "status": "pending"},
    ]


def test_full_prd_lists_every_task(workspace, session_dir):
    prompt, _ = memory.build_user_prompt(
        _prd()[1], workspace, session_dir, prd=_prd()
    )
    for tid in ("T-001", "T-002", "T-003"):
        assert tid in prompt
    assert "Scaffold" in prompt and "Wire CLI" in prompt


def test_full_prd_marks_the_current_task(workspace, session_dir):
    prompt, _ = memory.build_user_prompt(
        _prd()[1], workspace, session_dir, prd=_prd()
    )
    # the marker rides the current task's collapsed line (detail is under "Your task")
    line = next(ln for ln in prompt.splitlines() if "your task" in ln.lower() and "T-0" in ln)
    assert "T-002" in line
    assert prompt.count("← **your task**") == 1


def test_full_prd_is_framed_as_context_not_worklist(workspace, session_dir):
    prompt, _ = memory.build_user_prompt(
        _prd()[1], workspace, session_dir, prd=_prd()
    )
    assert "context, not work to do" in prompt
    assert "pre-empt" in prompt


def test_full_prd_carries_status_for_each_task(workspace, session_dir):
    prompt, _ = memory.build_user_prompt(
        _prd()[1], workspace, session_dir, prd=_prd()
    )
    assert "[done]" in prompt
    assert "[pending]" in prompt


def test_manifest_records_full_prd_channel(workspace, session_dir):
    _, manifest = memory.build_user_prompt(
        _prd()[1], workspace, session_dir, prd=_prd()
    )
    ch = manifest["channels"]["full_prd"]
    assert ch["present"] is True
    assert ch["n_tasks"] == 3
    assert ch["chars"] > 0


def test_full_prd_absent_when_not_passed(workspace, session_dir):
    _, manifest = memory.build_user_prompt(_prd()[1], workspace, session_dir)
    assert manifest["channels"]["full_prd"]["present"] is False


def test_full_prd_truncates_when_oversized(workspace, session_dir):
    big = [
        {"id": f"T-{i:03d}", "title": "x", "description": "y" * 1000,
         "acceptance_criteria": ["z"], "status": "pending"}
        for i in range(1, 40)
    ]
    _, manifest = memory.build_user_prompt(big[0], workspace, session_dir, prd=big)
    ch = manifest["channels"]["full_prd"]
    assert ch["truncated"] is True
    assert ch["chars"] <= memory.FULL_PRD_MAX_CHARS + 50
