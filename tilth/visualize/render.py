from __future__ import annotations

import html
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .theme import CSS, PAGE


def write_session_html(session_dir: Path) -> Path:
    out = session_dir / "chat.html"
    out.write_text(render_html(session_dir))
    return out


def render_html(session_dir: Path) -> str:
    events = _read_events(session_dir / "events.jsonl")
    session_id = session_dir.name

    if not events:
        body = '<div class="empty">No events recorded yet.</div>'
    else:
        body = _render_body(events)

    return PAGE.format(
        session_id=html.escape(session_id),
        body=body,
        css=CSS,
        count=len(events),
    )


def _read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.is_file():
        return events
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _render_body(events: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    last_task: str | None = None
    for ev in events:
        payload = ev.get("payload") or {}
        task_id = payload.get("task_id")
        if task_id and task_id != last_task:
            parts.append(_task_divider(task_id))
            last_task = task_id
        parts.append(_render_event(ev))
    return "\n".join(parts)


def _render_event(ev: dict[str, Any]) -> str:
    typ = ev.get("type", "")
    ts = ev.get("ts", "")
    payload = ev.get("payload") or {}
    handler = _RENDERERS.get(typ, _render_unknown)
    return handler(typ, ts, payload)


# --- per-event renderers ---------------------------------------------------

def _render_session_start(_typ: str, ts: str, p: dict[str, Any]) -> str:
    body = _kv([
        ("source", p.get("source", "")),
        ("worktree", p.get("worktree", "")),
        ("branch", p.get("branch", "")),
    ])
    return _card("session started", ts, body, kind="info")


def _render_session_resume(_typ: str, ts: str, p: dict[str, Any]) -> str:
    body = _kv([
        ("plan", p.get("plan", "")),
        ("last stop", p.get("last_stop") or "—"),
        ("retried", ", ".join(p.get("retried") or []) or "—"),
        ("pending", ", ".join(p.get("pending") or []) or "—"),
        ("unwound commit", "yes" if p.get("unwound_commit") else "no"),
    ])
    return _card("session resumed", ts, body, kind="info")


def _render_context_reset(_typ: str, ts: str, _p: dict[str, Any]) -> str:
    return _card("context reset", ts, "", kind="dim")


def _render_model_call(_typ: str, ts: str, p: dict[str, Any]) -> str:
    kind = p.get("kind") or "worker"
    iter_n = p.get("iter")
    model = p.get("model") or ""
    pt = int(p.get("prompt_tokens", 0) or 0)
    et = int(p.get("eval_tokens", 0) or 0)
    total = int(p.get("tokens_used_total", 0) or 0)
    if kind == "worker":
        badge_label = f"iter {iter_n}" if iter_n is not None else "worker"
    elif kind == "judge":
        badge_label = f"judge (iter {iter_n})" if iter_n is not None else "judge"
    else:
        badge_label = kind
    meta_bits = [f"prompt {pt:,}", f"eval {et:,}", f"total {total:,}"]
    if model:
        meta_bits.append(html.escape(model))
    strip = (
        '<div class="meta-strip">'
        f'<span class="badge">{html.escape(badge_label)}</span>'
        f'<span class="meta">{" · ".join(meta_bits)}</span>'
        f'<span class="ts">{html.escape(ts)}</span>'
        '</div>'
    )
    reasoning = _reasoning_text(p)
    if not reasoning:
        return strip
    return strip + (
        '<details class="reasoning">'
        '<summary>reasoning</summary>'
        f'<div class="reasoning-body">{html.escape(reasoning)}</div>'
        '</details>'
    )


def _reasoning_text(p: dict[str, Any]) -> str:
    details = p.get("reasoning_details")
    if isinstance(details, list) and details:
        parts = [
            (block.get("text") or "").strip()
            for block in details
            if isinstance(block, dict)
        ]
        joined = "\n\n".join(part for part in parts if part)
        if joined:
            return joined
    flat = p.get("reasoning")
    if isinstance(flat, str) and flat.strip():
        return flat.strip()
    return ""


def _render_tool_call(_typ: str, ts: str, p: dict[str, Any]) -> str:
    tool = p.get("tool", "")
    args = p.get("args") or {}
    pretty = json.dumps(args, indent=2, ensure_ascii=False)
    return _bubble(
        side="agent",
        title=f"tool call · {tool}",
        ts=ts,
        body=f"<pre>{html.escape(pretty)}</pre>",
    )


def _render_tool_result(_typ: str, ts: str, p: dict[str, Any]) -> str:
    tool = p.get("tool", "")
    preview = p.get("result_preview", "") or ""
    chars = int(p.get("result_chars", 0) or 0)
    truncated = chars > len(preview)
    suffix = f" · {chars:,} chars" + (" (truncated)" if truncated else "") if chars else ""
    return _bubble(
        side="tool",
        title=f"result · {tool}{suffix}",
        ts=ts,
        body=f"<pre>{html.escape(preview)}</pre>" if preview else "",
    )


def _render_pre_tool_block(_typ: str, ts: str, p: dict[str, Any]) -> str:
    tool = p.get("tool", "")
    preview = p.get("result_preview", "") or ""
    return _bubble(
        side="block",
        title=f"BLOCKED · {tool}",
        ts=ts,
        body=f"<pre>{html.escape(preview)}</pre>" if preview else "",
    )


def _render_validator_run(_typ: str, ts: str, p: dict[str, Any]) -> str:
    passed = bool(p.get("passed", False))
    results = p.get("results") or []
    items = "".join(
        f'<li class="{"pass" if r.get("passed") else "fail"}">'
        f'{html.escape(r.get("name", ""))}'
        f'<span class="check-mark">{"✓" if r.get("passed") else "✗"}</span>'
        '</li>'
        for r in results
    )
    body = f'<ul class="checks">{items}</ul>' if items else ""
    status = "validators passed" if passed else "validators failed"
    return _card(status, ts, body, kind="ok" if passed else "bad")


def _render_judge_verdict(_typ: str, ts: str, p: dict[str, Any]) -> str:
    accept = bool(p.get("accept", False))
    reasoning = (p.get("reasoning") or "").strip()
    label = "accepts" if accept else "rejects"
    return _bubble(
        side="judge",
        title=f"judge {label}",
        ts=ts,
        body=f'<div class="prose">{html.escape(reasoning)}</div>' if reasoning else "",
    )


def _render_task_done(_typ: str, ts: str, p: dict[str, Any]) -> str:
    summary = (p.get("summary") or "").strip()
    body = f'<div class="prose">{html.escape(summary)}</div>' if summary else ""
    return _card("task done", ts, body, kind="ok")


def _render_task_failed(_typ: str, ts: str, p: dict[str, Any]) -> str:
    return _card(f"task failed · {p.get('reason', '')}", ts, "", kind="bad")


def _render_agents_md_update(_typ: str, ts: str, p: dict[str, Any]) -> str:
    if not p.get("applied"):
        return _card(
            f"AGENTS.md unchanged · {p.get('reason', '')}", ts, "", kind="dim",
        )
    section = p.get("section", "")
    entry = p.get("entry", "")
    return _card(
        f"AGENTS.md ← {section}",
        ts,
        f'<div class="prose">{html.escape(entry)}</div>',
        kind="info",
    )


def _render_commit(_typ: str, ts: str, p: dict[str, Any]) -> str:
    sha = (p.get("sha") or "")[:8]
    return _card(f"committed · {sha}", ts, "", kind="ok")


def _render_stop(_typ: str, ts: str, p: dict[str, Any]) -> str:
    reason = p.get("reason", "")
    err = p.get("error", "")
    body = f'<div class="prose">{html.escape(err)}</div>' if err else ""
    kind = "ok" if reason == "all_done" else "bad"
    return _card(f"stop · {reason}", ts, body, kind=kind)


def _render_unknown(typ: str, ts: str, p: dict[str, Any]) -> str:
    body = f"<pre>{html.escape(json.dumps(p, indent=2, ensure_ascii=False))}</pre>"
    return _card(typ or "event", ts, body, kind="dim")


_RENDERERS: dict[str, Callable[[str, str, dict[str, Any]], str]] = {
    "session_start": _render_session_start,
    "session_resume": _render_session_resume,
    "context_reset": _render_context_reset,
    "model_call": _render_model_call,
    "tool_call": _render_tool_call,
    "tool_result": _render_tool_result,
    "pre_tool_block": _render_pre_tool_block,
    "validator_run": _render_validator_run,
    "judge_verdict": _render_judge_verdict,
    "task_done": _render_task_done,
    "task_failed": _render_task_failed,
    "agents_md_update": _render_agents_md_update,
    "commit": _render_commit,
    "stop": _render_stop,
}


# --- shared building blocks ------------------------------------------------

def _bubble(side: str, title: str, ts: str, body: str) -> str:
    return (
        f'<div class="bubble bubble-{side}">'
        '<div class="bubble-head">'
        f'<span class="bubble-title">{html.escape(title)}</span>'
        f'<span class="ts">{html.escape(ts)}</span>'
        '</div>'
        + (f'<div class="bubble-body">{body}</div>' if body else "")
        + '</div>'
    )


def _card(title: str, ts: str, body: str, kind: str = "info") -> str:
    return (
        f'<div class="card card-{kind}">'
        '<div class="card-head">'
        f'<span class="card-title">{html.escape(title)}</span>'
        f'<span class="ts">{html.escape(ts)}</span>'
        '</div>'
        + (f'<div class="card-body">{body}</div>' if body else "")
        + '</div>'
    )


def _task_divider(task_id: str) -> str:
    return f'<div class="divider"><span>{html.escape(task_id)}</span></div>'


def _kv(rows: list[tuple[str, str]]) -> str:
    items = "".join(
        f'<dt>{html.escape(k)}</dt><dd>{html.escape(str(v))}</dd>'
        for k, v in rows
    )
    return f'<dl class="kv">{items}</dl>'
