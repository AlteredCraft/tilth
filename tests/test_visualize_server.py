"""The live visualizer: a stdlib HTTP app over the sessions/ directory.

Contract pinned here:
- `read_new_events` tails events.jsonl by byte offset: only complete lines are
  consumed (the writer appends line-atomically, but the reader must not choke
  on a partial tail), corrupt/blank lines are skipped without stalling the
  offset, and an offset past EOF (session reset/replaced) restarts from 0.
- `render_events` carries the task-divider state across chunks via `last_task`,
  so incremental rendering produces byte-identical output to a one-shot render.
- Session IDs from the URL never touch the filesystem unvalidated — traversal
  shapes 404.
- The HTTP surface: / lists sessions, /session/<id> serves the app shell,
  /api/session/<id>/events streams fragments + live state, /assets/app.js is
  the poller.
"""

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from tilth.visualize.render import render_events
from tilth.visualize.server import (
    events_payload,
    list_sessions,
    make_server,
    read_new_events,
    resolve_session,
)

# ---- fixtures ------------------------------------------------------------------

EVENTS = [
    {"ts": "2026-06-10T17:00:00Z", "type": "session_start",
     "payload": {"source": "/src", "worktree": "/wt", "branch": "session/x"}},
    {"ts": "2026-06-10T17:00:01Z", "type": "model_call",
     "payload": {"task_id": "T-001", "iter": 1, "prompt_tokens": 10,
                 "eval_tokens": 2, "tokens_used_total": 12, "health": "ok"}},
    {"ts": "2026-06-10T17:00:02Z", "type": "tool_call",
     "payload": {"task_id": "T-001", "iter": 1, "tool": "bash",
                 "args": {"command": "ls"}}},
    {"ts": "2026-06-10T17:00:03Z", "type": "task_done",
     "payload": {"task_id": "T-001", "summary": "done"}},
]


def _write_events(path: Path, events: list[dict]) -> None:
    with path.open("a") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


@pytest.fixture
def sessions_root(tmp_path: Path) -> Path:
    root = tmp_path / "sessions"
    root.mkdir()
    sdir = root / "20260610-170000-aaaaaa"
    sdir.mkdir()
    _write_events(sdir / "events.jsonl", EVENTS)
    (sdir / "checkpoint.json").write_text(json.dumps({
        "session_id": "20260610-170000-aaaaaa",
        "status": "running",
        "tokens_used": 12,
    }))
    (sdir / "summary.json").write_text(json.dumps({
        "tasks": {"T-001": {"status": "done"}, "T-002": {"status": "failed"}},
        "tokens": {"total": 12},
        "started_at": "2026-06-10T17:00:00Z",
    }))
    return root


@pytest.fixture
def session_dir(sessions_root: Path) -> Path:
    return sessions_root / "20260610-170000-aaaaaa"


# ---- read_new_events: byte-offset tail ------------------------------------------

def test_full_read_from_zero(session_dir: Path):
    events, offset = read_new_events(session_dir / "events.jsonl", 0)
    assert [e["type"] for e in events] == [e["type"] for e in EVENTS]
    assert offset == (session_dir / "events.jsonl").stat().st_size


def test_incremental_read_returns_only_new_lines(session_dir: Path):
    path = session_dir / "events.jsonl"
    _, offset = read_new_events(path, 0)
    events, same_offset = read_new_events(path, offset)
    assert events == [] and same_offset == offset

    new_ev = {"ts": "2026-06-10T17:00:04Z", "type": "stop",
              "payload": {"reason": "all_done"}}
    _write_events(path, [new_ev])
    events, new_offset = read_new_events(path, offset)
    assert [e["type"] for e in events] == ["stop"]
    assert new_offset == path.stat().st_size


def test_partial_trailing_line_is_not_consumed(session_dir: Path):
    path = session_dir / "events.jsonl"
    _, offset = read_new_events(path, 0)
    with path.open("a") as f:
        f.write('{"ts": "2026-06-10T17:00:05Z", "type": "sto')  # no newline
    events, same_offset = read_new_events(path, offset)
    assert events == [] and same_offset == offset
    with path.open("a") as f:
        f.write('p", "payload": {"reason": "all_done"}}\n')
    events, new_offset = read_new_events(path, offset)
    assert [e["type"] for e in events] == ["stop"]
    assert new_offset == path.stat().st_size


