"""Inline CSS and HTML page shells for the visualizer app.

CSS lives in `theme.css` so the editor lints and highlights it. The shells stay
inline because they're a handful of lines and share no contract with external
tooling. Both pages inline the CSS (self-contained, no asset round-trip); the
session page additionally loads `/assets/app.js`, the polling client.

`load_css()` re-reads the file on each call so stylesheet edits hot-reload on
the next page load, matching how the server serves `app.js`. The read is on the
page-load path, not the 1s poll, so the cost is negligible.
"""

from __future__ import annotations

from pathlib import Path

CSS_PATH = Path(__file__).parent / "theme.css"


def load_css() -> str:
    return CSS_PATH.read_text()

APP_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>tilth · {session_id}</title>
<style>{css}</style>
</head>
<body data-session="{session_id}">
<div class="container">
  <header class="page-head">
    <div>
      <h1><a class="home-link" href="/">Tilth</a></h1>
      <span class="session-id">session: {session_id}</span>
    </div>
    <div class="live-chips">
      <span class="chip" id="chip-status">…</span>
      <span class="chip" id="chip-tokens"></span>
      <span class="chip" id="chip-count"></span>
    </div>
  </header>
  <section class="panel limits-panel" id="limits-panel" hidden>
    <h2>Limit utilization</h2>
    <div class="panel-sub">how close this run is to its configured caps</div>
    <div class="limit-group">
      <div class="limit-group-label">Per session</div>
      <div class="meters" id="session-meters"></div>
    </div>
    <div class="limit-group" id="task-limit-group" hidden>
      <div class="limit-group-label" id="task-limit-label">Per task</div>
      <div class="meters" id="task-meters"></div>
    </div>
  </section>
  <section class="stat-band" id="stat-band" hidden>
    <div class="stat">
      <div class="stat-label">Tokens</div>
      <div class="stat-value" id="stat-tokens">—</div>
      <div class="split-bar" id="stat-tokens-bar"></div>
      <div class="stat-sub" id="stat-tokens-sub"></div>
    </div>
    <div class="stat">
      <div class="stat-label">Cost</div>
      <div class="stat-value" id="stat-cost">—</div>
      <div class="stat-sub" id="stat-cost-sub"></div>
    </div>
    <div class="stat">
      <div class="stat-label">Model calls</div>
      <div class="stat-value" id="stat-calls">—</div>
      <div class="stat-sub" id="stat-calls-sub"></div>
    </div>
    <div class="stat">
      <div class="stat-label">Tool calls</div>
      <div class="stat-value" id="stat-tools">—</div>
      <div class="stat-sub" id="stat-tools-sub"></div>
    </div>
    <div class="stat">
      <div class="stat-label">Verdicts</div>
      <div class="stat-value" id="stat-verdicts">—</div>
      <div class="stat-sub" id="stat-verdicts-sub"></div>
    </div>
    <div class="stat">
      <div class="stat-label">Hook blocks</div>
      <div class="stat-value" id="stat-blocks">—</div>
      <div class="stat-sub" id="stat-blocks-sub"></div>
    </div>
    <div class="stat">
      <div class="stat-label">Wall clock</div>
      <div class="stat-value" id="stat-clock">—</div>
      <div class="stat-sub" id="stat-clock-sub"></div>
    </div>
  </section>

  <section class="panel" id="timeline-panel" hidden>
    <div class="panel-head">
      <div>
        <h2>Session timeline</h2>
        <div class="panel-sub">task spans · iteration ticks · verdict markers</div>
      </div>
      <span class="timeline-hint" id="timeline-hint">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
          stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path
          d="M3 3l7.07 16.97 2.51-7.39 7.39-2.51L3 3z"/><path d="M13 13l6 6"/></svg>
        drag to zoom
      </span>
      <button type="button" class="timeline-reset" id="timeline-reset" hidden>Reset view</button>
    </div>
    <div class="gantt" id="gantt"></div>
    <div class="gantt-axis" id="gantt-axis"></div>
  </section>

  <section class="panel" id="pressure-panel" hidden>
    <h2>Context pressure</h2>
    <div class="panel-sub">prompt tokens per model call · resets at task boundaries</div>
    <div class="bars" id="bars"><span class="y-hint" id="bars-max"></span></div>
    <div class="chart-legend" id="bars-legend"></div>
  </section>

  <div class="tail-head">
    <div class="tail-head-row">
      <h2>Communication</h2>
      <span class="filter-count" id="filter-count"></span>
    </div>
    <div class="filters">
      <button class="preset active" data-mode="everything">Everything</button>
      <button class="preset" data-mode="dialogue">Worker ↔ Evaluator</button>
      <button class="preset" data-mode="problems">Problems</button>
      <span class="filter-div"></span>
      <button class="fchip on" data-kind="worker">worker</button>
      <button class="fchip on" data-kind="tool">tools</button>
      <button class="fchip on" data-kind="evaluator">evaluator</button>
      <button class="fchip on" data-kind="harness">harness</button>
    </div>
  </div>

  <main id="events"></main>
</div>
<nav class="float-nav">
  <button id="jump-top" class="float-btn" title="Jump to the top">↑ top</button>
  <button id="jump-bottom" class="float-btn" title="Jump to the newest event">↓ bottom</button>
  <button id="follow-toggle" class="float-btn" aria-pressed="false"
          title="Keep the view pinned to the newest event">follow</button>
</nav>
<script src="/assets/app.js"></script>
</body>
</html>
"""

LIST_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="10">
<title>tilth · sessions</title>
<style>{css}</style>
</head>
<body>
<div class="container">
  <header class="page-head">
    <div>
      <h1>Tilth sessions</h1>
      <span class="session-id">{root}</span>
    </div>
    <div class="count">{count} session(s)</div>
  </header>
  <main>
    {body}
  </main>
</div>
</body>
</html>
"""
