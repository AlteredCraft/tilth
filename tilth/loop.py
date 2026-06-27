"""Ralph loop entry point.

The feature is authored as markdown in a feature directory (conventionally
`<repo>/.tilth/<feature>/`): an `overview.md` plus one `T-NNN-*.md` per task —
see `tilth/tasks.py`. For each pending task:
  1. Reset context — build a fresh message list from disk (workspace context
     files + feature overview + full plan + session progress tail + this task +
     the evaluator's prior verdicts on it).
  2. Tool-loop with the worker model.
  3. When the worker calls `submit_case` (its done-signal), the evaluator
     reviews the case + diff + ledger in a fresh context.
       - Accept: commit, mark done, next task.
       - Reject: feed the structured verdict back as the submit_case
                 tool_result; another iteration.

There are no codified validators (ruff/pytest) in this prompt-driven harness —
the evaluator is the only gate. The worker may run anything it likes via `bash`,
but nothing is enforced between iterations.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from rich.console import Console

from tilth import memory, paths, summary, tasks, tools, usage, visualize
from tilth import workspace as ws
from tilth.case import (
    NAME_SUBMIT_CASE,
    SUBMIT_CASE_TOOL,
    format_case_section,
    parse_case,
)
from tilth.client import (
    LLMClient,
    TilthConfig,
    assistant_history_message,
    response_health,
)
from tilth.session import Session, iter_events
from tilth.tasks import TasksError
from tilth.verdict import (
    SUBMIT_VERDICT_TOOL,
    VERDICT_SCHEMA_VERSION,
    format_ledger_section,
    format_reject_feedback,
    parse_verdict,
)

console = Console()

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
DATA_DIR = Path(__file__).resolve().parent / "data"
# Resolved at import from $TILTH_HOME / $TILTH_SESSIONS_DIR (default ~/.tilth).
# cli.main() re-resolves it after loading the .env, so a .env-provided override
# still takes effect.
SESSIONS_DIR = paths.sessions_dir()


def _trace_id() -> str:
    return uuid.uuid4().hex  # 32 hex chars, OTel trace_id shape


def _span_id() -> str:
    return uuid.uuid4().hex[:16]  # 16 hex chars, OTel span_id shape


def _refresh_summary(session: Session) -> None:
    """Re-roll events.jsonl into summary.json. Cheap; called at task boundaries
    and at stop. Failures here must not break the run."""
    try:
        summary.write_summary(
            session.events_path,
            session.root / "summary.json",
            session_id=session.session_id,
        )
    except Exception as exc:
        console.print(f"[dim]summary refresh failed: {type(exc).__name__}: {exc}[/dim]")


# --- prompt assembly --------------------------------------------------------

def _system_prompt() -> str:
    return (PROMPTS_DIR / "system.md").read_text()


def _evaluator_prompt() -> str:
    return (PROMPTS_DIR / "evaluator.md").read_text()


# --- evaluator -------------------------------------------

JUDGE_DIFF_MAX_CHARS = 12_000
PROMPT_ASSEMBLED_CHAR_CAP = 16_000
MODEL_RAW_ARGS_CHAR_CAP = 16_000  # generous — faithful capture is the priority
LEDGER_INJECT_LIMIT = 5  # OQ #1: last-N ledger entries injected into evaluator prompt

WORKER_NO_CASE_NUDGE = (
    "You stopped without calling `submit_case`. This harness no longer treats "
    "'no more tool calls' as done — when the task is complete and verified, "
    "present your case by calling `submit_case`. If you're not done, keep "
    "working with the other tools."
)

# Provider-health retry policy. A call whose response the provider itself marks
# unhealthy (see client.response_health) is retried with the message history
# untouched — an unhealthy response never becomes a conversation turn, never
# burns an iteration, and never routes to the no-case nudge (which would be a
# false accusation injected into the worker's context). Patience is sized to
# the documented failure: OpenRouter describes empty/no-content 200s as
# warm-up/scale-up transients lasting seconds-to-minutes, so the budget is
# ~3 minutes per logical call (2,4,8,16,32,60,60s), not a handful of seconds.
# Exhaustion stops the run with reason `provider_failure` — a *resumable* stop
# (status stays `running`, like token_cap), because nothing about the work is
# wrong; `tilth resume` retries the task.
PROVIDER_RETRY_MAX_ATTEMPTS = 8
PROVIDER_RETRY_BACKOFF_CAP_SECONDS = 60

MAX_CONSECUTIVE_NO_CASE_NUDGES = 3  # consecutive no-case turns before aborting the task


def _provider_backoff(attempt: int) -> int:
    return min(2 ** attempt, PROVIDER_RETRY_BACKOFF_CAP_SECONDS)


def _partition_worker_tool_calls(
    tool_calls: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split an assistant message's tool calls into (worktree, submit_case).

    Worktree tools route through `tools.dispatch`; `submit_case` is the
    control-flow done-signal, intercepted in `_run_task`. Returned as two
    lists (preserving order) so the loop can run worktree tools first, then
    handle the case.
    """
    worktree: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    for tc in tool_calls:
        name = (tc.get("function") or {}).get("name")
        (cases if name == NAME_SUBMIT_CASE else worktree).append(tc)
    return worktree, cases


def _answer_case_calls(
    messages: list[dict[str, Any]],
    case_tcs: list[dict[str, Any]],
    primary: str,
) -> None:
    """Append a `tool_result` for every `submit_case` tool call.

    Every tool call in an assistant message must be answered before the next
    model call, or the provider 400s. The first submit_case gets the real
    outcome feedback (`primary`); rare duplicates get a stub.
    """
    for i, tc in enumerate(case_tcs):
        body = primary if i == 0 else (
            "Duplicate submit_case ignored; act on the response to the first."
        )
        messages.append(
            {"role": "tool", "tool_call_id": tc.get("id") or "", "content": body}
        )


def _chat_healthy(
    client: LLMClient,
    session: Session,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
    base: dict[str, Any],
) -> dict[str, Any] | None:
    """The single model-calling site: chat until the provider returns a healthy
    turn, or exhaust the retry budget. Returns the healthy normalised response,
    or None on exhaustion. Never mutates `messages` — an unhealthy response
    must not leave a trace in the conversation.

    Health comes from `client.response_health` (the provider's own signals:
    `error` object, `finish_reason` — shape-emptiness last), not from what the
    message happens to contain. Every attempt emits a `model_call` event
    carrying `health`, `call_attempt`, and the provider evidence (`model`,
    `provider`, `response_id`, `error`) so a post-run reader can see exactly
    what the endpoint did. Tokens are recorded per attempt, before the event,
    so `tokens_used_total` is post-increment.
    """
    for attempt in range(1, PROVIDER_RETRY_MAX_ATTEMPTS + 1):
        resp = client.chat(messages, tools=tools, model=model)
        u = usage.extract_usage(resp.get("usage"))
        session.record_usage(u, base.get("phase"))

        health, detail = response_health(resp)
        msg = resp.get("message") or {}
        payload: dict[str, Any] = {
            **base,
            "call_attempt": attempt,
            "prompt_tokens": u["prompt"],
            "eval_tokens": u["eval"],
            "cached_tokens": u["cached"],
            "reasoning_tokens": u["reasoning"],
            "cost": u["cost"],
            "tokens_used_total": session.tokens_used,
            "health": health,
        }
        if finish_reason := resp.get("finish_reason"):
            payload["finish_reason"] = finish_reason
        for key in ("model", "provider", "response_id"):
            if resp.get(key):
                payload[key] = resp[key]
        if detail:
            payload["health_detail"] = detail
        if reasoning_details := msg.get("reasoning_details"):
            payload["reasoning_details"] = reasoning_details
        else:
            reasoning = msg.get("reasoning")
            if isinstance(reasoning, str) and reasoning.strip():
                payload["reasoning"] = reasoning

        if health == "ok":
            session.log("model_call", payload)
            return resp

        backoff = _provider_backoff(attempt) if attempt < PROVIDER_RETRY_MAX_ATTEMPTS else None
        if backoff is not None:
            payload["retry_backoff_seconds"] = backoff
        session.log("model_call", payload)
        if backoff is None:
            break
        console.print(
            f"[yellow]unhealthy model response ({health}: {detail}); "
            f"retry {attempt}/{PROVIDER_RETRY_MAX_ATTEMPTS - 1} in {backoff}s[/yellow]"
        )
        time.sleep(backoff)
    return None


def _raw_tool_calls(msg: dict[str, Any], cap: int) -> list[dict[str, Any]]:
    """Capture the raw tool-call arguments from an assistant message.

    For faithful post-run reconstruction of a model response that didn't
    parse — without this, a parse failure leaves no trace of what the model
    actually emitted. Each entry: {name, arguments (capped)}.
    """
    out: list[dict[str, Any]] = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw = fn.get("arguments")
        if isinstance(raw, dict):
            raw = json.dumps(raw)
        if isinstance(raw, str):
            out.append({"name": fn.get("name"), "arguments": raw[:cap]})
    return out


