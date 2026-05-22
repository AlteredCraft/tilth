"""client.parse_json_lenient salvages a JSON object from a model response.

Models intermittently wrap JSON in ```json fences, add chatty intros, or
return bare JSON. The judge and self-improve paths both feed model output
through this — a parse failure there falls back to "judge unparseable"
or "no AGENTS.md update", neither of which is silent but both of which
mean a wasted model call.
"""

from __future__ import annotations

import pytest

from tilth.client import parse_json_lenient


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"verdict": "accept"}', {"verdict": "accept"}),
        ('```json\n{"verdict": "accept"}\n```', {"verdict": "accept"}),
        ('```\n{"verdict": "accept"}\n```', {"verdict": "accept"}),
        (
            "Here is my verdict:\n\n"
            '```json\n{"verdict": "reject", "reasoning": "x"}\n```\n'
            "Done.",
            {"verdict": "reject", "reasoning": "x"},
        ),
        ('```json\n\n  {"a": 1}  \n\n```', {"a": 1}),
        ('{"a": {"b": [1, 2]}}', {"a": {"b": [1, 2]}}),
    ],
)
def test_parses_recoverable_shapes(text, expected):
    assert parse_json_lenient(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   \n  ",
        "this is not json at all",
        "{not: valid json}",
        "[1, 2, 3]",
        '"just a string"',
        "42",
        "true",
        "null",
        "```python\nprint('hi')\n```",
    ],
)
def test_returns_none_on_unparseable_or_non_object(text):
    assert parse_json_lenient(text) is None
