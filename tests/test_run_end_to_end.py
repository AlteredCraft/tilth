"""End-to-end loop wiring with a fake client (no live model, no validators).

Drives `loop.run()` against a real git worktree. Proves the prompt-driven
contract after the subtractive refactor:
  - submit_case routes straight to the evaluator (no ruff/pytest gate),
  - an accept commits the work and marks the task done in task-status.json,
  - the run stops `all_done`,
  - no `validator_run` event is ever emitted.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tilth import loop
from tilth.session import Session


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "worktree"
    wt.mkdir()
    _git(["init", "-b", "main"], wt)
    _git(["config", "user.email", "t@t.invalid"], wt)
    _git(["config", "user.name", "t"], wt)
    (wt / "seed.txt").write_text("seed\n")
    _git(["add", "-A"], wt)
    _git(["commit", "-m", "init"], wt)
    return wt


@pytest.fixture
def session(tmp_path: Path) -> Session:
    return Session.new(tmp_path / "sessions")


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        worker_model="worker-m",
        evaluator_model="evaluator-m",
        context_files=["AGENTS.md", "CLAUDE.md"],
        max_iterations_per_task=8,
        max_evaluator_calls_per_task=0,
        max_wall_clock_minutes=120,
        max_token_dollar_spend=10.0,
    )


class _FakeClient:
    """Worker writes a file + submits a case in one turn; evaluator accepts."""

    def __init__(self):
        self.config = _config()

    def chat(self, messages, tools=None, model=None, tool_choice=None):
        tool_names = {(t.get("function") or {}).get("name") for t in (tools or [])}
        if model == self.config.evaluator_model or "submit_verdict" in tool_names:
            msg = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "v1",
                        "function": {
                            "name": "submit_verdict",
                            "arguments": json.dumps(
                                {
                                    "verdict": "accept",
                                    "rejection_category": None,
                                    "concern": "satisfies the criterion",
                                    "evidence": [],
                                    "next_step": None,
                                }
                            ),
                        },
                    }
                ],
            }
        else:
            msg = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "w1",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({"path": "feature.txt", "content": "done\n"}),
                        },
                    },
                    {
                        "id": "c1",
                        "function": {
                            "name": "submit_case",
                            "arguments": json.dumps(
                                {
                                    "summary": "created feature.txt",
                                    "ac_coverage": [
                                        {
                                            "criterion": "feature.txt exists",
                                            "addressed_by": "feature.txt",
                                        }
                                    ],
                                }
                            ),
                        },
                    },
                ],
            }
        return {
            "message": msg,
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "finish_reason": "tool_calls",
        }


def _events(session: Session) -> list[dict]:
    return [json.loads(line) for line in session.events_path.read_text().splitlines()]


def test_run_accepts_commits_and_finishes(worktree, session):
    session.workspace = worktree
    session.branch = "main"
    static_tasks = [
        {"id": "T-001", "title": "make feature.txt", "description": "create feature.txt",
         "acceptance_criteria": ["feature.txt exists"]},
    ]
    loop.run(worktree, session, _FakeClient(), "# Feature\n\nthe why", static_tasks)

    # status tracked harness-side, task marked done
    status = json.loads((session.root / "task-status.json").read_text())
    assert status == {"T-001": "done"}

    # the worker's file was actually committed to the session branch
    assert (worktree / "feature.txt").read_text() == "done\n"
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=worktree, capture_output=True, text=True
    ).stdout
    assert "T-001" in log

    types = [e["type"] for e in _events(session)]
    assert "task_done" in types
    assert "commit" in types
    assert types[-1] == "stop"
    # the evaluator was the gate — no validator step ran
    assert "validator_run" not in types
    stop = [e for e in _events(session) if e["type"] == "stop"][-1]
    assert stop["payload"]["reason"] == "all_done"
