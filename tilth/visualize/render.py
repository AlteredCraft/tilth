from __future__ import annotations

import html
import json
from collections.abc import Callable
from typing import Any


def render_events(
    events: list[dict[str, Any]], last_task: str | None = None
) -> tuple[str, str | None]:
    """Render events to HTML fragments, inserting a task divider on task change.

    `last_task` carries the divider state across chunks so incremental
    rendering (the live server polling new lines) produces output identical to
    a one-shot render of the same events. Returns (html, last_task) — feed the
    returned `last_task` into the next chunk's call.
    """
    parts: list[str] = []
    for ev in events:
        payload = ev.get("payload") or {}
        task_id = payload.get("task_id")
        if task_id and task_id != last_task:
            parts.append(_task_divider(task_id))
            last_task = task_id
        parts.append(_render_event(ev))
    return "\n".join(parts), last_task


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
    iter_n = p.get("iter", "?")
    pt = int(p.get("prompt_tokens", 0) or 0)
    et = int(p.get("eval_tokens", 0) or 0)
    total = int(p.get("tokens_used_total", 0) or 0)
    health = p.get("health")
    unhealthy = health is not None and health != "ok"
    badges = f'<span class="badge">iter {html.escape(str(iter_n))}</span>'
    if unhealthy:
        attempt = p.get("call_attempt")
        label = f"{health} · attempt {attempt}" if attempt else str(health)
        badges += f'<span class="badge badge-bad">{html.escape(label)}</span>'
    strip = (
        '<div class="meta-strip">'
        f'{badges}'
        f'<span class="meta">prompt {pt:,} · eval {et:,} · total {total:,}</span>'
        f'<span class="ts">{html.escape(ts)}</span>'
        '</div>'
    )
    if unhealthy:
        detail = (p.get("health_detail") or "").strip()
        if detail:
            strip += f'<div class="meta">{html.escape(detail)}</div>'
    reasoning = _reasoning_text(p)
    if not reasoning:
        return strip
    return strip + (
        '<details class="reasoning">'
        '<summary>reasoning</summary>'
        f'<div class="reasoning-body">{html.escape(reasoning)}</div>'
        '</details>'
    )


def _render_nudge(_typ: str, ts: str, p: dict[str, Any]) -> str:
    """A harness-injected corrective message — part of the conversation the
    model actually saw, so it must appear in the chat view."""
    content = (p.get("content") or "").strip()
    title = f"nudge · {p.get('kind', '')}"
    return _bubble(
        side="tool",
        title=title,
        ts=ts,
        body=f'<div class="prose">{html.escape(content)}</div>' if content else "",
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


def _render_evaluator_verdict(_typ: str, ts: str, p: dict[str, Any]) -> str:
    """Render Phase 1's structured `evaluator_verdict` event.

    Surfaces the load-bearing fields — rejection_category, concern, evidence,
    next_step — so the post-run visual review matches the post-run jq review.
    """
    verdict = (p.get("verdict") or "").lower()
    accept = verdict == "accept"
    concern = (p.get("concern") or "").strip()
    category = (p.get("rejection_category") or "").strip()
    next_step = (p.get("next_step") or "").strip()
    evidence = p.get("evidence") or []

    label = "accepts" if accept else "rejects"
    if not accept and category:
        label = f"rejects · {category}"

    body_parts: list[str] = []
    if concern:
        body_parts.append(f'<div class="prose">{html.escape(concern)}</div>')
    if evidence:
        items = "".join(
            f'<li><code>{html.escape(str(e))}</code></li>' for e in evidence
        )
        body_parts.append(f'<ul class="evidence">{items}</ul>')
    if not accept and next_step:
        body_parts.append(
            f'<div class="next-step"><strong>Next step:</strong> '
            f'{html.escape(next_step)}</div>'
        )

    return _bubble(
        side="evaluator",
        title=f"evaluator {label}",
        ts=ts,
        body="".join(body_parts),
    )


def _render_task_done(_typ: str, ts: str, p: dict[str, Any]) -> str:
    summary = (p.get("summary") or "").strip()
    body = f'<div class="prose">{html.escape(summary)}</div>' if summary else ""
    return _card("task done", ts, body, kind="ok")


def _render_task_failed(_typ: str, ts: str, p: dict[str, Any]) -> str:
    return _card(f"task failed · {p.get('reason', '')}", ts, "", kind="bad")


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
    "nudge": _render_nudge,
    "tool_call": _render_tool_call,
    "tool_result": _render_tool_result,
    "pre_tool_block": _render_pre_tool_block,
    "evaluator_verdict": _render_evaluator_verdict,
    "task_done": _render_task_done,
    "task_failed": _render_task_failed,
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
