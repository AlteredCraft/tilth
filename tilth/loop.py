"""Ralph loop entry point.

For each pending task in sessions/<id>/prd.json:
  1. Reset context — build a fresh message list from disk
     (workspace context files + session progress tail + task).
  2. Tool-loop with the worker model.
  3. When the worker calls `submit_case` (its done-signal, Phase 3), run
     validators (ruff + pytest).
     - Pass: the evaluator reviews the case + diff + ledger in a fresh context.
       - Accept: collect a proposed learning (session-local, user-reviewed),
                 commit, mark done, next task.
       - Reject: feed the structured verdict back as the submit_case
                 tool_result; another iteration.
     - Fail: feed the validator report back as the submit_case tool_result;
             another iteration.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from rich.console import Console

from tilth import memory, summary, tools, validators, visualize
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
    parse_json_lenient,
)
from tilth.seed import FileSeedSink, TTYFrontend, run_interview
from tilth.seed.interview import InterviewAbort
from tilth.session import Session, dump_prompt, iter_events, session_label
from tilth.verdict import (
    SUBMIT_VERDICT_TOOL,
    VERDICT_SCHEMA_VERSION,
    format_ledger_section,
    format_reject_feedback,
    parse_verdict,
)

console = Console()

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
SESSIONS_DIR = REPO_ROOT / "sessions"


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


def _propose_learning_prompt() -> str:
    return (PROMPTS_DIR / "propose_learning.md").read_text()


# --- evaluator -------------------------------------------

JUDGE_DIFF_MAX_CHARS = 12_000
PROMPT_ASSEMBLED_CHAR_CAP = 16_000
MODEL_RAW_ARGS_CHAR_CAP = 16_000  # generous — faithful capture is the priority
LEDGER_INJECT_LIMIT = 5  # OQ #1: last-N ledger entries injected into evaluator prompt
# Phase 4 visibility: per-field injection caps for the evaluator prompt,
# separate from the prompt_assembled *log* cap above. A noisy validator dump or
# a long seed test must not blow the evaluator's context.
VALIDATOR_OUTPUT_INJECT_CAP = 4_000
SEED_TEST_INJECT_CAP = 6_000

WORKER_NO_CASE_NUDGE = (
    "You stopped without calling `submit_case`. This harness no longer treats "
    "'no more tool calls' as done — when the task is complete and verified, "
    "present your case by calling `submit_case`. If you're not done, keep "
    "working with the other tools."
)

# Robustness backstops for the worker loop. A provider that returns empty
# responses (no content, no tool calls — observed: OpenRouter 200s with zero
# usage) must not be mistaken for a worker that went quiet, or the loop nudges a
# dead endpoint until the iteration cap. And a worker that genuinely keeps going
# quiet shouldn't burn the whole iteration budget either.
EMPTY_RESPONSE_RETRY_LIMIT = 3  # consecutive empty responses before aborting the task
EMPTY_RESPONSE_BACKOFF_SECONDS = 2  # base backoff between empty-response retries (scaled by streak)
MAX_CONSECUTIVE_NO_CASE_NUDGES = 3  # consecutive no-case turns before aborting the task


def _is_empty_response(msg: dict[str, Any]) -> bool:
    """A model turn that produced *nothing* — no tool calls, no content, no
    reasoning. The signature of a provider hiccup, not a deliberate stop.

    Distinct from a worker that goes quiet *with* a prose summary (content
    present, no tool call) — that still routes to the no-case nudge. Echoing an
    empty turn back into the history appends a role-less `{}` message that
    poisons every subsequent request, so the caller skips the append and retries.
    """
    if msg.get("tool_calls"):
        return False
    if (msg.get("content") or "").strip():
        return False
    if msg.get("reasoning_details"):
        return False
    if isinstance(msg.get("reasoning"), str) and msg["reasoning"].strip():
        return False
    return True


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


def _log_model_call(
    session: Session,
    *,
    phase: str,
    msg: dict[str, Any],
    resp: dict[str, Any],
    base: dict[str, Any],
    prompt_dump: str | None = None,
) -> None:
    """Emit a `model_call` event for a non-worker model call.

    Mirrors the worker loop's inline model_call payload (tokens, finish_reason,
    reasoning round-trip) so every model-calling site in the system is
    observable the same way. `phase` tags which actor made the call
    (`evaluator`, `self_improve`); the worker omits phase by convention.
    Call AFTER `session.add_tokens` so `tokens_used_total` is post-increment,
    matching the worker.
    """
    usage = resp.get("usage") or {}
    payload: dict[str, Any] = {
        **base,
        "phase": phase,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "eval_tokens": int(usage.get("completion_tokens") or 0),
        "tokens_used_total": session.tokens_used,
    }
    if prompt_dump:
        payload["prompt_dump"] = prompt_dump
    if finish_reason := resp.get("finish_reason"):
        payload["finish_reason"] = finish_reason
    if reasoning_details := msg.get("reasoning_details"):
        payload["reasoning_details"] = reasoning_details
    else:
        reasoning = msg.get("reasoning")
        if isinstance(reasoning, str) and reasoning.strip():
            payload["reasoning"] = reasoning
    session.log("model_call", payload)


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


def _format_validator_section(results: list[validators.ValidatorResult]) -> str:
    """Render the actual validator output for the evaluator (Phase 4).

    Replaces the old static "all passed" line. The evaluator is only called
    *after* the validators pass, so this is passing output — its value is
    letting the evaluator see which tests ran and what they cover when judging
    `weak_test` / `acceptance_gap`, not surfacing failures (those go to the
    worker via the submit_case tool_result). Capped per the injection budget.
    """
    blocks: list[str] = []
    for r in results:
        out = (r.output or "").strip() or "(no output)"
        if len(out) > VALIDATOR_OUTPUT_INJECT_CAP:
            out = (
                out[:VALIDATOR_OUTPUT_INJECT_CAP]
                + f"\n... [truncated, total {len(r.output)} chars]"
            )
        status = "PASS" if r.passed else "FAIL"
        blocks.append(f"### {r.name} — {status}\n```\n{out}\n```")
    return "\n\n".join(blocks)


def _format_seed_test_section(worktree: Path, task_id: str) -> str:
    """Inline this task's seed acceptance test(s) for the evaluator (#16).

    Reads the worktree-current version — exactly what pytest validated. If the
    worker tampered with a seed test, the diff already surfaces that as
    cross-task interference; here the evaluator can read what the passing test
    actually asserts, to evaluator `weak_test`. Absent/unreadable → "".
    """
    tests_dir = worktree / "tests"
    if not tests_dir.is_dir():
        return ""
    matches = sorted(tests_dir.glob(validators.task_test_glob(task_id)))
    if not matches:
        return ""
    blocks = ["## Seed acceptance test (the test the validator ran)", ""]
    budget = SEED_TEST_INJECT_CAP
    for p in matches:
        try:
            body = p.read_text()
        except OSError:
            continue
        if len(body) > budget:
            body = body[:budget] + "\n... [truncated]"
        budget -= len(body)
        blocks.append(f"`{p.relative_to(worktree)}`:\n```python\n{body}\n```")
        if budget <= 0:
            break
    return "\n".join(blocks)


def _evaluator_task(
    task: dict[str, Any],
    worktree: Path,
    client: LLMClient,
    session: Session,
    iter_n: int,
    trace_id: str,
    span_id: str,
    case: dict[str, Any] | None = None,
    results: list[validators.ValidatorResult] | None = None,
) -> dict[str, Any]:
    """Call the evaluator in a fresh context. Returns a structured verdict.

    Shape: `{verdict, rejection_category, concern, evidence, next_step}`
    plus `parse_failed: True` on the fallback path where the model never
    produced a parseable `submit_verdict` call. The fallback verdict is
    `reject` so the loop keeps moving (and the operator sees the parse
    failure via `evaluator_parse_error` events).
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
    seed_test_section = _format_seed_test_section(worktree, task["id"])
    if seed_test_section:
        parts += ["", seed_test_section]
    parts += [
        "",
        "## Validator output (all objective validators PASSED — this is the proof)",
        "",
        _format_validator_section(results or [])
        or "All objective validators (ruff, pytest) PASSED.",
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
) -> dict[str, Any]:
    """Two-attempt tool-call loop with parse-error feedback between attempts.

    Attempt 1: send the assembled prompt. Parse the assistant message via
    `parse_verdict`. If clean → return.

    Attempt 2: only reached on parse failure. Echo the assistant message,
    respond to each emitted tool_call with the parse error as `tool_result`
    content, and retry the call. If still bad → synthesise a fallback
    reject verdict so the loop survives; `parse_failed: True` makes the
    failure visible to summary/visualizer consumers.
    """
    last_err = ""
    for attempt in (1, 2):
        dump_path = dump_prompt(
            session.root,
            getattr(client.config, "prompt_dump", False),
            f"{task_id}-iter{iter_n + 1:02d}-judge{attempt}",
            messages,
            [SUBMIT_VERDICT_TOOL],
        )
        resp = client.chat(
            messages,
            model=client.config.evaluator_model,
            tools=[SUBMIT_VERDICT_TOOL],
        )
        usage = resp.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        eval_tokens = int(usage.get("completion_tokens") or 0)
        session.add_tokens(prompt_tokens + eval_tokens)

        msg = resp.get("message") or {}
        _log_model_call(
            session,
            phase="evaluator",
            msg=msg,
            resp=resp,
            base={
                "task_id": task_id,
                "trace_id": trace_id,
                "span_id": span_id,
                "iter": iter_n + 1,
                "attempt": attempt,
            },
            prompt_dump=dump_path,
        )

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


