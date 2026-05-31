"""Defensive parsing of the worker's `submit_case` tool call (Phase 3).

Mirrors test_evaluator_verdict_parsing.py — the case reuses the same
tool-call + defensive-parse + value-local-normalize pattern as the verdict
(see the conventions block in proposals/completed/v1-implementation-plan.md).
"""

from __future__ import annotations

import json

import pytest

from tilth.case import format_case_section, parse_case


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


def _good_case(**over) -> dict:
    base = {
        "summary": "Implemented the add subcommand and wired the entry point.",
        "ac_coverage": [
            {
                "criterion": "main(['add','x']) returns 0",
                "addressed_by": "todo_cli/__main__.py:main() — argparse 'add' subparser",
                "evidence": "tests/test_t002_parse.py::test_add_ok",
            }
        ],
        "work_arounds": [],
        "uncertainties": [],
    }
    base.update(over)
    return base


# --- happy paths -------------------------------------------------------------


def test_happy_path():
    case, err = parse_case(_msg([_tc("submit_case", _good_case())]))
    assert err is None
    assert case["summary"].startswith("Implemented")
    assert case["ac_coverage"][0]["addressed_by"].startswith("todo_cli/")


def test_optional_lists_default_to_empty():
    args = {"summary": "x.", "ac_coverage": [
        {"criterion": "c", "addressed_by": "pkg/foo.py:bar"}]}
    case, err = parse_case(_msg([_tc("submit_case", args)]))
    assert err is None
    assert case["work_arounds"] == []
    assert case["uncertainties"] == []


def test_already_parsed_dict_args_accepted():
    msg = _msg([{"id": "c", "function": {"name": "submit_case",
                                         "arguments": _good_case()}}])
    case, err = parse_case(msg)
    assert err is None
    assert case["summary"]


def test_first_valid_wins_over_corrupted_sibling():
    msg = _msg([
        _tc("submit_case", _good_case()),
        _tc("submit_case", '{"summary": "x</|DSML|param>'),
    ])
    case, err = parse_case(msg)
    assert err is None
    assert case["summary"].startswith("Implemented")


# --- normalization (value-local) --------------------------------------------


def test_empty_strings_dropped_from_lists():
    args = _good_case(work_arounds=["real reason", "  ", ""],
                      uncertainties=[""])
    case, err = parse_case(_msg([_tc("submit_case", args)]))
    assert err is None
    assert case["work_arounds"] == ["real reason"]
    assert case["uncertainties"] == []


# --- validation failures -----------------------------------------------------


@pytest.mark.parametrize(
    "args,needle",
    [
        # missing required fields
        ({"ac_coverage": [{"criterion": "c", "addressed_by": "a/b.py:x"}]},
         "summary"),
        ({"summary": "x."}, "ac_coverage"),
        # ac_coverage entry shape
        ({"summary": "x.", "ac_coverage": [{"addressed_by": "a/b.py:x"}]},
         "criterion"),
        ({"summary": "x.", "ac_coverage": [{"criterion": "c"}]},
         "addressed_by"),
        # addressed_by is prose, not a pointer (sketch mitigation #1)
        ({"summary": "x.", "ac_coverage": [{
            "criterion": "c",
            "addressed_by": "the implementation thoughtfully considers all "
                            "the edge cases the criterion clearly implies here",
        }]}, "addressed_by"),
        # empty summary
        ({"summary": "  ", "ac_coverage": [
            {"criterion": "c", "addressed_by": "a/b.py:x"}]}, "summary"),
        # extra top-level field
        ({"summary": "x.", "ac_coverage": [
            {"criterion": "c", "addressed_by": "a/b.py:x"}],
          "confidence": 0.9}, "confidence"),
    ],
)
def test_validation_failures_name_the_problem(args, needle):
    case, err = parse_case(_msg([_tc("submit_case", args)]))
    assert case is None
    assert needle in err.lower()


def test_work_arounds_capped_at_five():
    args = _good_case(work_arounds=[f"w{i}" for i in range(6)])
    case, err = parse_case(_msg([_tc("submit_case", args)]))
    assert case is None
    assert "work_around" in err.lower()


def test_terse_pointer_addressed_by_accepted():
    """A short non-classic pointer (no slash/extension) is allowed — we only
    reject addressed_by that is clearly a prose sentence."""
    args = _good_case(ac_coverage=[
        {"criterion": "c", "addressed_by": "main() in __main__"}])
    _, err = parse_case(_msg([_tc("submit_case", args)]))
    assert err is None


# --- missing / wrong tool ----------------------------------------------------


def test_no_tool_calls_errors():
    case, err = parse_case(_msg([]))
    assert case is None
    assert "submit_case" in err


def test_wrong_tool_name_skipped():
    case, err = parse_case(_msg([_tc("submit_verdict", _good_case())]))
    assert case is None
    assert "submit_case" in err


# --- format_case_section -----------------------------------------------------


def test_format_section_renders_all_fields():
    case = _good_case(
        work_arounds=["deleted README.md — uv init side effect"],
        uncertainties=["AC phrasing on empty string is ambiguous"],
    )
    out = format_case_section(case)
    assert "## Worker's case" in out
    assert "Implemented" in out
    assert "main(['add','x']) returns 0" in out
    assert "todo_cli/__main__.py:main()" in out
    assert "deleted README.md — uv init side effect" in out
    assert "AC phrasing on empty string is ambiguous" in out


def test_format_section_omits_empty_optional_blocks():
    out = format_case_section(_good_case())
    # no dangling "Work-arounds" / "Uncertainties" headers when those are empty
    assert "work-around" not in out.lower()
    assert "uncertaint" not in out.lower()
