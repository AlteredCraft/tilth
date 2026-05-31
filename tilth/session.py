"""Session log and checkpoint management.

A session is an append-only events.jsonl file plus a checkpoint.json that snapshots
just enough state (last completed task, worktree branch) to resume on a fresh process.
A summary.json is also written at every task boundary as a denormalised view of
events.jsonl (built by tilth/summary.py) — consumers like the visualizer and any
external tools should prefer reading that over re-streaming the JSONL.

A per-task evaluator ledger lives at ledger/<task_id>.jsonl (Phase 2). It is the
evaluator's durable read path — its memory of a task's prior iterations, injected
into its prompt on each call. Distinct from events.jsonl (the audit trail): each
ledger append is mirrored by a `ledger_appended` event, but the ledger files are
what the evaluator reads. See `append_ledger_entry` / `read_ledger`.

Event types:
    model_call         — request/response metadata for a model call. Carries
                         `reasoning_details` (the OpenRouter-normalised
                         structured form) when the model emitted any, falling
                         back to a flat `reasoning` string. Either is omitted
                         when absent so non-thinking models keep slim events.
                         Also carries `finish_reason` when the provider returns
                         one — watch for `"length"`, which means the response
                         (often a tool argument) was cut off by the provider's
                         max-tokens limit and the agent will be working from
                         truncated output. Carries a `phase` field for non-worker
                         calls (`evaluator`, `self_improve`); the worker omits it
                         by convention, the interview sets `interview`. Every
                         model-calling site emits this — evaluator calls also
                         carry `attempt` (1 or 2) to pair retries with their
                         `evaluator_parse_error`.
    tool_call          — a tool invocation by the model. Worktree tools route
                         through tools.dispatch; the worker's `submit_case`
                         (Phase 3) is a control-flow done-signal intercepted in
                         the loop, but is still logged as a tool_call (tool=
                         "submit_case", args=the parsed case) so it shows in the
                         histogram and visualizer.
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
    prompt_assembled   — an assembled user message just before it's sent to a
                         model. Payload: {role (worker|evaluator|self_improve),
                         iter, content (capped at PROMPT_ASSEMBLED_CHAR_CAP),
                         chars (untruncated length), truncated (bool)}. Lets a
                         post-run reviewer reconstruct what each actor saw on
                         each turn without replaying the loop. Worker prompt
                         fires once per task at iter=0; evaluator prompt fires
                         once per judge call at the worker's current iter;
                         self_improve fires once per completed task at iter=0.
    evaluator_verdict  — structured verdict from the evaluator (v1 of the
                         dialogue, see proposals/completed/v1-implementation-plan.md
                         Phase 1). Payload: {verdict (accept|reject),
                         rejection_category (enum|null — null on accept),
                         concern, evidence, next_step (str|null — null on
                         accept), schema_version}. Also carries `parse_failed:
                         True` on the fallback path where the model never
                         produced a valid `submit_verdict` call (rare; see
                         evaluator_parse_error for the per-attempt detail).
                         Successor to the v0 `judge_verdict` event.
    evaluator_parse_error
                       — the evaluator's response could not be parsed as a
                         valid `submit_verdict` tool call. Logged per attempt
                         (up to 2). Payload: {attempt, error, raw_tool_calls}.
                         `raw_tool_calls` is the model's actual emitted args
                         (capped) so a failing payload is faithfully preserved
                         for post-run review — never lost. On the second
                         failure, the loop synthesises a fallback reject
                         verdict (see `evaluator_verdict.parse_failed`).
    case_parse_error   — the worker's `submit_case` (Phase 3) could not be
                         parsed/validated. Payload: {iter, error, raw_tool_calls}.
                         Parity with `evaluator_parse_error`: the raw payload is
                         captured (capped) so a failing case is reconstructable.
                         The loop feeds `error` back as the submit_case
                         tool_result and lets the worker retry — it does not
                         count as a judge call or terminate the task.
    ledger_appended    — an entry was appended to a task's evaluator ledger
                         (Phase 2). Lightweight pointer: {task_id, iter,
                         verdict_summary (e.g. "accept" or "reject:scope_creep")}.
                         The full entry (diff_summary, case, verdict) lives in
                         ledger/<task_id>.jsonl, not in this event. The `case`
                         field is the worker's submitted case (Phase 3), or null
                         on the parse-failure fallback path.
    empty_model_response
                       — the model returned an empty turn (no content, no tool
                         calls, no reasoning) — a provider hiccup, not the worker
                         going quiet. Payload: {iter, streak, finish_reason,
                         prompt_tokens, eval_tokens}. The loop retries with
                         backoff; a sustained streak aborts the task with
                         `task_failed` reason `empty_responses`.
    task_done          — task accepted (validators + evaluator passed)
    task_failed        — task could not be completed; payload.reason ∈
                         {iter_cap, judge_cap, empty_responses, no_case}
    proposed_learnings — self-improvement step's per-task verdict. Payload:
                         {task_id, trace_id, span_id, emitted, entry?, reason?}.
                         When emitted=True, `entry` carries the learning text
                         appended to sessions/<id>/proposed-learnings.md (a
                         session output for the user; never read by the worker
                         or judge). When emitted=False, `reason` carries why
                         (no_proposal | unparseable | empty_learning).
    context_reset      — beginning of a new task; messages rebuilt from disk
    session_start      — fresh session began (worktree created). Payload `phase`
                         distinguishes `prep-feature` (the interview) from `run`
                         (the Ralph loop); both emit a session_start for the same
                         session id. summary.py keys `prep_started_at` vs
                         `started_at` off this. Unphased = treated as run.
    session_prepared   — `tilth prep-feature` finished an interview and wrote a
                         seed bundle. Payload carries `prd_entries` (count),
                         `test_files` (count), `interviewer_model`, and
                         `tokens_used` for the interview. Flips checkpoint
                         status to `prepared`. The worktree was created at the
                         start of prep (ws.ensure_worktree); the seed lands in
                         it via FileSeedSink.write_seed.
    seed_committed     — prep anchored the seed bundle as a single commit on the
                         session branch right after `session_prepared`. Payload
                         carries `sha` (short) and `branch` (`session/<id>`).
                         Without this, every seeded test file would appear as
                         uncommitted "scope creep" in T-001's `task_diff`.
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

    # --- per-task evaluator ledger (Phase 2) --------------------------------
    # sessions/<id>/ledger/<task_id>.jsonl — append-only, one entry per
    # evaluator call. The evaluator's durable memory of a task across
    # iterations (and across resumes; it's plain files under root). Distinct
    # from events.jsonl: the ledger is the evaluator's *read path*, events is
    # the audit trail. A `ledger_appended` event mirrors each append.

    def ledger_dir(self) -> Path:
        d = self.root / "ledger"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def append_ledger_entry(self, task_id: str, entry: dict[str, Any]) -> None:
        """Append one entry to this task's ledger. Stamps `ts` automatically."""
        record = {"ts": _ts(), **entry}
        path = self.ledger_dir() / f"{task_id}.jsonl"
        with path.open("a") as f:
            f.write(json.dumps(record) + "\n")

    def read_ledger(
        self, task_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Read a task's ledger entries, oldest first.

        Missing ledger → []. `limit` returns the last N entries (still
        oldest-first). Blank/corrupt lines are skipped, same as iter_events.
        """
        path = self.root / "ledger" / f"{task_id}.jsonl"
        entries: list[dict[str, Any]] = []
        if not path.is_file():
            return entries
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if limit is not None and limit >= 0:
            return entries[-limit:] if limit else []
        return entries

    def ledger_task_ids(self) -> list[str]:
        """Task ids that have a ledger file, sorted. [] if none yet.

        Read-only enumerate-all over `ledger/*.jsonl` — does not create the
        directory (unlike `ledger_dir`). Phase 5's self-improver uses this to
        read every task's ledger for cross-task pattern signal.
        """
        d = self.root / "ledger"
        if not d.is_dir():
            return []
        return sorted(p.stem for p in d.glob("*.jsonl"))

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
