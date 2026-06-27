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
    model_call         — request/response metadata for a model call. Carries the
                         provider's usage detail, flat (see tilth/usage.py for
                         the canonical record): `prompt_tokens`, `eval_tokens`
                         (completion), `tokens_used_total` (post-increment
                         running token total), and the OpenRouter extras
                         `cached_tokens` (cache hits, a subset of prompt),
                         `reasoning_tokens` (thinking, a subset of eval), and
                         `cost` (USD — the dollar-spend cap counter). cached and
                         reasoning are subsets and never inflate the token total.
                         Carries `reasoning_details` (the OpenRouter-normalised
                         structured form) when the model emitted any, falling
                         back to a flat `reasoning` string. Either is omitted
                         when absent so non-thinking models keep slim events.
                         Also carries `finish_reason` when the provider returns
                         one — watch for `"length"`, which means the response
                         (often a tool argument) was cut off by the provider's
                         max-tokens limit and the agent will be working from
                         truncated output. Carries a `phase` field for non-worker
                         calls (`evaluator`); the worker omits it by convention.
                         Every model-calling site emits this — evaluator calls
                         also carry `attempt` (1 or 2) to pair retries with their
                         `evaluator_parse_error`.
                         Provider-health fields (every call routes through
                         loop._chat_healthy): `health` ("ok" | "provider_error"
                         | "empty" — see client.response_health), `call_attempt`
                         (1..PROVIDER_RETRY_MAX_ATTEMPTS; >1 means the prior
                         attempt was unhealthy and was retried with the history
                         untouched), and, when present in the response: `model`,
                         `provider`, `response_id`, `health_detail` (the
                         provider's error message), `retry_backoff_seconds`
                         (on unhealthy attempts that will be retried). Unhealthy
                         calls never become conversation turns.
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
                         truncated, sha256_8) and `user_prompt_chars`. The
                         `agents_md` channel is the project-context channel —
                         the files named by TILTH_CONTEXT_FILES (default
                         AGENTS.md,CLAUDE.md), concatenated; its aggregate
                         fields describe the combined injection and it also
                         carries `files` (one {name, present, chars, sha256_8}
                         per configured file) and `loaded` (names present, in
                         order).
    prompt_assembled   — an assembled user message just before it's sent to a
                         model. Payload: {role (worker|evaluator), iter, content
                         (capped at PROMPT_ASSEMBLED_CHAR_CAP), chars (untruncated
                         length), truncated (bool)}. Lets a post-run reviewer
                         reconstruct what each actor saw on each turn without
                         replaying the loop. Worker prompt fires once per task at
                         iter=0; evaluator prompt fires once per evaluator call at
                         the worker's current iter.
    evaluator_verdict  — structured verdict from the evaluator (v1 of the
                         dialogue, see proposals/completed/v1-implementation-plan.md
                         Phase 1). Payload: {verdict (accept|reject),
                         rejection_category (enum|null — null on accept),
                         concern, evidence, next_step (str|null — null on
                         accept), schema_version}. Also carries `parse_failed:
                         True` on the fallback path where the model never
                         produced a valid `submit_verdict` call (rare; see
                         evaluator_parse_error for the per-attempt detail).
                         Successor to the v0 `evaluator_verdict` event.
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
                         count as a evaluator call or terminate the task.
    ledger_appended    — an entry was appended to a task's evaluator ledger
                         (Phase 2). Lightweight pointer: {task_id, iter,
                         verdict_summary (e.g. "accept" or "reject:scope_creep")}.
                         The full entry (diff_summary, case, verdict) lives in
                         ledger/<task_id>.jsonl, not in this event. The `case`
                         field is the worker's submitted case (Phase 3), or null
                         on the parse-failure fallback path.
    nudge              — the harness injected a corrective user message into the
                         worker's conversation. Payload: {iter, kind ("no_case"),
                         streak, content}. Logged so the message history the
                         model saw is reconstructable from this file — without
                         it the next model_call looks like a reply to nothing.
    task_done          — task accepted (the evaluator accepted the case + diff)
    task_failed        — task could not be completed; payload.reason ∈
                         {iter_cap, evaluator_cap, provider_failure, no_case}.
                         `provider_failure` carries `call_attempts` and means
                         the endpoint never produced a healthy response within
                         the retry budget — the session stays resumable.
    commit             — a completed task's work was committed to the session
                         branch (after task_done). Payload: {task_id, trace_id,
                         sha}. Emitted only on the success path; the FAILED
                         commit path does not log this.
    context_reset      — beginning of a new task; messages rebuilt from disk
    session_start      — fresh session began (worktree created). Payload carries
                         {source, feature_dir, feature, phase: "run", worktree,
                         branch, worker_model, evaluator_model, base_url, limits,
                         task_count}; `feature_dir` is the feature directory this
                         run targets and `feature` its basename (for display);
                         summary.py keys
                         `started_at` off this. The model/endpoint config is
                         recorded so "what ran" is answerable from the log
                         alone, not from whatever .env says later. `limits` is
                         the configured cap dict (see TilthConfig.limits:
                         max_token_dollar_spend, max_wall_clock_minutes,
                         max_iterations_per_task, max_evaluator_calls_per_task)
                         — the visualizer shows utilization against it.
                         `task_count` is the feature's full task count (the
                         viewer shows "N tasks" before every task has an event).
    session_resume     — resume woke a session; payload carries the resume plan
                         (which failed tasks were retried, FAILED commit unwound, etc.)
    archived           — `tilth cleanse` retired the session: worktree + branch
                         removed, this dir kept as the audit record. Payload:
                         {branch, worktree}. Sets checkpoint `archived: true`;
                         the run-outcome `status` is left intact.
    stop               — run terminated; payload.reason ∈
                         {all_done, wall_clock, token_cap, iter_cap, evaluator_cap,
                         provider_failure, no_case, interrupted, error}.
                         provider_failure leaves the session status `running`
                         (resumable), like wall_clock/token_cap — see
                         loop._stop_to_status.

