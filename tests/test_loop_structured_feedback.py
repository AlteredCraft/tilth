"""When the evaluator rejects, the worker sees a structured message built
from `concern + evidence + next_step` — not free prose.

Today (v0): worker gets `"An independent reviewer rejected. Reasoning: <prose>"`
and has to extract intent from English. v1: the load-bearing fields are
broken out so a downstream reviewer (human or agent) and the worker itself
can see *which kind* of rejection it is, *what to look at*, and *what to do
next*.
"""

from __future__ import annotations

from tilth.verdict import format_reject_feedback


def test_all_load_bearing_fields_appear():
    verdict = {
        "verdict": "reject",
        "rejection_category": "acceptance_gap",
        "concern": "The empty-name case is not handled.",
        "evidence": ["pkg/foo.py:2", "tests/test_t001.py::test_empty"],
        "next_step": "Add a guard `if not name: return 'Hello, friend!'`.",
    }
    fb = format_reject_feedback(verdict)

    # category surfaced so the worker (and a post-run reviewer) can see
    # *which kind* of failure
    assert "acceptance_gap" in fb
    # concern present verbatim
    assert "The empty-name case is not handled." in fb
    # every evidence pointer is in there
    for ev in verdict["evidence"]:
        assert ev in fb
    # next_step is in there verbatim
    assert "Add a guard `if not name: return 'Hello, friend!'`." in fb


def test_evidence_renders_as_bulleted_list():
    """A many-pointer reject must not degenerate into a wall of prose —
    each pointer goes on its own line so post-run review is easy."""
    verdict = {
        "verdict": "reject",
        "rejection_category": "scope_creep",
        "concern": "Unrelated files modified.",
        "evidence": ["README.md", "pyproject.toml", "main.py"],
        "next_step": "Revert the unrelated files.",
    }
    fb = format_reject_feedback(verdict)
    for ev in verdict["evidence"]:
        # each pointer on its own line, as a bullet
        assert f"- {ev}" in fb


def test_empty_evidence_does_not_leave_an_empty_section():
    verdict = {
        "verdict": "reject",
        "rejection_category": "half_finished",
        "concern": "TODOs left in the code.",
        "evidence": [],
        "next_step": "Resolve the TODOs and remove the markers.",
    }
    fb = format_reject_feedback(verdict)
    # no dangling header with no bullets under it
    lines = [line.rstrip() for line in fb.splitlines()]
    for i, line in enumerate(lines):
        if line.lower().endswith("evidence:") or line.lower() == "evidence":
            following = "\n".join(lines[i + 1:i + 3])
            assert following.strip(), (
                "evidence section header present but no content underneath"
            )
    # the rest still works
    assert "TODOs left in the code." in fb
    assert "Resolve the TODOs and remove the markers." in fb


def test_category_appears_distinct_from_prose_concern():
    """rejection_category is a labelled field, not buried inside concern,
    so the visualizer and post-run agents can read it without NLP."""
    verdict = {
        "verdict": "reject",
        "rejection_category": "tests_pass_but_wrong",
        "concern": "The fix satisfies the assertion but skips the intent.",
        "evidence": ["pkg/foo.py:5"],
        "next_step": "Stop hardcoding; compute the value.",
    }
    fb = format_reject_feedback(verdict)
    # category should appear with its own label/anchor, not just inside
    # the concern sentence
    assert "tests_pass_but_wrong" in fb
    # and the concern doesn't have to mention the category text itself
    # for the worker to know it
    assert "The fix satisfies the assertion but skips the intent." in fb
