"""Dashboard data layer for the live visualizer.

Contract pinned here:
- Every rendered event fragment is wrapped in `<div class="msg" ...>` carrying
  `data-kind` ∈ {worker, tool, evaluator, harness} so the client can filter the
  tail without re-rendering. `data-dialog="1"` marks the worker↔evaluator
  dialogue (submit_case calls + verdicts); `data-problem="1"` marks rejects,
  blocks, failures, nudges, and unhealthy model calls. Task dividers stay
  outside the wrappers (always visible).
- `extract_facts` turns raw events into compact dashboard facts (timestamps as
  epoch seconds, token counts, phases) — the client builds every chart from
  these, so replay and live tailing produce identical dashboards.
- `events_payload` ships `facts` alongside the HTML chunk, sliced by the same
  byte cursor.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tilth.visualize.render import classify, extract_facts, render_events
from tilth.visualize.server import events_payload

# ---- classify: kind / dialog / problem --------------------------------------------


@pytest.mark.parametrize("event, kind", [
    ({"type": "model_call", "payload": {"iter": 1, "health": "ok"}}, "worker"),
    ({"type": "model_call",
      "payload": {"iter": 1, "health": "ok", "phase": "evaluator"}}, "evaluator"),
    ({"type": "tool_call", "payload": {"tool": "bash"}}, "worker"),
    ({"type": "tool_result", "payload": {"tool": "bash"}}, "tool"),
    ({"type": "pre_tool_block", "payload": {"tool": "bash"}}, "tool"),
    ({"type": "evaluator_verdict", "payload": {"verdict": "accept"}}, "evaluator"),
    ({"type": "nudge", "payload": {"kind": "no_case"}}, "harness"),
    ({"type": "task_done", "payload": {}}, "harness"),
    ({"type": "task_failed", "payload": {"reason": "iter_cap"}}, "harness"),
    ({"type": "commit", "payload": {}}, "harness"),
    ({"type": "session_start", "payload": {}}, "harness"),
    ({"type": "context_reset", "payload": {}}, "harness"),
    ({"type": "stop", "payload": {"reason": "all_done"}}, "harness"),
    ({"type": "hook_run", "payload": {}}, "harness"),
    ({"type": "never_seen_before", "payload": {}}, "harness"),
])
def test_classify_kind(event, kind):
    assert classify(event)[0] == kind


@pytest.mark.parametrize("event, dialog", [
    ({"type": "tool_call", "payload": {"tool": "submit_case"}}, True),
    ({"type": "tool_call", "payload": {"tool": "bash"}}, False),
    ({"type": "evaluator_verdict", "payload": {"verdict": "accept"}}, True),
    ({"type": "evaluator_verdict", "payload": {"verdict": "reject"}}, True),
    ({"type": "model_call", "payload": {"iter": 1}}, False),
])
def test_classify_dialog(event, dialog):
    assert classify(event)[1] is dialog


@pytest.mark.parametrize("event, problem", [
    ({"type": "evaluator_verdict", "payload": {"verdict": "reject"}}, True),
    ({"type": "evaluator_verdict", "payload": {"verdict": "accept"}}, False),
    ({"type": "pre_tool_block", "payload": {"tool": "bash"}}, True),
    ({"type": "task_failed", "payload": {"reason": "iter_cap"}}, True),
    ({"type": "task_done", "payload": {}}, False),
    ({"type": "nudge", "payload": {"kind": "no_case"}}, True),
    ({"type": "model_call", "payload": {"health": "provider_error"}}, True),
    ({"type": "model_call", "payload": {"health": "ok"}}, False),
    ({"type": "stop", "payload": {"reason": "provider_failure"}}, True),
    ({"type": "stop", "payload": {"reason": "all_done"}}, False),
])
def test_classify_problem(event, problem):
    assert classify(event)[2] is problem


# ---- the wrapper: every fragment is a filterable .msg ------------------------------


def test_fragments_are_wrapped_with_data_kind():
    events = [
        {"ts": "t", "type": "model_call",
         "payload": {"task_id": "T-001", "iter": 1, "health": "ok"}},
        {"ts": "t", "type": "tool_result",
         "payload": {"task_id": "T-001", "tool": "bash", "result_preview": "ok"}},
    ]
    html, _ = render_events(events, None)
    assert html.count('<div class="msg"') == 2
    assert 'data-kind="worker"' in html
    assert 'data-kind="tool"' in html


def test_dialog_and_problem_attributes_present():
    events = [
        {"ts": "t", "type": "tool_call",
         "payload": {"task_id": "T-001", "tool": "submit_case", "args": {}}},
        {"ts": "t", "type": "evaluator_verdict",
         "payload": {"task_id": "T-001", "verdict": "reject",
                     "rejection_category": "acceptance_gap", "concern": "c"}},
    ]
    html, _ = render_events(events, None)
    assert html.count('data-dialog="1"') == 2
    assert html.count('data-problem="1"') == 1


def test_divider_stays_outside_msg_wrappers():
    events = [{"ts": "t", "type": "model_call",
               "payload": {"task_id": "T-001", "iter": 1}}]
    html, _ = render_events(events, None)
    divider_at = html.index('class="divider"')
    msg_at = html.index('class="msg"')
    assert divider_at < msg_at  # divider precedes, not nested in, the wrapper


def test_incremental_render_still_matches_one_shot():
    events = [
        {"ts": "t", "type": "model_call",
         "payload": {"task_id": "T-001", "iter": 1}},
        {"ts": "t", "type": "tool_call",
         "payload": {"task_id": "T-001", "tool": "bash", "args": {}}},
        {"ts": "t", "type": "evaluator_verdict",
         "payload": {"task_id": "T-002", "verdict": "accept", "concern": "c"}},
    ]
    one_shot, _ = render_events(events, None)
    first, last_task = render_events(events[:2], None)
    second, _ = render_events(events[2:], last_task)
    assert first + "\n" + second == one_shot


# ---- extract_facts -----------------------------------------------------------------


EVENTS = [
    {"ts": "2026-06-10T20:00:00Z", "type": "session_start",
     "payload": {"source": "/src", "worktree": "/wt", "branch": "session/x"}},
    {"ts": "2026-06-10T20:00:05Z", "type": "model_call",
     "payload": {"task_id": "T-001", "iter": 1, "prompt_tokens": 4000,
                 "eval_tokens": 250, "health": "ok"}},
    {"ts": "2026-06-10T20:00:07Z", "type": "tool_call",
     "payload": {"task_id": "T-001", "iter": 1, "tool": "bash",
                 "args": {"command": "ls"}}},
    {"ts": "2026-06-10T20:00:20Z", "type": "model_call",
     "payload": {"task_id": "T-001", "iter": 1, "phase": "evaluator",
                 "prompt_tokens": 3800, "eval_tokens": 900, "health": "ok"}},
    {"ts": "2026-06-10T20:00:21Z", "type": "evaluator_verdict",
     "payload": {"task_id": "T-001", "verdict": "reject",
                 "rejection_category": "acceptance_gap", "concern": "c"}},
    {"ts": "2026-06-10T20:00:30Z", "type": "task_done",
     "payload": {"task_id": "T-001", "summary": "s"}},
    {"ts": "2026-06-10T20:00:31Z", "type": "commit",
     "payload": {"task_id": "T-001", "sha": "abc123"}},
    {"ts": "2026-06-10T20:00:32Z", "type": "stop",
     "payload": {"reason": "all_done"}},
]


def test_extract_facts_shapes():
    facts = extract_facts(EVENTS)
    kinds = [f["e"] for f in facts]
    assert kinds == [
        "start", "model", "tool", "model", "verdict", "task_end", "commit", "stop",
    ]

    start = facts[0]
    assert start["t"] == 1781121600  # 2026-06-10T20:00:00Z

    worker = facts[1]
    assert worker == {
        "e": "model", "t": 1781121605, "task": "T-001", "iter": 1,
        "pt": 4000, "et": 250, "ct": 0, "rt": 0, "cost": 0.0,
        "phase": "worker", "health": "ok",
    }

    ev_call = facts[3]
    assert ev_call["phase"] == "evaluator"

    tool = facts[2]
    assert tool == {"e": "tool", "t": 1781121607, "task": "T-001", "tool": "bash"}

    verdict = facts[4]
    assert verdict["verdict"] == "reject"
    assert verdict["category"] == "acceptance_gap"

    task_end = facts[5]
    assert task_end == {"e": "task_end", "t": 1781121630, "task": "T-001",
                        "status": "done"}

    stop = facts[7]
    assert stop["reason"] == "all_done"


def test_model_fact_carries_cached_reasoning_cost():
    """OpenRouter detail (cached/reasoning tokens, USD cost) rides on the model
    fact so the dashboard can chart it without re-reading events."""
    events = [
        {"ts": "2026-06-10T20:00:05Z", "type": "model_call",
         "payload": {"task_id": "T-001", "iter": 1,
                     "prompt_tokens": 4000, "eval_tokens": 250,
                     "cached_tokens": 3200, "reasoning_tokens": 180,
                     "cost": 0.0012, "health": "ok"}},
    ]
    fact = extract_facts(events)[0]
    assert fact["ct"] == 3200
    assert fact["rt"] == 180
    assert fact["cost"] == 0.0012


def test_extract_facts_skips_unmapped_and_bad_timestamps():
    events = [
        {"ts": "2026-06-10T20:00:00Z", "type": "prompt_assembled",
         "payload": {"task_id": "T-001"}},
        {"ts": "not a timestamp", "type": "model_call",
         "payload": {"task_id": "T-001", "iter": 1}},
        {"type": "model_call", "payload": {"task_id": "T-001", "iter": 1}},
    ]
    assert extract_facts(events) == []


def test_task_failed_maps_to_failed_status():
    events = [{"ts": "2026-06-10T20:00:00Z", "type": "task_failed",
               "payload": {"task_id": "T-002", "reason": "iter_cap"}}]
    facts = extract_facts(events)
    assert facts[0]["status"] == "failed"
    assert facts[0]["reason"] == "iter_cap"


def test_pre_tool_block_becomes_block_fact():
    events = [{"ts": "2026-06-10T20:00:00Z", "type": "pre_tool_block",
               "payload": {"task_id": "T-001", "tool": "bash"}}]
    facts = extract_facts(events)
    assert facts[0]["e"] == "block" and facts[0]["tool"] == "bash"


# ---- events_payload carries facts ---------------------------------------------------


def test_events_payload_includes_facts(tmp_path: Path):
    sdir = tmp_path / "20260610-200000-dddddd"
    sdir.mkdir()
    with (sdir / "events.jsonl").open("a") as f:
        for ev in EVENTS:
            f.write(json.dumps(ev) + "\n")

    p = events_payload(sdir, 0, None)
    assert len(p["facts"]) == len(EVENTS)
    assert p["facts"][0]["e"] == "start"

    # incremental poll: facts arrive only for new events
    p2 = events_payload(sdir, p["offset"], p["last_task"])
    assert p2["facts"] == []
