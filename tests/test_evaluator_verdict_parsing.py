"""Defensive parsing of the evaluator's `submit_verdict` tool call.

The probe (proposals/probes/phase1_verdict_tool_call_probe.py) showed that
some provider/model combos emit multiple tool_calls per response — one
clean OpenAI-shape call and a second corrupted by upstream-provider
template syntax leaking through. `parse_verdict` must iterate the list,
take the first call that names `submit_verdict`, parses as JSON, and
validates against the schema — ignoring siblings.

On total parse failure, it must return an error message the loop can
forward to the model as `tool_result` feedback so the next attempt has
a chance to recover.
"""

from __future__ import annotations

import json

import pytest

from tilth.verdict import parse_verdict


def _msg(tool_calls: list[dict]) -> dict:
    return {"role": "assistant", "content": None, "tool_calls": tool_calls}


def _tc(name: str, args: dict | str) -> dict:
    return {
        "id": "call_x",
        "function": {
            "name": name,
            "arguments": json.dumps(args) if isinstance(args, dict) else args,
        },
    }


def test_happy_path_accept():
    msg = _msg(
        [_tc("submit_verdict", {
            "verdict": "accept",
            "concern": "Looks good.",
            "evidence": ["pkg/foo.py:12"],
        })]
    )
    verdict, err = parse_verdict(msg)
    assert err is None
    assert verdict["verdict"] == "accept"
    assert verdict["concern"] == "Looks good."
    assert verdict["evidence"] == ["pkg/foo.py:12"]
    assert verdict.get("rejection_category") is None
    assert verdict.get("next_step") in (None, "")


def test_happy_path_reject():
    msg = _msg(
        [_tc("submit_verdict", {
            "verdict": "reject",
            "rejection_category": "acceptance_gap",
            "concern": "Empty-name case not handled.",
            "evidence": ["pkg/foo.py:2"],
            "next_step": "Add a guard for empty name.",
        })]
    )
    verdict, err = parse_verdict(msg)
    assert err is None
    assert verdict["verdict"] == "reject"
    assert verdict["rejection_category"] == "acceptance_gap"
    assert verdict["next_step"] == "Add a guard for empty name."


def test_no_tool_calls_returns_error():
    verdict, err = parse_verdict(_msg([]))
    assert verdict is None
    assert "submit_verdict" in err


def test_wrong_tool_name_skipped():
    msg = _msg([_tc("submit_case",
                    {"verdict": "accept", "concern": "x", "evidence": []})])
    verdict, err = parse_verdict(msg)
    assert verdict is None
    assert "submit_verdict" in err


def test_first_valid_call_wins_over_corrupted_sibling():
    """The DSML-leakage finding from the probe — corrupted second call,
    clean first call. Parser must use the first valid one."""
    msg = _msg(
        [
            _tc("submit_verdict",
                {"verdict": "accept", "concern": "Fine.", "evidence": []}),
            _tc("submit_verdict", '{"verdict": "accept</|DSML|param>'),
        ]
    )
    verdict, err = parse_verdict(msg)
    assert err is None
    assert verdict["verdict"] == "accept"


def test_clean_sibling_after_corrupted_first_still_wins():
    """Belt-and-braces: even if the leaked-template call comes first,
    parser must keep scanning and accept the well-formed sibling."""
    msg = _msg(
        [
            _tc("submit_verdict", '{"verdict": "accept</|DSML|param>'),
            _tc("submit_verdict",
                {"verdict": "accept", "concern": "Fine.", "evidence": []}),
        ]
    )
    verdict, err = parse_verdict(msg)
    assert err is None
    assert verdict["verdict"] == "accept"


def test_all_calls_unparseable_returns_aggregate_error():
    msg = _msg(
        [
            _tc("submit_verdict", '{"verdict": "accept</|DSML|param>'),
            _tc("submit_verdict", '{trailing'),
        ]
    )
    verdict, err = parse_verdict(msg)
    assert verdict is None
    assert "parse" in err.lower() or "json" in err.lower()


