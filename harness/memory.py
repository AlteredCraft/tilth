"""Memory plumbing — load the four channels into a fresh prompt each task.

The four channels (per Osmani's self-improving agents post):
    AGENTS.md       semantic knowledge (patterns, gotchas, style, recent learnings)
    git history     atomic commits — accessed via bash, not loaded here
    progress.txt    chronological journal — we inject a tail (last N lines)
    prd.json        machine-readable task list — caller picks the next task

This module is the place where context is *rebuilt from disk* on each task. That's
what makes "context resets, not just compaction" work — the durable artifacts on
disk are the source of truth, not the prior conversation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

PROGRESS_TAIL_LINES = 30
AGENTS_MD_MAX_CHARS = 8_000
PROGRESS_MAX_CHARS = 4_000


def load_agents_md(workspace: Path) -> str:
    p = workspace / "AGENTS.md"
    if not p.is_file():
        return ""
    text = p.read_text()
    if len(text) > AGENTS_MD_MAX_CHARS:
        # Keep the head — assumed to be the more stable rules section.
        return text[:AGENTS_MD_MAX_CHARS] + "\n\n[... AGENTS.md truncated; consider compacting it]"
    return text


def load_progress_tail(workspace: Path) -> str:
    p = workspace / "progress.txt"
    if not p.is_file():
        return ""
    lines = p.read_text().splitlines()
    tail = lines[-PROGRESS_TAIL_LINES:]
    text = "\n".join(tail)
    if len(text) > PROGRESS_MAX_CHARS:
        text = text[-PROGRESS_MAX_CHARS:]
    return text


def append_progress(workspace: Path, line: str) -> None:
    p = workspace / "progress.txt"
    with p.open("a") as f:
        f.write(line.rstrip() + "\n")


def build_user_prompt(task: dict[str, Any], workspace: Path) -> str:
    """Assemble the user-side prompt for a single task with all four channels in scope."""
    agents_md = load_agents_md(workspace)
    progress_tail = load_progress_tail(workspace)

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

    parts += [
        "",
        f"**Workspace root:** {workspace}",
        "",
        "Begin work now. Stop calling tools and respond with a brief summary when done.",
    ]
    return "\n".join(parts)
