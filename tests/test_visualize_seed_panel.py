"""Visualizer reads seed-meta.json and renders it as a panel above the timeline.

The panel is the human reviewer's first stop when opening a prepared session's
chat.html — TL;DR, open questions, blockers, scope notes. Absent or malformed
seed-meta must degrade silently; the rest of the page still renders.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tilth.visualize import render_html, write_session_html


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    d = tmp_path / "20260525-120000-abc123"
    d.mkdir()
    (d / "events.jsonl").write_text("")
    return d


def _seed_meta(**over) -> dict:
    meta = {
        "interviewer_model": "stub-model",
        "started_at": "2026-05-25T22:14:01Z",
        "ended_at": "2026-05-25T22:17:43Z",
        "tokens": {"prompt": 8000, "completion": 1500, "total": 9500},
        "tldr": "- **T-001:** scaffold — lays out hello.py",
        "open_questions": ["should we also handle X?"],
        "blockers": [],
        "scope_notes": "",
    }
    meta.update(over)
    return meta


def test_no_seed_meta_no_panel(session_dir):
    html = render_html(session_dir)
    # Match the section element specifically — the bare class name "seed-panel"
    # also appears in the embedded CSS rules.
    assert '<section class="seed-panel">' not in html


def test_seed_meta_renders_panel(session_dir):
    (session_dir / "seed-meta.json").write_text(json.dumps(_seed_meta()))
    html = render_html(session_dir)
    assert 'class="seed-panel"' in html
    assert "Seed" in html
    assert "stub-model" in html
    assert "9,500 tokens" in html


def test_panel_renders_tldr(session_dir):
    (session_dir / "seed-meta.json").write_text(json.dumps(_seed_meta()))
    html = render_html(session_dir)
    assert "TL;DR" in html
    assert "T-001" in html
    assert "lays out hello.py" in html


def test_panel_renders_open_questions(session_dir):
    (session_dir / "seed-meta.json").write_text(
        json.dumps(_seed_meta(open_questions=["q1?", "q2?"]))
    )
    html = render_html(session_dir)
    assert "Open questions" in html
    assert "q1?" in html
    assert "q2?" in html


def test_panel_renders_blockers_with_distinctive_class(session_dir):
    (session_dir / "seed-meta.json").write_text(
        json.dumps(_seed_meta(blockers=["refactor lacks coverage"]))
    )
    html = render_html(session_dir)
    assert "Blockers" in html
    assert "refactor lacks coverage" in html
    # Distinct class so the section is visually flagged.
    assert "seed-panel-section blockers" in html


def test_panel_omits_empty_sections(session_dir):
    (session_dir / "seed-meta.json").write_text(
        json.dumps(_seed_meta(blockers=[], scope_notes=""))
    )
    html = render_html(session_dir)
    assert "Blockers" not in html
    assert "Scope notes" not in html


def test_panel_renders_scope_notes(session_dir):
    (session_dir / "seed-meta.json").write_text(
        json.dumps(_seed_meta(scope_notes="Migrations are out of scope this seed."))
    )
    html = render_html(session_dir)
    assert "Scope notes" in html
    assert "Migrations are out of scope" in html


def test_malformed_seed_meta_renders_no_panel_but_doesnt_crash(session_dir):
    (session_dir / "seed-meta.json").write_text("{not valid json")
    html = render_html(session_dir)
    assert '<section class="seed-panel">' not in html
    # Page should still render fine — no events here so we expect the empty state.
    assert "No events recorded yet" in html


def test_seed_meta_with_unexpected_types_skips_those_fields(session_dir):
    (session_dir / "seed-meta.json").write_text(
        json.dumps(
            {
                "interviewer_model": "stub",
                "tokens": "not a dict",        # ignored
                "open_questions": [None, "real one", 123],  # only "real one" kept
                "blockers": "not a list",      # ignored
                "tldr": "",
            }
        )
    )
    html = render_html(session_dir)
    assert '<section class="seed-panel">' in html
    assert "real one" in html
    assert "Blockers" not in html


def test_session_prepared_event_renders_card(session_dir):
    """A session_prepared event in events.jsonl should render as a clean card,
    not as a generic JSON-dump."""
    event = {
        "ts": "2026-05-25T22:17:43Z",
        "type": "session_prepared",
        "payload": {
            "interviewer_model": "stub-model",
            "prd_entries": 3,
            "test_files": 3,
            "tokens_used": 9500,
        },
    }
    (session_dir / "events.jsonl").write_text(json.dumps(event) + "\n")
    html = render_html(session_dir)
    assert "seed prepared" in html
    assert "3 tasks" in html
    assert "3 tests" in html
    assert "9,500 tokens" in html
    assert "stub-model" in html
    # Generic fallback would dump the JSON in a <pre>; we shouldn't see that.
    assert "&quot;prd_entries&quot;" not in html


def test_write_session_html_includes_panel(session_dir):
    (session_dir / "seed-meta.json").write_text(json.dumps(_seed_meta()))
    out = write_session_html(session_dir)
    assert out.is_file()
    assert '<section class="seed-panel">' in out.read_text()