def _log_prompt_assembled(
    session: Session,
    *,
    role: str,
    task_id: str,
    trace_id: str,
    span_id: str,
    iter_value: int,
    content: str,
) -> None:
    """Capture an assembled user message in events.jsonl for post-run review.

    Cross-cutting concern from v1-implementation-plan.md: a post-run agent
    pointed at the session must be able to reconstruct *what each actor
    saw* on each turn. Capped so a giant prompt doesn't blow up the log.

    `iter_value` is the literal value to write to the payload's `iter`
    field — 0 for task-start prompts (before any iteration), 1..N for
    in-iteration prompts. Callers do the indexing themselves so the
    intent (task-start vs mid-loop) is visible at the call site.
    """
    chars = len(content)
    truncated = chars > PROMPT_ASSEMBLED_CHAR_CAP
    body = (
        content[:PROMPT_ASSEMBLED_CHAR_CAP] + "\n... [truncated]"
        if truncated
        else content
    )
    session.log(
        "prompt_assembled",
        {
            "task_id": task_id,
            "trace_id": trace_id,
            "span_id": span_id,
            "iter": iter_value,
            "role": role,
            "content": body,
            "chars": chars,
            "truncated": truncated,
        },
    )


def _build_ledger_entry(
    *,
    iter_n: int,
    diff_summary: str,
    case: dict[str, Any] | None,
    verdict: dict[str, Any],
) -> dict[str, Any]:
    """Construct one per-task ledger entry. `case` is the worker's submitted
    case (Phase 3) or None (parse-failure fallback / no case)."""
    return {
        "iter": iter_n,
        "diff_summary": diff_summary,
        "case": case,
        "verdict": verdict,
    }


def _evaluator_task(
    task: dict[str, Any],
    worktree: Path,
    client: LLMClient,
    session: Session,
    iter_n: int,
    trace_id: str,
    span_id: str,
    case: dict[str, Any] | None = None,
    overview: str | None = None,
) -> dict[str, Any] | None:
    """Call the evaluator in a fresh context. Returns a structured verdict.

    Shape: `{verdict, rejection_category, concern, evidence, next_step}`
    plus `parse_failed: True` on the fallback path where the model never
    produced a parseable `submit_verdict` call. The fallback verdict is
    `reject` so the loop keeps moving (and the operator sees the parse
    failure via `evaluator_parse_error` events).

    Returns None on provider failure — no verdict event, no ledger entry;
    the caller aborts the task as `provider_failure` (resumable).
    """
    diff = ws.task_diff(worktree)
    if len(diff) > JUDGE_DIFF_MAX_CHARS:
        diff = diff[:JUDGE_DIFF_MAX_CHARS] + f"\n... [truncated, total {len(diff)} chars]"

    parts = [
        f"# Task to evaluate: {task['id']} — {task['title']}",
        "",
        task.get("description", "").strip(),
    ]
    criteria = task.get("acceptance_criteria") or []
    if criteria:
        parts += ["", "## Acceptance criteria"] + [f"- {c}" for c in criteria]
    if overview and overview.strip():
        parts += [
            "",
            "## Feature overview (the why + scope boundaries)",
            "",
            overview.strip(),
        ]
    ctx_text, ctx_names = memory.load_context_files(worktree, client.config.context_files)
    if ctx_text.strip():
        parts += [
            "",
            f"## Project context ({', '.join(ctx_names)})",
            "",
            ctx_text.rstrip(),
        ]
    # Evaluator memory: prior verdicts on this same task (Phase 2). Read here
    # — before this call's verdict is appended below — so the section shows
    # only iterations that preceded the current one.
    ledger_section = format_ledger_section(
        session.read_ledger(task["id"], limit=LEDGER_INJECT_LIMIT)
    )
    if ledger_section:
        parts += ["", ledger_section]
    if case is not None:
        parts += ["", format_case_section(case)]
    parts += [
        "",
        "## Diff (working tree vs HEAD on this task's branch)",
        "",
        "```diff",
        diff if diff.strip() else "(empty diff)",
        "```",
        "",
        "Submit your verdict by calling `submit_verdict` — that tool call",
        "is the only acceptable response.",
    ]
    user_content = "\n".join(parts)
    _log_prompt_assembled(
        session,
        role="evaluator",
        task_id=task["id"],
        trace_id=trace_id,
        span_id=span_id,
        iter_value=iter_n + 1,
        content=user_content,
    )

    evaluator_messages: list[dict[str, Any]] = [
        {"role": "system", "content": _evaluator_prompt()},
        {"role": "user", "content": user_content},
    ]

    verdict = _call_evaluator_with_retry(
        client,
        evaluator_messages,
        session=session,
        task_id=task["id"],
        trace_id=trace_id,
        span_id=span_id,
        iter_n=iter_n,
    )
    if verdict is None:
        return None

    session.log(
        "evaluator_verdict",
        {
            "task_id": task["id"],
            "trace_id": trace_id,
            "span_id": span_id,
            "iter": iter_n + 1,
            "schema_version": VERDICT_SCHEMA_VERSION,
            **verdict,
        },
    )

    # Persist this iteration to the task ledger (the evaluator's read path on
    # the next call). `case` is the worker's submitted case (Phase 3).
    session.append_ledger_entry(
        task["id"],
        _build_ledger_entry(
            iter_n=iter_n + 1,
            diff_summary=ws.task_diff_summary(worktree),
            case=case,
            verdict=verdict,
        ),
    )
    category = verdict.get("rejection_category")
    verdict_summary = (
        f"reject:{category}"
        if verdict["verdict"] == "reject" and category
        else verdict["verdict"]
    )
    session.log(
        "ledger_appended",
        {
            "task_id": task["id"],
            "trace_id": trace_id,
            "span_id": span_id,
            "iter": iter_n + 1,
            "verdict_summary": verdict_summary,
        },
    )
    return verdict


def _call_evaluator_with_retry(
    client: LLMClient,
    messages: list[dict[str, Any]],
    *,
    session: Session,
    task_id: str,
    trace_id: str,
    span_id: str,
    iter_n: int,
) -> dict[str, Any] | None:
    """Two-attempt tool-call loop with parse-error feedback between attempts.

    Attempt 1: send the assembled prompt. Parse the assistant message via
    `parse_verdict`. If clean → return.

    Attempt 2: only reached on parse failure. Echo the assistant message,
    respond to each emitted tool_call with the parse error as `tool_result`
    content, and retry the call. If still bad → synthesise a fallback
    reject verdict so the loop survives; `parse_failed: True` makes the
    failure visible to summary/visualizer consumers.

    Returns None on provider failure (no healthy response within the retry
    budget) — distinct from the parse-failure fallback: a provider outage must
    not be recorded as a reject verdict in the task's ledger.
    """
    last_err = ""
    for attempt in (1, 2):
        resp = _chat_healthy(
            client,
            session,
            messages,
            model=client.config.evaluator_model,
            tools=[SUBMIT_VERDICT_TOOL],
            base={
                "task_id": task_id,
                "trace_id": trace_id,
                "span_id": span_id,
                "iter": iter_n + 1,
                "attempt": attempt,
                "phase": "evaluator",
            },
        )
        if resp is None:
            return None

        msg = resp.get("message") or {}
        verdict, err = parse_verdict(msg)
        if err is None:
            assert verdict is not None
            return verdict

        last_err = err
        session.log(
            "evaluator_parse_error",
            {
                "task_id": task_id,
                "trace_id": trace_id,
                "span_id": span_id,
                "iter": iter_n + 1,
                "attempt": attempt,
                "error": err,
                "raw_tool_calls": _raw_tool_calls(msg, MODEL_RAW_ARGS_CHAR_CAP),
            },
        )

        if attempt == 2:
            break

        messages.append(assistant_history_message(msg))
        tool_calls = msg.get("tool_calls") or []
        if tool_calls:
            for tc in tool_calls:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id") or "",
                        "content": err,
                    }
                )
        else:
            messages.append({"role": "user", "content": err})

    return {
        "verdict": "reject",
        "rejection_category": None,
        "concern": (
            "Evaluator response could not be parsed after one retry. "
            f"Last error: {last_err}"
        ),
        "evidence": [],
        "next_step": None,
        "parse_failed": True,
    }


# --- task list + status -----------------------------------------------------
#
# The task *content* is authored markdown in the feature dir (conventionally
# `<repo>/.tilth/<feature>/`; read-only, loaded via tilth.tasks). Per-task
# *status* is harness-owned and
# lives in sessions/<id>/task-status.json — a flat {task_id: status} map. A task
# absent from the map is `pending`. The loop overlays status onto the static
# task list to get the prd-shaped list the rest of the harness consumes.

STATUS_FILENAME = "task-status.json"


def _load_status(session_dir: Path) -> dict[str, str]:
    path = session_dir / STATUS_FILENAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


def _save_status(session_dir: Path, status_map: dict[str, str]) -> None:
    (session_dir / STATUS_FILENAME).write_text(json.dumps(status_map, indent=2) + "\n")


def _set_task_status(session_dir: Path, task_id: str, status: str) -> None:
    status_map = _load_status(session_dir)
    status_map[task_id] = status
    _save_status(session_dir, status_map)


def _overlay_status(
    static_tasks: list[dict[str, Any]], status_map: dict[str, str]
) -> list[dict[str, Any]]:
    """Return prd-shaped task dicts (copies) with a `status` field overlaid."""
    out: list[dict[str, Any]] = []
    for t in static_tasks:
        entry = dict(t)
        entry["status"] = status_map.get(t["id"], "pending")
        out.append(entry)
    return out


