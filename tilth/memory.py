"""Memory plumbing — load the four channels into a fresh prompt each task.

The four channels (per Osmani's self-improving agents post):
    AGENTS.md       semantic knowledge (patterns, gotchas, style, recent learnings)
    git history     atomic commits — accessed via bash, not loaded here
    progress.txt    chronological journal — we inject a tail (last N lines)
    prd.json        machine-readable task list — caller picks the next task

This module is the place where context is *rebuilt from disk* on each task. That's
what makes "context resets, not just compaction" work — the durable artifacts on
disk are the source of truth, not the prior conversation.

Every load returns a manifest alongside the text so the harness can emit a
`memory_load` event — observability of what the agent actually saw, including
truncation and a content hash to spot drift between tasks.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

PROGRESS_TAIL_LINES = 30
AGENTS_MD_MAX_CHARS = 8_000
PROGRESS_MAX_CHARS = 4_000

VALID_AGENTS_MD_SECTIONS = frozenset(
    {"Patterns", "Gotchas", "Style", "Recent learnings"}
)


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


def _load_progress_tail(workspace: Path) -> tuple[str, dict[str, Any]]:
    p = workspace / "progress.txt"
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


def load_progress_tail(workspace: Path) -> str:
    return _load_progress_tail(workspace)[0]


def append_progress(workspace: Path, line: str) -> None:
    p = workspace / "progress.txt"
    with p.open("a") as f:
        f.write(line.rstrip() + "\n")


def append_to_agents_md(workspace: Path, section: str, line: str) -> None:
    """Append `line` under `## {section}` in AGENTS.md, creating the section if missing."""
    p = workspace / "AGENTS.md"
    text = p.read_text() if p.is_file() else ""
    heading = f"## {section}"
    if heading in text:
        placeholder = "_(empty — agent appends here)_"
        parts = text.split(heading, 1)
        before = parts[0] + heading
        rest = parts[1]
        next_h2 = rest.find("\n## ")
        if next_h2 == -1:
            section_body = rest
            tail = ""
        else:
            section_body = rest[:next_h2]
            tail = rest[next_h2:]
        if placeholder in section_body:
            new_body = section_body.replace(placeholder, line)
        else:
            new_body = section_body.rstrip() + f"\n{line}\n"
        p.write_text(before + new_body + tail)
    else:
        p.write_text((text.rstrip() + f"\n\n## {section}\n\n{line}\n").lstrip("\n"))


def build_user_prompt(
    task: dict[str, Any], workspace: Path
) -> tuple[str, dict[str, Any]]:
    """Assemble the user-side prompt and a manifest of what was loaded.

    Returns (prompt, manifest). The manifest is suitable as the payload for a
    `memory_load` event — it describes which channels were present, their
    char counts, whether anything was truncated, and a short content hash.
    """
    agents_md, agents_meta = _load_agents_md(workspace)
    progress_tail, progress_meta = _load_progress_tail(workspace)

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
    prompt = "\n".join(parts)
    manifest = {
        "channels": {
            "agents_md": agents_meta,
            "progress_tail": progress_meta,
        },
        "user_prompt_chars": len(prompt),
    }
    return prompt, manifest
