"""session.iter_events is the only correct way to read events.jsonl.

Four call sites depend on it: the summary roll-up and the three resume
helpers (_last_stop_reason, _source_for_session, _find_resumable_session).
It must tolerate missing files, blank lines, and JSON-decode errors on the
last line (a crash mid-write leaves a partial line).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tilth.session import iter_events


@pytest.fixture
def events_path(tmp_path: Path) -> Path:
    return tmp_path / "events.jsonl"


def test_missing_file_yields_nothing(events_path):
    assert list(iter_events(events_path)) == []


def test_empty_file_yields_nothing(events_path):
    events_path.write_text("")
    assert list(iter_events(events_path)) == []


def test_yields_each_valid_record(events_path):
    events = [{"type": "a", "n": 1}, {"type": "b", "n": 2}]
    events_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    assert list(iter_events(events_path)) == events


def test_skips_blank_lines(events_path):
    events_path.write_text('\n{"type": "a"}\n\n\n{"type": "b"}\n\n')
    assert [e["type"] for e in iter_events(events_path)] == ["a", "b"]


def test_skips_corrupt_lines_silently(events_path):
    events_path.write_text(
        '{"type": "good"}\n'
        "not json at all\n"
        '{"type": "also good"}\n'
        "{half-written\n"
    )
    assert [e["type"] for e in iter_events(events_path)] == ["good", "also good"]


def test_handles_trailing_partial_line_without_newline(events_path):
    events_path.write_text(
        '{"type": "good"}\n'
        '{"type": "another"}\n'
        '{"type":'
    )
    assert [e["type"] for e in iter_events(events_path)] == ["good", "another"]