def _next_pending(prd: list[dict[str, Any]]) -> dict[str, Any] | None:
    for t in prd:
        if t.get("status", "pending") == "pending":
            return t
    return None


def _load_static_tasks_for_session(session: Session) -> list[dict[str, Any]]:
    """Best-effort load of a session's task list from its source repo.

    Used by summary/resume helpers that need the task set without the live
    `run()` having it in hand. Returns [] on any failure — these are reporting
    paths that must not crash the run."""
    if session.feature_dir is None:
        return []
    try:
        return tasks.load_tasks(session.feature_dir)
    except (TasksError, OSError):
        return []


# --- resume helpers ---------------------------------------------------------

def _latest_session_id(sessions_root: Path) -> str | None:
    """Most recent session by directory name (IDs sort chronologically)."""
    if not sessions_root.is_dir():
        return None
    candidates = [p.name for p in sessions_root.iterdir() if p.is_dir()]
    return max(candidates) if candidates else None


def _last_stop_reason(session: Session) -> str | None:
    """Read the most recent `stop` event's reason from events.jsonl, or None."""
    last: str | None = None
    for rec in iter_events(session.events_path):
        if rec.get("type") == "stop":
            last = ((rec.get("payload") or {}).get("reason")) or None
    return last


def _source_for_session(session_dir: Path) -> Path | None:
    """Recover the source repo path for a session by scanning its events log."""
    for rec in iter_events(session_dir / "events.jsonl"):
        if rec.get("type") == "session_start":
            src = (rec.get("payload") or {}).get("source")
            if src:
                return Path(src)
    return None


def _read_checkpoint(session_dir: Path) -> dict[str, Any]:
    cp_path = session_dir / "checkpoint.json"
    if not cp_path.is_file():
        return {}
    try:
        return json.loads(cp_path.read_text())
    except json.JSONDecodeError:
        return {}


def _read_summary(session_dir: Path) -> dict[str, Any]:
    summ_path = session_dir / "summary.json"
    if not summ_path.is_file():
        return {}
    try:
        return json.loads(summ_path.read_text())
    except json.JSONDecodeError:
        return {}


def _prepare_resume(session: Session, worktree: Path) -> str:
    """Flip trailing failed tasks back to pending and unwind their FAILED commits.

    Always logs a `session_resume` event with the plan summary and structured fields.
    Returns the one-line plan suitable for printing.
    """
    last_stop = _last_stop_reason(session)
    retried: list[str] = []
    pending: list[str] = []
    unwound = False

    static_tasks = _load_static_tasks_for_session(session)
    status_map = _load_status(session.root)
    prd = _overlay_status(static_tasks, status_map)

    if last_stop == "all_done":
        plan = "session reached all_done; nothing to resume"
    else:
        failed = [t for t in prd if t.get("status") == "failed"]
        if failed:
            for t in failed:
                status_map.pop(t["id"], None)  # back to pending (absent == pending)
            _save_status(session.root, status_map)
            unwound = ws.unwind_failed_commit(worktree)
            retried = [t["id"] for t in failed]

        pending = [
            t["id"] for t in prd
            if t.get("status", "pending") == "pending" and t["id"] not in retried
        ]

        bits: list[str] = []
        if retried:
            bits.append(f"retrying {', '.join(retried)} (was: failed)")
            if pending:
                bits.append(f"then: {', '.join(pending)}")
        elif pending:
            bits.append(f"pending: {', '.join(pending)}")
        else:
            bits.append("no failed or pending tasks; nothing to do")
        if last_stop:
            bits.append(f"last stop: {last_stop}")
        if unwound:
            bits.append("unwound FAILED placeholder commit")
        plan = "; ".join(bits)

    session.log(
        "session_resume",
        {
            "session_id": session.session_id,
            "last_stop": last_stop,
            "retried": retried,
            "pending": pending,
            "unwound_commit": unwound,
            "plan": plan,
        },
    )
    return plan


# --- the loop ---------------------------------------------------------------