# --- self-improvement: propose a learning -----------------------------------

def _self_improve_session_context(session: Session) -> str:
    """Phase 5: cross-task signal for the self-improver.

    Two pieces, both grounding a proposal in a *pattern* rather than one task's
    symptom: the session's rejection-category histogram, and every task's
    evaluator ledger (its verdict arc across iterations).

    The histogram is rebuilt fresh from events here (not read from summary.json)
    because `_self_improve` runs *before* the task-boundary summary refresh —
    reading the file would lag by the just-completed task. Reuses the canonical
    `summary.build_from_events` rollup so the counts match summary.json once it
    catches up. Returns "" when there's nothing yet (first task, no rejections).
    """
    parts: list[str] = []

    rollup = summary.build_from_events(session.events_path)
    categories = (rollup.get("evaluator") or {}).get("rejection_categories") or {}
    if categories:
        hist = ", ".join(f"{cat}: {n}" for cat, n in sorted(categories.items()))
        parts += ["## Rejection patterns across this session (by category)", "", hist, ""]

    ledger_blocks: list[str] = []
    for tid in session.ledger_task_ids():
        section = format_ledger_section(
            session.read_ledger(tid),
            header=f"### {tid} — the evaluator's iterations",
        )
        if section:
            ledger_blocks.append(section)
    if ledger_blocks:
        parts += [
            "## Per-task evaluator ledgers (this session)",
            "",
            *ledger_blocks,
        ]

    return "\n".join(parts).rstrip()


def _self_improve(
    task: dict[str, Any],
    worktree: Path,
    session: Session,
    client: LLMClient,
    trace_id: str,
) -> None:
    """Ask the worker model whether this task surfaced a durable learning worth capturing.

    A "yes" appends to sessions/<id>/proposed-learnings.md — a session output
    for the user's later review. The worker never reads that file back; the
    learning does not influence subsequent tasks in this run. Cross-run
    persistence happens when the user (or a future end-of-session hook)
    promotes proposals into AGENTS.md (or whichever project-context file they
    keep, per TILTH_CONTEXT_FILES) by hand.
    """
    diff = ws.task_diff(worktree)
    if len(diff) > JUDGE_DIFF_MAX_CHARS:
        diff = diff[:JUDGE_DIFF_MAX_CHARS] + "\n... [truncated]"

    ctx_text, ctx_names = memory.load_context_files(worktree, client.config.context_files)
    loaded_label = ", ".join(ctx_names) if ctx_names else (
        client.config.context_files[0] if client.config.context_files else "AGENTS.md"
    )
    agents_md = ctx_text.strip() or f"(no {loaded_label} in this project)"
    session_context = _self_improve_session_context(session)  # Phase 5

    parts = [
        f"Task just completed: {task['id']} — {task['title']}",
        "",
        f"Description:\n{task.get('description', '').strip()}",
        "",
        f"## Current project context ({loaded_label})",
        "",
        agents_md,
        "",
        "## Diff produced",
        "",
        f"```diff\n{diff or '(empty)'}\n```",
    ]
    if session_context:
        parts += ["", session_context]
    parts += ["", "Respond with strict JSON only."]
    user = "\n".join(parts)

    span_id = _span_id()
    # Observability parity (Phase 5): self_improve now emits prompt_assembled
    # like the worker and evaluator — so a post-run reviewer sees the cross-task
    # signal the self-improver actually had.
    _log_prompt_assembled(
        session,
        role="self_improve",
        task_id=task["id"],
        trace_id=trace_id,
        span_id=span_id,
        iter_value=0,
        content=user,
    )
    messages = [
        {"role": "system", "content": _propose_learning_prompt()},
        {"role": "user", "content": user},
    ]
    dump_path = dump_prompt(
        session.root,
        getattr(client.config, "prompt_dump", False),
        f"{task['id']}-self_improve",
        messages,
    )
    resp = client.chat(messages)
    usage = resp.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    eval_tokens = int(usage.get("completion_tokens") or 0)
    session.add_tokens(prompt_tokens + eval_tokens)

    base = {"task_id": task["id"], "trace_id": trace_id, "span_id": span_id}

    msg = resp.get("message") or {}
    _log_model_call(
        session, phase="self_improve", msg=msg, resp=resp, base=base, prompt_dump=dump_path
    )

    content = (msg.get("content") or "").strip()
    parsed = parse_json_lenient(content)
    if not parsed:
        session.log(
            "proposed_learnings",
            {**base, "emitted": False, "reason": "unparseable", "raw": content[:500]},
        )
        return

    if str(parsed.get("propose", "")).lower() != "yes":
        session.log(
            "proposed_learnings", {**base, "emitted": False, "reason": "no_proposal"}
        )
        return

    learning = str(parsed.get("learning", "")).strip()
    if not learning:
        session.log(
            "proposed_learnings", {**base, "emitted": False, "reason": "empty_learning"}
        )
        return

    memory.append_proposed_learning(session.root, task["id"], task["title"], learning)
    session.log(
        "proposed_learnings",
        {**base, "emitted": True, "entry": learning},
    )
    console.print(f"[blue]→ proposed learning[/blue] {learning}")


