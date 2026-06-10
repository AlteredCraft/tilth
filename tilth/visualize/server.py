"""Live session viewer — a stdlib HTTP app over the sessions/ directory.

Read-only: the server only ever opens files under the sessions root, so it is
safe to run alongside an active `tilth run`. Liveness comes from the harness's
own write discipline — `session.log` appends one complete JSON line per event
and `add_tokens`/`set_status` rewrite checkpoint.json — so a byte-offset tail
over events.jsonl plus a checkpoint read per poll is a faithful near-realtime
view with no harness coupling beyond the documented formats.

Routes:
    GET /                                   session index (newest first)
    GET /session/<id>                       live chat view (app shell)
    GET /api/session/<id>/events            incremental fragments; query params
                                            `offset` (byte cursor into
                                            events.jsonl) and `last_task`
                                            (divider state) round-trip from the
                                            previous response
    GET /assets/app.js                      the polling client

Rendering stays server-side in Python (`render.render_events`) — the client
only appends HTML strings, so there is exactly one renderer to maintain.
"""

from __future__ import annotations

import html
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .render import render_events
from .theme import APP_PAGE, CSS, LIST_PAGE

APP_JS_PATH = Path(__file__).parent / "app.js"

# One path component: no separators, no leading dot. A session id never needs
# more, and anything else must not reach the filesystem.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def resolve_session(root: Path, session_id: str) -> Path | None:
    """Map a URL session id to an existing directory under `root`, or None."""
    if not _SESSION_ID_RE.match(session_id):
        return None
    candidate = root / session_id
    return candidate if candidate.is_dir() else None


def read_new_events(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    """Tail events.jsonl from a byte offset; return (events, new_offset).

    Only complete lines are consumed — a partial trailing line stays unread
    until its newline lands, so the cursor never splits a record. An offset
    past EOF means the file was replaced (e.g. `tilth reset` + new run with a
    reused id); restart from 0. Corrupt or blank lines are skipped but still
    advance the cursor, matching the forgiving read the static renderer had.
    """
    if not path.is_file():
        return [], 0
    size = path.stat().st_size
    if offset > size:
        offset = 0
    with path.open("rb") as f:
        f.seek(offset)
        chunk = f.read()
    end = chunk.rfind(b"\n")
    if end < 0:
        return [], offset
    complete = chunk[: end + 1]
    events: list[dict[str, Any]] = []
    for line in complete.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events, offset + len(complete)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def events_payload(
    session_dir: Path, offset: int, last_task: str | None
) -> dict[str, Any]:
    """One polling response: new fragments + the cursor + live session state."""
    events, new_offset = read_new_events(session_dir / "events.jsonl", offset)
    html_chunk, last_task = render_events(events, last_task or None)
    checkpoint = _read_json(session_dir / "checkpoint.json")
    return {
        "session_id": session_dir.name,
        "html": html_chunk if events else "",
        "n_new": len(events),
        "offset": new_offset,
        "last_task": last_task,
        "status": checkpoint.get("status") or "unknown",
        "tokens_used": int(checkpoint.get("tokens_used") or 0),
    }


def list_sessions(root: Path) -> list[dict[str, Any]]:
    """Session index rows, newest first (the id's timestamp prefix sorts)."""
    rows: list[dict[str, Any]] = []
    if not root.is_dir():
        return rows
    for d in sorted(root.iterdir(), reverse=True):
        if not d.is_dir() or not _SESSION_ID_RE.match(d.name):
            continue
        checkpoint = _read_json(d / "checkpoint.json")
        summary = _read_json(d / "summary.json")
        tasks = {"done": 0, "failed": 0, "pending": 0}
        for t in (summary.get("tasks") or {}).values():
            status = t.get("status") if isinstance(t, dict) else None
            tasks[status if status in tasks else "pending"] += 1
        rows.append({
            "id": d.name,
            "status": checkpoint.get("status") or "unknown",
            "tokens_used": int(checkpoint.get("tokens_used") or 0),
            "started_at": summary.get("started_at") or "",
            "last_event_at": summary.get("last_event_at") or "",
            "tasks": tasks,
        })
    return rows


def _render_index(root: Path) -> str:
    rows = list_sessions(root)
    if not rows:
        body = '<div class="empty">No sessions yet — run `tilth run` first.</div>'
    else:
        cells = []
        for r in rows:
            t = r["tasks"]
            task_bits = []
            if t["done"]:
                task_bits.append(f'{t["done"]} done')
            if t["failed"]:
                task_bits.append(f'{t["failed"]} failed')
            if t["pending"]:
                task_bits.append(f'{t["pending"]} pending')
            cells.append(
                '<a class="session-row" href="/session/{id}">'
                '<span class="session-row-id">{id}</span>'
                '<span class="chip chip-{status}">{status}</span>'
                '<span class="meta">{tasks}</span>'
                '<span class="meta">{tokens:,} tokens</span>'
                '<span class="ts">{started}</span>'
                "</a>".format(
                    id=html.escape(r["id"]),
                    status=html.escape(r["status"]),
                    tasks=" · ".join(task_bits) or "—",
                    tokens=r["tokens_used"],
                    started=html.escape(r["started_at"]),
                )
            )
        body = '<div class="session-list">' + "\n".join(cells) + "</div>"
    return LIST_PAGE.format(css=CSS, root=html.escape(str(root)), count=len(rows), body=body)


class _Handler(BaseHTTPRequestHandler):
    sessions_root: Path  # set by make_server on the subclass

    # The viewer shares the user's terminal with the run output; stay quiet.
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        parts = [p for p in url.path.split("/") if p]
        query = parse_qs(url.query)

        if not parts:
            self._send(200, "text/html; charset=utf-8", _render_index(self.sessions_root))
            return
        if parts == ["assets", "app.js"]:
            self._send(200, "text/javascript; charset=utf-8", APP_JS_PATH.read_text())
            return
        if len(parts) == 2 and parts[0] == "session":
            session_dir = resolve_session(self.sessions_root, parts[1])
            if session_dir is None:
                self._send_404()
                return
            page = APP_PAGE.format(css=CSS, session_id=html.escape(session_dir.name))
            self._send(200, "text/html; charset=utf-8", page)
            return
        if (
            len(parts) == 4
            and parts[0] == "api"
            and parts[1] == "session"
            and parts[3] == "events"
        ):
            session_dir = resolve_session(self.sessions_root, parts[2])
            if session_dir is None:
                self._send_404()
                return
            try:
                offset = int((query.get("offset") or ["0"])[0])
            except ValueError:
                offset = 0
            last_task = (query.get("last_task") or [None])[0]
            payload = events_payload(session_dir, max(0, offset), last_task)
            self._send(
                200, "application/json; charset=utf-8", json.dumps(payload)
            )
            return
        self._send_404()

    def _send(self, status: int, content_type: str, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_404(self) -> None:
        self._send(404, "text/plain; charset=utf-8", "not found\n")


def make_server(
    sessions_root: Path, host: str = "127.0.0.1", port: int = 8765
) -> ThreadingHTTPServer:
    """Build the server (not yet serving). Port 0 picks an ephemeral port —
    used by tests; `server_port` carries the bound value either way."""
    handler = type("Handler", (_Handler,), {"sessions_root": sessions_root})
    return ThreadingHTTPServer((host, port), handler)


def serve(sessions_root: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Serve until interrupted. Binding is loopback-only by default — the
    viewer exposes full prompts and diffs, so it is not meant for the LAN."""
    server = make_server(sessions_root, host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