def _run_task(
    task: dict[str, Any],
    worktree: Path,
    client: LLMClient,
    session: Session,
    trace_id: str,
    *,
    prd: list[dict[str, Any]],
    overview: str | None = None,
) -> str:
    """Run one task. Returns 'done', 'iter_cap', 'evaluator_cap',
    'provider_failure', or 'no_case'.

    A task is 'done' only when the worker calls `submit_case` (its done-signal)
    AND the evaluator accepts. Rejects are fed back as the submit_case
    tool_result; the worker gets another iteration. There is no codified
    validator gate — the evaluator is the only gate. Stopping without a case
    nudges it to submit one — but only up to `MAX_CONSECUTIVE_NO_CASE_NUDGES` in
    a row (→ 'no_case'). Provider-unhealthy responses (error finishes, empty
    200s) are retried inside `_chat_healthy` with the history untouched; they
    never become turns, never burn iterations, and are never mistaken for the
    worker going quiet. Exhausting that budget → 'provider_failure' (resumable).

    `prd` is the full status-overlaid task list (built by `run()` from the
    static task set + the status store); the worker sees it as the feature plan.
    """
    setup_span = _span_id()
    session.log(
        "context_reset",
        {"task_id": task["id"], "trace_id": trace_id, "span_id": setup_span},
    )

    # The worker sees the feature overview, the full plan, and its own task
    # ledger (the evaluator's prior verdicts on this task). The ledger is empty
    # on a task's first run — its payoff is on resume, where prior-run verdicts
    # survive on disk.
    own_ledger = session.read_ledger(task["id"], limit=LEDGER_INJECT_LIMIT)
    user_prompt, mem_manifest = memory.build_user_prompt(
        task,
        worktree,
        session.root,
        prd=prd,
        own_ledger=own_ledger,
        context_files=client.config.context_files,
        overview=overview,
    )
    session.log(
        "memory_load",
        {
            "task_id": task["id"],
            "trace_id": trace_id,
            "span_id": setup_span,
            "trigger": "task_start",
            **mem_manifest,
        },
    )
    _log_prompt_assembled(
        session,
        role="worker",
        task_id=task["id"],
        trace_id=trace_id,
        span_id=setup_span,
        iter_value=0,  # task-start; in-loop worker prompts evolve via tool_results, not re-assembly
        content=user_prompt,
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": user_prompt},
    ]
    tool_schemas = [*tools.schemas(), SUBMIT_CASE_TOOL]
    evaluator_calls = 0
    no_case_streak = 0

    for iter_n in range(client.config.max_iterations_per_task):
        iter_span = _span_id()
        console.print(f"[dim]task {task['id']}  iter {iter_n + 1}[/dim]")
        resp = _chat_healthy(
            client,
            session,
            messages,
            tools=tool_schemas,
            base={
                "task_id": task["id"],
                "trace_id": trace_id,
                "span_id": iter_span,
                "iter": iter_n + 1,
            },
        )
        if resp is None:
            console.print(
                f"[red]task {task['id']} aborting: provider returned no healthy "
                f"response in {PROVIDER_RETRY_MAX_ATTEMPTS} attempts[/red]"
            )
            session.log(
                "task_failed",
                {
                    "task_id": task["id"],
                    "trace_id": trace_id,
                    "span_id": iter_span,
                    "reason": "provider_failure",
                    "call_attempts": PROVIDER_RETRY_MAX_ATTEMPTS,
                },
            )
            return "provider_failure"

        msg = resp.get("message") or {}
        messages.append(assistant_history_message(msg))

        tool_calls = msg.get("tool_calls") or []
        worktree_tcs, case_tcs = _partition_worker_tool_calls(tool_calls)

        # 1. Run worktree tools first (dispatch + tool_result), in order.
        for tc in worktree_tcs:
            tc_id = tc.get("id") or ""
            fn = tc.get("function") or {}
            tool_name = fn.get("name") or ""
            raw_args = fn.get("arguments") or {}
            try:
                args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)
            except json.JSONDecodeError as exc:
                # Malformed tool-arg JSON (token-boundary corruption on long
                # payloads). Feed the error back as a tool_result so the model
                # retries — same recovery pattern as the case/verdict parsers.
                err = f"ERROR: your `{tool_name}` arguments were not valid JSON: {exc}"
                session.log(
                    "tool_call",
                    {
                        "task_id": task["id"], "trace_id": trace_id,
                        "span_id": iter_span, "iter": iter_n + 1,
                        "tool": tool_name, "args": "(unparseable JSON)",
                    },
                )
                messages.append(
                    {"role": "tool", "tool_call_id": tc_id, "content": err}
                )
                continue

            session.log(
                "tool_call",
                {
                    "task_id": task["id"],
                    "trace_id": trace_id,
                    "span_id": iter_span,
                    "iter": iter_n + 1,
                    "tool": tool_name,
                    "args": args,
                },
            )
            console.print(f"[cyan]→ {tool_name}[/cyan] {json.dumps(args)[:200]}")

            outcome = tools.dispatch(tool_name, args, worktree)
            for hr in outcome.hook_runs:
                session.log(
                    "hook_run",
                    {
                        "task_id": task["id"],
                        "trace_id": trace_id,
                        "span_id": iter_span,
                        "iter": iter_n + 1,
                        "tool": tool_name,
                        **hr,
                    },
                )
            event_type = "pre_tool_block" if outcome.blocked else "tool_result"
            session.log(
                event_type,
                {
                    "task_id": task["id"],
                    "trace_id": trace_id,
                    "span_id": iter_span,
                    "iter": iter_n + 1,
                    "tool": tool_name,
                    "result_preview": outcome.result[:500],
                    "result_chars": len(outcome.result),
                },
            )
            if outcome.blocked:
                console.print(f"[red]✗ blocked[/red] {outcome.result}")
            messages.append(
                {"role": "tool", "tool_call_id": tc_id, "content": outcome.result}
            )

        # 2. No submit_case → either still working, or stopped without a case.
        if not case_tcs:
            if worktree_tcs:
                no_case_streak = 0  # made progress this turn; not a quiet stop
                continue  # did work this turn; not claiming done yet
            no_case_streak += 1
            if no_case_streak >= MAX_CONSECUTIVE_NO_CASE_NUDGES:
                console.print(
                    f"[red]task {task['id']} aborting: stopped without "
                    f"submit_case {no_case_streak} times in a row[/red]"
                )
                session.log(
                    "task_failed",
                    {
                        "task_id": task["id"],
                        "trace_id": trace_id,
                        "span_id": iter_span,
                        "reason": "no_case",
                        "nudges": no_case_streak,
                    },
                )
                return "no_case"
            console.print(
                f"[yellow]task {task['id']} stopped without submit_case; "
                f"nudging ({no_case_streak}/{MAX_CONSECUTIVE_NO_CASE_NUDGES})[/yellow]"
            )
            # Logged so events.jsonl can faithfully reconstruct the message
            # history the worker saw — an unlogged injection makes the next
            # model_call look like a spontaneous reply to nothing.
            session.log(
                "nudge",
                {
                    "task_id": task["id"],
                    "trace_id": trace_id,
                    "span_id": iter_span,
                    "iter": iter_n + 1,
                    "kind": "no_case",
                    "streak": no_case_streak,
                    "content": WORKER_NO_CASE_NUDGE,
                },
            )
            messages.append({"role": "user", "content": WORKER_NO_CASE_NUDGE})
            continue

        # 3. submit_case present — the done-signal. Parse it defensively.
        no_case_streak = 0  # a case was submitted; the quiet-stop streak is broken
        case, case_err = parse_case(msg)
        session.log(
            "tool_call",
            {
                "task_id": task["id"],
                "trace_id": trace_id,
                "span_id": iter_span,
                "iter": iter_n + 1,
                "tool": NAME_SUBMIT_CASE,
                "args": case if case is not None else "(unparseable case)",
            },
        )
        console.print(f"[cyan]→ {NAME_SUBMIT_CASE}[/cyan]")

        if case_err is not None:
            session.log(
                "case_parse_error",
                {
                    "task_id": task["id"],
                    "trace_id": trace_id,
                    "span_id": iter_span,
                    "iter": iter_n + 1,
                    "error": case_err,
                    "raw_tool_calls": _raw_tool_calls(msg, MODEL_RAW_ARGS_CHAR_CAP),
                },
            )
            _answer_case_calls(messages, case_tcs, case_err)
            continue

        # Valid case → hand straight to the evaluator (the only gate).
        content = (case.get("summary") or "").strip()
        console.print(f"[dim]task {task['id']} case summary:[/dim] {content[:200]}")
        console.print(f"[green]task {task['id']} case submitted → evaluator[/green]")
        verdict = _evaluator_task(
            task, worktree, client, session, iter_n, trace_id, iter_span,
            case=case, overview=overview,
        )
        if verdict is None:
            console.print(
                f"[red]task {task['id']} aborting: provider returned no healthy "
                f"response during evaluation[/red]"
            )
            session.log(
                "task_failed",
                {
                    "task_id": task["id"],
                    "trace_id": trace_id,
                    "span_id": iter_span,
                    "reason": "provider_failure",
                    "call_attempts": PROVIDER_RETRY_MAX_ATTEMPTS,
                },
            )
            return "provider_failure"
        evaluator_calls += 1
        concern = (verdict.get("concern") or "").strip()
        if verdict["verdict"] == "accept":
            console.print(f"[green]evaluator accepts:[/green] {concern[:200]}")
            session.log(
                "task_done",
                {
                    "task_id": task["id"],
                    "trace_id": trace_id,
                    "span_id": iter_span,
                    "summary": content,
                },
            )
            return "done"

        console.print(f"[yellow]evaluator rejects:[/yellow] {concern[:200]}")
        evaluator_cap = client.config.max_evaluator_calls_per_task
        if evaluator_cap > 0 and evaluator_calls >= evaluator_cap:
            console.print(
                f"[red]task {task['id']} hit evaluator cap[/red] "
                f"[dim][MAX_EVALUATOR_CALLS_PER_TASK={evaluator_cap}][/dim]"
            )
            session.log(
                "task_failed",
                {
                    "task_id": task["id"],
                    "trace_id": trace_id,
                    "span_id": iter_span,
                    "reason": "evaluator_cap",
                    "evaluator_calls": evaluator_calls,
                },
            )
            return "evaluator_cap"
        _answer_case_calls(messages, case_tcs, format_reject_feedback(verdict))

    cap = client.config.max_iterations_per_task
    console.print(
        f"[red]task {task['id']} hit iteration cap[/red] "
        f"[dim][TILTH_MAX_ITERATIONS_PER_TASK={cap}][/dim]"
    )
    session.log(
        "task_failed",
        {"task_id": task["id"], "trace_id": trace_id, "reason": "iter_cap"},
    )
    return "iter_cap"


def _stop_reason(client: LLMClient, session: Session) -> str | None:
    if session.elapsed_minutes() >= client.config.max_wall_clock_minutes:
        return "wall_clock"
    if session.cost_used() >= client.config.max_token_dollar_spend:
        return "token_cap"
    return None


_TERMINAL_FAILURE_STOPS = frozenset(
    {"iter_cap", "evaluator_cap", "no_case", "error"}
)


def _stop_to_status(reason: str) -> str:
    """Map a `stop` reason to the resulting session status.

    `all_done` is terminal-success; iter_cap / evaluator_cap / no_case / error
    are terminal-failure; everything else (wall_clock, token_cap,
    provider_failure, interrupted) leaves the session `running` — those are
    stops the user can resume from. provider_failure is deliberately in the
    resumable bucket: it says nothing about the work, only that the endpoint
    had a bad window — the most transient stop must not get the most terminal
    label (the 2026-06-10 session was marked failed over a ~1-minute blip).
    """
    if reason == "all_done":
        return "all_done"
    if reason in _TERMINAL_FAILURE_STOPS:
        return "failed"
    return "running"


def run(
    worktree: Path,
    session: Session,
    client: LLMClient,
    overview: str,
    static_tasks: list[dict[str, Any]],
) -> None:
    while True:
        stop = _stop_reason(client, session)
        if stop:
            detail = ""
            if stop == "wall_clock":
                detail = f" [TILTH_MAX_WALL_CLOCK_MINUTES={client.config.max_wall_clock_minutes}]"
            elif stop == "token_cap":
                detail = (
                    " [TILTH_MAX_TOKEN_DOLLAR_SPEND="
                    f"{usage.format_cost(client.config.max_token_dollar_spend)}]"
                )
            console.print(f"[yellow]stopping: {stop}[/yellow][dim]{detail}[/dim]")
            session.log("stop", {"reason": stop})
            session.set_status(_stop_to_status(stop))
            _refresh_summary(session)
            return

        prd = _overlay_status(static_tasks, _load_status(session.root))
        task = _next_pending(prd)
        if task is None:
            console.print("[green]all tasks complete[/green]")
            session.log("stop", {"reason": "all_done"})
            session.set_status("all_done")
            _refresh_summary(session)
            return

        trace_id = _trace_id()
        outcome = _run_task(
            task, worktree, client, session, trace_id, prd=prd, overview=overview
        )

        if outcome == "done":
            _set_task_status(session.root, task["id"], "done")
            memory.append_progress(session.root, f"{task['id']}\tdone\t{task['title']}")
            sha = ws.commit_task(worktree, task["id"], task["title"])
            session.log(
                "commit", {"task_id": task["id"], "trace_id": trace_id, "sha": sha}
            )
            console.print(f"[green]✓ {task['id']} committed ({sha})[/green]")
            _refresh_summary(session)
        else:
            _set_task_status(session.root, task["id"], "failed")
            memory.append_progress(
                session.root, f"{task['id']}\tfailed:{outcome}\t{task['title']}"
            )
            ws.commit_task(worktree, task["id"], f"FAILED ({outcome}): {task['title']}")
            detail = ""
            if outcome == "iter_cap":
                cap = client.config.max_iterations_per_task
                detail = f" [TILTH_MAX_ITERATIONS_PER_TASK={cap}]"
            elif outcome == "evaluator_cap":
                cap = client.config.max_evaluator_calls_per_task
                detail = f" [MAX_EVALUATOR_CALLS_PER_TASK={cap}]"
            elif outcome == "provider_failure":
                detail = (
                    " — the model endpoint kept returning errors or empty "
                    "responses (usually a provider or rate-limit window). "
                    "The session stays resumable: `tilth resume` retries the "
                    "task; the model_call events carry the provider's error "
                    "details."
                )
            elif outcome == "no_case":
                detail = (
                    " — the worker stopped repeatedly without presenting a case. "
                    "Inspect the session log for what it was doing."
                )
            console.print(
                f"[red]✗ {task['id']} failed ({outcome}); halting run[/red]"
                f"[dim]{detail}[/dim]"
            )
            session.log("stop", {"reason": outcome})
            session.set_status(_stop_to_status(outcome))
            _refresh_summary(session)
            return


