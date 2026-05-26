"""The interview engine — drives the tool-use loop end-to-end.

Mirrors the worker loop's shape (see tilth/loop.py:_run_task), but with a
narrower tool surface, no validators, no judge, and a terminal `write_seed`
call that ends the session. Everything observable on the worker side
(model_call events, token accounting, reasoning round-trip) is preserved here
so the visualizer renders interviews the same as runs.

Stop conditions, in priority order:
  1. `write_seed` succeeds → status flips to `prepared`, loop returns.
  2. Iteration cap hit → raises InterviewAbort.
  3. Model declares done without ever calling `write_seed` → raises InterviewAbort.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tilth.client import LLMClient, assistant_history_message
from tilth.seed import tools as seed_tools
from tilth.seed.frontend import InterviewFrontend, SeedSink
from tilth.session import Session

PROMPTS_DIR = Path(__file__).resolve().parent
MAX_INTERVIEW_ITERATIONS = 60  # generous; interviews aren't supposed to hit this


def _trace_id() -> str:
    return uuid.uuid4().hex


def _span_id() -> str:
    return uuid.uuid4().hex[:16]


def _system_prompt() -> str:
    return (PROMPTS_DIR / "prompts.md").read_text()


class InterviewAbort(RuntimeError):
    pass


@dataclass
class InterviewResult:
    prd_entries: list[dict[str, Any]]
    test_files: dict[str, str]
    meta: dict[str, Any]
    tokens_used: int


def run_interview(
    *,
    session: Session,
    source: Path,
    worktree: Path,
    client: LLMClient,
    frontend: InterviewFrontend,
    sink: SeedSink,
    feature_brief: str,
) -> InterviewResult:
    """Drive the interview to completion. Persists the seed via `sink`.

    `session` must already exist (Session.new) with `source` recorded, and
    `worktree` must point at a real session-branch worktree (typically created
    by ws.ensure_worktree just before this call). Reads (`read_file`, `glob`,
    `grep`) route to `source` so the seeder sees uncommitted in-flight work;
    writes (`write_seed`) route to `worktree` so the seed bundle lands in the
    session branch instead of dirtying the source repo. On success: appends a
    `session_prepared` event, flips status to `prepared`, returns the
    InterviewResult.
    """
    trace_id = _trace_id()
    started_at = time.time()
    interviewer_model = client.config.prep_model

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": _seed_user_prompt(source, feature_brief)},
    ]
    schemas = seed_tools.SCHEMAS

    prompt_total = 0
    completion_total = 0

    for iter_n in range(MAX_INTERVIEW_ITERATIONS):
        iter_span = _span_id()
        resp = client.chat(messages, tools=schemas, model=interviewer_model)
        msg = resp.get("message") or {}
        usage = resp.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        eval_tokens = int(usage.get("completion_tokens") or 0)
        prompt_total += prompt_tokens
        completion_total += eval_tokens
        session.add_tokens(prompt_tokens + eval_tokens)

        model_call_payload: dict[str, Any] = {
            "phase": "interview",
            "trace_id": trace_id,
            "span_id": iter_span,
            "iter": iter_n + 1,
            "prompt_tokens": prompt_tokens,
            "eval_tokens": eval_tokens,
            "tokens_used_total": session.tokens_used,
        }
        if finish_reason := resp.get("finish_reason"):
            model_call_payload["finish_reason"] = finish_reason
        if reasoning_details := msg.get("reasoning_details"):
            model_call_payload["reasoning_details"] = reasoning_details
        else:
            reasoning = msg.get("reasoning")
            if isinstance(reasoning, str) and reasoning.strip():
                model_call_payload["reasoning"] = reasoning
        session.log("model_call", model_call_payload)
        frontend.update_tokens(prompt_total, completion_total)

        messages.append(assistant_history_message(msg))

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            content = (msg.get("content") or "").strip()
            raise InterviewAbort(
                "model stopped before calling write_seed. "
                f"Last message: {content[:300]!r}"
            )

        for tc in tool_calls:
            tc_id = tc.get("id") or ""
            fn = tc.get("function") or {}
            tool_name = fn.get("name") or ""
            raw_args = fn.get("arguments") or {}

            try:
                args = raw_args if isinstance(raw_args, dict) else _parse_args(raw_args)
            except ValueError as exc:
                # Model emitted invalid JSON for its tool arguments. Common on
                # long write_seed payloads where a token boundary corrupts the
                # structure (e.g. concatenated JSON values produce
                # "Extra data" errors). Feed the parse error back as a
                # tool_result so the model can retry with a smaller / cleaner
                # payload, same pattern as sink.write_seed failures below.
                raw_len = len(raw_args) if isinstance(raw_args, str) else 0
                session.log(
                    "tool_call",
                    {
                        "phase": "interview",
                        "trace_id": trace_id,
                        "span_id": iter_span,
                        "iter": iter_n + 1,
                        "tool": tool_name,
                        "args": "(unparseable JSON)",
                        "args_chars": raw_len,
                    },
                )
                result_msg = (
                    f"ERROR: your `{tool_name}` arguments failed to parse "
                    f"as JSON: {exc}. This often happens when a long payload "
                    "(e.g. write_seed with verbose test_files) crosses a "
                    "token boundary and corrupts the structure. Retry the "
                    "call with terser content — for write_seed, keep the same "
                    "single-call contract but write shorter test bodies (one "
                    "assert per criterion, no decorative comments)."
                )
                session.log(
                    "tool_result",
                    {
                        "phase": "interview",
                        "trace_id": trace_id,
                        "span_id": iter_span,
                        "iter": iter_n + 1,
                        "tool": tool_name,
                        "result_preview": result_msg[:500],
                        "result_chars": len(result_msg),
                    },
                )
                messages.append(
                    {"role": "tool", "tool_call_id": tc_id, "content": result_msg}
                )
                continue

            session.log(
                "tool_call",
                {
                    "phase": "interview",
                    "trace_id": trace_id,
                    "span_id": iter_span,
                    "iter": iter_n + 1,
                    "tool": tool_name,
                    "args": _redact_for_log(tool_name, args),
                },
            )

            if tool_name == seed_tools.NAME_WRITE_SEED:
                meta = _build_meta(
                    args=args,
                    started_at=started_at,
                    interviewer_model=interviewer_model,
                    prompt_total=prompt_total,
                    completion_total=completion_total,
                )
                prd_entries = args.get("prd_entries") or []
                test_files = args.get("test_files") or {}
                try:
                    sink.write_seed(
                        session_dir=session.root,
                        workspace=worktree,
                        prd_entries=prd_entries,
                        test_files=test_files,
                        meta=meta,
                    )
                except Exception as exc:
                    result_msg = (
                        f"ERROR writing seed: {type(exc).__name__}: {exc}. "
                        "Fix the issue and call write_seed again with the corrected bundle."
                    )
                    session.log(
                        "tool_result",
                        {
                            "phase": "interview",
                            "trace_id": trace_id,
                            "span_id": iter_span,
                            "iter": iter_n + 1,
                            "tool": tool_name,
                            "result_preview": result_msg[:500],
                            "result_chars": len(result_msg),
                        },
                    )
                    messages.append(
                        {"role": "tool", "tool_call_id": tc_id, "content": result_msg}
                    )
                    continue

                session.log(
                    "session_prepared",
                    {
                        "trace_id": trace_id,
                        "interviewer_model": interviewer_model,
                        "prd_entries": len(prd_entries),
                        "test_files": len(test_files),
                        "tokens_used": prompt_total + completion_total,
                    },
                )
                session.set_status("prepared")
                frontend.show_summary(
                    tldr=args.get("tldr", "") or "",
                    open_questions=list(args.get("open_questions") or []),
                    blockers=list(args.get("blockers") or []),
                )
                return InterviewResult(
                    prd_entries=prd_entries,
                    test_files=test_files,
                    meta=meta,
                    tokens_used=prompt_total + completion_total,
                )

            result = _dispatch(tool_name, args, source, frontend)
            session.log(
                "tool_result",
                {
                    "phase": "interview",
                    "trace_id": trace_id,
                    "span_id": iter_span,
                    "iter": iter_n + 1,
                    "tool": tool_name,
                    "result_preview": result[:500],
                    "result_chars": len(result),
                },
            )
            messages.append(
                {"role": "tool", "tool_call_id": tc_id, "content": result}
            )

    raise InterviewAbort(
        f"interview hit iteration cap ({MAX_INTERVIEW_ITERATIONS}) "
        "without calling write_seed"
    )


def _seed_user_prompt(source: Path, feature_brief: str) -> str:
    return (
        "## Source repo\n"
        f"{source}\n\n"
        "## Feature / refactor brief\n"
        f"{feature_brief.strip()}\n\n"
        "Begin with step 1 of the workflow. One question per `ask_user` call."
    )


def _build_meta(
    *,
    args: dict[str, Any],
    started_at: float,
    interviewer_model: str,
    prompt_total: int,
    completion_total: int,
) -> dict[str, Any]:
    return {
        "interviewer_model": interviewer_model,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
        "ended_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tokens": {
            "prompt": prompt_total,
            "completion": completion_total,
            "total": prompt_total + completion_total,
        },
        "tldr": args.get("tldr", "") or "",
        "open_questions": list(args.get("open_questions") or []),
        "blockers": list(args.get("blockers") or []),
        "scope_notes": args.get("scope_notes", "") or "",
    }


def _dispatch(
    tool_name: str,
    args: dict[str, Any],
    source: Path,
    frontend: InterviewFrontend,
) -> str:
    if tool_name == seed_tools.NAME_ASK_USER:
        question = args.get("question")
        if not isinstance(question, str) or not question.strip():
            return "ERROR: 'question' must be a non-empty string"
        options = args.get("options")
        if options is not None and not isinstance(options, list):
            return "ERROR: 'options' must be a list of strings if provided"
        answer = frontend.ask_user(question, options=options)
        # ask_user can legitimately return empty (EOF, no input). The model
        # should treat that as a non-answer and ask again or proceed.
        return answer if answer else "(no answer)"

    fn = seed_tools.READ_TOOLS.get(tool_name)
    if fn is None:
        available = sorted(
            [*seed_tools.READ_TOOLS, seed_tools.NAME_ASK_USER, seed_tools.NAME_WRITE_SEED]
        )
        return f"ERROR: unknown tool {tool_name!r}. Available: {available}"
    try:
        return fn(args, source)
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"


def _parse_args(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"tool arguments were not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"tool arguments must be a JSON object, got {type(parsed).__name__}")
    return parsed


def _redact_for_log(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Keep events.jsonl readable. Test file contents can be large; preview only."""
    if tool_name != seed_tools.NAME_WRITE_SEED:
        return args
    test_files = args.get("test_files") or {}
    preview: dict[str, str] = {}
    for fname, content in test_files.items():
        text = content if isinstance(content, str) else str(content)
        preview[fname] = (
            text if len(text) <= 200 else text[:200] + f"… [+{len(text) - 200} chars]"
        )
    return {**args, "test_files": preview}
