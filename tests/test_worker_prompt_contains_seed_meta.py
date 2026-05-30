"""Phase 4 visibility: the worker's prompt carries curated seed context.

seed-meta.json is the interview audit trail. The worker only needs the
feature-shaping fields (TL;DR, scope notes, blockers, open questions) — *not*
the interview bookkeeping (model, tokens, timestamps), which is mechanics, not
work. This test pins that line.
"""

from __future__ import annotations

import json
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
    return {"id": "T-001", "title": "t", "description": "d"}


def _write_meta(session_dir: Path, **overrides) -> None:
    meta = {
        "interviewer_model": "deepseek/deepseek-v4-pro",
        "tokens": {"prompt": 62892, "completion": 11614, "total": 74506},
        "started_at": "2026-05-29T20:40:13Z",
        "tldr": "Add a single `add` command to the todo CLI.",
        "scope_notes": "Strict MVP: only the add command. Stdlib only.",
        "blockers": ["T-001 pins main([])==0; T-002 makes it non-zero."],
        "open_questions": ["Chose default-argument pattern for main(path)."],
    }
    meta.update(overrides)
    (session_dir / "seed-meta.json").write_text(json.dumps(meta))


def test_seed_meta_feature_fields_are_injected(workspace, session_dir):
    _write_meta(session_dir)
    prompt, _ = memory.build_user_prompt(_task(), workspace, session_dir)
    assert "Add a single `add` command" in prompt
    assert "Strict MVP" in prompt
    assert "T-001 pins main([])==0" in prompt
    assert "default-argument pattern" in prompt


def test_seed_meta_excludes_interview_bookkeeping(workspace, session_dir):
    _write_meta(session_dir)
    prompt, _ = memory.build_user_prompt(_task(), workspace, session_dir)
    assert "deepseek" not in prompt
    assert "62892" not in prompt
    assert "2026-05-29T20:40:13Z" not in prompt


def test_seed_meta_absent_when_file_missing(workspace, session_dir):
    _, manifest = memory.build_user_prompt(_task(), workspace, session_dir)
    assert manifest["channels"]["seed_meta"]["present"] is False


def test_seed_meta_absent_when_malformed(workspace, session_dir):
    (session_dir / "seed-meta.json").write_text("{not json")
    prompt, manifest = memory.build_user_prompt(_task(), workspace, session_dir)
    assert manifest["channels"]["seed_meta"]["present"] is False
    assert "Seed context" not in prompt


def test_seed_meta_empty_fields_yield_no_section(workspace, session_dir):
    _write_meta(
        session_dir, tldr="", scope_notes="", blockers=[], open_questions=[]
    )
    _, manifest = memory.build_user_prompt(_task(), workspace, session_dir)
    assert manifest["channels"]["seed_meta"]["present"] is False


def test_manifest_records_which_fields_were_used(workspace, session_dir):
    _write_meta(session_dir, blockers=[])  # blockers empty → dropped
    _, manifest = memory.build_user_prompt(_task(), workspace, session_dir)
    ch = manifest["channels"]["seed_meta"]
    assert ch["present"] is True
    assert "tldr" in ch["fields"]
    assert "blockers" not in ch["fields"]
