"""Robustness: provider failures must never become conversation turns.

Two real incidents are pinned here (same provider: deepseek-v4-flash via
OpenRouter):

- 2026-05-30 (session 20260530-073150-761226): empty 200s — no content, no
  tool calls, zero usage. The loop mistook each for "worker stopped without
  submit_case" and nudged a dead endpoint until interrupted.
- 2026-06-10 (session 20260610-100626-8c0142): `finish_reason: "error"` with a
  *partial reasoning trace*. The shape-based empty check saw the reasoning and
  passed the corrupted turn into history, plus a false "you stopped" nudge.
  The shape-patch from the first incident didn't cover the second shape.

The fix under test: health is classified from the provider's signals
(`response_health`), unhealthy calls are retried with the history untouched
(patience ~minutes, matching documented provider blip durations), and
exhaustion returns 'provider_failure' — a stop the session can resume from.

A worker that genuinely goes quiet *with* prose still routes to the bounded
no-case nudge (→ 'no_case'), and the nudge injection is logged as a `nudge`
event so events.jsonl can faithfully reconstruct the conversation.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tilth import loop
from tilth.loop import (
    MAX_CONSECUTIVE_NO_CASE_NUDGES,
    PROVIDER_RETRY_MAX_ATTEMPTS,
    WORKER_NO_CASE_NUDGE,
    _run_task,
    _stop_to_status,
)
from tilth.session import Session

# ---- canned responses --------------------------------------------------------

HARD_EMPTY = {"message": {}, "usage": {}, "finish_reason": "stop"}
ERROR_FINISH_PARTIAL_REASONING = {
    "message": {
        "role": "assistant",
        "content": None,
        "reasoning_details": [{"type": "reasoning.text", "text": "Good. Now let me te"}],
    },
    "usage": {"prompt_tokens": 4767, "completion_tokens": 36},
    "finish_reason": "error",
}
QUIET = {  # content present, no tool call — the legitimate "went quiet" shape
    "message": {"role": "assistant", "content": "I think I'm done."},
    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    "finish_reason": "stop",
}


def _tool_call_response(name: str = "bash", args: str = '{"command": "true"}') -> dict:
    return {
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "tc1", "function": {"name": name, "arguments": args}}
            ],
        },
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        "finish_reason": "tool_calls",
    }


# ---- fakes -------------------------------------------------------------------

def _config(max_iter: int = 60) -> SimpleNamespace:
    return SimpleNamespace(
        worker_model="worker-m",
        evaluator_model="evaluator-m",
        max_iterations_per_task=max_iter,
        max_evaluator_calls_per_task=0,
        context_files=["AGENTS.md", "CLAUDE.md"],
    )


class _SeqClient:
    """Returns scripted responses in order (last one repeats). Records each
    call's message list (deep-ish copy) so history poisoning is assertable."""

    def __init__(self, responses: list[dict], max_iter: int = 60):
        self._responses = list(responses)
        self.config = _config(max_iter)
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools=None, model=None, tool_choice=None):
        self.calls.append([dict(m) for m in messages])
        resp = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        return dict(resp)


@pytest.fixture
def session(tmp_path: Path) -> Session:
    return Session.new(tmp_path / "sessions")


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "worktree"
    wt.mkdir()
    return wt


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Don't actually back off during provider-health retries."""
    monkeypatch.setattr(loop.time, "sleep", lambda *_: None)


def _task() -> dict:
    return {"id": "T-001", "title": "t", "description": "d", "acceptance_criteria": ["a"]}


def _events(session: Session) -> list[dict]:
    return [json.loads(line) for line in session.events_path.read_text().splitlines()]


def _run(client, session, worktree) -> str:
    return _run_task(
        _task(), worktree, client, session, trace_id="tr",
        prd=[{**_task(), "status": "pending"}],
    )


# ---- persistent provider failure → provider_failure, history untouched -------

def test_persistent_empty_aborts_as_provider_failure(session, worktree):
    client = _SeqClient([HARD_EMPTY])
    outcome = _run(client, session, worktree)
    assert outcome == "provider_failure"
    # one logical call's retry budget — does NOT burn the iteration cap
    assert len(client.calls) == PROVIDER_RETRY_MAX_ATTEMPTS


def test_persistent_error_finish_aborts_as_provider_failure(session, worktree):
    client = _SeqClient([ERROR_FINISH_PARTIAL_REASONING])
    outcome = _run(client, session, worktree)
    assert outcome == "provider_failure"


