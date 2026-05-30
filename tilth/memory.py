"""Memory plumbing — load the four channels into a fresh prompt each task.

The four channels (per Osmani's self-improving agents post):
    AGENTS.md       semantic knowledge — user-owned project conventions; read-only to Tilth.
                    Lives in the user's workspace (the source repo).
    git history     atomic commits — accessed via bash, not loaded here
    progress.txt    chronological journal — we inject a tail (last N lines).
                    Lives under sessions/<id>/ — a harness-owned runtime artifact,
                    never written into the workspace.
    prd.json        machine-readable task list — caller picks the next task.
                    Also under sessions/<id>/ for the same reason.

Phase 4 (visibility expansion) widens the worker's view with three more
sections, all *about the work* rather than harness mechanics: the full feature
plan (every task, collapsed — so the worker doesn't pre-empt later tasks), the
seed context (curated from seed-meta.json), and the worker's own task ledger
(the evaluator's prior verdicts on this task — its payoff is on resume).

This module is the place where context is *rebuilt from disk* on each task. That's
what makes "context resets, not just compaction" work — the durable artifacts on
disk are the source of truth, not the prior conversation.

Every load returns a manifest alongside the text so the harness can emit a
`memory_load` event — observability of what the agent actually saw, including
truncation and a content hash to spot drift between tasks.

A separate write path — `append_proposed_learning` — collects the self-improvement
step's per-task observations into `sessions/<id>/proposed-learnings.md`. That file
is a session output for the user (and a future end-of-session hook) to review;
it is never read by the worker or judge.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .verdict import format_ledger_section

PROGRESS_TAIL_LINES = 30
AGENTS_MD_MAX_CHARS = 8_000
PROGRESS_MAX_CHARS = 4_000
# Phase 4 visibility expansion. Per-field injection caps, separate from the
# `prompt_assembled` *log* cap (loop.PROMPT_ASSEMBLED_CHAR_CAP). Keep these
# tight so the assembled worker prompt stays legible and under the log cap.
FULL_PRD_MAX_CHARS = 6_000
SEED_META_MAX_CHARS = 4_000

# The worker sees its *own* task ledger (the evaluator's prior verdicts on this
# task). Reuses verdict.format_ledger_section with a header that names the
# source so the worker reads it as feedback, not as its own notes.
WORKER_LEDGER_HEADER = "## Prior iterations on this task (from the evaluator)"

# Interview bookkeeping the worker doesn't need — only the feature-shaping
# fields are injected, in this order.
_SEED_META_FIELDS = ("tldr", "scope_notes", "blockers", "open_questions")
_SEED_META_TITLES = {
    "tldr": "TL;DR",
    "scope_notes": "Scope notes",
    "blockers": "Blockers / contradictions",
    "open_questions": "Open questions",
}

PROPOSED_LEARNINGS_HEADER = """# Proposed learnings — session {session_id}