# --- prd handling -----------------------------------------------------------

def _load_prd(session_dir: Path) -> list[dict[str, Any]]:
    prd_path = session_dir / "prd.json"
    if not prd_path.is_file():
        raise FileNotFoundError(
            f"No prd.json at {prd_path}. "
            "Seed a prepared session first (Phase 2: `tilth prep-feature`)."
        )
    return json.loads(prd_path.read_text())


def _save_prd(session_dir: Path, prd: list[dict[str, Any]]) -> None:
    (session_dir / "prd.json").write_text(json.dumps(prd, indent=2) + "\n")


def _next_pending(prd: list[dict[str, Any]]) -> dict[str, Any] | None:
    for t in prd:
        if t.get("status", "pending") == "pending":
            return t
    return None


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


def _find_resumable_session(sessions_root: Path, source: Path) -> tuple[str, str | None] | None:
    """Most recent session for `source` whose last stop ≠ all_done.

    Returns (session_id, last_stop_reason) or None. Sessions that haven't logged a
    stop event yet (e.g. crashed before stop) are still treated as resumable.

    Prepared sessions (created by `prep-feature` but never run) are skipped —
    they're picked up by the normal `tilth <workspace>` flow, not by --resume.
    """
    if not sessions_root.is_dir():
        return None
    target = str(source)
    for d in sorted(sessions_root.iterdir(), key=lambda p: p.name, reverse=True):
        if not d.is_dir():
            continue
        if _checkpoint_status(d) == "prepared":
            continue
        sess_source: str | None = None
        last_stop: str | None = None
        for rec in iter_events(d / "events.jsonl"):
            t = rec.get("type")
            if t == "session_start":
                sess_source = (rec.get("payload") or {}).get("source")
            elif t == "stop":
                last_stop = (rec.get("payload") or {}).get("reason")
        if sess_source != target:
            continue
        if last_stop == "all_done":
            continue
        return (d.name, last_stop)
    return None


def _checkpoint_status(session_dir: Path) -> str | None:
    cp = _read_checkpoint(session_dir)
    s = cp.get("status")
    return s if isinstance(s, str) else None


def _find_prepared_sessions(sessions_root: Path, source: Path) -> list[str]:
    """All session IDs for `source` whose checkpoint status is `prepared`.

    Used by `tilth <workspace>` to pick up a session prep-feature seeded.
    """
    if not sessions_root.is_dir():
        return []
    target = str(source)
    out: list[str] = []
    for d in sorted(sessions_root.iterdir()):
        if not d.is_dir():
            continue
        cp = _read_checkpoint(d)
        if cp.get("status") == "prepared" and cp.get("source") == target:
            out.append(d.name)
    return out


def _find_blocking_sessions(sessions_root: Path, source: Path) -> list[tuple[str, str]]:
    """Sessions for `source` in non-terminal states (`prepared|running|failed`).

    Used by prep-feature to refuse re-prep when an earlier session is still in
    flight. Returns a list of (session_id, status) sorted by id.
    """
    if not sessions_root.is_dir():
        return []
    target = str(source)
    blocking_statuses = {"prepared", "running", "failed"}
    out: list[tuple[str, str]] = []
    for d in sorted(sessions_root.iterdir()):
        if not d.is_dir():
            continue
        cp = _read_checkpoint(d)
        if cp.get("source") != target:
            continue
        status = cp.get("status")
        if isinstance(status, str) and status in blocking_statuses:
            out.append((d.name, status))
    return out


# --- interactive pickers ----------------------------------------------------
#
# The two failure cases that benefit most from interactive recovery:
#   - prep-feature with a blocking session for this workspace
#   - run with no prepared session for this workspace
#
# Both helpers take an injectable `prompt_func` so tests can drive them
# without touching real stdin. Default prompt_func is the built-in `input`.
# Each returns a short action string; the call site dispatches.

# Actions returned by _prompt_blocking_action.
_PREP_ACTION_RUN = "run_existing"          # one prepared → launch worker
_PREP_ACTION_RESUME = "resume_existing"    # one running/failed → resume it
_PREP_ACTION_RESET_AND_PREP = "reset_and_prep"
_PREP_ACTION_PREP_FRESH = "prep_fresh"     # start a new session, leave blockers alone
_PREP_ACTION_CANCEL = "cancel"

# Actions returned by _prompt_no_prep_action.
_RUN_ACTION_PREP_NOW = "prep_now"
_RUN_ACTION_RESUME = "resume"
_RUN_ACTION_RESET_AND_PREP = "reset_and_prep"
_RUN_ACTION_CANCEL = "cancel"


def _format_session_row(sid: str, status: str, sessions_root: Path) -> str:
    """`sessions/<sid>/  <status>  <label>` for a picker row."""
    label = session_label(sessions_root / sid)
    if label:
        return f"sessions/{sid}/  {status:<9}  {label}"
    return f"sessions/{sid}/  {status}"