def test_unhealthy_responses_never_touch_history(session, worktree):
    client = _SeqClient([ERROR_FINISH_PARTIAL_REASONING])
    _run(client, session, worktree)
    # every retry sends the identical, unpolluted message list
    lengths = {len(c) for c in client.calls}
    assert lengths == {len(client.calls[0])}
    for call in client.calls:
        assert all("role" in m for m in call)
        for m in call:
            if m.get("role") == "assistant":
                assert m.get("content") or m.get("tool_calls"), (
                    "a content-less assistant turn leaked into history"
                )


def test_provider_failure_logged_with_health_evidence(session, worktree):
    client = _SeqClient([ERROR_FINISH_PARTIAL_REASONING])
    _run(client, session, worktree)
    events = _events(session)
    calls = [e for e in events if e["type"] == "model_call"]
    assert len(calls) == PROVIDER_RETRY_MAX_ATTEMPTS
    assert all(e["payload"]["health"] == "provider_error" for e in calls)
    assert all(e["payload"]["call_attempt"] == i + 1 for i, e in enumerate(calls))
    failed = [e for e in events if e["type"] == "task_failed"]
    assert failed and failed[-1]["payload"]["reason"] == "provider_failure"
    assert failed[-1]["payload"]["call_attempts"] == PROVIDER_RETRY_MAX_ATTEMPTS


# ---- the 2026-06-10 regression: transient error turn -------------------------

def test_transient_error_turn_is_retried_not_nudged(session, worktree):
    """An errored turn followed by a healthy one: the corrupted partial must
    not enter history, no nudge fires, and the worker proceeds normally."""
    client = _SeqClient([
        ERROR_FINISH_PARTIAL_REASONING,
        QUIET,  # healthy quiet turn → routes to the *no_case* path, proving
        QUIET,  # the loop moved on past the provider blip
        QUIET,
    ])
    outcome = _run(client, session, worktree)
    assert outcome == "no_case"

    # call 2 (the retry) saw the exact same history as call 1 — no corrupted
    # assistant turn, no false "you stopped" nudge
    assert len(client.calls[1]) == len(client.calls[0])
    assert not any(
        m.get("role") == "user" and m.get("content") == WORKER_NO_CASE_NUDGE
        for m in client.calls[1]
    )
    assert not any(
        m.get("role") == "assistant" and not (m.get("content") or m.get("tool_calls"))
        for call in client.calls
        for m in call
    )


def test_transient_empty_then_tool_calls_continues_working(session, worktree):
    client = _SeqClient([
        _tool_call_response(args='{"command": "true"}'),
        HARD_EMPTY,            # blip mid-task
        QUIET, QUIET, QUIET,   # then quiet — bounded nudges end the task
    ])
    outcome = _run(client, session, worktree)
    assert outcome == "no_case"
    types = [e["type"] for e in _events(session)]
    assert "tool_result" in types  # the healthy turn's tool actually ran


# ---- genuinely quiet worker: nudge path unchanged, now observable -------------

def test_repeated_quiet_stops_abort_as_no_case(session, worktree):
    client = _SeqClient([QUIET])
    outcome = _run(client, session, worktree)
    assert outcome == "no_case"
    assert len(client.calls) == MAX_CONSECUTIVE_NO_CASE_NUDGES
    failed = [e for e in _events(session) if e["type"] == "task_failed"]
    assert failed and failed[-1]["payload"]["reason"] == "no_case"


def test_nudge_injection_is_logged(session, worktree):
    client = _SeqClient([QUIET])
    _run(client, session, worktree)
    nudges = [e for e in _events(session) if e["type"] == "nudge"]
    # the final quiet turn aborts instead of nudging
    assert len(nudges) == MAX_CONSECUTIVE_NO_CASE_NUDGES - 1
    assert all(n["payload"]["kind"] == "no_case" for n in nudges)
    assert all(n["payload"]["content"] == WORKER_NO_CASE_NUDGE for n in nudges)


# ---- stop classification: provider failures are resumable --------------------

@pytest.mark.parametrize(
    "reason, expected",
    [
        ("provider_failure", "running"),  # transient — resume retries the task
        ("token_cap", "running"),
        ("wall_clock", "running"),
        ("iter_cap", "failed"),
        ("no_case", "failed"),
        ("error", "failed"),
        ("all_done", "all_done"),
    ],
)
def test_stop_to_status(reason, expected):
    assert _stop_to_status(reason) == expected
