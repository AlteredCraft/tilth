"""format_ledger_section renders prior evaluator iterations for injection
into the evaluator's user message (Phase 2 of v1).

This is what gives the evaluator memory: instead of judging each iteration
in fresh context, it sees its own prior verdicts on the same task and can
escalate feedback / stop re-litigating.
"""

from __future__ import annotations

from tilth.verdict import format_ledger_section


def _entry(iter_n, verdict, category=None, concern="", next_step=None, diff_summary=""):
    return {
        "iter": iter_n,
        "ts": "2026-05-29T10:00:00Z",
        "diff_summary": diff_summary,
        "case": None,
        "verdict": {
            "verdict": verdict,
            "rejection_category": category,
            "concern": concern,
            "evidence": [],
            "next_step": next_step,
        },
    }


def test_empty_ledger_yields_empty_string():
    assert format_ledger_section([]) == ""


def test_section_has_the_canonical_header():
    out = format_ledger_section([_entry(1, "reject", "scope_creep")])
    assert "## Prior iterations on this task" in out


def test_renders_iter_verdict_category_concern_next_step():
    out = format_ledger_section([
        _entry(
            3, "reject", "acceptance_gap",
            concern="Empty-name case not handled.",
            next_step="Add a guard for empty name.",
            diff_summary="todo_cli/__main__.py (+4 -0)",
        )
    ])
    assert "3" in out                              # iteration number
    assert "reject" in out
    assert "acceptance_gap" in out
    assert "Empty-name case not handled." in out
    assert "Add a guard for empty name." in out
    assert "todo_cli/__main__.py (+4 -0)" in out


def test_oldest_first_ordering_preserved():
    out = format_ledger_section([
        _entry(1, "reject", "scope_creep", concern="FIRST"),
        _entry(2, "reject", "acceptance_gap", concern="SECOND"),
    ])
    assert out.index("FIRST") < out.index("SECOND")


def test_accept_entry_renders_without_category_or_next_step():
    out = format_ledger_section([_entry(5, "accept", concern="All criteria met.")])
    assert "accept" in out
    assert "All criteria met." in out
    # no dangling 'None' leaking from null category/next_step
    assert "None" not in out