def test_offset_past_eof_restarts_from_zero(session_dir: Path):
    path = session_dir / "events.jsonl"
    events, offset = read_new_events(path, path.stat().st_size + 999)
    assert [e["type"] for e in events] == [e["type"] for e in EVENTS]
    assert offset == path.stat().st_size


def test_corrupt_and_blank_lines_skipped_without_stalling(session_dir: Path):
    path = session_dir / "events.jsonl"
    _, offset = read_new_events(path, 0)
    with path.open("a") as f:
        f.write("not json at all\n\n")
        f.write(json.dumps({"type": "stop", "payload": {"reason": "all_done"}}) + "\n")
    events, new_offset = read_new_events(path, offset)
    assert [e["type"] for e in events] == ["stop"]
    assert new_offset == path.stat().st_size


def test_missing_file_yields_nothing(tmp_path: Path):
    events, offset = read_new_events(tmp_path / "nope.jsonl", 0)
    assert events == [] and offset == 0


# ---- render_events: divider state carries across chunks --------------------------

def test_incremental_render_matches_one_shot():
    one_shot, _ = render_events(EVENTS, None)
    first, last_task = render_events(EVENTS[:2], None)
    second, _ = render_events(EVENTS[2:], last_task)
    assert first + "\n" + second == one_shot


def test_divider_emitted_once_per_task_run():
    html, last_task = render_events(EVENTS, None)
    assert html.count('class="divider"') == 1
    assert last_task == "T-001"
    # a later chunk for the same task adds no divider
    more, _ = render_events([EVENTS[2]], last_task)
    assert 'class="divider"' not in more
    # a chunk for a new task does
    other = {"type": "model_call", "ts": "t", "payload": {"task_id": "T-002", "iter": 1}}
    more, last = render_events([other], last_task)
    assert 'class="divider"' in more and last == "T-002"


# ---- the XSS boundary: model-authored content must come out escaped ---------------
#
# Event payloads carry *model-authored* strings (reasoning, tool args, case
# summaries). The live client appends server-rendered HTML verbatim
# (insertAdjacentHTML), so the renderer is the one and only escaping boundary —
# a raw payload string reaching the page means an agent could run script in
# the developer's browser and exfiltrate the session log.

INJECTION = '<script>alert(1)</script><img src=x onerror=alert(2)>'


@pytest.mark.parametrize("event", [
    {"type": "model_call", "ts": "t",
     "payload": {"task_id": "T-001", "iter": 1, "health": "provider_error",
                 "health_detail": INJECTION,
                 "reasoning_details": [{"type": "reasoning.text", "text": INJECTION}]}},
    {"type": "tool_call", "ts": "t",
     "payload": {"task_id": "T-001", "tool": INJECTION, "args": {"cmd": INJECTION}}},
    {"type": "tool_result", "ts": "t",
     "payload": {"task_id": "T-001", "tool": "bash", "result_preview": INJECTION,
                 "result_chars": 5}},
    {"type": "evaluator_verdict", "ts": "t",
     "payload": {"task_id": "T-001", "verdict": "reject",
                 "rejection_category": INJECTION, "concern": INJECTION,
                 "evidence": [INJECTION], "next_step": INJECTION}},
    {"type": "nudge", "ts": "t",
     "payload": {"task_id": "T-001", "kind": "no_case", "content": INJECTION}},
    {"type": "task_failed", "ts": "t",
     "payload": {"task_id": INJECTION, "reason": INJECTION}},
    {"type": "never_seen_before_event", "ts": INJECTION,
     "payload": {"task_id": "T-001", "anything": INJECTION}},
])
def test_payload_strings_are_escaped(event):
    rendered, _ = render_events([event], None)
    assert "<script>" not in rendered  # no live tag survives
    assert "<img" not in rendered      # attribute-based vector inert too
    assert "&lt;script&gt;" in rendered  # ...because it was escaped, not dropped


