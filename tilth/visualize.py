"""Render a session's events.jsonl as an HTML chat conversation.

Standalone — no harness coupling beyond the events.jsonl format documented in
session.py. Output is a single self-contained HTML file (inline CSS, no JS).
"""

from __future__ import annotations

import html
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any


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

    return _PAGE.format(
        session_id=html.escape(session_id),
        body=body,
        css=_CSS,
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
    iter_n = p.get("iter", "?")
    pt = int(p.get("prompt_tokens", 0) or 0)
    et = int(p.get("eval_tokens", 0) or 0)
    total = int(p.get("tokens_used_total", 0) or 0)
    return (
        '<div class="meta-strip">'
        f'<span class="badge">iter {html.escape(str(iter_n))}</span>'
        f'<span class="meta">prompt {pt:,} · eval {et:,} · total {total:,}</span>'
        f'<span class="ts">{html.escape(ts)}</span>'
        '</div>'
    )


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


# --- presentation ----------------------------------------------------------

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #f7f6f2;
  --fg: #1c1c1e;
  --muted: #6b7280;
  --line: #e5e2da;
  --card: #ffffff;
  --agent: #eef3ff;
  --agent-line: #c7d6f5;
  --agent-fg: #1e3a8a;
  --tool: #f3f1ec;
  --tool-line: #d8d4c8;
  --tool-fg: #57534e;
  --judge: #f7efe3;
  --judge-line: #e1c79b;
  --judge-fg: #92400e;
  --ok: #15803d;
  --ok-bg: #e6f4ec;
  --bad: #b91c1c;
  --bad-bg: #fbe7e7;
  --code-bg: #1f2227;
  --code-fg: #e4e6eb;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1015;
    --fg: #e6e6e6;
    --muted: #9ca3af;
    --line: #26272d;
    --card: #1a1b21;
    --agent: #182236;
    --agent-line: #2e4677;
    --agent-fg: #93b4ff;
    --tool: #1d1e23;
    --tool-line: #34353c;
    --tool-fg: #b6b6ba;
    --judge: #271f15;
    --judge-line: #6b5429;
    --judge-fg: #f0c987;
    --ok: #4ade80;
    --ok-bg: #112318;
    --bad: #fb7185;
    --bad-bg: #2a1216;
    --code-bg: #0a0b0f;
    --code-fg: #e6e6e6;
  }
}

* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--fg);
  font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        "Helvetica Neue", Arial, sans-serif;
}

.container {
  max-width: 920px;
  margin: 0 auto;
  padding: 32px 24px 96px;
}

header.page-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid var(--line);
  padding-bottom: 16px;
  margin-bottom: 28px;
}
header.page-head h1 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  letter-spacing: -0.01em;
}
header.page-head .session-id {
  display: block;
  color: var(--muted);
  font: 12px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
  margin-top: 2px;
}
header.page-head .count {
  color: var(--muted);
  font-size: 12px;
}

.empty {
  color: var(--muted);
  text-align: center;
  padding: 64px 0;
}

.divider {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 36px 0 14px;
  color: var(--muted);
  font: 600 11px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
  text-transform: uppercase;
  letter-spacing: 0.1em;
}
.divider::before, .divider::after {
  content: "";
  flex: 1;
  height: 1px;
  background: var(--line);
}
.divider span {
  background: var(--card);
  border: 1px solid var(--line);
  padding: 5px 12px;
  border-radius: 999px;
  color: var(--fg);
}

.meta-strip {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 14px 0 4px;
  font: 11px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--muted);
}
.meta-strip .badge {
  background: var(--line);
  color: var(--fg);
  padding: 2px 8px;
  border-radius: 999px;
  font-weight: 600;
}
.meta-strip .ts { margin-left: auto; }

.bubble {
  border-radius: 12px;
  padding: 12px 14px;
  margin: 8px 0;
  border: 1px solid;
  max-width: 92%;
}
.bubble-agent {
  background: var(--agent);
  border-color: var(--agent-line);
}
.bubble-agent .bubble-title { color: var(--agent-fg); }

.bubble-tool {
  background: var(--tool);
  border-color: var(--tool-line);
  margin-left: 36px;
}
.bubble-tool .bubble-title { color: var(--tool-fg); }

.bubble-block {
  background: var(--bad-bg);
  border-color: var(--bad);
  margin-left: 36px;
}
.bubble-block .bubble-title { color: var(--bad); }

.bubble-judge {
  background: var(--judge);
  border-color: var(--judge-line);
  margin-left: auto;
}
.bubble-judge .bubble-title { color: var(--judge-fg); }

.bubble-head, .card-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 6px;
}
.bubble-title, .card-title {
  font-weight: 600;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.bubble-body { font-size: 13px; }

.ts {
  margin-left: auto;
  color: var(--muted);
  font: 11px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
}

pre {
  background: var(--code-bg);
  color: var(--code-fg);
  padding: 12px 14px;
  border-radius: 8px;
  overflow-x: auto;
  font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace;
  margin: 6px 0 0;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 480px;
  overflow-y: auto;
}

.prose { white-space: pre-wrap; word-break: break-word; }

.card {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 12px 14px;
  margin: 14px 0;
}
.card-ok { border-color: var(--ok); background: var(--ok-bg); }
.card-ok .card-title { color: var(--ok); }
.card-bad { border-color: var(--bad); background: var(--bad-bg); }
.card-bad .card-title { color: var(--bad); }
.card-dim .card-title { color: var(--muted); }
.card-info .card-title { color: var(--fg); }

.kv {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 4px 16px;
  margin: 0;
  font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
}
.kv dt { color: var(--muted); }
.kv dd { margin: 0; word-break: break-all; }

.checks { margin: 4px 0 0; padding-left: 18px; font-size: 13px; }
.checks li { display: flex; align-items: baseline; gap: 8px; }
.checks li.pass { color: var(--ok); }
.checks li.fail { color: var(--bad); }
.checks .check-mark { font-weight: 700; }
"""

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>tilth · {session_id}</title>
<style>{css}</style>
</head>
<body>
<div class="container">
  <header class="page-head">
    <div>
      <h1>Tilth session</h1>
      <span class="session-id">{session_id}</span>
    </div>
    <div class="count">{count} events</div>
  </header>
  <main>
    {body}
  </main>
</div>
</body>
</html>
"""