# --- summary ----------------------------------------------------------------

def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _usage_breakdown_lines(session_usage: dict[str, Any]) -> list[str]:
    """The token-detail sub-lines under the `tokens` summary row.

    Reads the live per-actor breakdown (`Session.usage`): a prompt/eval line
    that names cached/reasoning subsets when present, then the worker↔evaluator
    split. Detail-only — the cap line above already shows the headline total.
    """
    worker = session_usage.get("worker") or usage.zero_usage()
    evaluator = session_usage.get("evaluator") or usage.zero_usage()
    combined = usage.zero_usage()
    usage.add_usage(combined, worker)
    usage.add_usage(combined, evaluator)

    detail = f"prompt {combined['prompt']:,} · eval {combined['eval']:,}"
    if combined["cached"]:
        detail += f" · cached {combined['cached']:,}"
    if combined["reasoning"]:
        detail += f" · reasoning {combined['reasoning']:,}"

    w_total = worker["prompt"] + worker["eval"]
    e_total = evaluator["prompt"] + evaluator["eval"]
    split = f"worker {w_total:,} · evaluator {e_total:,}"
    return [detail, split]


def _print_summary(session: Session, client: LLMClient) -> None:
    elapsed = time.time() - session.started_at
    cfg = client.config
    cost_used = session.cost_used()
    cost_pct = (
        (cost_used / cfg.max_token_dollar_spend * 100)
        if cfg.max_token_dollar_spend else 0.0
    )
    wall_pct = (
        (elapsed / 60.0) / cfg.max_wall_clock_minutes * 100
        if cfg.max_wall_clock_minutes
        else 0.0
    )

    counts = {"done": 0, "failed": 0, "pending": 0}
    prd = _overlay_status(
        _load_static_tasks_for_session(session), _load_status(session.root)
    )
    for t in prd:
        status = t.get("status", "pending")
        if status not in counts:
            counts[status] = 0
        counts[status] += 1

    wall_dim = (
        f"({wall_pct:.1f}% of TILTH_MAX_WALL_CLOCK_MINUTES={cfg.max_wall_clock_minutes})"
    )
    cost_dim = (
        f"({cost_pct:.1f}% of TILTH_MAX_TOKEN_DOLLAR_SPEND="
        f"{usage.format_cost(cfg.max_token_dollar_spend)})"
    )
    base_keys = ("done", "failed", "pending")
    total = sum(counts.values())
    task_bits = [f"total={total}"] + [f"{k}={counts.get(k, 0)}" for k in base_keys]
    extras = [f"{k}={v}" for k, v in counts.items() if k not in base_keys]

    console.print()
    console.print("[bold]── run summary ──[/bold]")
    console.print(f"  session   {session.session_id}")
    if session.branch:
        console.print(f"  branch    {session.branch}")
    console.print(f"  duration  {_format_duration(elapsed)} [dim]{wall_dim}[/dim]")
    console.print(f"  tokens    {session.tokens_used:,}")
    for line in _usage_breakdown_lines(session.usage):
        console.print(f"            [dim]{line}[/dim]")
    console.print(f"  cost      {usage.format_cost(cost_used)} [dim]{cost_dim}[/dim]")
    console.print(f"  tasks     {' '.join(task_bits + extras)}")
    _print_working_with_session(session, counts)


def _print_working_with_session(session: Session, counts: dict[str, int]) -> None:
    """Post-run guidance: how to work with the session's branch next.

    The branch is checked out *in the worktree*, so `git switch <branch>` in the
    source repo fails ("already used by worktree"). Name that gotcha, then point
    at the next step: push / open a PR when the run finished cleanly, or resume
    when it stopped short (cap / interrupt / failed task). Runs inside
    `_print_summary`'s call from a `finally`, so every git lookup is failure-tolerant.
    """
    sid = session.session_id
    worktree = session.workspace
    branch = session.branch
    total = sum(counts.values())
    all_done = total > 0 and counts.get("pending", 0) == 0 and counts.get("failed", 0) == 0

    console.print()
    console.print("[bold]── working with this session ──[/bold]")
    if branch:
        console.print(
            f"  [dim]the {branch} branch is checked out in the worktree — "
            f"`git switch {branch}` in your repo won't work; use it there:[/dim]",
            soft_wrap=True,
        )
    if worktree:
        console.print("  inspect / build locally")
        console.print(f"    cd {_abbrev_home(worktree)}", soft_wrap=True)

    if all_done:
        has_remote = (
            session.source is not None
            and session.source.exists()
            and ws.remote_url(session.source) is not None
        )
        if has_remote:
            console.print("  get the work to origin")
            console.print(
                f"    tilth push {sid}   [dim]# push {branch} to origin[/dim]", soft_wrap=True
            )
            console.print(
                f"    tilth pr {sid}     [dim]# push + open a PR against main[/dim]",
                soft_wrap=True,
            )
        else:
            console.print(
                "  [dim]no `origin` remote — add one to publish "
                "(git -C <repo> remote add origin <url>), then `tilth push`[/dim]",
                soft_wrap=True,
            )
    else:
        console.print("  continue the run")
        console.print(
            f"    tilth resume {sid}   [dim]# retry the failed/pending task(s)[/dim]",
            soft_wrap=True,
        )


# --- reset ------------------------------------------------------------------

def _do_reset(session_id: str, assume_yes: bool) -> int:
    session_dir = SESSIONS_DIR / session_id
    if not session_dir.is_dir():
        console.print(f"[red]no session at {session_dir}[/red]")
        return 2

    cp = _read_checkpoint(session_dir)
    worktree = Path(cp["workspace"]) if cp.get("workspace") else None
    branch = cp.get("branch")
    source = _source_for_session(session_dir)

    src_label = (
        str(source) if source
        else "[dim](unknown — only session dir will be removed)[/dim]"
    )
    console.print(f"[bold]reset session[/bold] {session_id}")
    console.print(f"  source    {src_label}")
    console.print(f"  worktree  {worktree if worktree else '[dim](none)[/dim]'}")
    console.print(f"  branch    {branch if branch else '[dim](none)[/dim]'}")

    if not assume_yes:
        try:
            answer = input("Continue? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in {"y", "yes"}:
            console.print("[yellow]aborted[/yellow]")
            return 1

    notes = ws.reset_session_state(source, worktree, branch, session_dir)
    for note in notes:
        if note.startswith("worktree remove FAILED"):
            console.print(f"[red]✗ {note}[/red]")
            console.print(
                "[yellow]hint: investigate the worktree path manually — "
                "permissions, locks, or other filesystem state may be blocking removal.[/yellow]"
            )
            return 3
        console.print(f"  {note}")
    console.print("[green]reset complete[/green]")
    return 0


# --- onboarding + config ----------------------------------------------------

def _env_template() -> str:
    return (DATA_DIR / "env.example").read_text()


def _legacy_sessions_dir() -> Path | None:
    """The pre-#8 clone-local `sessions/`, when running from a source checkout
    and it still holds runs. Returns None for an installed tool (the package
    lives under site-packages, with no sessions beside it) or when it resolves
    to the active sessions dir anyway."""
    old = Path(__file__).resolve().parent.parent / "sessions"
    if old.resolve() == SESSIONS_DIR.resolve():
        return None
    if old.is_dir() and any(p.is_dir() for p in old.iterdir()):
        return old
    return None


def _print_migration_hint() -> None:
    old = _legacy_sessions_dir()
    if old is None:
        return
    count = sum(1 for p in old.iterdir() if p.is_dir())
    console.print(
        f"[yellow]note:[/yellow] {count} session(s) live in a previous location:\n"
        f"  {old}\n"
        "move them into the current one with:\n"
        f"  mkdir -p {SESSIONS_DIR} && mv {old}/* {SESSIONS_DIR}/",
        soft_wrap=True,  # keep the path + command copy-pasteable in narrow terminals
    )


def _load_config() -> TilthConfig | None:
    """Build the provider config, or print an actionable message and return None.

    Keeps the *what's wrong* (missing keys, from client) separate from the *how
    to fix it* (run `tilth init`, or edit the resolved .env) — which only the CLI
    knows, since it owns path resolution."""
    try:
        return TilthConfig.from_env()
    except RuntimeError as exc:
        env_file = paths.resolve_env_file()
        if env_file is None:
            target = paths.env_file_write_target()
            console.print(
                "[red]No provider configuration found.[/red]\n"
                f"Run [bold]tilth init[/bold] to create [bold]{target}[/bold], "
                "then set your API key and model.",
                soft_wrap=True,
            )
        else:
            console.print(
                f"[red]{exc}[/red]\n"
                f"Edit [bold]{env_file}[/bold] and set the missing value(s).",
                soft_wrap=True,
            )
        return None


def do_init_cmd() -> int:
    """Scaffold the per-user home: <home>/.env (from the template) and
    <home>/sessions/. Idempotent — never overwrites an existing .env."""
    target = paths.env_file_write_target()
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    if target.exists():
        console.print(f"[yellow]config already exists[/yellow] at {target}", soft_wrap=True)
        console.print(
            "[dim]edit it to change provider/model, or delete it to re-scaffold[/dim]"
        )
        _print_migration_hint()
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_env_template())
    console.print(f"[green]wrote[/green] {target}", soft_wrap=True)
    console.print(f"[dim]sessions are stored under {SESSIONS_DIR}[/dim]", soft_wrap=True)
    console.print(
        "[bold]next:[/bold] set TILTH_API_KEY and TILTH_WORKER_MODEL, "
        "then run [bold]tilth run <path>[/bold]"
    )
    _print_migration_hint()
    return 0


