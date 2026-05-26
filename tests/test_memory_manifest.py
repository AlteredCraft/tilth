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
    """User-owned source repo — only AGENTS.md is loaded from here."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    """Harness-owned session dir — progress.txt lives here, not in the workspace."""
    sd = tmp_path / "session"
    sd.mkdir()
    return sd


def _task() -> dict:
    return {"id": "T-1", "title": "do a thing", "description": "do it"}


def test_manifest_returned_alongside_prompt(workspace, session_dir):
    prompt, manifest = memory.build_user_prompt(_task(), workspace, session_dir)
    assert isinstance(prompt, str) and prompt
    assert "channels" in manifest
    assert "user_prompt_chars" in manifest
    assert manifest["user_prompt_chars"] == len(prompt)


def test_manifest_marks_missing_channels_absent(workspace, session_dir):
    _, manifest = memory.build_user_prompt(_task(), workspace, session_dir)
    channels = manifest["channels"]
    assert channels["agents_md"]["present"] is False
    assert channels["agents_md"]["chars"] == 0
    assert channels["progress_tail"]["present"] is False


def test_manifest_records_agents_md_when_present(workspace, session_dir):
    (workspace / "AGENTS.md").write_text("# rules\n- be concise\n")
    _, manifest = memory.build_user_prompt(_task(), workspace, session_dir)
    ch = manifest["channels"]["agents_md"]
    assert ch["present"] is True
    assert ch["chars"] > 0
    assert ch["truncated"] is False
    assert len(ch["sha256_8"]) == 8


def test_manifest_marks_truncated_when_agents_md_oversized(workspace, session_dir):
    big = "x" * (memory.AGENTS_MD_MAX_CHARS + 100)
    (workspace / "AGENTS.md").write_text(big)
    _, manifest = memory.build_user_prompt(_task(), workspace, session_dir)
    ch = manifest["channels"]["agents_md"]
    assert ch["truncated"] is True
    assert ch["chars"] == len(big)


def test_progress_tail_loaded_from_session_dir_not_workspace(workspace, session_dir):
    """progress.txt is a harness-owned runtime journal; it must not be read from
    the workspace even if a stray file is present there."""
    (workspace / "progress.txt").write_text("from-workspace\n")
    (session_dir / "progress.txt").write_text(
        "\n".join(f"line-{i}" for i in range(50)) + "\n"
    )
    _, manifest = memory.build_user_prompt(_task(), workspace, session_dir)
    ch = manifest["channels"]["progress_tail"]
    assert ch["present"] is True
    assert ch["lines"] == memory.PROGRESS_TAIL_LINES


def test_progress_absent_when_session_dir_has_no_progress_txt(workspace, session_dir):
    (workspace / "progress.txt").write_text("from-workspace\n")  # ignored
    _, manifest = memory.build_user_prompt(_task(), workspace, session_dir)
    assert manifest["channels"]["progress_tail"]["present"] is False


def test_append_progress_writes_into_session_dir(workspace, session_dir):
    memory.append_progress(session_dir, "T-001\tdone\thello")
    assert (session_dir / "progress.txt").read_text() == "T-001\tdone\thello\n"
    assert not (workspace / "progress.txt").is_file()


def test_manifest_hash_changes_when_content_changes(workspace, session_dir):
    (workspace / "AGENTS.md").write_text("v1")
    _, m1 = memory.build_user_prompt(_task(), workspace, session_dir)
    (workspace / "AGENTS.md").write_text("v2")
    _, m2 = memory.build_user_prompt(_task(), workspace, session_dir)
    assert m1["channels"]["agents_md"]["sha256_8"] != m2["channels"]["agents_md"]["sha256_8"]