Per-task observability fields:
    trace_id           — 32-hex (OTel-shape), constant for the whole task. Lets
                         downstream tools (Phoenix, Langfuse, Braintrust) ingest
                         events as a trace.
    span_id            — 16-hex (OTel-shape). Per-iteration for events inside an
                         iteration; per-sub-operation for memory_load.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tilth.usage import add_usage, phase_bucket, zero_usage


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


def _restore_usage(stored: Any) -> dict[str, Any]:
    """Rebuild the per-actor usage breakdown from a checkpoint.

    Each actor bucket starts zeroed and is overlaid with whatever the
    checkpoint carried, so old checkpoints (no `usage` key) and any
    forward-added fields both degrade cleanly to a well-formed breakdown.
    """
    stored = stored if isinstance(stored, dict) else {}
    out: dict[str, Any] = {}
    for actor in ("worker", "evaluator"):
        bucket = zero_usage()
        saved = stored.get(actor)
        if isinstance(saved, dict):
            bucket.update({k: v for k, v in saved.items() if k in bucket})
        out[actor] = bucket
    return out


SESSION_STATUSES = frozenset({"running", "all_done", "failed"})


@dataclass
class Session:
    session_id: str
    root: Path                          # sessions/<id>/
    events_path: Path                   # sessions/<id>/events.jsonl
    checkpoint_path: Path               # sessions/<id>/checkpoint.json
    started_at: float = field(default_factory=time.time)
    source: Path | None = None          # user's source repo path; set by callers
    feature_dir: Path | None = None     # feature dir (overview.md + T-NNN-*.md); set by callers
    workspace: Path | None = None       # worktree path; set when worktree exists
    branch: str | None = None
    tokens_used: int = 0                # cap counter: cumulative prompt + eval
    # Full token/cost detail, split by actor. The cap reads `tokens_used`; this
    # carries the breakdown (cached/reasoning subsets + cost) the cap discards.
    usage: dict[str, Any] = field(
        default_factory=lambda: {"worker": zero_usage(), "evaluator": zero_usage()}
    )
    status: str = "running"             # running | all_done | failed
    archived: bool = False              # `tilth cleanse`: worktree+branch removed,
                                        # session dir kept as the audit record

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
        The `usage` breakdown is preserved — its `cost` is the dollar-spend cap
        counter (`cost_used()`), so if the run hit `token_cap`, bump
        TILTH_MAX_TOKEN_DOLLAR_SPEND before resuming or it re-trips at once. Old
        checkpoints predate `usage`, so it defaults to a fresh zeroed breakdown;
        `tokens_used` still restores the running token total and the full
        per-call detail remains in events.jsonl.

        `status` is preserved from disk; a resuming caller flips it back to
        `running` via `set_status` as needed.
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
            feature_dir=Path(cp["feature_dir"]) if cp.get("feature_dir") else None,
            workspace=Path(cp["workspace"]) if cp.get("workspace") else None,
            branch=cp.get("branch"),
            tokens_used=cp.get("tokens_used", 0),
            usage=_restore_usage(cp.get("usage")),
            status=cp.get("status", "running"),
            archived=cp.get("archived", False),
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
            "feature_dir": str(self.feature_dir) if self.feature_dir else None,
            "workspace": str(self.workspace) if self.workspace else None,
            "branch": self.branch,
            "tokens_used": self.tokens_used,
            "usage": self.usage,
            "status": self.status,
            "archived": self.archived,
        }
        self.checkpoint_path.write_text(json.dumps(cp, indent=2))

    def set_status(self, status: str) -> None:
        if status not in SESSION_STATUSES:
            raise ValueError(
                f"unknown session status {status!r}; expected one of {sorted(SESSION_STATUSES)}"
            )
        self.status = status
        self.save_checkpoint()

    def mark_archived(self) -> None:
        """Record that `tilth cleanse` removed the worktree + branch but kept this
        session dir as the audit record. Separate from `status`, which preserves
        how the run ended (all_done / failed)."""
        self.archived = True
        self.save_checkpoint()

    def record_usage(self, u: dict[str, Any], phase: str | None = None) -> None:
        """Record one model call's usage into the actor's breakdown and the
        running token total, then persist.

        `tokens_used` advances by `prompt + eval` only — cached/reasoning are
        subsets of those, never added on top, so the token total keeps its exact
        prior semantics. The per-actor `usage` carries the full detail including
        `cost`, which `cost_used()` sums into the dollar-spend cap counter.
        Called per attempt, like the token recording it replaces, so
        provider-retry spend is counted even though the unhealthy response never
        became a turn.
        """
        add_usage(self.usage[phase_bucket(phase)], u)
        self.tokens_used += int(u.get("prompt") or 0) + int(u.get("eval") or 0)
        self.save_checkpoint()

    def cost_used(self) -> float:
        """Cumulative provider-reported USD spend — the dollar cap counter.

        Summed from the per-actor `usage` breakdown (worker + evaluator), which
        carries the provider's own `cost` figure and is persisted/restored
        across resumes alongside `tokens_used`. Providers that don't report cost
        leave this at 0.0, so the dollar cap never trips for them; wall-clock is
        the backstop there.
        """
        return float(
            (self.usage.get("worker") or {}).get("cost", 0.0)
            + (self.usage.get("evaluator") or {}).get("cost", 0.0)
        )

    def elapsed_minutes(self) -> float:
        return (time.time() - self.started_at) / 60.0
