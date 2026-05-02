"""Ralph loop entry point.

For each pending task in the workspace's prd.json:
  1. Reset context — build a fresh message list from disk (AGENTS.md + progress tail + task).
  2. Tool-loop with the worker model.
  3. When model stops calling tools, run validators (ruff + pytest).
     - Pass: judge model evaluates the diff in a fresh context.
       - Accept: prompt for AGENTS.md update, commit, mark done, next task.
       - Reject: inject judge feedback into worker loop; another iteration.
     - Fail: inject validator failure into worker loop; another iteration.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from rich.console import Console

from tilth import memory, tools, validators, visualize
from tilth import workspace as ws
from tilth.client import LLMClient, TilthConfig
from tilth.session import Session

console = Console()

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
SESSIONS_DIR = REPO_ROOT / "sessions"


# --- prompt assembly --------------------------------------------------------

def _system_prompt() -> str:
    return (PROMPTS_DIR / "system.md").read_text()


def _judge_prompt() -> str:
    return (PROMPTS_DIR / "judge.md").read_text()


def _agents_update_prompt() -> str:
    return (PROMPTS_DIR / "agents_update.md").read_text()


# --- JSON parsing tolerant to fences ---------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _parse_json_lenient(text: str) -> dict[str, Any] | None:
    """Try to parse a JSON object from a model response. Strips code fences."""
    candidates: list[str] = []
    m = _FENCE_RE.search(text)
    if m:
        candidates.append(m.group(1))
    candidates.append(text)
    for s in candidates:
        s = s.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


# --- judge ------------------------------------------------------------------

JUDGE_DIFF_MAX_CHARS = 12_000


def _judge_task(
    task: dict[str, Any],
    worktree: Path,
    client: LLMClient,
    session: Session,
    iter_n: int,
) -> tuple[bool, str]:
    """Call the judge in a fresh context. Returns (accept, reasoning)."""
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
    parts += [
        "",
        "## Validator status",
        "",
        "All objective validators (ruff, pytest) PASSED. Your job is the subjective check.",
        "",
        "## Diff (working tree vs HEAD on this task's branch)",
        "",
        "```diff",
        diff if diff.strip() else "(empty diff)",
        "```",
        "",
        "Respond with strict JSON only.",
    ]
    judge_messages = [
        {"role": "system", "content": _judge_prompt()},
        {"role": "user", "content": "\n".join(parts)},
    ]
    resp = client.chat(judge_messages, model=client.config.judge_model)
    usage = resp.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    eval_tokens = int(usage.get("completion_tokens") or 0)
    session.add_tokens(prompt_tokens + eval_tokens)

    content = ((resp.get("message") or {}).get("content") or "").strip()
    parsed = _parse_json_lenient(content)
    if not parsed or "verdict" not in parsed:
        session.log(
            "judge_verdict",
            {
                "task_id": task["id"],
                "iter": iter_n + 1,
                "accept": False,
                "reasoning": "judge returned unparseable response",
                "raw": content[:1000],
            },
        )
        return False, f"Judge response unparseable. Raw: {content[:300]}"

    accept = str(parsed.get("verdict", "")).lower() == "accept"
    reasoning = str(parsed.get("reasoning", "")).strip()
    session.log(
        "judge_verdict",
        {"task_id": task["id"], "iter": iter_n + 1, "accept": accept, "reasoning": reasoning},
    )
    return accept, reasoning


# --- self-improvement: AGENTS.md update -------------------------------------

_VALID_SECTIONS = {"Patterns", "Gotchas", "Style", "Recent learnings"}


def _self_improve(
    task: dict[str, Any],
    worktree: Path,
    client: LLMClient,
    session: Session,
) -> None:
    """Ask the worker model whether anything from this task should land in AGENTS.md."""
    diff = ws.task_diff(worktree)
    if len(diff) > JUDGE_DIFF_MAX_CHARS:
        diff = diff[:JUDGE_DIFF_MAX_CHARS] + "\n... [truncated]"

    user = (
        f"Task just completed: {task['id']} — {task['title']}\n\n"
        f"Description:\n{task.get('description', '').strip()}\n\n"
        f"## Current AGENTS.md\n\n"
        f"{memory.load_agents_md(worktree).strip()}\n\n"
        f"## Diff produced\n\n```diff\n{diff or '(empty)'}\n```\n\n"
        "Respond with strict JSON only."
    )
    update_messages = [
        {"role": "system", "content": _agents_update_prompt()},
        {"role": "user", "content": user},
    ]
    resp = client.chat(update_messages)
    usage = resp.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    eval_tokens = int(usage.get("completion_tokens") or 0)
    session.add_tokens(prompt_tokens + eval_tokens)

    content = ((resp.get("message") or {}).get("content") or "").strip()
    parsed = _parse_json_lenient(content)
    if not parsed:
        session.log(
            "agents_md_update",
            {
                "task_id": task["id"],
                "applied": False,
                "reason": "unparseable",
                "raw": content[:500],
            },
        )
        return

    if str(parsed.get("update", "")).lower() != "yes":
        session.log(
            "agents_md_update",
            {"task_id": task["id"], "applied": False, "reason": "no_update"},
        )
        return

    section = str(parsed.get("section", "Recent learnings")).strip()
    if section not in _VALID_SECTIONS:
        section = "Recent learnings"
    entry = str(parsed.get("entry", "")).strip()
    if not entry:
        session.log(
            "agents_md_update", {"task_id": task["id"], "applied": False, "reason": "empty_entry"}
        )
        return

    _append_to_agents_md(worktree, section, f"- {entry}")
    session.log(
        "agents_md_update",
        {"task_id": task["id"], "applied": True, "section": section, "entry": entry},
    )
    console.print(f"[blue]→ AGENTS.md ({section})[/blue] {entry}")


def _append_to_agents_md(worktree: Path, section: str, line: str) -> None:
    """Append `line` under `## {section}` in AGENTS.md, creating the section if missing."""
    p = worktree / "AGENTS.md"
    text = p.read_text() if p.is_file() else ""
    heading = f"## {section}"
    if heading in text:
        # Replace the section's "(empty — agent appends here)" placeholder if present;
        # otherwise append the line at the end of the section.
        placeholder = "_(empty — agent appends here)_"
        # Simple rewrite: split on the heading, locate the next "## " or EOF.
        parts = text.split(heading, 1)
        before = parts[0] + heading
        rest = parts[1]
        # Find next H2.
        next_h2 = rest.find("\n## ")
        if next_h2 == -1:
            section_body = rest
            tail = ""
        else:
            section_body = rest[:next_h2]
            tail = rest[next_h2:]
        if placeholder in section_body:
            new_body = section_body.replace(placeholder, line)
        else:
            new_body = section_body.rstrip() + f"\n{line}\n"
        p.write_text(before + new_body + tail)
    else:
        p.write_text((text.rstrip() + f"\n\n## {section}\n\n{line}\n").lstrip("\n"))


# --- prd handling -----------------------------------------------------------

def _load_prd(worktree: Path) -> list[dict[str, Any]]:
    prd_path = worktree / "prd.json"
    if not prd_path.is_file():
        raise FileNotFoundError(f"No prd.json at {prd_path}")
    return json.loads(prd_path.read_text())


def _save_prd(worktree: Path, prd: list[dict[str, Any]]) -> None:
    (worktree / "prd.json").write_text(json.dumps(prd, indent=2) + "\n")


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
    if not session.events_path.is_file():
        return None
    last: str | None = None
    with session.events_path.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") == "stop":
                last = ((rec.get("payload") or {}).get("reason")) or None
    return last


def _source_for_session(session_dir: Path) -> Path | None:
    """Recover the source repo path for a session by scanning its events log."""
    events = session_dir / "events.jsonl"
    if not events.is_file():
        return None
    with events.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
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
    """
    if not sessions_root.is_dir():
        return None
    target = str(source)
    for d in sorted(sessions_root.iterdir(), key=lambda p: p.name, reverse=True):
        if not d.is_dir():
            continue
        events = d / "events.jsonl"
        if not events.is_file():
            continue
        sess_source: str | None = None
        last_stop: str | None = None
        with events.open() as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
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


