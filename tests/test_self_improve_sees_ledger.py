"""Phase 5: the self-improver reads cross-task signal — the session's
rejection-category histogram and every task's evaluator ledger.

Before Phase 5 the self-improve step saw only the just-finished task's diff +
AGENTS.md, so proposals could only be one-task observations. Now it can ground a
proposal in a *pattern* (e.g. repeated scope_creep across tasks). Closes #9.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tilth import loop
from tilth.loop import _self_improve, _self_improve_session_context
from tilth.session import Session


def _entry(iter_n, verdict, category=None, concern="", next_step=None):
    return {
        "iter": iter_n,
        "diff_summary": "todo_cli/__main__.py (+4 -0)",
        "case": None,
        "verdict": {
            "verdict": verdict,
            "rejection_category": category,
            "concern": concern,
            "evidence": [],
            "next_step": next_step,
        },
    }


@pytest.fixture
def session(tmp_path: Path) -> Session:
    return Session.new(tmp_path / "sessions")


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "worktree"
    wt.mkdir()
    return wt


def _seed_two_tasks(session: Session) -> None:
    """T-001 rejected twice (scope_creep) then accepted; T-002 accepted clean.
    Ledger files drive the per-task arc; evaluator_verdict events drive the
    histogram (summary.build_from_events reads those)."""
    session.append_ledger_entry("T-001", _entry(1, "reject", "scope_creep",
                                                 concern="Edited tests/test_t002_*.py."))
    session.append_ledger_entry("T-001", _entry(2, "reject", "scope_creep",
                                                 concern="Still touching T-002's test."))
    session.append_ledger_entry("T-001", _entry(3, "accept", concern="Resolved."))
    session.append_ledger_entry("T-002", _entry(1, "accept", concern="Clean."))
    for cat in ("scope_creep", "scope_creep"):
        session.log("evaluator_verdict",
                    {"task_id": "T-001", "verdict": "reject", "rejection_category": cat})
    session.log("evaluator_verdict", {"task_id": "T-001", "verdict": "accept"})
    session.log("evaluator_verdict", {"task_id": "T-002", "verdict": "accept"})


# ---- _self_improve_session_context (reads the session) ----------------------

def test_context_includes_rejection_histogram(session):
    _seed_two_tasks(session)
    ctx = _self_improve_session_context(session)
    assert "Rejection patterns" in ctx
    assert "scope_creep: 2" in ctx


def test_context_includes_each_tasks_ledger_arc(session):
    _seed_two_tasks(session)
    ctx = _self_improve_session_context(session)
    assert "T-001" in ctx and "T-002" in ctx
    assert "Edited tests/test_t002_*.py." in ctx  # a concern from the arc


def test_context_empty_when_no_ledgers_or_verdicts(session):
    assert _self_improve_session_context(session) == ""


def test_ledger_task_ids_enumerates_without_creating_dir(session):
    assert session.ledger_task_ids() == []  # no dir yet
    assert not (session.root / "ledger").exists()  # the read must not create it
    session.append_ledger_entry("T-003", _entry(1, "accept"))
    session.append_ledger_entry("T-001", _entry(1, "accept"))
    assert session.ledger_task_ids() == ["T-001", "T-003"]  # sorted


# ---- driving _self_improve end-to-end --------------------------------------

class _FakeClient:
    def chat(self, messages, tools=None, model=None, tool_choice=None):
        # decline to propose — we only care about the assembled prompt + events
        return {"message": {"role": "assistant", "content": '{"propose": "no"}'},
                "usage": {}, "finish_reason": "stop"}


def _prompt_assembled(session: Session, role: str) -> list[dict]:
    events = [json.loads(ln) for ln in session.events_path.read_text().splitlines()]
    return [e for e in events
            if e["type"] == "prompt_assembled" and e["payload"]["role"] == role]


def test_self_improve_emits_self_improve_prompt_assembled(session, worktree):
    _seed_two_tasks(session)
    task = {"id": "T-002", "title": "Add core fn", "description": "implement add_todo"}
    _self_improve(task, worktree, session, _FakeClient(), trace_id="tr")

    pa = _prompt_assembled(session, "self_improve")
    assert len(pa) == 1
    content = pa[0]["payload"]["content"]
    # the cross-task signal is actually in the prompt the model saw
    assert "scope_creep: 2" in content
    assert "Per-task evaluator ledgers" in content
    assert "Edited tests/test_t002_*.py." in content


def test_self_improve_prompt_assembled_uses_iter_zero(session, worktree):
    _seed_two_tasks(session)
    task = {"id": "T-002", "title": "t", "description": "d"}
    _self_improve(task, worktree, session, _FakeClient(), trace_id="tr")
    assert _prompt_assembled(session, "self_improve")[0]["payload"]["iter"] == 0
