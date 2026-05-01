"""Regression tests for `_assistant_history_message`.

Pin the wire-format contract observed live against deepseek/deepseek-v4-flash
via OpenRouter. Stripping reasoning content from the echoed assistant message
causes the upstream provider to reject the next request with HTTP 400
"reasoning_content must be passed back to the API". Note: the error text
names SiliconFlow's *upstream-internal* field; the field name on OpenRouter's
wire is `reasoning_details` (with a flat `reasoning` string alongside).
"""

from __future__ import annotations

from tilth.loop import _assistant_history_message


def test_preserves_role_content_and_tool_calls():
    msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"command": "ls"}'},
            }
        ],
    }
    assert _assistant_history_message(msg) == msg


def test_preserves_reasoning_details_openrouter_real_shape():
    """Full assistant message shape probed live from OpenRouter."""
    msg = {
        "role": "assistant",
        "content": None,
        "reasoning": "The user wants me to list files in /tmp.",
        "reasoning_details": [
            {
                "type": "reasoning.text",
                "text": "The user wants me to list files in /tmp.",
                "format": "unknown",
                "index": 0,
            }
        ],
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"command": "ls /tmp"}'},
            }
        ],
    }
    out = _assistant_history_message(msg)
    assert out["reasoning_details"] == msg["reasoning_details"]
    assert out["reasoning"] == msg["reasoning"]
    assert out["tool_calls"] == msg["tool_calls"]
    assert out["content"] is None
    assert out["role"] == "assistant"


def test_drops_output_only_fields():
    """OpenRouter responses include output-only fields the request rejects."""
    msg = {
        "role": "assistant",
        "content": "blocked",
        "refusal": "I can't do that",
        "annotations": [{"type": "url_citation"}],
        "audio": {"id": "audio_1"},
        "function_call": None,
    }
    out = _assistant_history_message(msg)
    assert set(out.keys()) == {"role", "content"}
    assert out["content"] == "blocked"
