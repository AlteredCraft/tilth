"""Live session viewer over the sessions/ directory.

Standalone — no harness coupling beyond the events.jsonl / checkpoint.json
formats documented in session.py. `serve()` runs a read-only stdlib HTTP app
(see server.py); rendering is server-side Python (render.py), shared by every
view, so there is exactly one renderer.
"""

from __future__ import annotations

from .render import render_events
from .server import serve

__all__ = ["render_events", "serve"]
