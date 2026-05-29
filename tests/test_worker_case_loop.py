"""Worker-loop decision logic for the submit_case done-signal (Phase 3).

The full loop isn't driven here (matches the suite's altitude — loop tests
exercise factored decisions, not a live model). These cover the pure
partition helper and the nudge constant the loop uses when the worker stops
without submitting a case.
"""

from __future__ import annotations

from tilth.loop import WORKER_NO_CASE_NUDGE, _partition_worker_tool_calls


def _tc(name: str, tc_id: str = "x") -> dict:
    return {"id": tc_id, "function": {"name": name, "arguments": "{}"}}


def test_separates_submit_case_from_worktree_tools():
    calls = [_tc("read_file", "a"), _tc("submit_case", "b"), _tc("bash", "c")]
    worktree, cases = _partition_worker_tool_calls(calls)
    assert [c["id"] for c in worktree] == ["a", "c"]
    assert [c["id"] for c in cases] == ["b"]


def test_only_worktree_tools():
    worktree, cases = _partition_worker_tool_calls([_tc("read_file"), _tc("bash")])
    assert len(worktree) == 2
    assert cases == []


def test_only_submit_case():
    worktree, cases = _partition_worker_tool_calls([_tc("submit_case")])
    assert worktree == []
    assert len(cases) == 1


def test_multiple_submit_case_all_collected():
    worktree, cases = _partition_worker_tool_calls(
        [_tc("submit_case", "a"), _tc("submit_case", "b")]
    )
    assert worktree == []
    assert [c["id"] for c in cases] == ["a", "b"]


def test_empty():
    assert _partition_worker_tool_calls([]) == ([], [])


def test_nudge_mentions_submit_case():
    assert "submit_case" in WORKER_NO_CASE_NUDGE
    assert WORKER_NO_CASE_NUDGE.strip()
