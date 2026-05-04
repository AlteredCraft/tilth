"""summary.build_from_events rolls events.jsonl into a snapshot suitable for
sessions/<id>/summary.json.

The summary is what the visualize layer (and external consumers like the article
screenshots, a stat printer, etc.) read instead of re-streaming the JSONL on
every refresh. It must be cheap to recompute and stable in shape.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tilth import summary


@pytest.fixture
def events_path(tmp_path: Path) -> Path:
    return tmp_path / "events.jsonl"


def _write(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_empty_events_file_yields_zeroed_summary(events_path):
    events_path.write_text("")
    s = summary.build_from_events(events_path)
    assert s["tokens"] == {"prompt": 0, "eval": 0, "total": 0}
    assert s["tasks"] == {}
    assert s["tool_histogram"] == {}


def test_session_metadata_captured(events_path):
    _write(
        events_path,
        [
            {
                "ts": "2026-05-04T10:00:00Z",
                "type": "session_start",
                "payload": {"source": "/tmp/foo", "worktree": "/tmp/wt", "branch": "session/x"},
            },
            {"ts": "2026-05-04T10:05:00Z", "type": "stop", "payload": {"reason": "all_done"}},
        ],
    )
    s = summary.build_from_events(events_path)
    assert s["started_at"] == "2026-05-04T10:00:00Z"
    assert s["last_event_at"] == "2026-05-04T10:05:00Z"
    assert s["stop"] == {"reason": "all_done", "ts": "2026-05-04T10:05:00Z"}


def test_tokens_summed_across_model_calls(events_path):
    _write(
        events_path,
        [
            {
                "ts": "T1",
                "type": "model_call",
                "payload": {
                    "task_id": "T-1", "iter": 1,
                    "prompt_tokens": 100, "eval_tokens": 50,
                    "tokens_used_total": 150,
                },
            },
            {
                "ts": "T2",
                "type": "model_call",
                "payload": {
                    "task_id": "T-1", "iter": 2,
                    "prompt_tokens": 200, "eval_tokens": 30,
                    "tokens_used_total": 380,
                },
            },
        ],
    )
    s = summary.build_from_events(events_path)
    assert s["tokens"] == {"prompt": 300, "eval": 80, "total": 380}


def _mc(ts: str, tid: str, iter_n: int) -> dict:
    return {
        "ts": ts,
        "type": "model_call",
        "payload": {"task_id": tid, "iter": iter_n, "prompt_tokens": 10, "eval_tokens": 5},
    }


def test_per_task_iteration_count_and_status(events_path):
    _write(
        events_path,
        [
            {"ts": "T1", "type": "context_reset", "payload": {"task_id": "T-1"}},
            _mc("T2", "T-1", 1),
            _mc("T3", "T-1", 2),
            {"ts": "T4", "type": "task_done", "payload": {"task_id": "T-1", "summary": "ok"}},
            {"ts": "T5", "type": "context_reset", "payload": {"task_id": "T-2"}},
            _mc("T6", "T-2", 1),
            {
                "ts": "T7",
                "type": "task_failed",
                "payload": {"task_id": "T-2", "reason": "iter_cap"},
            },
        ],
    )
    s = summary.build_from_events(events_path)
    assert s["tasks"]["T-1"]["status"] == "done"
    assert s["tasks"]["T-1"]["iterations"] == 2
    assert s["tasks"]["T-2"]["status"] == "failed"
    assert s["tasks"]["T-2"]["failure_reason"] == "iter_cap"


def test_tool_histogram_counts_tool_calls(events_path):
    _write(
        events_path,
        [
            {"ts": "T1", "type": "tool_call", "payload": {"task_id": "T-1", "tool": "bash"}},
            {"ts": "T2", "type": "tool_call", "payload": {"task_id": "T-1", "tool": "bash"}},
            {"ts": "T3", "type": "tool_call", "payload": {"task_id": "T-1", "tool": "write_file"}},
        ],
    )
    s = summary.build_from_events(events_path)
    assert s["tool_histogram"] == {"bash": 2, "write_file": 1}
    assert s["tasks"]["T-1"]["tool_calls"] == {"bash": 2, "write_file": 1}


def _hr(ts: str, hook: str, outcome: str, tool: str) -> dict:
    return {
        "ts": ts,
        "type": "hook_run",
        "payload": {"hook": hook, "outcome": outcome, "tool": tool},
    }


def test_hook_outcomes_aggregated(events_path):
    _write(
        events_path,
        [
            _hr("T1", "pre_tool", "allow", "bash"),
            _hr("T2", "pre_tool", "block", "bash"),
            _hr("T3", "post_edit", "silent", "write_file"),
            _hr("T4", "post_edit", "warned", "write_file"),
        ],
    )
    s = summary.build_from_events(events_path)
    assert s["hook_outcomes"]["pre_tool"] == {"allow": 1, "block": 1}
    assert s["hook_outcomes"]["post_edit"] == {"silent": 1, "warned": 1}


def test_judge_accept_reject_counted(events_path):
    _write(
        events_path,
        [
            {"ts": "T1", "type": "judge_verdict", "payload": {"task_id": "T-1", "accept": True}},
            {"ts": "T2", "type": "judge_verdict", "payload": {"task_id": "T-1", "accept": False}},
            {"ts": "T3", "type": "judge_verdict", "payload": {"task_id": "T-2", "accept": True}},
        ],
    )
    s = summary.build_from_events(events_path)
    assert s["judge"] == {"accepts": 2, "rejects": 1}
    assert s["tasks"]["T-1"]["judge"] == {"accepts": 1, "rejects": 1}


def test_write_summary_writes_json_file(tmp_path):
    events_path = tmp_path / "events.jsonl"
    out_path = tmp_path / "summary.json"
    events_path.write_text("")
    summary.write_summary(events_path, out_path)
    assert out_path.is_file()
    loaded = json.loads(out_path.read_text())
    assert "tokens" in loaded
