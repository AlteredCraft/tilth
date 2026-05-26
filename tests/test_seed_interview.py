"""run_interview drives a tool-use loop with mock LLMClient + frontend + sink.

The engine has three jobs:
  1. Translate model tool calls into frontend / read-tool / sink calls.
  2. Maintain the token surface (per-turn deltas → frontend.update_tokens).
  3. Terminate cleanly on `write_seed` (success), abort cleanly on bad shapes.

We mock the LLM client with a queue of canned responses so we can probe each
branch without burning real tokens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tilth.seed import FileSeedSink, run_interview
from tilth.seed.interview import InterviewAbort
from tilth.seed.sink import SeedWriteError
from tilth.session import Session


@dataclass
class FakeClient:
    """Returns canned responses in order; records what it was sent."""

    responses: list[dict[str, Any]]
    config: Any = None
    seen_requests: list[list[dict[str, Any]]] = field(default_factory=list)
    _idx: int = 0

    def chat(self, messages, tools=None, model=None):
        self.seen_requests.append(list(messages))
        resp = self.responses[self._idx]
        self._idx += 1
        return resp


@dataclass
class _Config:
    worker_model: str = "stub-model"
    prep_model: str = "stub-model"


@dataclass
class FakeFrontend:
    ask_answers: list[str] = field(default_factory=list)
    asked: list[tuple[str, list[str] | None]] = field(default_factory=list)
    summaries: list[tuple[str, list[str], list[str]]] = field(default_factory=list)
    token_updates: list[tuple[int, int]] = field(default_factory=list)
    _idx: int = 0

    def ask_user(self, question, options=None):
        self.asked.append((question, options))
        if self._idx < len(self.ask_answers):
            ans = self.ask_answers[self._idx]
            self._idx += 1
            return ans
        return ""

    def show_summary(self, tldr, open_questions, blockers):
        self.summaries.append((tldr, open_questions, blockers))

    def update_tokens(self, prompt_total, completion_total):
        self.token_updates.append((prompt_total, completion_total))


def _tool_call(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


def _model_response(
    tool_calls: list[dict[str, Any]] | None = None,
    content: str | None = None,
    prompt: int = 100,
    completion: int = 50,
) -> dict[str, Any]:
    return {
        "message": {
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        },
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
        "finish_reason": "tool_calls" if tool_calls else "stop",
    }


@pytest.fixture
def sessions_root(tmp_path: Path) -> Path:
    return tmp_path / "sessions"


@pytest.fixture
def source(tmp_path: Path) -> Path:
    s = tmp_path / "source"
    s.mkdir()
    (s / "todo_cli.py").write_text("def main():\n    pass\n")
    return s


def _good_seed_call() -> dict[str, Any]:
    return _tool_call(
        "c1",
        "write_seed",
        {
            "prd_entries": [
                {
                    "id": "T-001",
                    "title": "scaffold",
                    "description": "lay things out",
                    "acceptance_criteria": ["thing exists"],
                }
            ],
            "test_files": {
                "test_t001_scaffold.py": 'def test_x():\n    assert True\n',
            },
            "tldr": "- **T-001:** scaffold — lays out the thing",
            "open_questions": ["should X also do Y?"],
            "blockers": [],
        },
    )


def test_single_turn_write_seed_terminates_and_flips_status(sessions_root, source):
    client = FakeClient(
        responses=[_model_response(tool_calls=[_good_seed_call()])],
        config=_Config(),
    )
    frontend = FakeFrontend()
    sink = FileSeedSink()
    session = Session.new(sessions_root)
    session.source = source

    result = run_interview(
        session=session, source=source, client=client,
        frontend=frontend, sink=sink, feature_brief="add a thing",
    )

    assert session.status == "prepared"
    assert [e["id"] for e in result.prd_entries] == ["T-001"]
    assert result.tokens_used == 150
    assert (session.root / "prd.json").is_file()
    assert (session.root / "seed-meta.json").is_file()
    assert (source / "tests" / "test_t001_scaffold.py").is_file()
    # Frontend got the closing summary and a token update.
    assert frontend.summaries and "scaffold" in frontend.summaries[0][0]
    assert frontend.token_updates[-1] == (100, 50)


def test_ask_user_routes_to_frontend_then_write_seed_terminates(sessions_root, source):
    """Two-turn interview: ask_user, get answer, then write_seed."""
    client = FakeClient(
        responses=[
            _model_response(
                tool_calls=[
                    _tool_call(
                        "c1",
                        "ask_user",
                        {"question": "single file or split?", "options": ["single", "split"]},
                    )
                ]
            ),
            _model_response(tool_calls=[_good_seed_call()]),
        ],
        config=_Config(),
    )
    frontend = FakeFrontend(ask_answers=["split"])
    session = Session.new(sessions_root)
    session.source = source

    run_interview(
        session=session, source=source, client=client,
        frontend=frontend, sink=FileSeedSink(), feature_brief="add a thing",
    )

    assert frontend.asked == [("single file or split?", ["single", "split"])]
    # Engine surfaces tokens after every model turn.
    assert len(frontend.token_updates) == 2


def test_read_file_routes_to_source_repo(sessions_root, source):
    client = FakeClient(
        responses=[
            _model_response(
                tool_calls=[_tool_call("c1", "read_file", {"path": "todo_cli.py"})]
            ),
            _model_response(tool_calls=[_good_seed_call()]),
        ],
        config=_Config(),
    )
    frontend = FakeFrontend()
    session = Session.new(sessions_root)
    session.source = source

    run_interview(
        session=session, source=source, client=client,
        frontend=frontend, sink=FileSeedSink(), feature_brief="add a thing",
    )

    # Second model call includes the read result in the message history.
    second_request = client.seen_requests[1]
    tool_msg = next(m for m in second_request if m.get("role") == "tool")
    assert "def main()" in tool_msg["content"]


def test_unknown_tool_returns_error_string(sessions_root, source):
    client = FakeClient(
        responses=[
            _model_response(tool_calls=[_tool_call("c1", "nonexistent", {})]),
            _model_response(tool_calls=[_good_seed_call()]),
        ],
        config=_Config(),
    )
    session = Session.new(sessions_root)
    session.source = source

    run_interview(
        session=session, source=source, client=client,
        frontend=FakeFrontend(), sink=FileSeedSink(), feature_brief="thing",
    )

    tool_msg = next(m for m in client.seen_requests[1] if m.get("role") == "tool")
    assert "unknown tool" in tool_msg["content"]


def test_model_stopping_without_write_seed_aborts(sessions_root, source):
    client = FakeClient(
        responses=[_model_response(content="I'm done thinking but did nothing.")],
        config=_Config(),
    )
    session = Session.new(sessions_root)
    session.source = source

    with pytest.raises(InterviewAbort, match="stopped before calling write_seed"):
        run_interview(
            session=session, source=source, client=client,
            frontend=FakeFrontend(), sink=FileSeedSink(), feature_brief="thing",
        )
    assert session.status == "running"  # status not flipped on abort


def test_sink_failure_feeds_error_back_and_allows_retry(sessions_root, source):
    """A failed write_seed shouldn't terminate; the model can fix and retry."""
    bad_call = _tool_call(
        "c1",
        "write_seed",
        {
            "prd_entries": [
                {
                    "id": "T-bad",  # malformed id
                    "title": "x",
                    "description": "y",
                    "acceptance_criteria": ["z"],
                }
            ],
            "test_files": {"test_t001_x.py": "def test_x(): pass"},
            "tldr": "",
        },
    )
    client = FakeClient(
        responses=[
            _model_response(tool_calls=[bad_call]),
            _model_response(tool_calls=[_good_seed_call()]),
        ],
        config=_Config(),
    )
    session = Session.new(sessions_root)
    session.source = source

    run_interview(
        session=session, source=source, client=client,
        frontend=FakeFrontend(), sink=FileSeedSink(), feature_brief="thing",
    )

    # First retry sees the validation error as a tool message.
    second_request = client.seen_requests[1]
    err_msg = next(
        m for m in second_request
        if m.get("role") == "tool" and "ERROR" in (m.get("content") or "")
    )
    assert "task id must match" in err_msg["content"]
    assert session.status == "prepared"  # second call succeeded


