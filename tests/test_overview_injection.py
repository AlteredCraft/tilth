"""The feature overview (`.tilth/tasks/overview.md`) is injected as worker context.

It replaces the old seed-meta "why this was scoped this way" channel. The caller
loads the text and passes it to build_user_prompt; memory wraps it in a labelled
section and records an `overview` channel in the manifest.
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


def _task() -> dict:
    return {"id": "T-001", "title": "do a thing", "description": "do it"}


def test_overview_injected_when_present(workspace, session_dir):
    prompt, manifest = memory.build_user_prompt(
        _task(), workspace, session_dir, overview="# Cool feature\n\nThe big why."
    )
    assert "## Feature overview" in prompt
    assert "The big why." in prompt
    assert manifest["channels"]["overview"]["present"] is True
    assert manifest["channels"]["overview"]["chars"] > 0


def test_overview_absent_marks_channel_absent(workspace, session_dir):
    _, manifest = memory.build_user_prompt(_task(), workspace, session_dir)
    assert manifest["channels"]["overview"]["present"] is False
    # the old seed_meta channel must not resurface
    assert "seed_meta" not in manifest["channels"]


def test_overview_truncated_when_oversized(workspace, session_dir):
    big = "x" * (memory.OVERVIEW_MAX_CHARS + 500)
    _, manifest = memory.build_user_prompt(_task(), workspace, session_dir, overview=big)
    assert manifest["channels"]["overview"]["truncated"] is True
