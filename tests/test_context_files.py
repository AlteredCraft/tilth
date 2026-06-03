"""Project-context channel is configurable and multi-file (issue #29).

Tilth used to read a single hard-coded `AGENTS.md` from the workspace root. The
channel is now driven by `TILTH_CONTEXT_FILES` (default `AGENTS.md,CLAUDE.md`),
read in order and concatenated, so a repo that keeps its conventions in
`CLAUDE.md` is no longer invisible to the worker. These tests pin the loader,
the manifest's per-file honesty, the aggregate truncation cap, and the
config-driven default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tilth import memory


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    sd = tmp_path / "session"
    sd.mkdir()
    return sd


def _task() -> dict:
    return {"id": "T-1", "title": "do a thing", "description": "do it"}


# ── the loader ──────────────────────────────────────────────────────────────


def test_default_is_agents_then_claude():
    assert tuple(memory.DEFAULT_CONTEXT_FILES) == ("AGENTS.md", "CLAUDE.md")


def test_single_present_file_has_no_subheader(workspace):
    (workspace / "AGENTS.md").write_text("# rules\n- be concise\n")
    text, loaded = memory.load_context_files(workspace, ["AGENTS.md", "CLAUDE.md"])
    assert loaded == ["AGENTS.md"]
    assert "### AGENTS.md" not in text  # single file → clean body, no provenance header
    assert "be concise" in text


def test_claude_md_picked_up_when_agents_absent(workspace):
    """The headline win: a Claude Code repo (CLAUDE.md, no AGENTS.md) is seen."""
    (workspace / "CLAUDE.md").write_text("project rules in claude file\n")
    text, loaded = memory.load_context_files(workspace, ["AGENTS.md", "CLAUDE.md"])
    assert loaded == ["CLAUDE.md"]
    assert "project rules in claude file" in text


def test_both_files_concatenated_with_provenance_headers(workspace):
    (workspace / "AGENTS.md").write_text("AGENTS body\n")
    (workspace / "CLAUDE.md").write_text("CLAUDE body\n")
    text, loaded = memory.load_context_files(workspace, ["AGENTS.md", "CLAUDE.md"])
    assert loaded == ["AGENTS.md", "CLAUDE.md"]
    assert "### AGENTS.md" in text and "### CLAUDE.md" in text
    assert text.index("AGENTS body") < text.index("CLAUDE body")  # order follows arg


def test_order_follows_the_filenames_argument(workspace):
    (workspace / "AGENTS.md").write_text("AGENTS body\n")
    (workspace / "CLAUDE.md").write_text("CLAUDE body\n")
    text, loaded = memory.load_context_files(workspace, ["CLAUDE.md", "AGENTS.md"])
    assert loaded == ["CLAUDE.md", "AGENTS.md"]
    assert text.index("CLAUDE body") < text.index("AGENTS body")


def test_none_present_returns_empty(workspace):
    text, loaded = memory.load_context_files(workspace, ["AGENTS.md", "CLAUDE.md"])
    assert text == ""
    assert loaded == []


def test_aggregate_cap_truncates_across_files(workspace):
    half = memory.AGENTS_MD_MAX_CHARS  # each file alone is already at the cap
    (workspace / "AGENTS.md").write_text("a" * half)
    (workspace / "CLAUDE.md").write_text("b" * half)
    text, manifest = memory._load_context_files(workspace, ["AGENTS.md", "CLAUDE.md"])
    assert manifest["truncated"] is True
    assert manifest["chars"] > memory.AGENTS_MD_MAX_CHARS  # raw combined length, pre-cut
    # the injected text is bounded by the cap (plus the short truncation marker)
    assert len(text) <= memory.AGENTS_MD_MAX_CHARS + 120


# ── manifest honesty (memory_load observability) ─────────────────────────────


def test_manifest_has_one_entry_per_configured_file(workspace):
    (workspace / "CLAUDE.md").write_text("only claude\n")
    _, manifest = memory._load_context_files(workspace, ["AGENTS.md", "CLAUDE.md"])
    files = {f["name"]: f for f in manifest["files"]}
    assert set(files) == {"AGENTS.md", "CLAUDE.md"}
    assert files["AGENTS.md"]["present"] is False
    assert files["CLAUDE.md"]["present"] is True
    assert files["CLAUDE.md"]["chars"] > 0
    assert len(files["CLAUDE.md"]["sha256_8"]) == 8


# ── build_user_prompt integration ────────────────────────────────────────────


def test_build_user_prompt_default_picks_up_claude_md(workspace, session_dir):
    (workspace / "CLAUDE.md").write_text("CONVENTION: tabs not spaces\n")
    prompt, manifest = memory.build_user_prompt(_task(), workspace, session_dir)
    assert "## Project context (CLAUDE.md)" in prompt
    assert "tabs not spaces" in prompt
    assert manifest["channels"]["agents_md"]["present"] is True


def test_build_user_prompt_header_lists_loaded_files(workspace, session_dir):
    (workspace / "AGENTS.md").write_text("a\n")
    (workspace / "CLAUDE.md").write_text("c\n")
    prompt, _ = memory.build_user_prompt(_task(), workspace, session_dir)
    assert "## Project context (AGENTS.md, CLAUDE.md)" in prompt


def test_build_user_prompt_honors_explicit_context_files(workspace, session_dir):
    (workspace / "CONVENTIONS.md").write_text("house style\n")
    (workspace / "AGENTS.md").write_text("ignored\n")
    prompt, _ = memory.build_user_prompt(
        _task(), workspace, session_dir, context_files=["CONVENTIONS.md"]
    )
    assert "## Project context (CONVENTIONS.md)" in prompt
    assert "house style" in prompt
    assert "ignored" not in prompt


def test_build_user_prompt_manifest_files_list_present(workspace, session_dir):
    (workspace / "AGENTS.md").write_text("a\n")
    _, manifest = memory.build_user_prompt(_task(), workspace, session_dir)
    names = [f["name"] for f in manifest["channels"]["agents_md"]["files"]]
    assert names == ["AGENTS.md", "CLAUDE.md"]