def _ask_choice(prompt_func, valid: set[str]) -> str:
    """Loop on prompt_func until the answer is in `valid`. EOF/Ctrl-D → cancel."""
    while True:
        try:
            raw = prompt_func("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""
        if raw in valid:
            return raw
        console.print(f"[yellow]please choose one of: {sorted(valid)}[/yellow]")


def _prompt_blocking_action(
    blocking: list[tuple[str, str]],
    sessions_root: Path,
    prompt_func=input,
) -> str:
    """Picker shown when prep-feature finds blocking sessions for this workspace.

    Single-blocker shape: offers run-or-resume (depending on status) vs.
    reset-and-prep vs. cancel. Multi-blocker shape: offers reset-all vs. cancel.
    """
    fresh_note = (
        "  3) start a new session (don't touch any existing sessions)"
    )
    if len(blocking) == 1:
        sid, status = blocking[0]
        console.print(
            "[yellow]This workspace already has a session in flight:[/yellow]"
        )
        console.print(f"  {_format_session_row(sid, status, sessions_root)}")
        console.print()
        console.print("What would you like to do?")
        if status == "prepared":
            console.print("  1) run it now")
            console.print("  2) discard it and start a new prep")
            console.print(fresh_note)
            console.print("  0) cancel")
            choice = _ask_choice(prompt_func, {"0", "1", "2", "3"})
            return {
                "1": _PREP_ACTION_RUN,
                "2": _PREP_ACTION_RESET_AND_PREP,
                "3": _PREP_ACTION_PREP_FRESH,
                "0": _PREP_ACTION_CANCEL,
                "": _PREP_ACTION_CANCEL,
            }[choice]
        # running or failed
        console.print("  1) resume it")
        console.print("  2) discard it and start a new prep")
        console.print(fresh_note)
        console.print("  0) cancel")
        choice = _ask_choice(prompt_func, {"0", "1", "2", "3"})
        return {
            "1": _PREP_ACTION_RESUME,
            "2": _PREP_ACTION_RESET_AND_PREP,
            "3": _PREP_ACTION_PREP_FRESH,
            "0": _PREP_ACTION_CANCEL,
            "": _PREP_ACTION_CANCEL,
        }[choice]

    console.print(
        f"[yellow]This workspace has {len(blocking)} sessions in flight:[/yellow]"
    )
    for sid, status in blocking:
        console.print(f"  {_format_session_row(sid, status, sessions_root)}")
    console.print()
    console.print(
        "Heads up: `tilth run` requires exactly one prepared session per workspace, "
        "so starting a new one alongside means you'll need to `tilth reset` the "
        "extras before the next run."
    )
    console.print("What would you like to do?")
    console.print(f"  1) discard all {len(blocking)} and start a new prep")
    console.print("  2) start a new session (don't touch any existing sessions)")
    console.print("  0) cancel")
    choice = _ask_choice(prompt_func, {"0", "1", "2"})
    return {
        "1": _PREP_ACTION_RESET_AND_PREP,
        "2": _PREP_ACTION_PREP_FRESH,
        "0": _PREP_ACTION_CANCEL,
        "": _PREP_ACTION_CANCEL,
    }[choice]


def _prompt_no_prep_action(
    prior: tuple[str, str | None] | None,
    sessions_root: Path,
    prompt_func=input,
) -> str:
    """Picker shown when `tilth run` finds no prepared session for this workspace.

    Distinguishes two cases:
      - no prior session at all → just offer prep-now or cancel.
      - resumable prior exists → offer resume / reset-and-prep / cancel.
    """
    if prior is None:
        console.print(
            "[yellow]No prepared session for this workspace, "
            "and no prior session to resume.[/yellow]"
        )
        console.print()
        console.print("What would you like to do?")
        console.print("  1) prep one now (anchored interview)")
        console.print("  0) cancel")
        choice = _ask_choice(prompt_func, {"0", "1"})
        return {
            "1": _RUN_ACTION_PREP_NOW,
            "0": _RUN_ACTION_CANCEL,
            "": _RUN_ACTION_CANCEL,
        }[choice]

    sid, last_stop = prior
    status = _checkpoint_status(sessions_root / sid) or "(unknown)"
    console.print(
        "[yellow]No prepared session for this workspace, "
        "but a prior session is resumable:[/yellow]"
    )
    row = _format_session_row(sid, status, sessions_root)
    if last_stop:
        row = f"{row}  [last stop: {last_stop}]"
    console.print(f"  {row}")
    console.print()
    console.print("What would you like to do?")
    console.print("  1) resume that session")
    console.print("  2) discard it and prep a new one")
    console.print("  0) cancel")
    choice = _ask_choice(prompt_func, {"0", "1", "2"})
    return {
        "1": _RUN_ACTION_RESUME,
        "2": _RUN_ACTION_RESET_AND_PREP,
        "0": _RUN_ACTION_CANCEL,
        "": _RUN_ACTION_CANCEL,
    }[choice]


def _prepare_resume(session: Session, worktree: Path) -> str:
    """Flip trailing failed tasks back to pending and unwind their FAILED commits.

    Always logs a `session_resume` event with the plan summary and structured fields.
    Returns the one-line plan suitable for printing.
    """
    last_stop = _last_stop_reason(session)
    retried: list[str] = []
    pending: list[str] = []
    unwound = False

    prd = _load_prd(session.root)

    if last_stop == "all_done":
        plan = "session reached all_done; nothing to resume"
    else:
        failed = [t for t in prd if t.get("status") == "failed"]
        if failed:
            for t in failed:
                t["status"] = "pending"
            _save_prd(session.root, prd)
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
) -> str:
    """Run one task. Returns 'done', 'iter_cap', 'evaluator_cap', 'empty_responses',
    or 'no_case'.

    A task is 'done' only when the worker calls `submit_case` (its done-signal,
    Phase 3), the validators (ruff + pytest) pass, AND the evaluator accepts.
    Validator failures and rejects are fed back as the submit_case tool_result;
    the worker gets another iteration. Stopping without a case nudges it to
    submit one — but only up to `MAX_CONSECUTIVE_NO_CASE_NUDGES` in a row
    (→ 'no_case'). An empty model response (provider hiccup) is retried with
    backoff up to `EMPTY_RESPONSE_RETRY_LIMIT` (→ 'empty_responses'); it is not
    mistaken for the worker going quiet.
    """
    setup_span = _span_id()
    session.log(
        "context_reset",
        {"task_id": task["id"], "trace_id": trace_id, "span_id": setup_span},
    )

    # Phase 4 visibility: the worker now sees the full feature plan and its own
    # task ledger (the evaluator's prior verdicts on this task). The ledger is
    # empty on a task's first run — its payoff is on resume, where prior-run
    # verdicts survive on disk.
    prd = _load_prd(session.root)
    own_ledger = session.read_ledger(task["id"], limit=LEDGER_INJECT_LIMIT)
    user_prompt, mem_manifest = memory.build_user_prompt(
        task,
        worktree,
        session.root,
        prd=prd,
        own_ledger=own_ledger,
        context_files=client.config.context_files,
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
    empty_streak = 0
    no_case_streak = 0

    for iter_n in range(client.config.max_iterations_per_task):
        iter_span = _span_id()
        console.print(f"[dim]task {task['id']}  iter {iter_n + 1}[/dim]")
        dump_path = dump_prompt(
            session.root,
            getattr(client.config, "prompt_dump", False),
            f"{task['id']}-iter{iter_n + 1:02d}",
            messages,
            tool_schemas,
        )
        resp = client.chat(messages, tools=tool_schemas)

        usage = resp.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        eval_tokens = int(usage.get("completion_tokens") or 0)
        session.add_tokens(prompt_tokens + eval_tokens)

        msg = resp.get("message") or {}
        model_call_payload: dict[str, Any] = {
            "task_id": task["id"],
            "trace_id": trace_id,
            "span_id": iter_span,
            "iter": iter_n + 1,
            "prompt_tokens": prompt_tokens,
            "eval_tokens": eval_tokens,
            "tokens_used_total": session.tokens_used,
        }
        if dump_path:
            model_call_payload["prompt_dump"] = dump_path
        if finish_reason := resp.get("finish_reason"):
            model_call_payload["finish_reason"] = finish_reason
        if reasoning_details := msg.get("reasoning_details"):
            model_call_payload["reasoning_details"] = reasoning_details
        else:
            reasoning = msg.get("reasoning")
            if isinstance(reasoning, str) and reasoning.strip():
                model_call_payload["reasoning"] = reasoning
        session.log("model_call", model_call_payload)

        # Empty model response (no content, no tool calls, no reasoning) — a
        # provider hiccup, not the worker going quiet. Don't append it (a
        # role-less `{}` message poisons every later request) and don't route it
        # to the no-case nudge (which spins forever on a dead endpoint). Retry
        # with backoff; abort the task with a clear reason if it persists.
        if _is_empty_response(msg):
            empty_streak += 1
            session.log(
                "empty_model_response",
                {
                    "task_id": task["id"],
                    "trace_id": trace_id,
                    "span_id": iter_span,
                    "iter": iter_n + 1,
                    "streak": empty_streak,
                    "finish_reason": resp.get("finish_reason"),
                    "prompt_tokens": prompt_tokens,
                    "eval_tokens": eval_tokens,
                },
            )
            if empty_streak >= EMPTY_RESPONSE_RETRY_LIMIT:
                console.print(
                    f"[red]task {task['id']} aborting: {empty_streak} consecutive "
                    f"empty model responses (provider issue?)[/red]"
                )
                session.log(
                    "task_failed",
                    {
                        "task_id": task["id"],
                        "trace_id": trace_id,
                        "span_id": iter_span,
                        "reason": "empty_responses",
                        "streak": empty_streak,
                    },
                )
                return "empty_responses"
            console.print(
                f"[yellow]task {task['id']} empty model response "
                f"({empty_streak}/{EMPTY_RESPONSE_RETRY_LIMIT}); retrying[/yellow]"
            )
            time.sleep(EMPTY_RESPONSE_BACKOFF_SECONDS * empty_streak)
            continue
        empty_streak = 0

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
                # retries — same recovery pattern as the seed interview.
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

        # Valid case → verify with validators, then the evaluator.
        content = (case.get("summary") or "").strip()
        console.print(f"[dim]task {task['id']} case summary:[/dim] {content[:200]}")

        done_ids = [t["id"] for t in prd if t.get("status") == "done"]
        results = validators.run_all(worktree, [*done_ids, task["id"]])
        passed = validators.all_passed(results)
        session.log(
            "validator_run",
            {
                "task_id": task["id"],
                "trace_id": trace_id,
                "span_id": iter_span,
                "iter": iter_n + 1,
                "passed": passed,
                "results": [{"name": r.name, "passed": r.passed} for r in results],
            },
        )

        if not passed:
            report = validators.combined_report(results)
            console.print(
                f"[yellow]task {task['id']} validators failed; feeding back[/yellow]"
            )
            feedback = (
                "The validators failed — the workspace does not pass yet, so the "
                "case can't be reviewed.\nRead the report below, fix the issues, "
                "and call `submit_case` again once validators will pass.\n\n"
                f"{report}"
            )
            _answer_case_calls(messages, case_tcs, feedback)
            continue

        console.print(f"[green]task {task['id']} validators passed → evaluator[/green]")
        verdict = _evaluator_task(
            task, worktree, client, session, iter_n, trace_id, iter_span,
            case=case, results=results,
        )
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
    if session.tokens_used >= client.config.max_tokens:
        return "token_cap"
    return None


_TERMINAL_FAILURE_STOPS = frozenset(
    {"iter_cap", "evaluator_cap", "empty_responses", "no_case", "error"}
)


def _stop_to_status(reason: str) -> str:
    """Map a `stop` reason to the resulting session status.

    `all_done` is terminal-success; iter_cap / evaluator_cap / empty_responses /
    no_case / error are terminal-failure; everything else (wall_clock,
    token_cap, interrupted) leaves the session `running` — those are stops the
    user can resume from.
    """
    if reason == "all_done":
        return "all_done"
    if reason in _TERMINAL_FAILURE_STOPS:
        return "failed"
    return "running"


def run(worktree: Path, session: Session, client: LLMClient) -> None:
    while True:
        stop = _stop_reason(client, session)
        if stop:
            detail = ""
            if stop == "wall_clock":
                detail = f" [TILTH_MAX_WALL_CLOCK_MINUTES={client.config.max_wall_clock_minutes}]"
            elif stop == "token_cap":
                detail = f" [TILTH_MAX_TOKENS={client.config.max_tokens}]"
            console.print(f"[yellow]stopping: {stop}[/yellow][dim]{detail}[/dim]")
            session.log("stop", {"reason": stop})
            session.set_status(_stop_to_status(stop))
            _refresh_summary(session)
            return

        prd = _load_prd(session.root)
        task = _next_pending(prd)
        if task is None:
            console.print("[green]all tasks complete[/green]")
            session.log("stop", {"reason": "all_done"})
            session.set_status("all_done")
            _refresh_summary(session)
            return

        trace_id = _trace_id()
        outcome = _run_task(task, worktree, client, session, trace_id)

        if outcome == "done":
            _self_improve(task, worktree, session, client, trace_id)
            task["status"] = "done"
            _save_prd(session.root, prd)
            memory.append_progress(session.root, f"{task['id']}\tdone\t{task['title']}")
            sha = ws.commit_task(worktree, task["id"], task["title"])
            session.log(
                "commit", {"task_id": task["id"], "trace_id": trace_id, "sha": sha}
            )
            console.print(f"[green]✓ {task['id']} committed ({sha})[/green]")
            _refresh_summary(session)
        else:
            task["status"] = "failed"
            _save_prd(session.root, prd)
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
            elif outcome == "empty_responses":
                detail = (
                    " — the model endpoint returned empty responses; this is "
                    "usually a provider or rate-limit hiccup. Check your "
                    "provider status and re-run."
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


def _print_summary(session: Session, client: LLMClient) -> None:
    elapsed = time.time() - session.started_at
    cfg = client.config
    tokens_pct = (session.tokens_used / cfg.max_tokens * 100) if cfg.max_tokens else 0.0
    wall_pct = (
        (elapsed / 60.0) / cfg.max_wall_clock_minutes * 100
        if cfg.max_wall_clock_minutes
        else 0.0
    )

    counts = {"done": 0, "failed": 0, "pending": 0}
    try:
        prd = _load_prd(session.root)
    except Exception:
        prd = None
    if prd is not None:
        for t in prd:
            status = t.get("status", "pending")
            if status not in counts:
                counts[status] = 0
            counts[status] += 1

    wall_dim = (
        f"({wall_pct:.1f}% of TILTH_MAX_WALL_CLOCK_MINUTES={cfg.max_wall_clock_minutes})"
    )
    tokens_dim = f"({tokens_pct:.1f}% of TILTH_MAX_TOKENS={cfg.max_tokens:,})"
    base_keys = ("done", "failed", "pending")
    total = sum(counts.values())
    task_bits = [f"total={total}"] + [f"{k}={counts.get(k, 0)}" for k in base_keys]
    extras = [f"{k}={v}" for k, v in counts.items() if k not in base_keys]

    learnings_emitted = sum(
        1
        for rec in iter_events(session.events_path)
        if rec.get("type") == "proposed_learnings"
        and (rec.get("payload") or {}).get("emitted") is True
    )

    console.print()
    console.print("[bold]── run summary ──[/bold]")
    console.print(f"  session   {session.session_id}")
    if session.branch:
        console.print(f"  branch    {session.branch}")
    console.print(f"  duration  {_format_duration(elapsed)} [dim]{wall_dim}[/dim]")
    console.print(f"  tokens    {session.tokens_used:,} [dim]{tokens_dim}[/dim]")
    console.print(f"  tasks     {' '.join(task_bits + extras)}")
    if learnings_emitted > 0:
        path = session.root / "proposed-learnings.md"
        plural = "s" if learnings_emitted != 1 else ""
        console.print(
            f"[blue]→ {learnings_emitted} proposed learning{plural} written to {path}"
            f" — review when ready[/blue]"
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


# --- prep-feature -----------------------------------------------------------

def _do_prep_feature(
    source: Path,
    brief: str | None,
    *,
    force: bool = False,
    keep_existing: bool = False,
) -> int:
    if force and keep_existing:
        console.print("[red]--force and --keep-existing are mutually exclusive[/red]")
        return 2

    blocking = _find_blocking_sessions(SESSIONS_DIR, source)
    if blocking and keep_existing:
        # Non-interactive equivalent of picker option "start a new session
        # (don't touch any existing sessions)". Falls through to normal prep.
        console.print(
            f"[dim]--keep-existing: prepping alongside {len(blocking)} "
            f"existing in-flight session(s)[/dim]"
        )
        blocking = []  # skip the picker / refusal logic below
    if blocking and not force:
        # Non-TTY → today's refuse-and-hint (preserves CI behavior).
        if not sys.stdin.isatty():
            console.print(
                f"[red]cannot prep:[/red] this workspace already has "
                f"{len(blocking)} session(s) in flight:"
            )
            for sid, status in blocking:
                console.print(f"  {_format_session_row(sid, status, SESSIONS_DIR)}")
            console.print(
                "[yellow]hint:[/yellow] discard with "
                "[bold]tilth reset <id>[/bold] (or "
                "[bold]tilth resume[/bold] to continue), or pass "
                "[bold]--force[/bold] to auto-discard."
            )
            return 2

        # TTY → interactive picker. Dispatch on the user's choice.
        action = _prompt_blocking_action(blocking, SESSIONS_DIR)
        if action == _PREP_ACTION_CANCEL:
            console.print("[yellow]cancelled[/yellow]")
            return 0
        if action == _PREP_ACTION_RUN:
            return do_run_cmd(source)
        if action == _PREP_ACTION_RESUME:
            sid = blocking[0][0]
            return do_resume_cmd(sid)
        if action == _PREP_ACTION_PREP_FRESH:
            # Leave blockers alone; just fall through to normal prep flow.
            pass
        elif action == _PREP_ACTION_RESET_AND_PREP:
            for sid, _status in blocking:
                rc = _do_reset(sid, assume_yes=True)
                if rc != 0:
                    console.print(f"[red]reset of {sid} failed; aborting prep[/red]")
                    return rc

    elif blocking and force:
        # --force: auto-discard blockers, proceed silently (with a one-line note).
        console.print(
            f"[dim]--force: discarding {len(blocking)} blocking session(s)[/dim]"
        )
        for sid, _status in blocking:
            rc = _do_reset(sid, assume_yes=True)
            if rc != 0:
                console.print(f"[red]reset of {sid} failed; aborting prep[/red]")
                return rc

    config = TilthConfig.from_env()
    client = LLMClient(config)

    if not brief:
        console.print(f"[bold]prep-feature[/bold]  {source}")
        console.print("Describe the feature or refactor (one line):")
        try:
            brief = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]aborted[/yellow]")
            return 130
        if not brief:
            console.print("[red]empty brief; aborting[/red]")
            return 2

    session = Session.new(SESSIONS_DIR)
    session.source = source
    worktree, branch = ws.ensure_worktree(
        source, session.session_id, session.root / "workspace"
    )
    session.workspace = worktree
    session.branch = branch
    session.save_checkpoint()
    session.log(
        "session_start",
        {
            "source": str(source),
            "phase": "prep-feature",
            "worktree": str(worktree),
            "branch": branch,
        },
    )

    console.print(f"[bold]session[/bold]  {session.session_id}")
    console.print(f"[bold]branch[/bold]   {branch}")
    console.print(f"[bold]model[/bold]    {config.prep_model}")

    frontend = TTYFrontend(console=console)
    sink = FileSeedSink()

    try:
        result = run_interview(
            session=session,
            source=source,
            worktree=worktree,
            client=client,
            frontend=frontend,
            sink=sink,
            feature_brief=brief,
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]interrupted[/yellow]")
        session.log("stop", {"reason": "interrupted"})
        _refresh_summary(session)
        return 130
    except InterviewAbort as exc:
        console.print(f"[red]interview aborted:[/red] {exc}")
        session.log("stop", {"reason": "error", "error": str(exc)})
        session.set_status("failed")
        _refresh_summary(session)
        return 1
    except Exception as exc:
        console.print(f"[red]error: {type(exc).__name__}: {exc}[/red]")
        session.log("stop", {"reason": "error", "error": f"{type(exc).__name__}: {exc}"})
        session.set_status("failed")
        _refresh_summary(session)
        raise

    # Anchor the seed bundle in the session branch so subsequent task_diff()s
    # don't carry every uncommitted seeded test as "scope creep" until each
    # task's commit lands. Without this, the evaluator sees future-task tests in
    # T-001's diff and rejects, and a confused worker may delete them.
    try:
        seed_sha = ws.commit_seed(worktree, len(result.prd_entries), len(result.test_files))
    except ws.WorkspaceError as exc:
        console.print(f"[red]seed commit failed:[/red] {exc}")
        session.log("stop", {"reason": "error", "error": f"seed_commit: {exc}"})
        session.set_status("failed")
        _refresh_summary(session)
        return 1
    if seed_sha:
        session.log("seed_committed", {"sha": seed_sha, "branch": branch})
        console.print(f"[dim]seed commit {seed_sha}[/dim]")

    _refresh_summary(session)
    console.print()
    console.print(f"[green]prepared[/green] sessions/{session.session_id}/")
    console.print(
        f"[green]→[/green] run: [bold]tilth run {source}[/bold] to start work"
    )
    return 0


# --- per-subcommand handlers ------------------------------------------------

def _resolve_session_id(maybe_id: str | None) -> str | None:
    """Translate '' or None into the latest session id; return None if no
    sessions exist at all. Callers print the 'no sessions found' message."""
    if maybe_id:
        return maybe_id
    return _latest_session_id(SESSIONS_DIR)


def do_reset_cmd(maybe_id: str | None, assume_yes: bool) -> int:
    sid = _resolve_session_id(maybe_id)
    if not sid:
        console.print(f"[red]no sessions found under {SESSIONS_DIR}[/red]")
        return 2
    if not maybe_id:
        console.print(f"[dim]reset: latest session is {sid}[/dim]")
    return _do_reset(sid, assume_yes=assume_yes)


def do_visualize_cmd(maybe_id: str | None) -> int:
    sid = _resolve_session_id(maybe_id)
    if not sid:
        console.print(f"[red]no sessions found under {SESSIONS_DIR}[/red]")
        return 2
    if not maybe_id:
        console.print(f"[dim]visualize: latest session is {sid}[/dim]")
    session_dir = SESSIONS_DIR / sid
    if not session_dir.is_dir():
        console.print(f"[red]no session at {session_dir}[/red]")
        return 2
    out = visualize.write_session_html(session_dir)
    console.print(f"[green]wrote[/green] {out}")
    return 0


def do_prep_feature_cmd(
    workspace: Path,
    brief: str | None,
    *,
    force: bool = False,
    keep_existing: bool = False,
) -> int:
    source = workspace.resolve()
    ws.ensure_git_repo(source)
    return _do_prep_feature(source, brief, force=force, keep_existing=keep_existing)


def do_resume_cmd(maybe_id: str | None) -> int:
    sid = _resolve_session_id(maybe_id)
    if not sid:
        console.print(f"[red]no sessions found under {SESSIONS_DIR}[/red]")
        return 2
    if not maybe_id:
        console.print(f"[dim]resume: latest session is {sid}[/dim]")
    config = TilthConfig.from_env()
    client = LLMClient(config)
    session = Session.wake(SESSIONS_DIR, sid)
    if session.workspace is None:
        console.print(
            "[red]session has no worktree recorded[/red] — if it's a `prepared` "
            "session, run [bold]tilth run <workspace>[/bold] to pick it up."
        )
        return 2
    worktree = session.workspace
    plan = _prepare_resume(session, worktree)
    console.print(f"[bold]resume plan[/bold] {plan}")
    return _run_session(session, worktree, client, config)


def do_run_cmd(workspace: Path) -> int:
    source = workspace.resolve()
    ws.ensure_git_repo(source)
    config = TilthConfig.from_env()
    client = LLMClient(config)

    prepared = _find_prepared_sessions(SESSIONS_DIR, source)
    if len(prepared) > 1:
        console.print(
            f"[red]multiple prepared sessions for this workspace:[/red] "
            f"{len(prepared)} found"
        )
        for sid in prepared:
            console.print(f"  {_format_session_row(sid, 'prepared', SESSIONS_DIR)}")
        console.print(
            "[yellow]hint:[/yellow] discard the ones you don't want with "
            "[bold]tilth reset <id>[/bold] until exactly one remains."
        )
        return 2
    if len(prepared) == 1:
        sid = prepared[0]
        console.print(
            f"[dim]picking up prepared session sessions/{sid}/[/dim]"
        )
        session = Session.wake(SESSIONS_DIR, sid)
        session.source = source
        worktree, branch = ws.ensure_worktree(
            source, session.session_id, session.root / "workspace"
        )
        session.workspace = worktree
        session.branch = branch
        session.set_status("running")
        session.log(
            "session_start",
            {"source": str(source), "phase": "run", "worktree": str(worktree), "branch": branch},
        )
    else:
        # No prepared session for this workspace. Don't create state and crash
        # later at PRD-load — surface the choice up front.
        prior = _find_resumable_session(SESSIONS_DIR, source)

        if not sys.stdin.isatty():
            # Non-TTY: clean error with the right pointer; exit 2, no orphan
            # session or worktree.
            if prior is None:
                console.print(
                    f"[red]no prepared session for {source}.[/red]"
                )
                console.print(
                    f"  → [bold]tilth prep-feature {source}[/bold] to seed one first"
                )
                return 2
            sid, last_stop = prior
            reason = last_stop or "no stop event"
            console.print(
                f"[red]no prepared session for {source}; "
                f"sessions/{sid}/ is resumable ({reason}).[/red]"
            )
            console.print(f"  → [bold]tilth resume {sid}[/bold]  (continue it)")
            console.print(
                f"  → [bold]tilth reset {sid} && tilth prep-feature {source}[/bold]  "
                "(discard and start fresh)"
            )
            return 2

        # TTY: interactive picker.
        action = _prompt_no_prep_action(prior, SESSIONS_DIR)
        if action == _RUN_ACTION_CANCEL:
            console.print("[yellow]cancelled[/yellow]")
            return 0
        if action == _RUN_ACTION_RESUME:
            assert prior is not None
            return do_resume_cmd(prior[0])
        if action == _RUN_ACTION_RESET_AND_PREP:
            assert prior is not None
            rc = _do_reset(prior[0], assume_yes=True)
            if rc != 0:
                return rc
            rc = _do_prep_feature(source, brief=None)
            if rc != 0:
                return rc
            return do_run_cmd(source)
        # _RUN_ACTION_PREP_NOW
        rc = _do_prep_feature(source, brief=None)
        if rc != 0:
            return rc
        return do_run_cmd(source)

    return _run_session(session, worktree, client, config)


def _run_session(
    session: Session, worktree: Path, client: LLMClient, config: TilthConfig
) -> int:
    console.print(f"[bold]session[/bold] {session.session_id}")
    console.print(f"[bold]worktree[/bold] {worktree}")
    if session.branch:
        console.print(f"[bold]branch[/bold] {session.branch}")
    console.print(f"[bold]model[/bold] {config.worker_model}")

    try:
        run(worktree, session, client)
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


def _legacy_main() -> int:
    """The pre-Phase-3 argparse surface, retained for back-compat dispatch.

    The verb router (tilth.cli) calls this when the user invokes the bare
    positional form `tilth <workspace>` or one of the old action flags.
    Emits a deprecation warning on the bare form per proposals/completed/prep-feature.md
    §5.5; removal scheduled for one minor version out.
    """
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="tilth",
        description="Run Tilth — a minimal long-running agent harness.",
    )
    parser.add_argument(
        "workspace",
        nargs="?",
        type=Path,
        help=(
            "Path to a workspace dir (a git repo, optionally with AGENTS.md / CLAUDE.md). "
            "prd.json and progress.txt live under sessions/<id>/ — they're "
            "harness-owned runtime artifacts, not workspace files."
        ),
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--resume",
        nargs="?",
        const="",
        default=None,
        metavar="SESSION_ID",
        help=(
            "Resume an interrupted session. With no value, picks the most recent "
            "session in sessions/. Trailing failed tasks are flipped back to pending "
            "and their FAILED placeholder commit is unwound."
        ),
    )
    action.add_argument(
        "--reset",
        nargs="?",
        const="",
        default=None,
        metavar="SESSION_ID",
        help=(
            "Tear down a session: remove its worktree (even if dirty), delete its "
            "session/<id> branch from the source repo, and drop sessions/<id>/. "
            "With no value, targets the most recent session."
        ),
    )
    action.add_argument(
        "--visualize",
        nargs="?",
        const="",
        default=None,
        metavar="SESSION_ID",
        help=(
            "Render a session's events.jsonl as a chat-style HTML page at "
            "sessions/<id>/chat.html. With no value, targets the most recent session."
        ),
    )
    action.add_argument(
        "--prep-feature",
        action="store_true",
        help=(
            "Interview the reasoning model against the workspace to produce a task "
            "seed (prd.json + matching test files), then stop. The seeded session is "
            "picked up by a subsequent bare `tilth <workspace>` invocation."
        ),
    )
    parser.add_argument(
        "--brief",
        default=None,
        metavar="TEXT",
        help=(
            "One-line feature/refactor brief for --prep-feature. If omitted, you'll "
            "be prompted interactively."
        ),
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip the confirmation prompt for --reset.",
    )
    args = parser.parse_args()

    if args.reset is not None:
        sid = args.reset or _latest_session_id(SESSIONS_DIR)
        if not sid:
            console.print(f"[red]no sessions found under {SESSIONS_DIR}[/red]")
            return 2
        if not args.reset:
            console.print(f"[dim]--reset: latest session is {sid}[/dim]")
        return _do_reset(sid, assume_yes=args.yes)

    if args.visualize is not None:
        sid = args.visualize or _latest_session_id(SESSIONS_DIR)
        if not sid:
            console.print(f"[red]no sessions found under {SESSIONS_DIR}[/red]")
            return 2
        if not args.visualize:
            console.print(f"[dim]--visualize: latest session is {sid}[/dim]")
        session_dir = SESSIONS_DIR / sid
        if not session_dir.is_dir():
            console.print(f"[red]no session at {session_dir}[/red]")
            return 2
        out = visualize.write_session_html(session_dir)
        console.print(f"[green]wrote[/green] {out}")
        return 0

    if args.prep_feature:
        if args.workspace is None:
            parser.error("--prep-feature requires a workspace path")
        source = args.workspace.resolve()
        ws.ensure_git_repo(source)
        return _do_prep_feature(source, args.brief)

    config = TilthConfig.from_env()
    client = LLMClient(config)

    if args.resume is not None:
        sid = args.resume or _latest_session_id(SESSIONS_DIR)
        if not sid:
            console.print(f"[red]no sessions found under {SESSIONS_DIR}[/red]")
            return 2
        if not args.resume:
            console.print(f"[dim]--resume: latest session is {sid}[/dim]")
        session = Session.wake(SESSIONS_DIR, sid)
        if session.workspace is None:
            console.print("[red]resumed session has no workspace recorded[/red]")
            return 2
        worktree = session.workspace
        plan = _prepare_resume(session, worktree)
        console.print(f"[bold]resume plan[/bold] {plan}")
    else:
        if args.workspace is None:
            parser.error("workspace path required when not using --resume")
        source = args.workspace.resolve()
        ws.ensure_git_repo(source)

        prepared = _find_prepared_sessions(SESSIONS_DIR, source)
        if len(prepared) > 1:
            console.print(
                f"[red]multiple prepared sessions for this workspace:[/red] "
                f"{len(prepared)} found"
            )
            for sid in prepared:
                console.print(f"  • sessions/{sid}/")
            console.print(
                "[yellow]hint:[/yellow] discard the ones you don't want with "
                "[bold]uv run tilth --reset <id>[/bold] until exactly one remains."
            )
            return 2
        if len(prepared) == 1:
            sid = prepared[0]
            console.print(
                f"[dim]picking up prepared session sessions/{sid}/[/dim]"
            )
            session = Session.wake(SESSIONS_DIR, sid)
            session.source = source
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
                    "phase": "run",
                    "worktree": str(worktree),
                    "branch": branch,
                },
            )
        else:
            # No prepared session for this workspace — delegate to the verb-router
            # behavior (picker on TTY, clean error pointer on non-TTY). Keeps
            # legacy and verb-router paths identical so we have one code path to
            # maintain when the legacy flags are eventually removed.
            return do_run_cmd(source)

    console.print(f"[bold]session[/bold] {session.session_id}")
    console.print(f"[bold]worktree[/bold] {worktree}")
    if session.branch:
        console.print(f"[bold]branch[/bold] {session.branch}")
    console.print(f"[bold]model[/bold] {config.worker_model}")

    try:
        run(worktree, session, client)
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


if __name__ == "__main__":
    sys.exit(main())
