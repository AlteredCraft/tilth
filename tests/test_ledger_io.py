"""Per-task evaluator ledger I/O on the Session (Phase 2 of v1).

The ledger is the evaluator's durable memory of a task's prior iterations:
sessions/<id>/ledger/<task_id>.jsonl, one entry per evaluator call. These
tests pin the append/read/ordering/cap contract the loop relies on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tilth.session import Session


@pytest.fixture
def session(tmp_path: Path) -> Session:
    return Session.new(tmp_path)


def test_append_then_read_roundtrip(session):
    session.append_ledger_entry("T-001", {"iter": 1, "verdict": {"verdict": "reject"}})
    entries = session.read_ledger("T-001")
    assert len(entries) == 1
    assert entries[0]["iter"] == 1
    assert entries[0]["verdict"] == {"verdict": "reject"}


def test_entries_are_timestamped_on_append(session):
    session.append_ledger_entry("T-001", {"iter": 1})
    entries = session.read_ledger("T-001")
    assert "ts" in entries[0] and entries[0]["ts"]


def test_read_preserves_append_order(session):
    for i in range(1, 4):
        session.append_ledger_entry("T-001", {"iter": i})
    iters = [e["iter"] for e in session.read_ledger("T-001")]
    assert iters == [1, 2, 3]


def test_limit_returns_last_n_in_order(session):
    for i in range(1, 8):
        session.append_ledger_entry("T-001", {"iter": i})
    last3 = session.read_ledger("T-001", limit=3)
    assert [e["iter"] for e in last3] == [5, 6, 7]


def test_limit_larger_than_count_returns_all(session):
    session.append_ledger_entry("T-001", {"iter": 1})
    assert len(session.read_ledger("T-001", limit=5)) == 1


def test_read_missing_ledger_returns_empty(session):
    assert session.read_ledger("T-999") == []


def test_ledgers_are_per_task(session):
    session.append_ledger_entry("T-001", {"iter": 1})
    session.append_ledger_entry("T-002", {"iter": 1})
    session.append_ledger_entry("T-001", {"iter": 2})
    assert [e["iter"] for e in session.read_ledger("T-001")] == [1, 2]
    assert [e["iter"] for e in session.read_ledger("T-002")] == [1]


def test_ledger_dir_is_under_session_root(session):
    session.append_ledger_entry("T-001", {"iter": 1})
    assert (session.root / "ledger" / "T-001.jsonl").is_file()


def test_blank_and_corrupt_lines_skipped(session):
    session.append_ledger_entry("T-001", {"iter": 1})
    path = session.root / "ledger" / "T-001.jsonl"
    with path.open("a") as f:
        f.write("\n")            # blank line
        f.write("{not json}\n")  # corrupt line (partial write on crash)
    session.append_ledger_entry("T-001", {"iter": 2})
    iters = [e["iter"] for e in session.read_ledger("T-001")]
    assert iters == [1, 2]