def _prepare_resume(session: Session, worktree: Path) -> str:
    """Flip trailing failed tasks back to pending and unwind their FAILED commits.

    Always logs a `session_resume` event with the plan summary and structured fields.
    Returns the one-line plan suitable for printing.
    """
    last_stop = _last_stop_reason(session)
    retried: list[str] = []
    pending: list[str] = []
    unwound = False

    prd = _load_prd(worktree)

    if last_stop == "all_done":
        plan = "session reached all_done; nothing to resume"
    else:
        failed = [t for t in prd if t.get("status") == "failed"]
        if failed:
            for t in failed:
                t["status"] = "pending"
            _save_prd(worktree, prd)
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

_HISTORY_KEEP = frozenset({
    "role",
    "content",
    "tool_calls",
    "reasoning",
    "reasoning_details",
})


def _assistant_history_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Shape an assistant response for re-injection into the message history.

    Why: thinking-mode models reject the next request with HTTP 400 if the
    reasoning content from the prior assistant turn isn't echoed back. OpenRouter's
    normalised response carries it in `reasoning_details` (structured blocks, the
    documented form) and a flat `reasoning` string. We keep both — observed
    on the wire against deepseek/deepseek-v4-flash. Output-only metadata
    (refusal, annotations, audio, function_call) is dropped.

    Pair this with `extra_body={"reasoning": {"enabled": True}}` on the request
    side (see client.py) — without that opt-in, OpenRouter sometimes omits
    reasoning on parallel-tool-call turns and there's nothing to echo.
    """
    return {k: v for k, v in msg.items() if k in _HISTORY_KEEP}


def _run_task(
    task: dict[str, Any],
    worktree: Path,
    client: LLMClient,
    session: Session,
) -> str:
    """Run one task. Returns 'done', 'iter_cap', or 'judge_cap'.

    A task is 'done' only when the model stops calling tools AND the validators
    (ruff + pytest) pass. Validator failures are fed back into the loop as the next
    user message; the model gets another iteration to fix things.
    """
    session.log("context_reset", {"task_id": task["id"]})

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": memory.build_user_prompt(task, worktree)},
    ]
    tool_schemas = tools.schemas()
    judge_calls = 0

    for iter_n in range(client.config.max_iterations_per_task):
        console.print(f"[dim]task {task['id']}  iter {iter_n + 1}[/dim]")
        resp = client.chat(messages, tools=tool_schemas)

        usage = resp.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        eval_tokens = int(usage.get("completion_tokens") or 0)
        session.add_tokens(prompt_tokens + eval_tokens)

        msg = resp.get("message") or {}
        model_call_payload: dict[str, Any] = {
            "task_id": task["id"],
            "iter": iter_n + 1,
            "prompt_tokens": prompt_tokens,
            "eval_tokens": eval_tokens,
            "tokens_used_total": session.tokens_used,
        }
        if reasoning_details := msg.get("reasoning_details"):
            model_call_payload["reasoning_details"] = reasoning_details
        else:
            reasoning = msg.get("reasoning")
            if isinstance(reasoning, str) and reasoning.strip():
                model_call_payload["reasoning"] = reasoning
        session.log("model_call", model_call_payload)

        messages.append(_assistant_history_message(msg))

        tool_calls = msg.get("tool_calls") or []

        if tool_calls:
            for tc in tool_calls:
                tc_id = tc.get("id") or ""
                fn = tc.get("function") or {}
                tool_name = fn.get("name") or ""
                raw_args = fn.get("arguments") or {}
                args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)

                session.log(
                    "tool_call",
                    {"task_id": task["id"], "iter": iter_n + 1, "tool": tool_name, "args": args},
                )
                console.print(f"[cyan]→ {tool_name}[/cyan] {json.dumps(args)[:200]}")

                outcome = tools.dispatch(tool_name, args, worktree)
                event_type = "pre_tool_block" if outcome.blocked else "tool_result"
                session.log(
                    event_type,
                    {
                        "task_id": task["id"],
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
            continue

        # Model declared done — verify with validators.
        content = (msg.get("content") or "").strip()
        console.print(f"[dim]task {task['id']} model summary:[/dim] {content[:200]}")

        prd = _load_prd(worktree)
        done_ids = [t["id"] for t in prd if t.get("status") == "done"]
        results = validators.run_all(worktree, [*done_ids, task["id"]])
        passed = validators.all_passed(results)
        session.log(
            "validator_run",
            {
                "task_id": task["id"],
                "iter": iter_n + 1,
                "passed": passed,
                "results": [{"name": r.name, "passed": r.passed} for r in results],
            },
        )

        if passed:
            console.print(f"[green]task {task['id']} validators passed → judge[/green]")
            accept, reasoning = _judge_task(task, worktree, client, session, iter_n)
            judge_calls += 1
            if accept:
                console.print(f"[green]judge accepts:[/green] {reasoning[:200]}")
                session.log("task_done", {"task_id": task["id"], "summary": content})
                return "done"

            console.print(f"[yellow]judge rejects:[/yellow] {reasoning[:200]}")
            judge_cap = client.config.max_judge_calls_per_task
            if judge_cap > 0 and judge_calls >= judge_cap:
                console.print(
                    f"[red]task {task['id']} hit judge cap[/red] "
                    f"[dim][TILTH_MAX_JUDGE_CALLS_PER_TASK={judge_cap}][/dim]"
                )
                session.log(
                    "task_failed",
                    {"task_id": task["id"], "reason": "judge_cap", "judge_calls": judge_calls},
                )
                return "judge_cap"
            judge_feedback = (
                "An independent reviewer rejected the work. Their reasoning:\n\n"
                f"{reasoning}\n\n"
                "Read the rejection carefully, fix the issues it points at, and continue working. "
                "Stop calling tools and respond with a summary only when the issue is resolved."
            )
            messages.append({"role": "user", "content": judge_feedback})
            continue

        report = validators.combined_report(results)
        console.print(f"[yellow]task {task['id']} validators failed; feeding back[/yellow]")
        feedback = (
            "The validators failed. You said you were done, but the workspace does not pass.\n"
            "Read the report below, fix the issues, and continue working. "
            "Stop calling tools and respond with a summary only when validators will pass.\n\n"
            f"{report}"
        )
        messages.append({"role": "user", "content": feedback})

    cap = client.config.max_iterations_per_task
    console.print(
        f"[red]task {task['id']} hit iteration cap[/red] "
        f"[dim][TILTH_MAX_ITERATIONS_PER_TASK={cap}][/dim]"
    )
    session.log("task_failed", {"task_id": task["id"], "reason": "iter_cap"})
    return "iter_cap"


def _stop_reason(client: LLMClient, session: Session) -> str | None:
    if session.elapsed_minutes() >= client.config.max_wall_clock_minutes:
        return "wall_clock"
    if session.tokens_used >= client.config.max_tokens:
        return "token_cap"
    return None


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
            return

        prd = _load_prd(worktree)
        task = _next_pending(prd)
        if task is None:
            console.print("[green]all tasks complete[/green]")
            session.log("stop", {"reason": "all_done"})
            return

        outcome = _run_task(task, worktree, client, session)

        if outcome == "done":
            _self_improve(task, worktree, client, session)
            task["status"] = "done"
            _save_prd(worktree, prd)
            memory.append_progress(worktree, f"{task['id']}\tdone\t{task['title']}")
            sha = ws.commit_task(worktree, task["id"], task["title"])
            session.log("commit", {"task_id": task["id"], "sha": sha})
            console.print(f"[green]✓ {task['id']} committed ({sha})[/green]")
        else:
            task["status"] = "failed"
            _save_prd(worktree, prd)
            memory.append_progress(worktree, f"{task['id']}\tfailed:{outcome}\t{task['title']}")
            ws.commit_task(worktree, task["id"], f"FAILED ({outcome}): {task['title']}")
            detail = ""
            if outcome == "iter_cap":
                cap = client.config.max_iterations_per_task
                detail = f" [TILTH_MAX_ITERATIONS_PER_TASK={cap}]"
            elif outcome == "judge_cap":
                cap = client.config.max_judge_calls_per_task
                detail = f" [TILTH_MAX_JUDGE_CALLS_PER_TASK={cap}]"
            console.print(
                f"[red]✗ {task['id']} failed ({outcome}); halting run[/red]"
                f"[dim]{detail}[/dim]"
            )
            session.log("stop", {"reason": outcome})
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


def _print_summary(session: Session, client: LLMClient, worktree: Path | None) -> None:
    elapsed = time.time() - session.started_at
    cfg = client.config
    tokens_pct = (session.tokens_used / cfg.max_tokens * 100) if cfg.max_tokens else 0.0
    wall_pct = (
        (elapsed / 60.0) / cfg.max_wall_clock_minutes * 100
        if cfg.max_wall_clock_minutes
        else 0.0
    )

    counts = {"done": 0, "failed": 0, "pending": 0}
    if worktree is not None:
        try:
            prd = _load_prd(worktree)
            for t in prd:
                status = t.get("status", "pending")
                if status not in counts:
                    counts[status] = 0
                counts[status] += 1
        except Exception:
            pass

    wall_dim = (
        f"({wall_pct:.1f}% of TILTH_MAX_WALL_CLOCK_MINUTES={cfg.max_wall_clock_minutes})"
    )
    tokens_dim = f"({tokens_pct:.1f}% of TILTH_MAX_TOKENS={cfg.max_tokens:,})"
    base_keys = ("done", "failed", "pending")
    task_bits = [f"{k}={counts.get(k, 0)}" for k in base_keys]
    extras = [f"{k}={v}" for k, v in counts.items() if k not in base_keys]

    console.print()
    console.print("[bold]── run summary ──[/bold]")
    console.print(f"  session   {session.session_id}")
    if session.branch:
        console.print(f"  branch    {session.branch}")
    console.print(f"  duration  {_format_duration(elapsed)} [dim]{wall_dim}[/dim]")
    console.print(f"  tokens    {session.tokens_used:,} [dim]{tokens_dim}[/dim]")
    console.print(f"  tasks     {' '.join(task_bits + extras)}")


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


# --- CLI --------------------------------------------------------------------

def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="tilth",
        description="Run Tilth — a minimal long-running agent harness.",
    )
    parser.add_argument(
        "workspace",
        nargs="?",
        type=Path,
        help="Path to a workspace dir (a git repo containing prd.json, AGENTS.md, progress.txt).",
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

        prior = _find_resumable_session(SESSIONS_DIR, source)
        if prior is not None:
            sid, last_stop = prior
            reason = last_stop or "no stop event"
            console.print(
                f"[yellow]heads up:[/yellow] sessions/{sid}/ ended in "
                f"[bold]{reason}[/bold] and is resumable"
            )
            console.print("  → [bold]uv run tilth --resume[/bold]       (continue that work)")
            console.print("  → [bold]uv run tilth --reset --yes[/bold]  (discard it first)")
            console.print("[dim]starting fresh anyway in 5s... (Ctrl-C to abort)[/dim]")
            try:
                time.sleep(5)
            except KeyboardInterrupt:
                console.print("[yellow]aborted[/yellow]")
                return 130

        session = Session.new(SESSIONS_DIR)
        worktree, branch = ws.create_worktree(
            source, session.session_id, session.root / "workspace"
        )
        session.workspace = worktree
        session.branch = branch
        session.save_checkpoint()
        session.log(
            "session_start",
            {"source": str(source), "worktree": str(worktree), "branch": branch},
        )

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
        return 130
    except Exception as exc:
        console.print(f"[red]error: {type(exc).__name__}: {exc}[/red]")
        session.log("stop", {"reason": "error", "error": f"{type(exc).__name__}: {exc}"})
        raise
    finally:
        _print_summary(session, client, worktree)
    return 0


if __name__ == "__main__":
    sys.exit(main())
