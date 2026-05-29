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
    seed_meta = _read_seed_meta(session_dir / "seed-meta.json")

    parts: list[str] = []
    if seed_meta is not None:
        parts.append(_render_seed_panel(seed_meta))
    if not events:
        parts.append('<div class="empty">No events recorded yet.</div>')
    else:
        parts.append(_render_body(events))

    return PAGE.format(
        session_id=html.escape(session_id),
        body="\n".join(parts),
        css=CSS,
        count=len(events),
    )


def _read_seed_meta(path: Path) -> dict[str, Any] | None:
    """Best-effort load of seed-meta.json. Absent or malformed → None (skip panel)."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


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
    iter_n = p.get("iter", "?")
    pt = int(p.get("prompt_tokens", 0) or 0)
    et = int(p.get("eval_tokens", 0) or 0)
    total = int(p.get("tokens_used_total", 0) or 0)
    strip = (
        '<div class="meta-strip">'
        f'<span class="badge">iter {html.escape(str(iter_n))}</span>'
        f'<span class="meta">prompt {pt:,} · eval {et:,} · total {total:,}</span>'
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
        side="judge",
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


def _render_proposed_learnings(_typ: str, ts: str, p: dict[str, Any]) -> str:
    if not p.get("emitted"):
        return _card(
            f"no learning proposed · {p.get('reason', '')}", ts, "", kind="dim",
        )
    entry = p.get("entry", "")
    return _card(
        "proposed learning",
        ts,
        f'<div class="prose">{html.escape(entry)}</div>',
        kind="info",
    )


def _render_session_prepared(_typ: str, ts: str, p: dict[str, Any]) -> str:
    """Minimal chronological marker for when the interview finished.

    The rich detail (TL;DR, open questions, blockers) lives in the seed-meta
    panel above the timeline — this card just pins the moment in time so the
    visualizer's event sequence stays continuous from interview to run.
    """
    bits = [
        f"{p.get('prd_entries', '?')} tasks",
        f"{p.get('test_files', '?')} tests",
    ]
    tokens = p.get("tokens_used")
    if isinstance(tokens, int):
        bits.append(f"{tokens:,} tokens")
    model = p.get("interviewer_model")
    if model:
        bits.append(html.escape(str(model)))
    return _card("seed prepared", ts, " · ".join(bits), kind="info")


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
    "session_prepared": _render_session_prepared,
    "context_reset": _render_context_reset,
    "model_call": _render_model_call,
    "tool_call": _render_tool_call,
    "tool_result": _render_tool_result,
    "pre_tool_block": _render_pre_tool_block,
    "validator_run": _render_validator_run,
    "evaluator_verdict": _render_evaluator_verdict,
    "task_done": _render_task_done,
    "task_failed": _render_task_failed,
    "proposed_learnings": _render_proposed_learnings,
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


# --- seed-meta panel -------------------------------------------------------

def _render_seed_panel(meta: dict[str, Any]) -> str:
    """Context panel rendered above the chat timeline.

    `seed-meta.json` is the interview audit trail (`tilth prep-feature`'s
    structured output). It carries the TL;DR, open questions, blockers,
    and scope notes — everything the human reviewer wants surfaced before
    they read the run's per-task events.
    """
    sections: list[str] = []

    tldr = (meta.get("tldr") or "").strip()
    if tldr:
        sections.append(
            '<div class="seed-panel-section">'
            '<p class="seed-panel-section-title">TL;DR</p>'
            f'<div class="seed-panel-tldr">{html.escape(tldr)}</div>'
            "</div>"
        )

    blockers = _string_list(meta.get("blockers"))
    if blockers:
        items = "".join(f"<li>{html.escape(b)}</li>" for b in blockers)
        sections.append(
            '<div class="seed-panel-section blockers">'
            '<p class="seed-panel-section-title">Blockers / contradictions</p>'
            f'<ul>{items}</ul>'
            "</div>"
        )

    open_qs = _string_list(meta.get("open_questions"))
    if open_qs:
        items = "".join(f"<li>{html.escape(q)}</li>" for q in open_qs)
        sections.append(
            '<div class="seed-panel-section">'
            '<p class="seed-panel-section-title">Open questions</p>'
            f'<ul>{items}</ul>'
            "</div>"
        )

    scope = (meta.get("scope_notes") or "").strip()
    if scope:
        sections.append(
            '<div class="seed-panel-section">'
            '<p class="seed-panel-section-title">Scope notes</p>'
            f'<div class="seed-panel-scope">{html.escape(scope)}</div>'
            "</div>"
        )

    head = (
        '<div class="seed-panel-head">'
        '<span class="seed-panel-title">Seed</span>'
        f'<span class="seed-panel-meta">{html.escape(_seed_meta_summary(meta))}</span>'
        "</div>"
    )

    return f'<section class="seed-panel">{head}{"".join(sections)}</section>'


def _string_list(value: Any) -> list[str]:
    """Coerce a seed-meta field into a clean list of non-empty strings.

    Strings get rejected even though they're iterable — we want a list of
    bullet items, not a list of characters.
    """
    if not isinstance(value, list):
        return []
    return [s.strip() for s in value if isinstance(s, str) and s.strip()]


def _seed_meta_summary(meta: dict[str, Any]) -> str:
    bits: list[str] = []
    model = meta.get("interviewer_model")
    if isinstance(model, str) and model.strip():
        bits.append(model.strip())
    tokens = meta.get("tokens") or {}
    if isinstance(tokens, dict):
        total = tokens.get("total")
        if isinstance(total, int):
            bits.append(f"{total:,} tokens")
    started = meta.get("started_at")
    ended = meta.get("ended_at")
    if started and ended:
        bits.append(f"{started} → {ended}")
    return " · ".join(bits) if bits else "interview audit trail"