Tilth's self-improvement step collected these during the run as candidates worth
persisting. They are NOT applied anywhere automatically — they are observations
the worker made about this codebase that might be worth keeping. Review and
decide which (if any) belong in your AGENTS.md, your team docs, or anywhere
else. The end-of-session findings hook will eventually assist with this.
"""


def _hash8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _load_agents_md(workspace: Path) -> tuple[str, dict[str, Any]]:
    p = workspace / "AGENTS.md"
    if not p.is_file():
        return "", {"present": False, "chars": 0, "truncated": False, "sha256_8": ""}
    raw = p.read_text()
    truncated = len(raw) > AGENTS_MD_MAX_CHARS
    if truncated:
        text = raw[:AGENTS_MD_MAX_CHARS] + "\n\n[... AGENTS.md truncated; consider compacting it]"
    else:
        text = raw
    return text, {
        "present": True,
        "chars": len(raw),
        "truncated": truncated,
        "sha256_8": _hash8(raw),
    }


def _load_progress_tail(session_dir: Path) -> tuple[str, dict[str, Any]]:
    p = session_dir / "progress.txt"
    if not p.is_file():
        return "", {"present": False, "chars": 0, "lines": 0, "truncated": False}
    raw_lines = p.read_text().splitlines()
    tail = raw_lines[-PROGRESS_TAIL_LINES:]
    text = "\n".join(tail)
    char_truncated = len(text) > PROGRESS_MAX_CHARS
    if char_truncated:
        text = text[-PROGRESS_MAX_CHARS:]
    return text, {
        "present": True,
        "chars": len(text),
        "lines": len(tail),
        "truncated": char_truncated or len(raw_lines) > PROGRESS_TAIL_LINES,
    }


def load_agents_md(workspace: Path) -> str:
    """Public single-string accessor — used outside the per-task prompt build."""
    return _load_agents_md(workspace)[0]


def load_progress_tail(session_dir: Path) -> str:
    return _load_progress_tail(session_dir)[0]


def append_progress(session_dir: Path, line: str) -> None:
    p = session_dir / "progress.txt"
    with p.open("a") as f:
        f.write(line.rstrip() + "\n")


def append_proposed_learning(
    session_dir: Path, task_id: str, task_title: str, entry: str
) -> None:
    """Append a proposed learning to sessions/<id>/proposed-learnings.md.

    Creates the file with a header on first append. The entry is added as a
    bullet under a task-tagged section. The file is never read by the worker
    or judge — it is a session output for the user (and the future hook).
    """
    p = session_dir / "proposed-learnings.md"
    if not p.is_file():
        p.write_text(PROPOSED_LEARNINGS_HEADER.format(session_id=session_dir.name))
    block = f"\n## From {task_id} — {task_title}\n\n- {entry.strip()}\n"
    with p.open("a") as f:
        f.write(block)


def _render_full_prd(
    prd: list[dict[str, Any]] | None, current_task_id: str
) -> tuple[str, dict[str, Any]]:
    """Render every task in the feature plan, collapsed, as worker *context*.

    The worker's own task is also shown in detail elsewhere; this section is
    the surrounding plan — what came before, what comes after — so the worker
    understands *why* not to build ahead of its task (the F9 friction). It is
    context, not a worklist. Empty/absent prd → ("", absent-manifest).
    """
    tasks = prd or []
    if not tasks:
        return "", {"present": False, "chars": 0, "truncated": False, "n_tasks": 0}

    lines = [
        "## Full feature plan (all tasks — context, not work to do)",
        "",
        "These are every task in this feature, in order. Only the task below"
        " under **Your task** is yours to build now; the rest are here so you"
        " understand the whole and don't pre-empt later tasks.",
        "",
    ]
    for t in tasks:
        tid = t.get("id", "?")
        status = t.get("status", "pending")
        if tid == current_task_id:
            # Detailed in full under "Your task" below — don't repeat it here.
            lines += [
                f"### {tid} — {t.get('title', '')}  [{status}]  ← **your task** "
                "(full detail below)",
                "",
            ]
            continue
        lines.append(f"### {tid} — {t.get('title', '')}  [{status}]")
        desc = (t.get("description") or "").strip()
        if desc:
            lines.append(desc)
        for c in t.get("acceptance_criteria") or []:
            lines.append(f"- {c}")
        lines.append("")
    text = "\n".join(lines).rstrip()
    truncated = len(text) > FULL_PRD_MAX_CHARS
    if truncated:
        text = text[:FULL_PRD_MAX_CHARS] + "\n... [full plan truncated]"
    return text, {
        "present": True,
        "chars": len(text),
        "truncated": truncated,
        "n_tasks": len(tasks),
    }


def _load_seed_meta(session_dir: Path) -> tuple[str, dict[str, Any]]:
    """Curate seed-meta.json into worker-facing context (Phase 4).

    seed-meta.json is the interview audit trail. The worker only needs the
    feature-shaping fields (TL;DR, scope notes, blockers, open questions) — not
    the interview bookkeeping (model, tokens, timestamps). Best-effort: absent
    or malformed → ("", absent-manifest), never raises.
    """
    path = session_dir / "seed-meta.json"
    absent = {"present": False, "chars": 0, "truncated": False, "fields": []}
    if not path.is_file():
        return "", absent
    try:
        meta = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return "", absent
    if not isinstance(meta, dict):
        return "", absent

    lines = ["## Seed context (why this feature was scoped this way)", ""]
    used: list[str] = []
    for field in _SEED_META_FIELDS:
        value = meta.get(field)
        if isinstance(value, str) and value.strip():
            lines += [f"### {_SEED_META_TITLES[field]}", value.strip(), ""]
            used.append(field)
        elif isinstance(value, list):
            items = [s.strip() for s in value if isinstance(s, str) and s.strip()]
            if items:
                lines.append(f"### {_SEED_META_TITLES[field]}")
                lines += [f"- {item}" for item in items]
                lines.append("")
                used.append(field)
    if not used:
        return "", absent
    text = "\n".join(lines).rstrip()
    truncated = len(text) > SEED_META_MAX_CHARS
    if truncated:
        text = text[:SEED_META_MAX_CHARS] + "\n... [seed context truncated]"
    return text, {
        "present": True,
        "chars": len(text),
        "truncated": truncated,
        "fields": used,
    }


def build_user_prompt(
    task: dict[str, Any],
    workspace: Path,
    session_dir: Path,
    *,
    prd: list[dict[str, Any]] | None = None,
    own_ledger: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Assemble the user-side prompt and a manifest of what was loaded.

    AGENTS.md is read from `workspace` (user-owned, in the source repo). The
    progress tail is read from `session_dir` (harness-owned runtime journal).

    Phase 4 widens what the worker sees: the full feature plan (`prd`), the
    seed context (`seed-meta.json` under `session_dir`), and its own task
    ledger (`own_ledger` — the evaluator's prior verdicts on this task, read by
    the caller via `session.read_ledger`). All three are *about the work*, not
    harness mechanics; they ride the existing `prompt_assembled` capture.

    Returns (prompt, manifest). The manifest is suitable as the payload for a
    `memory_load` event — it describes which channels were present, their
    char counts, whether anything was truncated, and a short content hash.
    """
    agents_md, agents_meta = _load_agents_md(workspace)
    progress_tail, progress_meta = _load_progress_tail(session_dir)
    full_prd, prd_meta = _render_full_prd(prd, task["id"])
    seed_meta_text, seed_meta_meta = _load_seed_meta(session_dir)
    ledger_text = format_ledger_section(own_ledger or [], WORKER_LEDGER_HEADER)

    parts: list[str] = []

    if agents_md.strip():
        parts += [
            "## Project context (AGENTS.md)",
            "",
            agents_md.rstrip(),
            "",
        ]

    if progress_tail.strip():
        parts += [
            "## Recent progress (last entries from progress.txt)",
            "",
            progress_tail,
            "",
        ]

    if full_prd:
        parts += [full_prd, ""]

    if seed_meta_text:
        parts += [seed_meta_text, ""]

    parts += [
        "## Your task",
        "",
        f"**ID:** {task['id']}",
        f"**Title:** {task['title']}",
        "",
        task.get("description", "").strip(),
    ]

    criteria = task.get("acceptance_criteria") or []
    if criteria:
        parts += ["", "### Acceptance criteria"]
        parts += [f"- {c}" for c in criteria]

    if ledger_text:
        parts += ["", ledger_text]

    parts += [
        "",
        f"**Workspace root:** {workspace}",
        "",
        "Begin work now. When the work is complete and verified, present it by"
        " calling `submit_case`.",
    ]
    prompt = "\n".join(parts)
    manifest = {
        "channels": {
            "agents_md": agents_meta,
            "progress_tail": progress_meta,
            "full_prd": prd_meta,
            "seed_meta": seed_meta_meta,
            "own_ledger": {
                "present": bool(ledger_text),
                "chars": len(ledger_text),
                "entries": len(own_ledger or []),
            },
        },
        "user_prompt_chars": len(prompt),
    }
    return prompt, manifest
