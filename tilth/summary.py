"""Roll up events.jsonl into a sessions/<id>/summary.json snapshot.

Cheap to recompute on every task boundary. Consumers (visualize, the article
screenshots, anyone reading session state without parsing the full JSONL) get a
stable, denormalised view.

Stable shape — bump SUMMARY_VERSION if breaking.

Schema (v4 — full token/cost detail; see tilth/usage.py for the canonical record):

    {
        "version": 4,
        "session_id": str | null,
        "started_at":   "<ISO ts of the run's session_start>" | null,
        "last_event_at":"<ISO ts of most recent event>" | null,
        "tokens": <usage>,            # session totals; see <usage> below
        "tasks": {
            "<task_id>": {
                "status": "in_progress" | "done" | "failed",
                "iterations": int,            # max iter seen on a model_call
                "tokens": <usage>,            # this task only (was a bare int in v3)
                "tool_calls": {"<tool>": int, ...},
                "hook_blocks": int,           # pre_tool blocks for this task
                "evaluator": {
                    "accepts": int,
                    "rejects": int,
                    "rejection_categories": {"<category>": int, ...},
                },
                "failure_reason": str,        # only when status == "failed"
            }
        },
        "tool_histogram":  {"<tool>":  int, ...},
        "hook_outcomes":   {"<hook>":  {"<outcome>": int, ...}, ...},
        "evaluator":       {
            "accepts": int,
            "rejects": int,
            "rejection_categories": {"<category>": int, ...},
        },
        "stop":            {"reason": str, "ts": str},   # absent if no stop yet
    }

    <usage> = {
        "prompt": int, "eval": int, "total": int,      # eval == completion tokens
        "cached": int,        # cache-hit tokens, a SUBSET of prompt (not additive)
        "reasoning": int,     # thinking tokens, a SUBSET of eval (not additive)
        "cost": float,        # provider's USD figure (OpenRouter); 0.0 otherwise
        "by_phase": { "worker": <usage-without-by_phase>,
                      "evaluator": <usage-without-by_phase> },
    }

    Session `tokens.total` is `max(model_call.tokens_used_total)` (ties to the
    cap counter); every other figure — and all by_phase / per-task totals — is a
    field-wise sum across model_call events (worker + evaluator, including
    provider-retry attempts). cached/reasoning never inflate total or the cap.

Refreshed at every task boundary and at every stop path (see loop.py:
_refresh_summary). Refresh is best-effort — failures must not break the run.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from tilth import usage
from tilth.session import iter_events

SUMMARY_VERSION = 4


def _usage_acc() -> dict[str, Any]:
    """A zeroed usage accumulator with a per-actor split nested inside."""
    acc = usage.zero_usage()
    acc["by_phase"] = {"worker": usage.zero_usage(), "evaluator": usage.zero_usage()}
    return acc


def _accumulate_usage(acc: dict[str, Any], u: dict[str, Any], bucket: str) -> None:
    """Add one call's usage to an accumulator and to its actor bucket."""
    usage.add_usage(acc, u)
    usage.add_usage(acc["by_phase"][bucket], u)


def _empty_task() -> dict[str, Any]:
    return {
        "status": "in_progress",
        "iterations": 0,
        "tokens": _usage_acc(),
        "tool_calls": defaultdict(int),
        "hook_blocks": 0,
        "evaluator": {
            "accepts": 0,
            "rejects": 0,
            "rejection_categories": defaultdict(int),
        },
    }


def build_from_events(
    events_path: Path, session_id: str | None = None
) -> dict[str, Any]:
    tokens = _usage_acc()
    tasks: dict[str, dict[str, Any]] = {}
    tool_histogram: dict[str, int] = defaultdict(int)
    hook_outcomes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    evaluator: dict[str, Any] = {
        "accepts": 0,
        "rejects": 0,
        "rejection_categories": defaultdict(int),
    }
    started_at: str | None = None
    last_event_at: str | None = None
    stop: dict[str, Any] | None = None

    def task_for(tid: str) -> dict[str, Any]:
        if tid not in tasks:
            tasks[tid] = _empty_task()
        return tasks[tid]

    for ev in iter_events(events_path):
        ts = ev.get("ts")
        if ts:
            last_event_at = ts
        typ = ev.get("type", "")
        p = ev.get("payload") or {}
        tid = p.get("task_id")

        if typ == "session_start":
            started_at = ts
        elif typ == "session_resume" and started_at is None:
            started_at = ts
            if session_id is None:
                session_id = p.get("session_id")
        elif typ == "stop":
            stop = {"reason": p.get("reason"), "ts": ts}
        elif typ == "model_call":
            u = usage.from_event(p)
            bucket = usage.phase_bucket(p.get("phase"))
            _accumulate_usage(tokens, u, bucket)
            # Session total ties to the cap counter, not the field-wise sum.
            running = p.get("tokens_used_total")
            if isinstance(running, int):
                tokens["total"] = max(tokens["total"], running)
            if tid:
                t = task_for(tid)
                _accumulate_usage(t["tokens"], u, bucket)
                t["iterations"] = max(t["iterations"], int(p.get("iter") or 0))
        elif typ == "tool_call":
            tool = p.get("tool") or ""
            if tool:
                tool_histogram[tool] += 1
                if tid:
                    task_for(tid)["tool_calls"][tool] += 1
        elif typ == "hook_run":
            hook = p.get("hook") or ""
            outcome = p.get("outcome") or ""
            if hook and outcome:
                hook_outcomes[hook][outcome] += 1
                if hook == "pre_tool" and outcome == "block" and tid:
                    task_for(tid)["hook_blocks"] += 1
        elif typ == "evaluator_verdict":
            verdict = (p.get("verdict") or "").lower()
            if verdict == "accept":
                evaluator["accepts"] += 1
                if tid:
                    task_for(tid)["evaluator"]["accepts"] += 1
            else:
                evaluator["rejects"] += 1
                if tid:
                    task_for(tid)["evaluator"]["rejects"] += 1
                category = p.get("rejection_category")
                if isinstance(category, str) and category:
                    evaluator["rejection_categories"][category] += 1
                    if tid:
                        task_for(tid)["evaluator"]["rejection_categories"][category] += 1
        elif typ == "task_done" and tid:
            task_for(tid)["status"] = "done"
        elif typ == "task_failed" and tid:
            t = task_for(tid)
            t["status"] = "failed"
            reason = p.get("reason")
            if reason:
                t["failure_reason"] = reason

    for t in tasks.values():
        t["tool_calls"] = dict(t["tool_calls"])
        t["evaluator"]["rejection_categories"] = dict(
            t["evaluator"]["rejection_categories"]
        )

    out: dict[str, Any] = {
        "version": SUMMARY_VERSION,
        "session_id": session_id,
        "started_at": started_at,
        "last_event_at": last_event_at,
        "tokens": tokens,
        "tasks": tasks,
        "tool_histogram": dict(tool_histogram),
        "hook_outcomes": {k: dict(v) for k, v in hook_outcomes.items()},
        "evaluator": {
            **evaluator,
            "rejection_categories": dict(evaluator["rejection_categories"]),
        },
    }
    if stop is not None:
        out["stop"] = stop
    return out


def write_summary(
    events_path: Path, out_path: Path, session_id: str | None = None
) -> None:
    data = build_from_events(events_path, session_id=session_id)
    out_path.write_text(json.dumps(data, indent=2))