def test_session_prepared_event_is_logged_with_counts(sessions_root, source):
    client = FakeClient(
        responses=[_model_response(tool_calls=[_good_seed_call()])],
        config=_Config(),
    )
    session = Session.new(sessions_root)
    session.source = source

    run_interview(
        session=session, source=source, client=client,
        frontend=FakeFrontend(), sink=FileSeedSink(), feature_brief="thing",
    )

    from tilth.session import iter_events

    events = [e for e in iter_events(session.events_path) if e["type"] == "session_prepared"]
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["prd_entries"] == 1
    assert payload["test_files"] == 1
    assert payload["interviewer_model"] == "stub-model"


def test_sink_can_raise_unexpected_error_without_corrupting_status(sessions_root, source):
    """A non-validation sink failure (e.g. disk full) propagates without flipping
    status to prepared. We simulate this with a sink that always raises."""

    class BoomSink:
        def write_seed(self, **kwargs):
            raise SeedWriteError("boom")

    client = FakeClient(
        responses=[
            _model_response(tool_calls=[_good_seed_call()]),
            _model_response(tool_calls=[_good_seed_call()]),
            _model_response(content="giving up"),
        ],
        config=_Config(),
    )
    session = Session.new(sessions_root)
    session.source = source

    with pytest.raises(InterviewAbort):
        run_interview(
            session=session, source=source, client=client,
            frontend=FakeFrontend(), sink=BoomSink(), feature_brief="thing",
        )
    assert session.status == "running"
