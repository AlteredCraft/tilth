"""memory.append_proposed_learning writes the per-session learnings file.

Self-improvement no longer mutates the user's AGENTS.md; it appends to
`sessions/<id>/proposed-learnings.md` for the user (and the future hook) to
review. Behaviour is small but load-bearing — wrong path, wrong format, or
shared-file overwrites would break the review workflow.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tilth import memory


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    d = tmp_path / "20260525-120000-abc123"
    d.mkdir()
    return d


def _read(session_dir: Path) -> str:
    return (session_dir / "proposed-learnings.md").read_text()


def test_creates_file_with_header_on_first_append(session_dir):
    memory.append_proposed_learning(
        session_dir, "T-001", "Create hello.py", "Use flush=True for CLI output."
    )
    text = _read(session_dir)
    assert text.startswith("# Proposed learnings — session ")
    assert session_dir.name in text
    assert "## From T-001 — Create hello.py" in text
    assert "- Use flush=True for CLI output." in text


def test_subsequent_appends_preserve_prior_proposals(session_dir):
    memory.append_proposed_learning(session_dir, "T-001", "First task", "first learning")
    memory.append_proposed_learning(session_dir, "T-002", "Second task", "second learning")
    text = _read(session_dir)
    assert "## From T-001 — First task" in text
    assert "- first learning" in text
    assert "## From T-002 — Second task" in text
    assert "- second learning" in text
    # Order is append-only: T-001 must precede T-002.
    assert text.find("T-001") < text.find("T-002")


def test_header_written_only_once(session_dir):
    memory.append_proposed_learning(session_dir, "T-001", "First task", "first")
    memory.append_proposed_learning(session_dir, "T-002", "Second task", "second")
    text = _read(session_dir)
    # Header line appears once.
    assert text.count("# Proposed learnings — session ") == 1


def test_entry_text_is_stripped(session_dir):
    memory.append_proposed_learning(
        session_dir, "T-001", "A task", "   leading and trailing whitespace   "
    )
    text = _read(session_dir)
    assert "- leading and trailing whitespace\n" in text


def test_does_not_touch_workspace_agents_md(session_dir, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("# AGENTS.md\n\n## Project\nUser-owned.\n")
    memory.append_proposed_learning(session_dir, "T-001", "A task", "a learning")
    # The user's file must be byte-identical.
    assert (workspace / "AGENTS.md").read_text() == "# AGENTS.md\n\n## Project\nUser-owned.\n"
