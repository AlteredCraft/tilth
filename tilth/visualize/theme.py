"""Inline CSS and HTML page template for the visualizer.

Kept separate so the renderer reads as logic, not a 270-line CSS string.
"""

from __future__ import annotations

CSS = """
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

details.reasoning {
  margin: 6px 0 16px;
  padding: 10px 14px;
  border-left: 4px solid var(--agent-fg);
  background: var(--agent);
  border-radius: 0 8px 8px 0;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}
details.reasoning summary {
  cursor: pointer;
  color: var(--agent-fg);
  font: 700 12px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  list-style: none;
  user-select: none;
}
details.reasoning summary::-webkit-details-marker { display: none; }
details.reasoning summary::before {
  content: "▸ ";
  display: inline-block;
}
details.reasoning[open] summary::before {
  content: "▾ ";
}
.reasoning-body {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid var(--agent-line);
  font-style: italic;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--agent-fg);
  font-size: 13px;
  line-height: 1.5;
}

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

PAGE = """<!DOCTYPE html>
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
