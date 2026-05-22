"""memory.append_to_agents_md surgically appends to AGENTS.md sections.

The self-improve loop calls this after a task completes. The function has
four behaviour branches and they all touch real markdown structure — easier
to test directly than via the loop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tilth import memory


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


def test_creates_new_file_when_agents_md_absent(workspace):
    memory.append_to_agents_md(workspace, "Patterns", "- first lesson")
    text = (workspace / "AGENTS.md").read_text()
    assert "## Patterns" in text
    assert "- first lesson" in text
    assert not text.startswith("\n")


def test_appends_new_section_when_section_absent(workspace):
    (workspace / "AGENTS.md").write_text("# Title\n\nSome intro.\n")
    memory.append_to_agents_md(workspace, "Gotchas", "- watch out")
    text = (workspace / "AGENTS.md").read_text()
    assert text.startswith("# Title")
    assert "## Gotchas" in text
    assert "- watch out" in text


def test_replaces_placeholder_when_section_empty(workspace):
    (workspace / "AGENTS.md").write_text(
        "## Patterns\n\n_(empty — agent appends here)_\n\n## Gotchas\n"
    )
    memory.append_to_agents_md(workspace, "Patterns", "- first real entry")
    text = (workspace / "AGENTS.md").read_text()
    assert "_(empty — agent appends here)_" not in text
    assert "- first real entry" in text
    assert "## Gotchas" in text


def test_appends_within_section_preserving_downstream_sections(workspace):
    (workspace / "AGENTS.md").write_text(
        "## Patterns\n\n- existing entry\n\n## Gotchas\n\n- gotcha entry\n"
    )
    memory.append_to_agents_md(workspace, "Patterns", "- newer entry")
    text = (workspace / "AGENTS.md").read_text()
    assert "- existing entry" in text
    assert "- newer entry" in text
    assert "## Gotchas" in text
    assert "- gotcha entry" in text
    # New entry must land inside Patterns, not after Gotchas.
    patterns_idx = text.find("## Patterns")
    gotchas_idx = text.find("## Gotchas")
    newer_idx = text.find("- newer entry")
    assert patterns_idx < newer_idx < gotchas_idx
