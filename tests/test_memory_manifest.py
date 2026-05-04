"""build_user_prompt must return a manifest describing what was loaded.

The manifest is what the harness logs as `memory_load`. It needs to answer:
- Which channels were present?
- How many chars (raw, before truncation)?
- Was the channel truncated?
- A short content hash so the developer can spot changes between tasks
  without diffing the events.jsonl.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tilth import memory


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


def _task() -> dict:
    return {"id": "T-1", "title": "do a thing", "description": "do it"}


def test_manifest_returned_alongside_prompt(workspace):
    prompt, manifest = memory.build_user_prompt(_task(), workspace)
    assert isinstance(prompt, str) and prompt
    assert "channels" in manifest
    assert "user_prompt_chars" in manifest
    assert manifest["user_prompt_chars"] == len(prompt)


def test_manifest_marks_missing_channels_absent(workspace):
    _, manifest = memory.build_user_prompt(_task(), workspace)
    channels = manifest["channels"]
    assert channels["agents_md"]["present"] is False
    assert channels["agents_md"]["chars"] == 0
    assert channels["progress_tail"]["present"] is False


def test_manifest_records_agents_md_when_present(workspace):
    (workspace / "AGENTS.md").write_text("# rules\n- be concise\n")
    _, manifest = memory.build_user_prompt(_task(), workspace)
    ch = manifest["channels"]["agents_md"]
    assert ch["present"] is True
    assert ch["chars"] > 0
    assert ch["truncated"] is False
    assert len(ch["sha256_8"]) == 8


def test_manifest_marks_truncated_when_agents_md_oversized(workspace):
    big = "x" * (memory.AGENTS_MD_MAX_CHARS + 100)
    (workspace / "AGENTS.md").write_text(big)
    _, manifest = memory.build_user_prompt(_task(), workspace)
    ch = manifest["channels"]["agents_md"]
    assert ch["truncated"] is True
    assert ch["chars"] == len(big)


def test_manifest_records_progress_tail_lines(workspace):
    (workspace / "progress.txt").write_text("\n".join(f"line-{i}" for i in range(50)) + "\n")
    _, manifest = memory.build_user_prompt(_task(), workspace)
    ch = manifest["channels"]["progress_tail"]
    assert ch["present"] is True
    assert ch["lines"] == memory.PROGRESS_TAIL_LINES


def test_manifest_hash_changes_when_content_changes(workspace):
    (workspace / "AGENTS.md").write_text("v1")
    _, m1 = memory.build_user_prompt(_task(), workspace)
    (workspace / "AGENTS.md").write_text("v2")
    _, m2 = memory.build_user_prompt(_task(), workspace)
    assert m1["channels"]["agents_md"]["sha256_8"] != m2["channels"]["agents_md"]["sha256_8"]