@pytest.mark.parametrize(
    "args,expected_in_error",
    [
        # missing required field
        ({"concern": "no verdict", "evidence": []}, "verdict"),
        ({"verdict": "accept", "evidence": []}, "concern"),
        ({"verdict": "accept", "concern": "x"}, "evidence"),
        # bad verdict enum
        ({"verdict": "approve", "concern": "x", "evidence": []}, "verdict"),
        # reject without rejection_category
        ({"verdict": "reject", "concern": "x", "evidence": [],
          "next_step": "x"}, "rejection_category"),
        # reject without next_step
        ({"verdict": "reject", "rejection_category": "acceptance_gap",
          "concern": "x", "evidence": []}, "next_step"),
        # bad rejection_category enum
        ({"verdict": "reject", "rejection_category": "made_up",
          "concern": "x", "evidence": [], "next_step": "x"},
         "rejection_category"),
        # evidence not a list
        ({"verdict": "accept", "concern": "x",
          "evidence": "pkg/foo.py:1"}, "evidence"),
        # empty concern
        ({"verdict": "accept", "concern": "", "evidence": []}, "concern"),
        # accept with non-null rejection_category
        ({"verdict": "accept", "rejection_category": "scope_creep",
          "concern": "x", "evidence": []}, "rejection_category"),
    ],
)
def test_schema_violations_each_named_in_error(args, expected_in_error):
    msg = _msg([_tc("submit_verdict", args)])
    verdict, err = parse_verdict(msg)
    assert verdict is None
    assert expected_in_error in err.lower()


def test_already_parsed_dict_arguments_accepted():
    """Some SDK paths return tool-call arguments as a dict, not a JSON
    string. Mirror what loop.py:694 already does for worker tool calls."""
    msg = _msg(
        [
            {
                "id": "call_x",
                "function": {
                    "name": "submit_verdict",
                    "arguments": {
                        "verdict": "accept",
                        "concern": "x",
                        "evidence": [],
                    },
                },
            }
        ]
    )
    verdict, err = parse_verdict(msg)
    assert err is None
    assert verdict["verdict"] == "accept"


def test_empty_string_rejection_category_coerced_to_null_on_accept():
    """Observed in session 20260528-074315: models emit `""` for an optional
    field instead of omitting it. Empty string is unambiguously 'no value' —
    coerce it, don't burn a retry. Value-local, not a verdict-based heuristic."""
    msg = _msg(
        [_tc("submit_verdict", {
            "verdict": "accept",
            "rejection_category": "",
            "next_step": "",
            "concern": "Looks good.",
            "evidence": [],
        })]
    )
    verdict, err = parse_verdict(msg)
    assert err is None
    assert verdict["verdict"] == "accept"
    assert verdict["rejection_category"] is None
    assert verdict["next_step"] is None


def test_empty_string_next_step_coerced_on_reject_still_needs_category():
    """Empty-string coercion is value-local — it applies on reject too.
    A reject with an empty next_step becomes next_step=None, which then
    correctly fails the 'reject needs next_step' rule."""
    msg = _msg(
        [_tc("submit_verdict", {
            "verdict": "reject",
            "rejection_category": "acceptance_gap",
            "next_step": "   ",
            "concern": "Missing behaviour.",
            "evidence": ["pkg/foo.py:2"],
        })]
    )
    verdict, err = parse_verdict(msg)
    assert verdict is None
    assert "next_step" in err.lower()


def test_real_category_on_accept_is_not_silently_coerced():
    """A real category on an accept is a genuine contradiction — we do NOT
    null it based on the verdict value. It stays a validation error so the
    model re-emits a coherent verdict."""
    msg = _msg(
        [_tc("submit_verdict", {
            "verdict": "accept",
            "rejection_category": "scope_creep",
            "concern": "x",
            "evidence": [],
        })]
    )
    verdict, err = parse_verdict(msg)
    assert verdict is None
    assert "rejection_category" in err.lower()


def test_extra_fields_rejected():
    """Forward-compat ratchet: unexpected keys mean either schema drift
    or the model hallucinated a field, and either way we want to know."""
    msg = _msg(
        [_tc("submit_verdict", {
            "verdict": "accept",
            "concern": "x",
            "evidence": [],
            "confidence": 0.9,  # not in schema
        })]
    )
    verdict, err = parse_verdict(msg)
    assert verdict is None
    assert "confidence" in err.lower() or "unexpected" in err.lower()
