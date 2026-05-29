"""summary.build_from_events rolls `evaluator_verdict` events into both
overall and per-task accept/reject counts AND `rejection_category` counts.

The v0 accepts/rejects bool was too coarse — a visualizer or post-run
review agent can't tell whether a 3-reject task hit the same failure
shape every time or three different ones. The Phase 1 sketch hinges on
that signal being preserved into summary.json.
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


def _verdict(
    ts: str, tid: str, verdict: str, category: str | None = None
) -> dict:
    payload: dict = {
        "task_id": tid,
        "verdict": verdict,
        "concern": "...",
        "evidence": [],
    }
    if verdict == "reject":
        payload["rejection_category"] = category
        payload["next_step"] = "..."
    return {"ts": ts, "type": "evaluator_verdict", "payload": payload}


def test_overall_accept_reject_counts(events_path):
    _write(
        events_path,
        [
            _verdict("T1", "T-1", "reject", "scope_creep"),
            _verdict("T2", "T-1", "reject", "acceptance_gap"),
            _verdict("T3", "T-1", "accept"),
            _verdict("T4", "T-2", "accept"),
        ],
    )
    s = summary.build_from_events(events_path)
    assert s["evaluator"]["accepts"] == 2
    assert s["evaluator"]["rejects"] == 2


def test_rejection_categories_aggregated_overall(events_path):
    _write(
        events_path,
        [
            _verdict("T1", "T-1", "reject", "scope_creep"),
            _verdict("T2", "T-1", "reject", "scope_creep"),
            _verdict("T3", "T-2", "reject", "acceptance_gap"),
            _verdict("T4", "T-2", "accept"),
        ],
    )
    s = summary.build_from_events(events_path)
    assert s["evaluator"]["rejection_categories"] == {
        "scope_creep": 2,
        "acceptance_gap": 1,
    }


def test_rejection_categories_per_task(events_path):
    _write(
        events_path,
        [
            _verdict("T1", "T-1", "reject", "scope_creep"),
            _verdict("T2", "T-1", "reject", "scope_creep"),
            _verdict("T3", "T-1", "reject", "acceptance_gap"),
            _verdict("T4", "T-2", "reject", "weak_test"),
        ],
    )
    s = summary.build_from_events(events_path)
    assert s["tasks"]["T-1"]["evaluator"]["accepts"] == 0
    assert s["tasks"]["T-1"]["evaluator"]["rejects"] == 3
    assert s["tasks"]["T-1"]["evaluator"]["rejection_categories"] == {
        "scope_creep": 2,
        "acceptance_gap": 1,
    }
    assert s["tasks"]["T-2"]["evaluator"]["rejection_categories"] == {
        "weak_test": 1,
    }


def test_accept_doesnt_pollute_rejection_categories(events_path):
    _write(
        events_path,
        [
            _verdict("T1", "T-1", "accept"),
            _verdict("T2", "T-2", "accept"),
        ],
    )
    s = summary.build_from_events(events_path)
    assert s["evaluator"]["rejection_categories"] == {}


def test_reject_with_null_category_doesnt_crash(events_path):
    """A defensive-parse fallback might emit a reject verdict with a
    null rejection_category (parse-failure case). Summary should count
    it as a reject without trying to index a null key."""
    _write(
        events_path,
        [_verdict("T1", "T-1", "reject", None)],
    )
    s = summary.build_from_events(events_path)
    assert s["evaluator"]["rejects"] == 1
    assert s["evaluator"]["rejection_categories"] == {}
