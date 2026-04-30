"""Session log and checkpoint management.

A session is an append-only events.jsonl file plus a checkpoint.json that snapshots
just enough state (last completed task, worktree branch) to resume on a fresh process.

Event types:
    model_call         — request/response metadata for an Ollama call
    tool_call          — a tool invocation by the model
    tool_result        — the harness's response to a tool call
    pre_tool_block     — pre_tool hook vetoed a tool call
    validator_run      — pytest/ruff/mypy result
    judge_verdict      — judge model verdict on a finished task
    task_done          — task accepted (validators + judge passed)
    task_failed        — task could not be completed; payload.reason ∈ {iter_cap}
    agents_md_update   — agent appended a learning to AGENTS.md
    context_reset      — beginning of a new task; messages rebuilt from disk
    session_start      — fresh session began (worktree created)
    session_resume     — --resume woke a session; payload carries the resume plan
                         (which failed tasks were retried, FAILED commit unwound, etc.)
    stop               — run terminated; payload.reason ∈
                         {all_done, wall_clock, token_cap, iter_cap, interrupted, error}
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


@dataclass
class Session:
    session_id: str
    root: Path                          # sessions/<id>/
    events_path: Path                   # sessions/<id>/events.jsonl
    checkpoint_path: Path               # sessions/<id>/checkpoint.json
    started_at: float = field(default_factory=time.time)
    workspace: Path | None = None       # set by workspace.py later
    branch: str | None = None
    tokens_used: int = 0

    @classmethod
    def new(cls, sessions_root: Path) -> Session:
        sid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        root = sessions_root / sid
        root.mkdir(parents=True, exist_ok=True)
        s = cls(
            session_id=sid,
            root=root,
            events_path=root / "events.jsonl",
            checkpoint_path=root / "checkpoint.json",
        )
        s.events_path.touch()
        s.save_checkpoint()
        return s

    @classmethod
    def wake(cls, sessions_root: Path, session_id: str) -> Session:
        """Resume a previous session.

        `started_at` is reset to now so the wall-clock cap applies per-resume rather
        than cumulatively (otherwise a resume tomorrow trips the cap immediately).
        `tokens_used` is preserved — if the run hit `token_cap`, bump the env var
        explicitly before resuming.
        """
        root = sessions_root / session_id
        if not root.is_dir():
            raise FileNotFoundError(f"No session at {root}")
        cp = json.loads((root / "checkpoint.json").read_text())
        s = cls(
            session_id=session_id,
            root=root,
            events_path=root / "events.jsonl",
            checkpoint_path=root / "checkpoint.json",
            started_at=time.time(),
            workspace=Path(cp["workspace"]) if cp.get("workspace") else None,
            branch=cp.get("branch"),
            tokens_used=cp.get("tokens_used", 0),
        )
        s.save_checkpoint()
        return s

    def log(self, event_type: str, payload: dict[str, Any]) -> None:
        record = {"ts": _ts(), "type": event_type, "payload": payload}
        with self.events_path.open("a") as f:
            f.write(json.dumps(record) + "\n")

    def save_checkpoint(self) -> None:
        cp = {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "workspace": str(self.workspace) if self.workspace else None,
            "branch": self.branch,
            "tokens_used": self.tokens_used,
        }
        self.checkpoint_path.write_text(json.dumps(cp, indent=2))

    def add_tokens(self, n: int) -> None:
        self.tokens_used += n
        self.save_checkpoint()

    def elapsed_minutes(self) -> float:
        return (time.time() - self.started_at) / 60.0
