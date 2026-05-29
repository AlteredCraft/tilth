"""Phase 3: ledger entries now carry the worker's case (was null in Phase 2).

`_build_ledger_entry` is the pure constructor `_judge_task` uses, so the
"case is in the ledger" contract is testable without driving a model.
"""

from __future__ import annotations

from tilth.loop import _build_ledger_entry


def _verdict():
    return {"verdict": "accept", "rejection_category": None,
            "concern": "ok", "evidence": [], "next_step": None}


def test_entry_carries_case():
    case = {"summary": "did the thing", "ac_coverage": [],
            "work_arounds": [], "uncertainties": []}
    entry = _build_ledger_entry(
        iter_n=3, diff_summary="pkg/foo.py (+4 -0)", case=case, verdict=_verdict()
    )
    assert entry["iter"] == 3
    assert entry["diff_summary"] == "pkg/foo.py (+4 -0)"
    assert entry["case"] == case
    assert entry["verdict"]["verdict"] == "accept"


def test_null_case_still_supported():
    """Defensive: a verdict path with no case (e.g. parse-failure fallback,
    or pre-case-submission) still builds a valid entry."""
    entry = _build_ledger_entry(
        iter_n=1, diff_summary="(no changes)", case=None, verdict=_verdict()
    )
    assert entry["case"] is None
    assert entry["iter"] == 1
