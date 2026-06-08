"""Memory plumbing — load the context channels into a fresh prompt each task.

The channels (per Osmani's self-improving agents post):
    AGENTS.md       semantic knowledge — user-owned project conventions; read-only to Tilth.
                    Lives in the user's workspace (the source repo).
    git history     atomic commits — accessed via bash, not loaded here
    progress.txt    chronological journal — we inject a tail (last N lines).
                    Lives under sessions/<id>/ — a harness-owned runtime artifact,
                    never written into the workspace.
    task markdown   the feature, authored in `<workspace>/.tilth/tasks/` (see
                    `tilth/tasks.py`): an `overview.md` (feature "why") plus one
                    `T-NNN-*.md` per task. The caller loads them; this module
                    injects the overview + the full plan (every task collapsed,
                    so the worker doesn't pre-empt later tasks) + the worker's
                    own task ledger (the evaluator's prior verdicts on this task).

This module is the place where context is *rebuilt from disk* on each task. That's
what makes "context resets, not just compaction" work — the durable artifacts on
disk are the source of truth, not the prior conversation.

Every load returns a manifest alongside the text so the harness can emit a
`memory_load` event — observability of what the agent actually saw, including
truncation and a content hash to spot drift between tasks.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .verdict import format_ledger_section

PROGRESS_TAIL_LINES = 30
# Aggregate cap across all configured context files once concatenated — the
# worker/evaluator prompt budget cares about the combined injection, not any
# single file. (Name kept for back-compat with the `agents_md` manifest channel.)
AGENTS_MD_MAX_CHARS = 8_000
# The project-conventions channel. Read in order, first-listed highest priority;
# every present file is concatenated. Overridable via TILTH_CONTEXT_FILES so a
# repo carrying its conventions in CLAUDE.md (Claude Code's convention) is seen.
DEFAULT_CONTEXT_FILES: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md")
PROGRESS_MAX_CHARS = 4_000
# Phase 4 visibility expansion. Per-field injection caps, separate from the
# `prompt_assembled` *log* cap (loop.PROMPT_ASSEMBLED_CHAR_CAP). Keep these
# tight so the assembled worker prompt stays legible and under the log cap.
FULL_PRD_MAX_CHARS = 6_000
OVERVIEW_MAX_CHARS = 4_000

# The worker sees its *own* task ledger (the evaluator's prior verdicts on this
# task). Reuses verdict.format_ledger_section with a header that names the
# source so the worker reads it as feedback, not as its own notes.
WORKER_LEDGER_HEADER = "## Prior iterations on this task (from the evaluator)"


def _hash8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _load_context_files(
    workspace: Path, filenames: Sequence[str]
) -> tuple[str, dict[str, Any]]:
    """Load the configured project-context files from the workspace root, in order.

    Each present file is concatenated (a `### <name>` provenance header is added
    only when more than one file is present, so the single-file case stays a
    clean body). Truncation is applied to the *combined* text against the
    aggregate cap. Returns (text, manifest); the manifest carries the aggregate
    {present, chars, truncated, sha256_8} plus a `files` list with one
    {name, present, chars, sha256_8} entry per configured filename and `loaded`
    (the names actually present, in order) — so `events.jsonl` stays honest
    about what the worker saw.
    """
    file_manifests: list[dict[str, Any]] = []
    blocks: list[tuple[str, str]] = []
    for name in filenames:
        p = workspace / name
        if not p.is_file():
            file_manifests.append(
                {"name": name, "present": False, "chars": 0, "sha256_8": ""}
            )
            continue
        raw = p.read_text()
        file_manifests.append(
            {"name": name, "present": True, "chars": len(raw), "sha256_8": _hash8(raw)}
        )
        blocks.append((name, raw))

    loaded = [name for name, _ in blocks]
    if not blocks:
        return "", {
            "present": False,
            "chars": 0,
            "truncated": False,
            "sha256_8": "",
            "files": file_manifests,
            "loaded": [],
        }

    if len(blocks) == 1:
        combined = blocks[0][1]
    else:
        combined = "\n\n".join(f"### {name}\n\n{raw.rstrip()}" for name, raw in blocks)

    truncated = len(combined) > AGENTS_MD_MAX_CHARS
    if truncated:
        text = combined[:AGENTS_MD_MAX_CHARS] + (
            "\n\n[... project context truncated; consider compacting it]"
        )
    else:
        text = combined
    return text, {
        "present": True,
        "chars": len(combined),
        "truncated": truncated,
        "sha256_8": _hash8(combined),
        "files": file_manifests,
        "loaded": loaded,
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


def load_context_files(
    workspace: Path, filenames: Sequence[str]
) -> tuple[str, list[str]]:
    """Public accessor — used outside the per-task prompt build (evaluator, self-
    improve). Returns (concatenated body, names actually loaded in order)."""
    text, manifest = _load_context_files(workspace, filenames)
    return text, manifest["loaded"]


def load_progress_tail(session_dir: Path) -> str:
    return _load_progress_tail(session_dir)[0]


def append_progress(session_dir: Path, line: str) -> None:
    p = session_dir / "progress.txt"
    with p.open("a") as f:
        f.write(line.rstrip() + "\n")


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


def _render_overview(overview: str | None) -> tuple[str, dict[str, Any]]:
    """Wrap the feature `overview.md` text as a worker-facing context section.

    `overview` is loaded by the caller from `<workspace>/.tilth/tasks/overview.md`
    (see `tilth/tasks.py`). It is the feature-level "why" — goal, scope
    boundaries, reviewer notes — so the worker understands the whole before
    building one slice. Absent/empty → ("", absent-manifest).
    """
    absent = {"present": False, "chars": 0, "truncated": False}
    body = (overview or "").strip()
    if not body:
        return "", absent
    truncated = len(body) > OVERVIEW_MAX_CHARS
    if truncated:
        body = body[:OVERVIEW_MAX_CHARS] + "\n... [overview truncated]"
    text = "\n".join(["## Feature overview (why this feature, what's in/out of scope)", "", body])
    return text, {"present": True, "chars": len(text), "truncated": truncated}


def build_user_prompt(
    task: dict[str, Any],
    workspace: Path,
    session_dir: Path,
    *,
    prd: list[dict[str, Any]] | None = None,
    own_ledger: list[dict[str, Any]] | None = None,
    context_files: Sequence[str] | None = None,
    overview: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Assemble the user-side prompt and a manifest of what was loaded.

    The project-context files (`context_files`, default DEFAULT_CONTEXT_FILES)
    are read from `workspace` (user-owned, in the source repo). The progress
    tail is read from `session_dir` (harness-owned runtime journal).

    The worker also sees the work it's part of: the feature `overview` (loaded
    by the caller from `.tilth/tasks/overview.md`), the full feature plan
    (`prd` — every task collapsed, so the worker doesn't pre-empt later ones),
    and its own task ledger (`own_ledger` — the evaluator's prior verdicts on
    this task, read by the caller via `session.read_ledger`). All three are
    *about the work*, not harness mechanics; they ride the existing
    `prompt_assembled` capture.

    Returns (prompt, manifest). The manifest is suitable as the payload for a
    `memory_load` event — it describes which channels were present, their
    char counts, whether anything was truncated, and a short content hash.
    """
    agents_md, agents_meta = _load_context_files(
        workspace, context_files if context_files is not None else DEFAULT_CONTEXT_FILES
    )
    progress_tail, progress_meta = _load_progress_tail(session_dir)
    overview_text, overview_meta = _render_overview(overview)
    full_prd, prd_meta = _render_full_prd(prd, task["id"])
    ledger_text = format_ledger_section(own_ledger or [], WORKER_LEDGER_HEADER)

    parts: list[str] = []

    if agents_md.strip():
        loaded = agents_meta.get("loaded") or []
        header = f"## Project context ({', '.join(loaded)})" if loaded else "## Project context"
        parts += [
            header,
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

    if overview_text:
        parts += [overview_text, ""]

    if full_prd:
        parts += [full_prd, ""]

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
            "overview": overview_meta,
            "full_prd": prd_meta,
            "own_ledger": {
                "present": bool(ledger_text),
                "chars": len(ledger_text),
                "entries": len(own_ledger or []),
            },
        },
        "user_prompt_chars": len(prompt),
    }
    return prompt, manifest
