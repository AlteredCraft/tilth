"""Optional per-call prompt dump (issue #6).

`dump_prompt` writes the exact request (system + user + history + tool schemas)
to sessions/<id>/prompts/ before a model call, behind TILTH_PROMPT_DUMP. Off by
default — the disabled path must not touch the filesystem. Enabled, it returns a
session-relative path (with a monotonic, resume-stable sequence prefix) that the
caller stashes in the `model_call` event.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from tilth.loop import _self_improve
from tilth.session import Session, dump_prompt

MESSAGES = [
    {"role": "system", "content": "You are a worker."},
    {"role": "user", "content": "Do the thing."},
    {
        "role": "assistant",
        "content": "ok",
        "tool_calls": [
            {"id": "c1", "function": {"name": "bash", "arguments": '{"cmd": "ls"}'}}
        ],
        "reasoning": "thinking out loud",
    },
    {"role": "tool", "tool_call_id": "c1", "content": "file.txt"},
]
TOOLS = [{"type": "function", "function": {"name": "bash", "parameters": {}}}]


def test_disabled_is_a_noop(tmp_path: Path):
    assert dump_prompt(tmp_path, False, "T-001-iter01", MESSAGES, TOOLS) is None
    assert not (tmp_path / "prompts").exists()


def test_writes_file_and_returns_relative_path(tmp_path: Path):
    rel = dump_prompt(tmp_path, True, "T-001-iter01", MESSAGES, TOOLS)
    assert rel == "prompts/0001-T-001-iter01.md"
    body = (tmp_path / rel).read_text()
    # Faithful, uncapped: every channel the model received is present.
    assert "You are a worker." in body
    assert "Do the thing." in body
    assert '"cmd": "ls"' in body
    assert "file.txt" in body
    assert "thinking out loud" in body
    assert '"name": "bash"' in body  # tool schema


def test_sequence_is_monotonic_and_unique(tmp_path: Path):
    p1 = dump_prompt(tmp_path, True, "T-001-iter01", MESSAGES)
    p2 = dump_prompt(tmp_path, True, "T-001-iter01-judge1", MESSAGES)
    p3 = dump_prompt(tmp_path, True, "T-001-iter01-judge2", MESSAGES)
    assert [p1, p2, p3] == [
        "prompts/0001-T-001-iter01.md",
        "prompts/0002-T-001-iter01-judge1.md",
        "prompts/0003-T-001-iter01-judge2.md",
    ]


def test_sequence_survives_resume(tmp_path: Path):
    """The counter is derived from files already on disk, so a fresh process
    (resume) continues numbering instead of clobbering prior dumps."""
    first = dump_prompt(tmp_path, True, "T-001-iter01", MESSAGES)
    # Simulate a later process: nothing carried over in memory, only the files.
    second = dump_prompt(tmp_path, True, "T-002-iter01", MESSAGES)
    assert first == "prompts/0001-T-001-iter01.md"
    assert second == "prompts/0002-T-002-iter01.md"
    assert len(list((tmp_path / "prompts").glob("*.md"))) == 2


# ---- wiring: model_call event references the dump path ----------------------

class _FakeClient:
    def __init__(self, prompt_dump: bool):
        self.config = SimpleNamespace(
            context_files=["AGENTS.md", "CLAUDE.md"], prompt_dump=prompt_dump
        )

    def chat(self, messages, tools=None, model=None, tool_choice=None):
        return {"message": {"role": "assistant", "content": '{"propose": "no"}'},
                "usage": {}, "finish_reason": "stop"}


def _model_calls(session: Session) -> list[dict]:
    events = [json.loads(ln) for ln in session.events_path.read_text().splitlines()]
    return [e for e in events if e["type"] == "model_call"]


def test_model_call_references_dump_when_enabled(tmp_path: Path):
    session = Session.new(tmp_path / "sessions")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    task = {"id": "T-002", "title": "t", "description": "d"}
    _self_improve(task, worktree, session, _FakeClient(True), trace_id="tr")

    (call,) = _model_calls(session)
    assert call["payload"]["prompt_dump"] == "prompts/0001-T-002-self_improve.md"
    assert (session.root / call["payload"]["prompt_dump"]).is_file()


def test_model_call_omits_dump_when_disabled(tmp_path: Path):
    session = Session.new(tmp_path / "sessions")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    task = {"id": "T-002", "title": "t", "description": "d"}
    _self_improve(task, worktree, session, _FakeClient(False), trace_id="tr")

    (call,) = _model_calls(session)
    assert "prompt_dump" not in call["payload"]
    assert not (session.root / "prompts").exists()