# ---- session resolution -----------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "../outside", "..", "a/b", "/etc", "a\\b", ".hidden", "", "a b",
])
def test_resolve_session_rejects_unsafe_ids(sessions_root: Path, bad: str):
    assert resolve_session(sessions_root, bad) is None


def test_resolve_session_accepts_existing_dir(sessions_root: Path):
    resolved = resolve_session(sessions_root, "20260610-170000-aaaaaa")
    assert resolved == sessions_root / "20260610-170000-aaaaaa"


def test_resolve_session_rejects_missing_dir(sessions_root: Path):
    assert resolve_session(sessions_root, "20990101-000000-ffffff") is None


# ---- events_payload: the polling response -----------------------------------------

def test_events_payload_shape(session_dir: Path):
    p = events_payload(session_dir, 0, None)
    assert p["n_new"] == len(EVENTS)
    assert p["status"] == "running"
    assert p["tokens_used"] == 12
    assert p["last_task"] == "T-001"
    assert 'class="divider"' in p["html"]
    # second poll from the returned cursor: nothing new, state intact
    p2 = events_payload(session_dir, p["offset"], p["last_task"])
    assert p2["n_new"] == 0 and p2["html"] == ""
    assert p2["offset"] == p["offset"]


def test_events_payload_status_unknown_without_checkpoint(sessions_root: Path):
    bare = sessions_root / "20260610-180000-bbbbbb"
    bare.mkdir()
    p = events_payload(bare, 0, None)
    assert p["status"] == "unknown" and p["n_new"] == 0


# ---- list_sessions -----------------------------------------------------------------

def test_list_sessions_newest_first_with_state(sessions_root: Path):
    older = sessions_root / "20260601-000000-cccccc"
    older.mkdir()
    (older / "checkpoint.json").write_text(json.dumps({"status": "all_done"}))
    rows = list_sessions(sessions_root)
    assert [r["id"] for r in rows] == [
        "20260610-170000-aaaaaa", "20260601-000000-cccccc",
    ]
    assert rows[0]["status"] == "running"
    assert rows[0]["tasks"] == {"done": 1, "failed": 1, "pending": 0}
    assert rows[1]["status"] == "all_done"


# ---- HTTP surface ------------------------------------------------------------------

@pytest.fixture
def http_server(sessions_root: Path):
    srv = make_server(sessions_root, host="127.0.0.1", port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()


def _get(srv, path: str) -> tuple[int, str, str]:
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read().decode()
    ctype = resp.getheader("Content-Type") or ""
    conn.close()
    return resp.status, ctype, body


def test_index_lists_sessions(http_server):
    status, ctype, body = _get(http_server, "/")
    assert status == 200 and "text/html" in ctype
    assert "20260610-170000-aaaaaa" in body


def test_session_page_serves_app_shell(http_server):
    status, _, body = _get(http_server, "/session/20260610-170000-aaaaaa")
    assert status == 200
    assert 'data-session="20260610-170000-aaaaaa"' in body
    assert "/assets/app.js" in body


def test_events_api_round_trip(http_server):
    status, ctype, body = _get(
        http_server, "/api/session/20260610-170000-aaaaaa/events?offset=0"
    )
    assert status == 200 and "application/json" in ctype
    data = json.loads(body)
    assert data["n_new"] == len(EVENTS) and data["status"] == "running"
    status, _, body = _get(
        http_server,
        f"/api/session/20260610-170000-aaaaaa/events"
        f"?offset={data['offset']}&last_task={data['last_task']}",
    )
    assert json.loads(body)["n_new"] == 0


def test_app_js_served(http_server):
    status, ctype, _ = _get(http_server, "/assets/app.js")
    assert status == 200 and "javascript" in ctype


@pytest.mark.parametrize("path", [
    "/session/../../etc/passwd",
    "/api/session/../x/events",
    "/api/session/20990101-000000-ffffff/events",
    "/nope",
])
def test_unknown_and_unsafe_paths_404(http_server, path: str):
    status, _, _ = _get(http_server, path)
    assert status == 404
