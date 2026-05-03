"""Regression tests for `_token_breakdowns`.

The end-of-session summary's per-task / per-model lines are computed by replaying
events.jsonl, not from in-memory state. Pin the contract: aggregate every
`model_call` event by `task_id` and `model`, split per-task by `kind`, and
gracefully degrade for older events that pre-date the `kind`/`model` fields.
"""

from __future__ import annotations

import json
from pathlib import Path

from tilth.loop import _token_breakdowns
from tilth.session import Session


def _make_session(tmp_path: Path, events: list[dict]) -> Session:
    root = tmp_path / "sess"
    root.mkdir()
    events_path = root / "events.jsonl"
    with events_path.open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return Session(
        session_id="test",
        root=root,
        events_path=events_path,
        checkpoint_path=root / "checkpoint.json",
    )


def test_aggregates_by_model_and_task_with_kind_split(tmp_path: Path) -> None:
    session = _make_session(
        tmp_path,
        [
            {"type": "model_call", "payload": {
                "task_id": "T1", "kind": "worker", "model": "worker-x",
                "prompt_tokens": 100, "eval_tokens": 50,
            }},
            {"type": "model_call", "payload": {
                "task_id": "T1", "kind": "worker", "model": "worker-x",
                "prompt_tokens": 200, "eval_tokens": 0,
            }},
            {"type": "model_call", "payload": {
                "task_id": "T1", "kind": "judge", "model": "judge-y",
                "prompt_tokens": 80, "eval_tokens": 20,
            }},
            {"type": "model_call", "payload": {
                "task_id": "T1", "kind": "self_improve", "model": "worker-x",
                "prompt_tokens": 30, "eval_tokens": 10,
            }},
            {"type": "model_call", "payload": {
                "task_id": "T2", "kind": "worker", "model": "worker-x",
                "prompt_tokens": 5, "eval_tokens": 5,
            }},
            {"type": "tool_call", "payload": {"task_id": "T1", "tool": "bash"}},
        ],
    )

    by_model, by_task = _token_breakdowns(session)

    assert by_model == {"worker-x": 100 + 50 + 200 + 30 + 10 + 5 + 5, "judge-y": 100}
    assert by_task["T1"]["total"] == 100 + 50 + 200 + 80 + 20 + 30 + 10
    assert by_task["T1"]["worker"] == 100 + 50 + 200
    assert by_task["T1"]["judge"] == 80 + 20
    assert by_task["T1"]["self_improve"] == 30 + 10
    assert by_task["T2"]["total"] == 10
    assert by_task["T2"]["worker"] == 10


def test_skips_zero_token_events(tmp_path: Path) -> None:
    """Defensive: providers occasionally return 0/null usage; don't pollute the breakdown."""
    session = _make_session(
        tmp_path,
        [
            {"type": "model_call", "payload": {
                "task_id": "T1", "kind": "worker", "model": "m",
                "prompt_tokens": 0, "eval_tokens": 0,
            }},
            {"type": "model_call", "payload": {
                "task_id": "T1", "kind": "worker", "model": "m",
                "prompt_tokens": None, "eval_tokens": None,
            }},
        ],
    )
    by_model, by_task = _token_breakdowns(session)
    assert by_model == {}
    assert by_task == {}


def test_legacy_events_without_kind_or_model_bucketed_safely(tmp_path: Path) -> None:
    """Pre-breakdown events lack `kind` and `model`. Replay must not crash."""
    session = _make_session(
        tmp_path,
        [
            {"type": "model_call", "payload": {
                "task_id": "T1",
                "prompt_tokens": 10, "eval_tokens": 5,
            }},
        ],
    )
    by_model, by_task = _token_breakdowns(session)
    assert by_model == {"unknown": 15}
    assert by_task["T1"]["total"] == 15
    assert by_task["T1"]["worker"] == 15


def test_missing_events_file_returns_empty(tmp_path: Path) -> None:
    root = tmp_path / "no-log"
    root.mkdir()
    session = Session(
        session_id="test",
        root=root,
        events_path=root / "events.jsonl",
        checkpoint_path=root / "checkpoint.json",
    )
    assert _token_breakdowns(session) == ({}, {})


def test_skips_malformed_jsonl_lines(tmp_path: Path) -> None:
    """Match `_last_stop_reason`'s tolerance: skip lines that aren't valid JSON."""
    root = tmp_path / "sess"
    root.mkdir()
    events_path = root / "events.jsonl"
    events_path.write_text(
        json.dumps({"type": "model_call", "payload": {
            "task_id": "T1", "kind": "worker", "model": "m",
            "prompt_tokens": 10, "eval_tokens": 5,
        }}) + "\n"
        "not valid json\n"
    )
    session = Session(
        session_id="test",
        root=root,
        events_path=events_path,
        checkpoint_path=root / "checkpoint.json",
    )
    by_model, by_task = _token_breakdowns(session)
    assert by_model == {"m": 15}
    assert by_task["T1"]["total"] == 15
