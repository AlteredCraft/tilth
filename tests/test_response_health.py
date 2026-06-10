"""Provider-health classification: judge a model call by the provider's own
signals, not by the shape of the message.

Regression context (session 20260610-100626-8c0142): OpenRouter returned
`finish_reason: "error"` mid-generation — a partial response carrying 36
tokens of truncated reasoning, no content, no tool calls. The shape-based
empty check saw the reasoning and called the turn healthy; the loop appended
the corrupted partial to history and nudged the worker for "going quiet".
`response_health` exists so the next failure *shape* can't repeat this: the
provider's `error` object and `finish_reason` are checked before any shape
heuristic.

Also pins that `_normalise` retains the evidence those checks (and post-run
forensics) need — `error`, `provider`, `response_id`, `model` — instead of
flattening them away.
"""

from __future__ import annotations

from typing import Any

import pytest

from tilth.client import _normalise, response_health

# ---- response_health (pure) -------------------------------------------------

OK_TOOL_CALLS = {
    "message": {"role": "assistant", "content": None, "tool_calls": [{"id": "x"}]},
    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    "finish_reason": "tool_calls",
}
OK_CONTENT = {
    "message": {"role": "assistant", "content": "done."},
    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    "finish_reason": "stop",
}
# The 2026-06-10 iter-5 shape: provider errored mid-generation, partial
# reasoning survived. Must classify unhealthy *despite* the reasoning.
ERROR_FINISH_PARTIAL_REASONING = {
    "message": {
        "role": "assistant",
        "content": None,
        "reasoning_details": [{"type": "reasoning.text", "text": "Good. Now let me te"}],
    },
    "usage": {"prompt_tokens": 4767, "completion_tokens": 36},
    "finish_reason": "error",
}
# OpenRouter mid-stream error body: error object alongside a choice.
ERROR_OBJECT = {
    "message": {"role": "assistant", "content": ""},
    "usage": {},
    "finish_reason": "stop",
    "error": {"code": 502, "message": "upstream provider error"},
}
# The 2026-05-30 shape: empty 200 — no message payload, no usage at all.
HARD_EMPTY = {"message": {}, "usage": {}, "finish_reason": "stop"}
# Reasoning-only with a clean stop is a *healthy* (if quiet) turn — it routes
# to the no-case nudge, not the provider-retry path.
QUIET_REASONING_ONLY = {
    "message": {"role": "assistant", "content": "", "reasoning": "hmm"},
    "usage": {"prompt_tokens": 10, "completion_tokens": 3},
    "finish_reason": "stop",
}


@pytest.mark.parametrize(
    "resp, expected",
    [
        (OK_TOOL_CALLS, "ok"),
        (OK_CONTENT, "ok"),
        (ERROR_FINISH_PARTIAL_REASONING, "provider_error"),
        (ERROR_OBJECT, "provider_error"),
        (HARD_EMPTY, "empty"),
        (QUIET_REASONING_ONLY, "ok"),
    ],
)
def test_response_health(resp: dict[str, Any], expected: str):
    health, _detail = response_health(resp)
    assert health == expected


def test_provider_error_detail_carries_the_error_message():
    _health, detail = response_health(ERROR_OBJECT)
    assert "upstream provider error" in (detail or "")


def test_error_object_wins_over_healthy_looking_message():
    resp = {**OK_TOOL_CALLS, "error": {"message": "boom"}}
    health, _ = response_health(resp)
    assert health == "provider_error"


# ---- _normalise keeps the evidence ------------------------------------------

class _Stub:
    def __init__(self, d: dict[str, Any]):
        self._d = d

    def model_dump(self) -> dict[str, Any]:
        return self._d


def test_normalise_keeps_choice_level_error():
    resp = _normalise(_Stub({
        "id": "gen-123",
        "model": "deepseek/deepseek-v4-flash",
        "provider": "SomeUpstream",
        "choices": [{
            "message": {"role": "assistant", "content": None},
            "finish_reason": "error",
            "error": {"code": 502, "message": "upstream blew up"},
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 0},
    }))
    assert resp["error"] == {"code": 502, "message": "upstream blew up"}
    assert resp["finish_reason"] == "error"
    assert resp["provider"] == "SomeUpstream"
    assert resp["response_id"] == "gen-123"
    assert resp["model"] == "deepseek/deepseek-v4-flash"


def test_normalise_keeps_top_level_error():
    resp = _normalise(_Stub({
        "choices": [{"message": {}, "finish_reason": "stop"}],
        "usage": {},
        "error": {"message": "rate limited"},
    }))
    assert resp["error"] == {"message": "rate limited"}


def test_normalise_omits_absent_evidence_fields():
    resp = _normalise(_Stub({
        "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }))
    for key in ("error", "provider", "response_id", "model"):
        assert key not in resp
