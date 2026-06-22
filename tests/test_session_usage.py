"""Session.record_usage feeds the per-actor breakdown and the running token
total, and the breakdown round-trips through the checkpoint.

The load-bearing property: `tokens_used` advances by `prompt + eval` only —
cached/reasoning are subsets and must not inflate it. The full detail (including
`cost`) lives in the per-actor `usage` breakdown; `cost_used()` sums that cost
into the dollar-spend cap counter.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tilth.session import Session
from tilth.usage import zero_usage


@pytest.fixture
def sessions_root(tmp_path: Path) -> Path:
    return tmp_path


def _u(prompt, eval_, *, cached=0, reasoning=0, cost=0.0):
    return {
        "prompt": prompt, "eval": eval_, "total": prompt + eval_,
        "cached": cached, "reasoning": reasoning, "cost": cost,
    }


def test_token_total_advances_by_prompt_plus_eval_only(sessions_root):
    s = Session.new(sessions_root)
    s.record_usage(_u(100, 40, cached=80, reasoning=30, cost=0.001))
    # cached (⊆prompt) and reasoning (⊆eval) must NOT be added on top.
    assert s.tokens_used == 140


def test_cost_used_sums_both_actors(sessions_root):
    s = Session.new(sessions_root)
    s.record_usage(_u(100, 40, cost=0.002))
    s.record_usage(_u(50, 10, cost=0.003), phase="evaluator")
    # The dollar-spend cap reads this — worker + evaluator cost combined.
    assert s.cost_used() == pytest.approx(0.005)


def test_cost_used_round_trips_through_wake(sessions_root):
    s = Session.new(sessions_root)
    s.record_usage(_u(100, 40, cost=0.002), phase="evaluator")
    woken = Session.wake(sessions_root, s.session_id)
    assert woken.cost_used() == pytest.approx(0.002)


def test_cost_used_zero_when_provider_omits_cost(sessions_root):
    s = Session.new(sessions_root)
    s.record_usage(_u(100, 40))  # no cost reported
    assert s.cost_used() == 0.0


def test_usage_routes_to_worker_by_default(sessions_root):
    s = Session.new(sessions_root)
    s.record_usage(_u(100, 40, cost=0.002))
    assert s.usage["worker"]["prompt"] == 100
    assert s.usage["worker"]["eval"] == 40
    assert s.usage["worker"]["cost"] == pytest.approx(0.002)
    assert s.usage["evaluator"] == zero_usage()


def test_usage_routes_to_evaluator_on_phase(sessions_root):
    s = Session.new(sessions_root)
    s.record_usage(_u(50, 10), phase="evaluator")
    s.record_usage(_u(100, 40), phase=None)
    assert s.usage["evaluator"]["prompt"] == 50
    assert s.usage["worker"]["prompt"] == 100
    assert s.tokens_used == 200


def test_detail_accumulates_across_calls(sessions_root):
    s = Session.new(sessions_root)
    s.record_usage(_u(100, 40, cached=80, reasoning=30, cost=0.001))
    s.record_usage(_u(200, 30, cached=150, reasoning=10, cost=0.002))
    w = s.usage["worker"]
    assert (w["prompt"], w["eval"], w["cached"], w["reasoning"]) == (300, 70, 230, 40)
    assert w["cost"] == pytest.approx(0.003)


def test_usage_round_trips_through_wake(sessions_root):
    s = Session.new(sessions_root)
    s.record_usage(_u(100, 40, cached=10, reasoning=5, cost=0.001), phase="evaluator")
    woken = Session.wake(sessions_root, s.session_id)
    assert woken.usage["evaluator"]["prompt"] == 100
    assert woken.usage["evaluator"]["cost"] == pytest.approx(0.001)
    assert woken.tokens_used == 140


def test_usage_present_in_checkpoint_json(sessions_root):
    s = Session.new(sessions_root)
    s.record_usage(_u(10, 5))
    cp = json.loads(s.checkpoint_path.read_text())
    assert set(cp["usage"]) == {"worker", "evaluator"}
    assert cp["usage"]["worker"]["prompt"] == 10


def test_wake_tolerates_old_checkpoint_without_usage(sessions_root):
    """Pre-refactor checkpoints have no `usage` key; wake must default it to a
    zeroed breakdown while still restoring the running token total."""
    s = Session.new(sessions_root)
    s.record_usage(_u(100, 40))
    cp = json.loads(s.checkpoint_path.read_text())
    cp.pop("usage", None)
    s.checkpoint_path.write_text(json.dumps(cp))

    woken = Session.wake(sessions_root, s.session_id)
    assert woken.usage == {"worker": zero_usage(), "evaluator": zero_usage()}
    assert woken.tokens_used == 140  # running token total still restored
