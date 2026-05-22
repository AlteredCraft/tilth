"""Inline CSS and HTML page template for the visualizer.

CSS lives in `theme.css` so the editor lints and highlights it. PAGE stays
inline because it's only a handful of lines and shares no contract with
external tooling.
"""

from __future__ import annotations

from pathlib import Path

CSS = (Path(__file__).parent / "theme.css").read_text()

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
