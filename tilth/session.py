"""Session log and checkpoint management.

A session is an append-only events.jsonl file plus a checkpoint.json that snapshots
just enough state (last completed task, worktree branch) to resume on a fresh process.
A summary.json is also written at every task boundary as a denormalised view of
events.jsonl (built by tilth/summary.py) — consumers like the visualizer and any
external tools should prefer reading that over re-streaming the JSONL.

Event types:
    model_call         — request/response metadata for a worker call. Carries
                         `reasoning_details` (the OpenRouter-normalised
                         structured form) when the model emitted any, falling
                         back to a flat `reasoning` string. Either is omitted
                         when absent so non-thinking models keep slim events.
                         Also carries `finish_reason` when the provider returns
                         one — watch for `"length"`, which means the response
                         (often a tool argument) was cut off by the provider's
                         max-tokens limit and the agent will be working from
                         truncated output.
    tool_call          — a tool invocation by the model
    tool_result        — the harness's response to a tool call
    pre_tool_block     — pre_tool hook vetoed a tool call (also captured as a
                         hook_run with outcome=block; this event is kept for the
                         agent-feedback path the visualizer renders specially)
    hook_run           — a lifecycle hook ran. Payload: hook (pre_tool|post_edit),
                         outcome (allow|block|silent|warned), tool, optional
                         reason. Successful silent runs are logged so developers
                         can distinguish "ran, said nothing" from "didn't run".
    memory_load        — a memory channel was loaded into a prompt. Payload
                         carries `channels` (per-channel: present, chars,
                         truncated, sha256_8) and `user_prompt_chars`.
    validator_run      — pytest/ruff/mypy result
    judge_verdict      — judge model verdict on a finished task
    task_done          — task accepted (validators + judge passed)
    task_failed        — task could not be completed; payload.reason ∈ {iter_cap}
    proposed_learnings — self-improvement step's per-task verdict. Payload:
                         {task_id, trace_id, span_id, emitted, entry?, reason?}.
                         When emitted=True, `entry` carries the learning text
                         appended to sessions/<id>/proposed-learnings.md (a
                         session output for the user; never read by the worker
                         or judge). When emitted=False, `reason` carries why
                         (no_proposal | unparseable | empty_learning).
    context_reset      — beginning of a new task; messages rebuilt from disk
    session_start      — fresh session began (worktree created)
    session_prepared   — `tilth prep-feature` finished an interview and wrote a
                         seed bundle. Payload carries `prd_entries` (count),
                         `test_files` (count), `interviewer_model`, and
                         `tokens_used` for the interview. Flips checkpoint
                         status to `prepared`. No worktree exists yet — that's
                         created on the subsequent `tilth run`.
    session_resume     — --resume woke a session; payload carries the resume plan
                         (which failed tasks were retried, FAILED commit unwound, etc.)
    stop               — run terminated; payload.reason ∈
                         {all_done, wall_clock, token_cap, iter_cap, interrupted, error}

Per-task observability fields:
    trace_id           — 32-hex (OTel-shape), constant for the whole task. Lets
                         downstream tools (Phoenix, Langfuse, Braintrust) ingest
                         events as a trace.
    span_id            — 16-hex (OTel-shape). Per-iteration for events inside an
                         iteration; per-sub-operation for memory_load and
                         proposed_learnings.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def iter_events(events_path: Path) -> Iterator[dict[str, Any]]:
    """Yield parsed event records from an events.jsonl file.

    Missing files yield nothing. Blank lines and JSON-decode errors are
    silently skipped — the log is append-only and a partial last line on
    crash is expected.
    """
    if not events_path.is_file():
        return
    with events_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


SESSION_STATUSES = frozenset({"prepared", "running", "all_done", "failed"})


@dataclass
class Session:
    session_id: str
    root: Path                          # sessions/<id>/
    events_path: Path                   # sessions/<id>/events.jsonl
    checkpoint_path: Path               # sessions/<id>/checkpoint.json
    started_at: float = field(default_factory=time.time)
    source: Path | None = None          # user's source repo path; set by callers
    workspace: Path | None = None       # worktree path; set when worktree exists
    branch: str | None = None
    tokens_used: int = 0
    status: str = "running"             # prepared | running | all_done | failed

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

        `status` is preserved from disk. Callers (e.g. `tilth run` waking a
        `prepared` session) are responsible for flipping it to `running` and
        re-saving.
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
            source=Path(cp["source"]) if cp.get("source") else None,
            workspace=Path(cp["workspace"]) if cp.get("workspace") else None,
            branch=cp.get("branch"),
            tokens_used=cp.get("tokens_used", 0),
            status=cp.get("status", "running"),
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
            "source": str(self.source) if self.source else None,
            "workspace": str(self.workspace) if self.workspace else None,
            "branch": self.branch,
            "tokens_used": self.tokens_used,
            "status": self.status,
        }
        self.checkpoint_path.write_text(json.dumps(cp, indent=2))

    def set_status(self, status: str) -> None:
        if status not in SESSION_STATUSES:
            raise ValueError(
                f"unknown session status {status!r}; expected one of {sorted(SESSION_STATUSES)}"
            )
        self.status = status
        self.save_checkpoint()

    def add_tokens(self, n: int) -> None:
        self.tokens_used += n
        self.save_checkpoint()

    def elapsed_minutes(self) -> float:
        return (time.time() - self.started_at) / 60.0


_LABEL_MAX_CHARS = 60


def session_label(session_dir: Path, max_chars: int = _LABEL_MAX_CHARS) -> str:
    """Short human-readable label for a session, for use in picker menus.

    Best-effort: tries seed-meta.json's tldr first, then prd.json's first entry
    title, then returns "". Never raises — pickers can't tolerate a malformed
    sessions/<id>/ taking down the menu.
    """
    label = _label_from_seed_meta(session_dir) or _label_from_prd(session_dir) or ""
    if max_chars and len(label) > max_chars:
        label = label[: max_chars - 1].rstrip() + "…"
    return label


def _label_from_seed_meta(session_dir: Path) -> str:
    path = session_dir / "seed-meta.json"
    if not path.is_file():
        return ""
    try:
        meta = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return ""
    tldr = meta.get("tldr") if isinstance(meta, dict) else None
    if not isinstance(tldr, str):
        return ""
    for raw in tldr.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        # Strip a leading list-bullet marker (`- ` or `* `) but preserve
        # markdown emphasis like `**T-001:**` verbatim — balancing it would
        # need a real parser.
        if stripped[:2] in ("- ", "* "):
            stripped = stripped[2:].strip()
        if stripped:
            return stripped
    return ""


def _label_from_prd(session_dir: Path) -> str:
    path = session_dir / "prd.json"
    if not path.is_file():
        return ""
    try:
        prd = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return ""
    if not isinstance(prd, list) or not prd:
        return ""
    first = prd[0]
    if not isinstance(first, dict):
        return ""
    tid = first.get("id", "")
    title = first.get("title", "")
    if tid and title:
        return f"{tid}: {title}"
    return str(title or tid or "")
