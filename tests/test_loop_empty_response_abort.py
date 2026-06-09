"""Robustness: the worker loop must not spin on a misbehaving model.

Regression for the stuck run on 2026-05-30: the provider (deepseek-v4-flash via
OpenRouter) started returning empty 200s — no content, no tool calls, zero
tokens — and the loop mistook each for "worker stopped without submit_case",
nudging a dead endpoint until interrupted. Empty calls cost 0 tokens, so the
token cap never tripped; only the iteration cap (60) would have.

Two backstops are pinned here:
- empty model responses → retry with backoff, then abort ('empty_responses');
- a worker that keeps going quiet *with* prose → bounded nudges, then abort
  ('no_case').
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tilth import loop
from tilth.loop import (
    EMPTY_RESPONSE_RETRY_LIMIT,
    MAX_CONSECUTIVE_NO_CASE_NUDGES,
    _is_empty_response,
    _run_task,
)
from tilth.session import Session

# ---- _is_empty_response (pure) ---------------------------------------------

@pytest.mark.parametrize(
    "msg, expected",
    [
        ({}, True),
        ({"role": "assistant", "content": "", "tool_calls": None}, True),
        ({"content": "   "}, True),  # whitespace-only is still empty
        ({"content": "I'm done."}, False),
        ({"tool_calls": [{"id": "x"}]}, False),
        ({"reasoning": "let me think"}, False),
        ({"reasoning_details": [{"type": "reasoning.text"}]}, False),
    ],
)
def test_is_empty_response(msg, expected):
    assert _is_empty_response(msg) is expected


# ---- driving _run_task with a fake client ----------------------------------

class _FakeClient:
    """Returns the same canned response every call and records each call's
    message list so we can assert the history isn't poisoned."""

    def __init__(self, response: dict, max_iter: int = 60):
        self._response = response
        self.config = SimpleNamespace(
            max_iterations_per_task=max_iter,
            max_evaluator_calls_per_task=0,
            context_files=["AGENTS.md", "CLAUDE.md"],
        )
        self.message_lengths: list[int] = []
        self.had_roleless_message = False

    def chat(self, messages, tools=None, model=None, tool_choice=None):
        self.message_lengths.append(len(messages))
        if any("role" not in m for m in messages):
            self.had_roleless_message = True
        return dict(self._response)


@pytest.fixture
def session(tmp_path: Path) -> Session:
    s = Session.new(tmp_path / "sessions")
    prd = [{
        "id": "T-001", "title": "t", "description": "d",
        "acceptance_criteria": ["a"], "status": "pending",
    }]
    (s.root / "prd.json").write_text(json.dumps(prd))
    return s


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "worktree"
    wt.mkdir()
    return wt


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Don't actually back off during the empty-response retries."""
    monkeypatch.setattr(loop.time, "sleep", lambda *_: None)


def _task() -> dict:
    return {"id": "T-001", "title": "t", "description": "d", "acceptance_criteria": ["a"]}


EMPTY = {"message": {}, "usage": {}, "finish_reason": "stop"}
QUIET = {  # content present, no tool call — the legitimate "went quiet" shape
    "message": {"role": "assistant", "content": "I think I'm done."},
    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    "finish_reason": "stop",
}


def test_empty_responses_abort_instead_of_spinning(session, worktree):
    client = _FakeClient(EMPTY)
    outcome = _run_task(_task(), worktree, client, session, trace_id="tr",
              prd=[{**_task(), "status": "pending"}])
    assert outcome == "empty_responses"
    # aborts at the retry limit — does NOT run to the 60-iteration cap
    assert len(client.message_lengths) == EMPTY_RESPONSE_RETRY_LIMIT


def test_empty_responses_do_not_poison_history(session, worktree):
    client = _FakeClient(EMPTY)
    _run_task(_task(), worktree, client, session, trace_id="tr",
              prd=[{**_task(), "status": "pending"}])
    # an empty turn is never echoed back, so no role-less `{}` message is sent
    assert client.had_roleless_message is False
    # the message list doesn't grow across empty retries (nothing appended)
    assert len(set(client.message_lengths)) == 1


def test_empty_responses_logged_then_task_failed(session, worktree):
    client = _FakeClient(EMPTY)
    _run_task(_task(), worktree, client, session, trace_id="tr",
              prd=[{**_task(), "status": "pending"}])
    events = [json.loads(line) for line in session.events_path.read_text().splitlines()]
    types = [e["type"] for e in events]
    assert types.count("empty_model_response") == EMPTY_RESPONSE_RETRY_LIMIT
    failed = [e for e in events if e["type"] == "task_failed"]
    assert failed and failed[-1]["payload"]["reason"] == "empty_responses"


def test_repeated_quiet_stops_abort_as_no_case(session, worktree):
    client = _FakeClient(QUIET)
    outcome = _run_task(_task(), worktree, client, session, trace_id="tr",
              prd=[{**_task(), "status": "pending"}])
    assert outcome == "no_case"
    assert len(client.message_lengths) == MAX_CONSECUTIVE_NO_CASE_NUDGES
    events = [json.loads(line) for line in session.events_path.read_text().splitlines()]
    failed = [e for e in events if e["type"] == "task_failed"]
    assert failed and failed[-1]["payload"]["reason"] == "no_case"
