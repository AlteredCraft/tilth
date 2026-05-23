"""finish_reason must round-trip from the upstream choice to the dict the loop
sees, so loop.py can record it on the model_call event.

Why this matters: a `finish_reason` of `"length"` means the response — often a
tool-call argument — was cut off by the provider's max-tokens limit. The agent
will then be acting on truncated output. If we never surface this in
events.jsonl, the only signal is downstream (e.g. ruff catching a syntax error
from a half-written file), which is several iterations late.
"""

from __future__ import annotations

from typing import Any

from tilth.client import _normalise


def _resp(finish_reason: Any) -> Any:
    """Stand-in for the OpenAI ChatCompletion shape `_normalise` consumes."""
    class _Stub:
        def model_dump(self) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
    return _Stub()


def test_finish_reason_length_is_propagated():
    assert _normalise(_resp("length")).get("finish_reason") == "length"


def test_finish_reason_stop_is_propagated():
    assert _normalise(_resp("stop")).get("finish_reason") == "stop"


def test_finish_reason_absent_when_provider_omits_it():
    assert _normalise(_resp(None)).get("finish_reason") is None


def test_finish_reason_absent_when_no_choices():
    class _Empty:
        def model_dump(self) -> dict[str, Any]:
            return {"choices": [], "usage": {}}
    assert _normalise(_Empty()).get("finish_reason") is None
