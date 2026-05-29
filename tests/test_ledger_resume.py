"""The ledger survives a resume (Phase 2 of v1).

`tilth resume` constructs a fresh Session via Session.wake against the same
sessions/<id>/ root. Because the ledger is plain files under that root, a
woken session must see the prior run's entries — the evaluator's memory of a
task persists across process restarts.
"""

from __future__ import annotations

from pathlib import Path

from tilth.session import Session


def test_woken_session_reads_prior_run_ledger(tmp_path: Path):
    # First run: a session accumulates ledger entries.
    s1 = Session.new(tmp_path)
    s1.source = tmp_path / "src"
    s1.workspace = tmp_path / "wt"
    s1.branch = "session/x"
    s1.save_checkpoint()
    s1.append_ledger_entry("T-001", {"iter": 1, "verdict": {"verdict": "reject"}})
    s1.append_ledger_entry("T-001", {"iter": 2, "verdict": {"verdict": "reject"}})

    # Resume: a fresh Session object over the same root.
    s2 = Session.wake(tmp_path, s1.session_id)
    entries = s2.read_ledger("T-001")
    assert [e["iter"] for e in entries] == [1, 2]

    # And further appends continue the same file, not a new one.
    s2.append_ledger_entry("T-001", {"iter": 3, "verdict": {"verdict": "accept"}})
    assert [e["iter"] for e in s2.read_ledger("T-001")] == [1, 2, 3]
