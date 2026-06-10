"""Inline CSS and HTML page shells for the visualizer app.

CSS lives in `theme.css` so the editor lints and highlights it. The shells stay
inline because they're a handful of lines and share no contract with external
tooling. Both pages inline the CSS (self-contained, no asset round-trip); the
session page additionally loads `/assets/app.js`, the polling client.
"""

from __future__ import annotations

from pathlib import Path

CSS = (Path(__file__).parent / "theme.css").read_text()

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
      <h1><a class="home-link" href="/">Tilth</a> session</h1>
      <span class="session-id">{session_id}</span>
    </div>
    <div class="live-chips">
      <span class="chip" id="chip-status">…</span>
      <span class="chip" id="chip-tokens"></span>
      <span class="chip" id="chip-count"></span>
    </div>
  </header>
  <main id="events"></main>
  <button id="follow" class="follow-btn" hidden>↓ follow</button>
</div>
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
