"""Limit-utilization data path for the visualizer.

The caps a run is configured with (token budget, wall clock, per-task
iterations, per-task evaluator calls) are config-derived — they live in env
vars, not in any session file. The read-only viewer can't read the config, so
the caps are recorded in the `session_start` event and carried through
`extract_facts` like every other chart input. Pinned here:

- `TilthConfig.limits()` is the single source of the cap dict (so the recorded
  shape and the enforced caps can't drift).
- the `session_start` fact carries those limits when present and omits the key
  otherwise (old sessions predate the field — the client degrades gracefully).
- `events_payload` ships the limits inside the start fact, on the same byte
  cursor as every other fact.
"""

from __future__ import annotations

import json
from pathlib import Path

from tilth.client import TilthConfig
from tilth.visualize.render import extract_facts
from tilth.visualize.server import events_payload


def _config(**over) -> TilthConfig:
    base = dict(
        base_url="u",
        api_key="k",
        worker_model="w",
        evaluator_base_url="u",
        evaluator_api_key="k",
        evaluator_model="w",
        max_iterations_per_task=32,
        max_evaluator_calls_per_task=0,
        max_wall_clock_minutes=120,
        max_token_dollar_spend=10.0,
    )
    base.update(over)
    return TilthConfig(**base)


def test_config_limits_dict():
    assert _config().limits() == {
        "max_token_dollar_spend": 10.0,
        "max_wall_clock_minutes": 120,
        "max_iterations_per_task": 32,
        "max_evaluator_calls_per_task": 0,
    }


def test_config_limits_reflects_overrides():
    limits = _config(max_token_dollar_spend=5.0, max_iterations_per_task=8).limits()
    assert limits["max_token_dollar_spend"] == 5.0
    assert limits["max_iterations_per_task"] == 8


def test_session_start_fact_carries_limits():
    limits = _config(max_token_dollar_spend=5.0).limits()
    ev = {
        "ts": "2026-06-10T20:00:00Z",
        "type": "session_start",
        "payload": {"source": "/s", "limits": limits},
    }
    fact = extract_facts([ev])[0]
    assert fact["e"] == "start"
    assert fact["limits"] == limits


def test_session_start_fact_omits_limits_when_absent():
    ev = {
        "ts": "2026-06-10T20:00:00Z",
        "type": "session_start",
        "payload": {"source": "/s"},
    }
    fact = extract_facts([ev])[0]
    assert fact["e"] == "start"
    assert "limits" not in fact


def test_session_start_fact_ignores_non_dict_limits():
    ev = {
        "ts": "2026-06-10T20:00:00Z",
        "type": "session_start",
        "payload": {"source": "/s", "limits": "nope"},
    }
    fact = extract_facts([ev])[0]
    assert "limits" not in fact


def test_session_start_fact_carries_task_count():
    ev = {
        "ts": "2026-06-10T20:00:00Z",
        "type": "session_start",
        "payload": {"source": "/s", "task_count": 4},
    }
    fact = extract_facts([ev])[0]
    assert fact["task_count"] == 4


def test_session_start_fact_omits_task_count_when_absent():
    ev = {
        "ts": "2026-06-10T20:00:00Z",
        "type": "session_start",
        "payload": {"source": "/s"},
    }
    fact = extract_facts([ev])[0]
    assert "task_count" not in fact


def test_events_payload_ships_limits_in_start_fact(tmp_path: Path):
    sdir = tmp_path / "20260610-200000-aaaaaa"
    sdir.mkdir()
    limits = _config().limits()
    ev = {
        "ts": "2026-06-10T20:00:00Z",
        "type": "session_start",
        "payload": {"source": "/s", "limits": limits},
    }
    (sdir / "events.jsonl").write_text(json.dumps(ev) + "\n")

    p = events_payload(sdir, 0, None)
    assert p["facts"][0]["e"] == "start"
    assert p["facts"][0]["limits"] == limits
