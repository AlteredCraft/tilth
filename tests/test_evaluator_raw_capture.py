"""`_raw_tool_calls` faithfully captures a model's emitted tool-call args.

This is the mechanism behind the observability guarantee added after
session 20260528-074315: when the evaluator's response fails to parse,
the raw payload must survive in `events.jsonl` so a post-run reviewer can
see what the model actually emitted — without replaying the model.
"""

from __future__ import annotations

import json

from tilth.loop import _raw_tool_calls


def _msg(tool_calls: list[dict]) -> dict:
    return {"role": "assistant", "tool_calls": tool_calls}


def test_captures_string_arguments_verbatim():
    raw = '{"verdict": "accept</|DSML|param>'  # the unparseable shape
    msg = _msg([{"function": {"name": "submit_verdict", "arguments": raw}}])
    out = _raw_tool_calls(msg, 16_000)
    assert out == [{"name": "submit_verdict", "arguments": raw}]


def test_dict_arguments_are_json_encoded():
    msg = _msg(
        [{"function": {"name": "submit_verdict",
                       "arguments": {"verdict": "accept"}}}]
    )
    out = _raw_tool_calls(msg, 16_000)
    assert len(out) == 1
    assert json.loads(out[0]["arguments"]) == {"verdict": "accept"}


def test_caps_long_arguments():
    raw = "x" * 50_000
    msg = _msg([{"function": {"name": "submit_verdict", "arguments": raw}}])
    out = _raw_tool_calls(msg, 16_000)
    assert len(out[0]["arguments"]) == 16_000


def test_captures_all_siblings():
    """The DSML-leakage case: a clean call plus a corrupted one. Both are
    captured so the reviewer sees the full picture, not just the survivor."""
    msg = _msg(
        [
            {"function": {"name": "submit_verdict",
                          "arguments": '{"verdict": "accept", "concern": "ok"}'}},
            {"function": {"name": "submit_verdict",
                          "arguments": '{"verdict": "accept</|DSML|param>'}},
        ]
    )
    out = _raw_tool_calls(msg, 16_000)
    assert len(out) == 2


def test_no_tool_calls_yields_empty_list():
    assert _raw_tool_calls({"role": "assistant", "content": "hi"}, 16_000) == []