# --- per-subcommand handlers ------------------------------------------------

def _resolve_session_id(maybe_id: str | None) -> str | None:
    """Translate '' or None into the latest session id; return None if no
    sessions exist at all. Callers print the 'no sessions found' message."""
    if maybe_id:
        return maybe_id
    return _latest_session_id(SESSIONS_DIR)


# --- info + config ----------------------------------------------------------
#
# Read-only discovery surface. `info` answers "what sessions exist and where did
# their worktrees land"; `config` answers "what settings is the harness running
# with". Both tolerate a half-configured environment — they report rather than
# enforce, so they work before `tilth init` and never replay events.jsonl.

def _abbrev_home(p: Path | str | None) -> str:
    """Render a path with $HOME collapsed to ~, for compact, copy-pasteable output."""
    if not p:
        return "(none)"
    s = str(p)
    home = str(Path.home())
    return "~" + s[len(home):] if s == home or s.startswith(home + os.sep) else s


def _print_locations() -> None:
    home = paths.tilth_home()
    env_file = paths.resolve_env_file()
    console.print("[bold]locations[/bold]")
    env_label = _abbrev_home(env_file) if env_file else "[dim](none — run tilth init)[/dim]"
    console.print(f"  tilth home  {_abbrev_home(home)}")
    console.print(f"  .env        {env_label}")
    console.print(f"  sessions    {_abbrev_home(SESSIONS_DIR)}")


def _list_session_ids() -> list[str]:
    if not SESSIONS_DIR.is_dir():
        return []
    return sorted((p.name for p in SESSIONS_DIR.iterdir() if p.is_dir()), reverse=True)


def _session_brief(sid: str) -> dict[str, Any]:
    """Denormalised one-session view from checkpoint.json + summary.json.

    Best-effort — both files may be absent on a half-started run; missing pieces
    come back as None/0 rather than raising."""
    session_dir = SESSIONS_DIR / sid
    cp = _read_checkpoint(session_dir)
    summ = _read_summary(session_dir)
    tasks_map = summ.get("tasks") or {}
    done = sum(1 for t in tasks_map.values() if t.get("status") == "done")
    tokens = cp.get("tokens_used")
    if tokens is None:
        tokens = (summ.get("tokens") or {}).get("total")
    return {
        "status": cp.get("status") or (summ.get("stop") or {}).get("reason") or "—",
        "done": done,
        "total": len(tasks_map),
        "tokens": tokens,
        "cost": (summ.get("tokens") or {}).get("cost"),
        "branch": cp.get("branch"),
        "source": cp.get("source"),
        "workspace": cp.get("workspace"),
        "feature_dir": cp.get("feature_dir"),
        "started_at": summ.get("started_at"),
        "archived": cp.get("archived", False),
    }


def _info_list() -> int:
    _print_locations()
    console.print()
    ids = _list_session_ids()
    if not ids:
        console.print(f"[dim]no sessions under {_abbrev_home(SESSIONS_DIR)}[/dim]")
        _print_migration_hint()
        return 0
    latest = ids[0]
    console.print(f"[bold]{len(ids)} session(s)[/bold] [dim](newest first)[/dim]")
    console.print(
        f"[dim]  {'SESSION':<30} {'STATUS':<9} {'TASKS':>6} {'TOKENS':>10}  SOURCE[/dim]"
    )
    for sid in ids:
        b = _session_brief(sid)
        label = sid + (" (latest)" if sid == latest else "")
        tasks_cell = f"{b['done']}/{b['total']}" if b["total"] else "—"
        tokens_cell = f"{b['tokens']:,}" if isinstance(b["tokens"], int) else "—"
        console.print(
            f"  {label:<30} {b['status']:<9} {tasks_cell:>6} {tokens_cell:>10}  "
            f"{_abbrev_home(b['source'])}"
        )
    console.print()
    console.print("[dim]tilth info <session-id> for full detail[/dim]")
    return 0


def _info_detail(sid: str) -> int:
    session_dir = SESSIONS_DIR / sid
    if not session_dir.is_dir():
        console.print(f"[red]no session at {session_dir}[/red]")
        return 2

    b = _session_brief(sid)
    source = Path(b["source"]) if b["source"] else _source_for_session(session_dir)
    worktree = Path(b["workspace"]) if b["workspace"] else None
    latest = _latest_session_id(SESSIONS_DIR)

    latest_tag = "  [dim](latest)[/dim]" if sid == latest else ""
    archived_tag = "  [dim](archived)[/dim]" if b.get("archived") else ""
    console.print(f"[bold]session[/bold]   {sid}{latest_tag}")
    console.print(f"  status    {b['status']}{archived_tag}")
    console.print(
        f"  source    {_abbrev_home(source) if source else '[dim](unknown)[/dim]'}"
    )
    feat = b["feature_dir"]
    if feat:
        console.print(f"  feature   {Path(feat).name}  [dim]{_abbrev_home(feat)}[/dim]")
    else:
        console.print("  feature   [dim](unknown)[/dim]")

    if worktree and b.get("archived"):
        # cleansed: the worktree + branch were intentionally removed, the record kept.
        console.print(
            f"  worktree  {_abbrev_home(worktree)}  "
            "[dim](archived — worktree removed, record kept)[/dim]",
            soft_wrap=True,
        )
    elif worktree:
        present = "present" if worktree.exists() else "[yellow]missing[/yellow]"
        console.print(f"  worktree  {_abbrev_home(worktree)}  [dim]{present}[/dim]")
        gitdir = ws.worktree_gitdir(worktree) if worktree.exists() else None
        reg = ws.worktree_registered(source, worktree) if source else None
        reg_label = {
            True: "[dim]registered[/dim]",
            False: "[yellow]stale — run git worktree prune[/yellow]",
            None: "[dim]source unavailable[/dim]",
        }[reg]
        if gitdir:
            console.print(f"    └ gitdir  {_abbrev_home(gitdir)}  {reg_label}")
        else:
            console.print(f"    └ gitdir  [dim](unresolved)[/dim]  {reg_label}")
    else:
        console.print("  worktree  [dim](none)[/dim]")

    console.print(f"  branch    {b['branch'] or '[dim](none)[/dim]'}")
    if b["started_at"]:
        console.print(f"  started   {b['started_at']}")
    tokens_cell = f"{b['tokens']:,}" if isinstance(b["tokens"], int) else "—"
    cost = b["cost"]
    cost_cell = (
        f"   cost ~{usage.format_cost(cost)}"
        if isinstance(cost, (int, float)) and cost
        else ""
    )
    console.print(f"  tokens    {tokens_cell}{cost_cell}")
    if b["total"]:
        console.print(f"  tasks     done {b['done']} / {b['total']}")
    return 0


def do_info_cmd(maybe_id: str | None) -> int:
    """No id → overview + session table. With id → that session's full dossier."""
    if maybe_id:
        return _info_detail(maybe_id)
    return _info_list()


def _mask_key(value: str) -> str:
    """Plain masked key — never reveals more than the last 4 chars. Plain (no
    markup) so it can be width-aligned alongside the other config values."""
    if not value:
        return "unset"
    tail = value[-4:] if len(value) >= 4 else ""
    return f"set (…{tail})" if tail else "set"


# Every env var the config view reports, in display order — also the source for
# the name-column width so all rows line up under one another.
_CONFIG_VAR_NAMES = (
    "TILTH_BASE_URL", "TILTH_WORKER_MODEL", "TILTH_API_KEY",
    "TILTH_EVALUATOR_BASE_URL", "TILTH_EVALUATOR_MODEL", "TILTH_EVALUATOR_API_KEY",
    "TILTH_MAX_ITERATIONS_PER_TASK", "MAX_EVALUATOR_CALLS_PER_TASK",
    "TILTH_MAX_WALL_CLOCK_MINUTES", "TILTH_MAX_TOKEN_DOLLAR_SPEND",
    "TILTH_CONTEXT_FILES",
)
_SOURCE_LABEL = {"file": ".env", "env": "environment", "default": "default"}


