"""Roll up events.jsonl into a sessions/<id>/summary.json snapshot.

Cheap to recompute on every task boundary. Consumers (visualize, the article
screenshots, anyone reading session state without parsing the full JSONL) get a
stable, denormalised view.

Stable shape — bump SUMMARY_VERSION if breaking.

Schema (v1):

    {
        "version": 1,
        "session_id": str | null,
        "started_at":   "<ISO ts of session_start>" | null,
        "last_event_at":"<ISO ts of most recent event>" | null,
        "tokens": {
            "prompt": int,            # sum across all model_call events
            "eval":   int,            # sum across all model_call events
            "total":  int,            # max of model_call.tokens_used_total
        },
        "tasks": {
            "<task_id>": {
                "status": "in_progress" | "done" | "failed",
                "iterations": int,            # max iter seen on a model_call
                "tokens": int,                # prompt + eval, this task only
                "tool_calls": {"<tool>": int, ...},
                "hook_blocks": int,           # pre_tool blocks for this task
                "judge": {"accepts": int, "rejects": int},
                "failure_reason": str,        # only when status == "failed"
            }
        },
        "tool_histogram":  {"<tool>":  int, ...},
        "hook_outcomes":   {"<hook>":  {"<outcome>": int, ...}, ...},
        "judge":           {"accepts": int, "rejects": int},
        "stop":            {"reason": str, "ts": str},   # absent if no stop yet
    }

Refreshed at every task boundary and at every stop path (see loop.py:
_refresh_summary). Refresh is best-effort — failures must not break the run.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

SUMMARY_VERSION = 1


def _empty_task() -> dict[str, Any]:
    return {
        "status": "in_progress",
        "iterations": 0,
        "tokens": 0,
        "tool_calls": defaultdict(int),
        "hook_blocks": 0,
        "judge": {"accepts": 0, "rejects": 0},
    }


def _iter_events(events_path: Path):
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


def build_from_events(events_path: Path) -> dict[str, Any]:
    tokens = {"prompt": 0, "eval": 0, "total": 0}
    tasks: dict[str, dict[str, Any]] = {}
    tool_histogram: dict[str, int] = defaultdict(int)
    hook_outcomes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    judge = {"accepts": 0, "rejects": 0}
    started_at: str | None = None
    last_event_at: str | None = None
    stop: dict[str, Any] | None = None
    session_id: str | None = None

    def task_for(tid: str) -> dict[str, Any]:
        if tid not in tasks:
            tasks[tid] = _empty_task()
        return tasks[tid]

    for ev in _iter_events(events_path):
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
            session_id = p.get("session_id") or session_id
        elif typ == "stop":
            stop = {"reason": p.get("reason"), "ts": ts}
        elif typ == "model_call":
            pt = int(p.get("prompt_tokens") or 0)
            et = int(p.get("eval_tokens") or 0)
            tokens["prompt"] += pt
            tokens["eval"] += et
            total = p.get("tokens_used_total")
            if isinstance(total, int):
                tokens["total"] = max(tokens["total"], total)
            if tid:
                t = task_for(tid)
                t["tokens"] += pt + et
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
        elif typ == "judge_verdict":
            if p.get("accept"):
                judge["accepts"] += 1
                if tid:
                    task_for(tid)["judge"]["accepts"] += 1
            else:
                judge["rejects"] += 1
                if tid:
                    task_for(tid)["judge"]["rejects"] += 1
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

    out: dict[str, Any] = {
        "version": SUMMARY_VERSION,
        "session_id": session_id,
        "started_at": started_at,
        "last_event_at": last_event_at,
        "tokens": tokens,
        "tasks": tasks,
        "tool_histogram": dict(tool_histogram),
        "hook_outcomes": {k: dict(v) for k, v in hook_outcomes.items()},
        "judge": judge,
    }
    if stop is not None:
        out["stop"] = stop
    return out


def write_summary(events_path: Path, out_path: Path) -> None:
    data = build_from_events(events_path)
    out_path.write_text(json.dumps(data, indent=2))
