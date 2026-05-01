"""Render a session's events.jsonl as an HTML chat conversation.

Standalone — no harness coupling beyond the events.jsonl format documented in
session.py. Output is a single self-contained HTML file (inline CSS, no JS).
"""

from __future__ import annotations

from .render import render_html, write_session_html

__all__ = ["render_html", "write_session_html"]