def _config_source(name: str, file_vals: dict[str, str | None]) -> str:
    """Where env var `name`'s effective value comes from: 'file' (the resolved
    .env), 'env' (the process environment — a shell export or inline on the
    command line), or 'default' (unset/blank, so `from_env`'s built-in).

    The .env is loaded with override=False, so a shell value beats a file value;
    a file entry that differs from what's now in `os.environ` therefore means the
    shell overrode it — which is how the two are told apart after the merge."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return "default"
    fval = (file_vals.get(name) or "").strip()
    return "file" if fval and fval == raw else "env"


def do_config_cmd() -> int:
    """Show resolved provider config + caps by real env var name, each annotated
    with where its value came from (the .env, the environment, or a built-in
    default). Keys masked; safe to paste anywhere."""
    _print_locations()
    console.print()
    try:
        cfg = TilthConfig.from_env()
    except RuntimeError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        for key in ("TILTH_BASE_URL", "TILTH_API_KEY", "TILTH_WORKER_MODEL"):
            raw = os.environ.get(key, "").strip()
            shown = _mask_key(raw) if key.endswith("API_KEY") else (raw or "[dim]unset[/dim]")
            console.print(f"  {key:<20} {shown}")
        env_file = paths.resolve_env_file()
        hint = f"edit {_abbrev_home(env_file)}" if env_file else "run tilth init"
        console.print(f"[dim]incomplete config — {hint} and set the missing value(s)[/dim]")
        return 1

    env_file = paths.resolve_env_file()
    file_vals = dotenv_values(env_file) if env_file else {}
    nw = max(len(n) for n in _CONFIG_VAR_NAMES)

    def src(name: str) -> str:
        return _SOURCE_LABEL[_config_source(name, file_vals)]

    def eval_src(eval_var: str, worker_var: str) -> str:
        s = _config_source(eval_var, file_vals)
        return f"inherits {worker_var}" if s == "default" else _SOURCE_LABEL[s]

    def section(title: str, rows: list[tuple[str, str, str, str]]) -> None:
        console.print(f"[bold]{title}[/bold]")
        vw = max((len(value) for _, value, _, _ in rows), default=0)
        for name, value, source, note in rows:
            note_s = f"   [dim]({note})[/dim]" if note else ""
            console.print(
                f"  {name:<{nw}}  {value:<{vw}}  [dim]{source}[/dim]{note_s}",
                soft_wrap=True,
            )

    section("worker", [
        ("TILTH_BASE_URL", cfg.base_url, src("TILTH_BASE_URL"), ""),
        ("TILTH_WORKER_MODEL", cfg.worker_model, src("TILTH_WORKER_MODEL"), ""),
        ("TILTH_API_KEY", _mask_key(cfg.api_key), src("TILTH_API_KEY"), ""),
    ])
    section("evaluator", [
        ("TILTH_EVALUATOR_BASE_URL", cfg.evaluator_base_url,
         eval_src("TILTH_EVALUATOR_BASE_URL", "TILTH_BASE_URL"), ""),
        ("TILTH_EVALUATOR_MODEL", cfg.evaluator_model,
         eval_src("TILTH_EVALUATOR_MODEL", "TILTH_WORKER_MODEL"), ""),
        ("TILTH_EVALUATOR_API_KEY", _mask_key(cfg.evaluator_api_key),
         eval_src("TILTH_EVALUATOR_API_KEY", "TILTH_API_KEY"), ""),
    ])
    evcalls = cfg.max_evaluator_calls_per_task
    section("limits", [
        ("TILTH_MAX_ITERATIONS_PER_TASK", str(cfg.max_iterations_per_task),
         src("TILTH_MAX_ITERATIONS_PER_TASK"), ""),
        ("MAX_EVALUATOR_CALLS_PER_TASK", str(evcalls),
         src("MAX_EVALUATOR_CALLS_PER_TASK"), "0 = unlimited" if evcalls == 0 else ""),
        ("TILTH_MAX_WALL_CLOCK_MINUTES", str(cfg.max_wall_clock_minutes),
         src("TILTH_MAX_WALL_CLOCK_MINUTES"), ""),
        ("TILTH_MAX_TOKEN_DOLLAR_SPEND", str(cfg.max_token_dollar_spend),
         src("TILTH_MAX_TOKEN_DOLLAR_SPEND"), ""),
    ])
    section("context files", [
        ("TILTH_CONTEXT_FILES", ", ".join(cfg.context_files),
         src("TILTH_CONTEXT_FILES"), ""),
    ])
    return 0


def do_reset_cmd(maybe_id: str | None, assume_yes: bool) -> int:
    sid = _resolve_session_id(maybe_id)
    if not sid:
        console.print(f"[red]no sessions found under {SESSIONS_DIR}[/red]", soft_wrap=True)
        _print_migration_hint()
        return 2
    if not maybe_id:
        console.print(f"[dim]reset: latest session is {sid}[/dim]")
    return _do_reset(sid, assume_yes=assume_yes)


def do_visualize_cmd(maybe_id: str | None, port: int = 8765) -> int:
    """Serve the live session viewer over sessions/.

    Always serves the whole sessions directory (the index lists every run);
    a session id argument is a typo guard + a printed deep link, not a scope.
    Read-only, loopback-only — safe to leave running next to an active
    `tilth run`; the page tails events.jsonl in near-realtime.
    """
    if maybe_id and not (SESSIONS_DIR / maybe_id).is_dir():
        console.print(f"[red]no session at {SESSIONS_DIR / maybe_id}[/red]")
        return 2
    sid = _resolve_session_id(maybe_id)
    url = f"http://127.0.0.1:{port}"
    console.print(f"[bold]visualizer[/bold] {url}/")
    if sid:
        label = "session" if maybe_id else "latest session"
        console.print(f"[bold]{label}[/bold] {url}/session/{sid}")
    console.print("[dim]Ctrl-C to stop[/dim]")
    try:
        visualize.serve(SESSIONS_DIR, port=port)
    except KeyboardInterrupt:
        console.print("\n[dim]visualizer stopped[/dim]")
    except OSError as exc:
        console.print(f"[red]cannot bind 127.0.0.1:{port}: {exc.strerror or exc}[/red]")
        console.print("[dim]another visualizer running? pick a port with --port[/dim]")
        return 2
    return 0


def do_resume_cmd(maybe_id: str | None) -> int:
    sid = _resolve_session_id(maybe_id)
    if not sid:
        console.print(f"[red]no sessions found under {SESSIONS_DIR}[/red]", soft_wrap=True)
        _print_migration_hint()
        return 2
    if not maybe_id:
        console.print(f"[dim]resume: latest session is {sid}[/dim]")
    config = _load_config()
    if config is None:
        return 2
    client = LLMClient(config)
    session = Session.wake(SESSIONS_DIR, sid)
    if session.workspace is None or session.source is None or session.feature_dir is None:
        console.print(
            "[red]session has no worktree/source/feature recorded; cannot resume[/red]"
        )
        return 2
    try:
        overview, static_tasks = tasks.load_feature(session.feature_dir)
    except TasksError as exc:
        console.print(f"[red]cannot load tasks for resume:[/red]\n{exc}")
        return 2
    worktree = session.workspace
    plan = _prepare_resume(session, worktree)
    console.print(f"[bold]resume plan[/bold] {plan}")
    return _run_session(session, worktree, client, config, overview, static_tasks)


# --- publishing (push / pr) -------------------------------------------------
#
# User-invoked, opt-in ways to get a session branch out to a remote. They mirror
# resume's session resolution (latest-by-default → Session.wake) but skip the
# config/client/tasks load — they only need the source repo + branch, which the
# worktree shares with the source's object store. Never auto-run inside the loop;
# the branch is pushed/PR'd, never merged (invariant #5).


def _wake_for_op(maybe_id: str | None, verb: str) -> Session | None:
    """Resolve + wake a session for push/pr. Prints and returns None on any problem
    (no sessions, missing source/branch, source repo gone) — callers map that to exit 2."""
    sid = _resolve_session_id(maybe_id)
    if not sid:
        console.print(f"[red]no sessions found under {SESSIONS_DIR}[/red]", soft_wrap=True)
        _print_migration_hint()
        return None
    if not maybe_id:
        console.print(f"[dim]{verb}: latest session is {sid}[/dim]")
    session = Session.wake(SESSIONS_DIR, sid)
    if session.source is None or session.branch is None:
        console.print(f"[red]session has no source repo / branch recorded; cannot {verb}[/red]")
        return None
    if not session.source.exists():
        console.print(f"[red]source repo not found: {_abbrev_home(session.source)}[/red]")
        return None
    return session


def _no_remote_msg(source: Path, remote: str) -> None:
    console.print(f"[red]no '{remote}' remote on {_abbrev_home(source)}[/red]")
    console.print(
        f"[dim]add one then retry: git -C {source} remote add {remote} <url>[/dim]",
        soft_wrap=True,
    )


def do_push_cmd(maybe_id: str | None, remote: str = "origin") -> int:
    """Push a session's branch to `remote`. Latest session if no id given."""
    session = _wake_for_op(maybe_id, "push")
    if session is None:
        return 2
    source, branch = session.source, session.branch
    if ws.remote_url(source, remote) is None:
        _no_remote_msg(source, remote)
        return 2
    try:
        ws.push_branch(source, branch, remote)
    except ws.WorkspaceError as exc:
        console.print(f"[red]{exc}[/red]")
        return 3
    console.print(f"[green]✓ pushed {branch} to {remote}[/green]")
    console.print(f"[dim]open a PR: tilth pr {session.session_id}[/dim]")
    return 0


