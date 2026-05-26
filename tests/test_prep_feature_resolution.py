"""Session-resolution helpers used by `tilth --prep-feature` and bare `tilth <ws>`.

These two helpers gate the prep-feature flow:
  - _find_blocking_sessions     — refuse re-prep when an in-flight session exists
  - _find_prepared_sessions     — pick up a prepared session on bare invocation
plus _find_resumable_session, which must now skip prepared sessions so the
"heads up: resumable" warning doesn't fire for them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tilth.loop import (
    _find_blocking_sessions,
    _find_prepared_sessions,
    _find_resumable_session,
)


def _make_session(
    sessions_root: Path,
    sid: str,
    *,
    source: str,
    status: str,
    last_stop: str | None = None,
) -> Path:
    d = sessions_root / sid
    d.mkdir(parents=True)
    cp = {"session_id": sid, "source": source, "status": status}
    (d / "checkpoint.json").write_text(json.dumps(cp))
    events: list[dict] = [{"type": "session_start", "payload": {"source": source}}]
    if last_stop is not None:
        events.append({"type": "stop", "payload": {"reason": last_stop}})
    (d / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + ("\n" if events else "")
    )
    return d


@pytest.fixture
def sessions_root(tmp_path: Path) -> Path:
    r = tmp_path / "sessions"
    r.mkdir()
    return r


@pytest.fixture
def source(tmp_path: Path) -> Path:
    s = tmp_path / "project"
    s.mkdir()
    return s


# --- _find_prepared_sessions ------------------------------------------------

def test_prepared_sessions_empty_when_none_match(sessions_root, source):
    assert _find_prepared_sessions(sessions_root, source) == []


def test_prepared_sessions_returns_single_match(sessions_root, source):
    _make_session(sessions_root, "20260525-100000-aaa", source=str(source), status="prepared")
    assert _find_prepared_sessions(sessions_root, source) == ["20260525-100000-aaa"]


def test_prepared_sessions_returns_all_matches_sorted(sessions_root, source):
    _make_session(sessions_root, "20260525-100000-bbb", source=str(source), status="prepared")
    _make_session(sessions_root, "20260525-090000-aaa", source=str(source), status="prepared")
    assert _find_prepared_sessions(sessions_root, source) == [
        "20260525-090000-aaa",
        "20260525-100000-bbb",
    ]


def test_prepared_sessions_ignores_other_workspaces(sessions_root, source, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    _make_session(sessions_root, "20260525-100000-aaa", source=str(other), status="prepared")
    assert _find_prepared_sessions(sessions_root, source) == []


@pytest.mark.parametrize("status", ["running", "all_done", "failed"])
def test_prepared_sessions_ignores_non_prepared_statuses(sessions_root, source, status):
    _make_session(sessions_root, "20260525-100000-aaa", source=str(source), status=status)
    assert _find_prepared_sessions(sessions_root, source) == []


# --- _find_blocking_sessions ------------------------------------------------

def test_blocking_empty_when_no_sessions(sessions_root, source):
    assert _find_blocking_sessions(sessions_root, source) == []


@pytest.mark.parametrize("status", ["prepared", "running", "failed"])
def test_blocking_includes_each_in_flight_status(sessions_root, source, status):
    _make_session(sessions_root, f"20260525-100000-{status[:3]}", source=str(source), status=status)
    out = _find_blocking_sessions(sessions_root, source)
    assert [s[1] for s in out] == [status]


def test_blocking_excludes_all_done(sessions_root, source):
    _make_session(sessions_root, "20260525-100000-aaa", source=str(source), status="all_done")
    assert _find_blocking_sessions(sessions_root, source) == []


def test_blocking_excludes_other_workspaces(sessions_root, source, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    _make_session(sessions_root, "20260525-100000-aaa", source=str(other), status="prepared")
    assert _find_blocking_sessions(sessions_root, source) == []


# --- _find_resumable_session interaction ------------------------------------

def test_resumable_skips_prepared_sessions(sessions_root, source):
    """Prepared sessions are picked up by the bare-workspace flow, not by
    --resume. The 'heads up: resumable' warning must NOT fire for them."""
    _make_session(sessions_root, "20260525-100000-aaa", source=str(source), status="prepared")
    assert _find_resumable_session(sessions_root, source) is None


def test_resumable_still_finds_running_with_failure_stop(sessions_root, source):
    _make_session(
        sessions_root,
        "20260525-100000-aaa",
        source=str(source),
        status="failed",
        last_stop="iter_cap",
    )
    out = _find_resumable_session(sessions_root, source)
    assert out == ("20260525-100000-aaa", "iter_cap")
