"""Phase 4 visibility: the worker sees its *own* task ledger.

This is the one surface that widens the visibility wall — the worker now reads
the evaluator's prior verdicts on this task. It's "about the work", not harness
mechanics. The section is empty on a task's first run (the ledger only fills
after a evaluator call); its payoff is on resume, so the test seeds the ledger.
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
    return {"id": "T-002", "title": "t", "description": "d"}


def _entry(iter_n, verdict, category=None, concern="", next_step=None, diff_summary=""):
    return {
        "iter": iter_n,
        "ts": "2026-05-29T10:00:00Z",
        "diff_summary": diff_summary,
        "case": None,
        "verdict": {
            "verdict": verdict,
            "rejection_category": category,
            "concern": concern,
            "evidence": [],
            "next_step": next_step,
        },
    }


def test_own_ledger_section_present_and_attributed(workspace, session_dir):
    ledger = [_entry(1, "reject", "acceptance_gap",
                     concern="Empty-name case not handled.",
                     next_step="Add a guard for empty name.")]
    prompt, _ = memory.build_user_prompt(
        _task(), workspace, session_dir, own_ledger=ledger
    )
    assert "## Prior iterations on this task (from the evaluator)" in prompt
    assert "Empty-name case not handled." in prompt
    assert "Add a guard for empty name." in prompt


def test_own_ledger_absent_when_empty(workspace, session_dir):
    prompt, manifest = memory.build_user_prompt(
        _task(), workspace, session_dir, own_ledger=[]
    )
    assert "Prior iterations on this task" not in prompt
    assert manifest["channels"]["own_ledger"]["present"] is False
    assert manifest["channels"]["own_ledger"]["entries"] == 0


def test_own_ledger_absent_when_not_passed(workspace, session_dir):
    _, manifest = memory.build_user_prompt(_task(), workspace, session_dir)
    assert manifest["channels"]["own_ledger"]["present"] is False


def test_manifest_counts_ledger_entries(workspace, session_dir):
    ledger = [_entry(1, "reject", "scope_creep"), _entry(2, "accept")]
    _, manifest = memory.build_user_prompt(
        _task(), workspace, session_dir, own_ledger=ledger
    )
    ch = manifest["channels"]["own_ledger"]
    assert ch["present"] is True
    assert ch["entries"] == 2