def do_pr_cmd(
    maybe_id: str | None,
    base: str | None = None,
    remote: str = "origin",
    web: bool = False,
) -> int:
    """Ensure a session's branch is on `remote`, then open a PR against `base`.

    Hybrid: uses `gh` when present (and not `--web`), else prints the GitHub
    compare URL. `base` defaults to the remote's tracked default branch, then 'main'.
    """
    session = _wake_for_op(maybe_id, "pr")
    if session is None:
        return 2
    source, branch = session.source, session.branch
    url = ws.remote_url(source, remote)
    if url is None:
        _no_remote_msg(source, remote)
        return 2
    base = base or ws.default_remote_branch(source, remote) or "main"

    if not ws.branch_on_remote(source, branch, remote):
        console.print(f"[dim]{branch} not on {remote} yet; pushing…[/dim]")
        try:
            ws.push_branch(source, branch, remote)
        except ws.WorkspaceError as exc:
            console.print(f"[red]{exc}[/red]")
            return 3
        console.print(f"[green]✓ pushed {branch} to {remote}[/green]")

    if not web and shutil.which("gh"):
        rc = _gh_open_pr(source, branch, base, session)
        if rc is not None:
            return rc  # gh handled it (opened or reported existing); else fall through

    web_base = ws.remote_web_url(url)
    if web_base:
        console.print(f"[bold]open a PR:[/bold] {web_base}/compare/{base}...{branch}?expand=1")
    else:
        console.print(f"[green]✓ branch {branch} is on {remote}[/green]")
        console.print("[dim]open a pull/merge request in your host's web UI[/dim]")
    return 0


def _gh_open_pr(source: Path, branch: str, base: str, session: Session) -> int | None:
    """Open (or report an already-open) PR via `gh`. Returns an exit code on success,
    or None to signal the caller to fall back to the compare URL."""
    view = subprocess.run(
        ["gh", "pr", "view", branch, "--json", "url", "-q", ".url"],
        cwd=str(source), capture_output=True, text=True, check=False,
    )
    if view.returncode == 0 and view.stdout.strip():
        console.print(f"[green]PR already open:[/green] {view.stdout.strip()}")
        return 0

    feature = session.feature_dir.name if session.feature_dir else "work"
    title = f"{feature} (tilth session {session.session_id})"
    body = (
        f"Automated changes from Tilth session `{session.session_id}` on branch "
        f"`{branch}`.\n\nGenerated by Tilth — review before merging."
    )
    create = subprocess.run(
        ["gh", "pr", "create", "--base", base, "--head", branch,
         "--title", title, "--body", body],
        cwd=str(source), capture_output=True, text=True, check=False,
    )
    if create.returncode == 0:
        console.print(f"[green]✓ opened PR[/green] {create.stdout.strip()}")
        return 0
    console.print(
        f"[yellow]gh pr create failed ({create.stderr.strip() or 'unknown error'}); "
        "falling back to a compare URL[/yellow]",
        soft_wrap=True,
    )
    return None


# --- cleanse (retire a finished session, keep its audit record) --------------


def do_cleanse_cmd(maybe_id: str | None, assume_yes: bool = False) -> int:
    """Retire a finished, integrated session: remove its worktree + branch but keep
    the session dir as the audit record (still inspectable via `tilth visualize`).

    Refuses unless the branch is merged into another branch or pushed to a remote —
    use `tilth reset` to discard unintegrated work instead. Prompts before acting
    (skip with --yes), since the bare form targets the latest session."""
    session = _wake_for_op(maybe_id, "cleanse")
    if session is None:
        return 2
    if session.archived:
        console.print(
            f"[dim]{session.session_id} is already cleansed; record kept at "
            f"{_abbrev_home(session.root)}.[/dim]",
            soft_wrap=True,
        )
        return 0
    source, branch = session.source, session.branch

    if ws.branch_exists(source, branch) and not ws.branch_integrated(source, branch):
        console.print(
            f"[yellow]{branch} isn't merged into another branch or pushed to a "
            "remote — cleanse would lose its commits.[/yellow]",
            soft_wrap=True,
        )
        console.print(
            "[dim]land it first (merge to main, or tilth push / tilth pr), then "
            "retry — or tilth reset to discard it. If you merged it on the remote, "
            "run git fetch first so this can see it.[/dim]",
            soft_wrap=True,
        )
        return 2

    console.print(f"[bold]cleanse session[/bold] {session.session_id}")
    console.print(f"  status    {session.status}")
    console.print(f"  branch    {branch}  [dim](removed)[/dim]")
    if session.workspace:
        console.print(
            f"  worktree  {_abbrev_home(session.workspace)}  [dim](removed)[/dim]"
        )
    console.print(f"  keeps     {_abbrev_home(session.root)}  [dim](audit record)[/dim]")
    if not assume_yes:
        try:
            answer = input("Continue? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in {"y", "yes"}:
            console.print("[yellow]aborted[/yellow]")
            return 1

    try:
        notes = ws.cleanse_session_state(source, session.workspace, branch)
    except ws.WorkspaceError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print(
            "[yellow]hint: a process may be holding the worktree, or locks/"
            "permissions are blocking removal.[/yellow]",
            soft_wrap=True,
        )
        return 3

    for note in notes:
        console.print(f"  {note}")
    session.log(
        "archived",
        {"branch": branch, "worktree": str(session.workspace) if session.workspace else None},
    )
    session.mark_archived()
    console.print(f"[green]✓ cleansed {session.session_id}[/green]")
    console.print(
        f"[dim]kept the session record at {_abbrev_home(session.root)} — "
        f"inspect anytime with: tilth visualize {session.session_id}[/dim]",
        soft_wrap=True,
    )
    return 0


def do_run_cmd(feature_dir: Path) -> int:
    feature_dir = feature_dir.resolve()

    # Fail fast on a missing/malformed feature *before* creating any session or
    # worktree — no orphan state, and the user gets the templates inline.
    try:
        overview, static_tasks = tasks.load_feature(feature_dir)
    except TasksError as exc:
        console.print(f"[red]cannot start run:[/red]\n{exc}")
        return 2

    # The worktree source is the git repo the feature directory lives in.
    try:
        source = ws.repo_root(feature_dir)
        ws.ensure_git_repo(source)
    except ws.WorkspaceError as exc:
        console.print(f"[red]cannot start run:[/red]\n{exc}")
        return 2

    config = _load_config()
    if config is None:
        return 2
    client = LLMClient(config)

    session = Session.new(SESSIONS_DIR)
    session.source = source
    session.feature_dir = feature_dir
    worktree, branch = ws.ensure_worktree(
        source, session.session_id, session.root / "workspace"
    )
    session.workspace = worktree
    session.branch = branch
    session.set_status("running")
    session.log(
        "session_start",
        {
            "source": str(source),
            "feature_dir": str(feature_dir),
            "feature": feature_dir.name,
            "phase": "run",
            "worktree": str(worktree),
            "branch": branch,
            # Which models ran should be answerable from the log alone, not
            # from whatever .env happens to say later.
            "worker_model": config.worker_model,
            "evaluator_model": config.evaluator_model,
            "base_url": config.base_url,
            # The caps this run enforces — recorded so the viewer can show
            # utilization against them without reading the (mutable) config.
            "limits": config.limits(),
            # The feature's full task count, so the viewer can show "N tasks"
            # before every task has produced an event.
            "task_count": len(static_tasks),
        },
    )
    console.print(
        f"[dim]loaded {len(static_tasks)} task(s) for feature "
        f"'{feature_dir.name}' from {feature_dir}[/dim]"
    )
    return _run_session(session, worktree, client, config, overview, static_tasks)


def _run_session(
    session: Session,
    worktree: Path,
    client: LLMClient,
    config: TilthConfig,
    overview: str,
    static_tasks: list[dict[str, Any]],
) -> int:
    console.print(f"[bold]session[/bold] {session.session_id}")
    console.print(f"[bold]worktree[/bold] {worktree}")
    if session.branch:
        console.print(f"[bold]branch[/bold] {session.branch}")
    console.print(f"[bold]model[/bold] {config.worker_model}")

    try:
        run(worktree, session, client, overview, static_tasks)
    except KeyboardInterrupt:
        console.print("\n[yellow]interrupted[/yellow]")
        session.log("stop", {"reason": "interrupted"})
        session.set_status(_stop_to_status("interrupted"))
        _refresh_summary(session)
        return 130
    except Exception as exc:
        console.print(f"[red]error: {type(exc).__name__}: {exc}[/red]")
        session.log("stop", {"reason": "error", "error": f"{type(exc).__name__}: {exc}"})
        session.set_status(_stop_to_status("error"))
        _refresh_summary(session)
        raise
    finally:
        _print_summary(session, client)
    return 0


# --- CLI --------------------------------------------------------------------

def main() -> int:
    """Legacy entry point. Delegates to tilth.cli.main so old callers stay green."""
    from tilth.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
