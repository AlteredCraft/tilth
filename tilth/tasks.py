"""Load a feature's per-task markdown from a feature directory.

This replaces the prep-feature-generated ``prd.json``. A feature is authored by
hand (or by a model following the template) as a directory of markdown files in
the source repo, conventionally ``<repo>/.tilth/<feature>/`` so one repo can
hold several:

    .tilth/<feature>/
        overview.md          (required) — feature-level context, the "why"
        T-001-<slug>.md      one per task, ordered by id
        T-002-<slug>.md
        ...

``tilth run <feature-dir>`` is given the path to that directory directly; the
harness derives the enclosing git repo for the worktree.

Each task file is small frontmatter (``id``, ``title``) plus two body sections:

    ---
    id: T-001
    title: Add the `add` subcommand
    ---

    ## Description
    <prose the worker sees as its task>

    ## Acceptance criteria
    - externally checkable behaviour
    - another

Status (pending / done / failed) is **not** stored here — task files are
read-only inputs. The harness tracks status under
``sessions/<id>/task-status.json`` so the user's authored docs are never mutated.

Parsing is deliberately forgiving (a hand-rolled ``key: value`` frontmatter
reader, no YAML dependency) but validation fails fast with an actionable message
when the required pieces are missing — there are no silent defaults for a
feature with no tasks.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# A feature directory holds overview.md + one T-NNN-*.md per task, directly.
# Conventionally <repo>/.tilth/<feature>/, but the path is whatever the user
# passes to `tilth run`.
OVERVIEW_FILENAME = "overview.md"
TASK_GLOB = "T-*.md"

# Task id shape: T- followed by digits (zero-padded by convention, e.g. T-001).
_ID_RE = re.compile(r"^T-\d+$")


class TasksError(RuntimeError):
    """A feature directory is missing or malformed.

    Carries a fully-formed, user-facing message (path + what to do). The CLI
    prints ``str(exc)`` and exits non-zero — no internal detail leaks beyond the
    path the user owns.
    """


OVERVIEW_TEMPLATE = """\
# <Feature name>

## Goal
<1-3 sentences: what this feature/refactor delivers and why now.>

## Context
<Which modules/files this touches — real paths. What the worker needs to
understand the whole before building one slice.>

## Scope boundaries
- In scope: …
- Out of scope: …          ← the high-leverage part; keeps slices from growing

## Notes for the reviewer
<Risks, open questions, assumptions to sanity-check before merging the branch.>
"""

TASK_TEMPLATE = """\
---
id: T-001
title: <short imperative title>
---

## Description
<What to build, in the worker's voice. Real paths and symbols
(pkg/module.py:func()), not "the entrypoint". This block becomes the user
message the worker sees. Note any non-load-bearing assumptions here.>

## Acceptance criteria
- <externally checkable behaviour>
- <another>
"""


def _scaffold_hint(feature_dir: Path) -> str:
    return (
        f"Author a feature as markdown in {feature_dir}/ :\n\n"
        f"  {feature_dir}/{OVERVIEW_FILENAME}   (required — feature context)\n"
        f"  {feature_dir}/T-001-<slug>.md       (one file per task, ordered by id)\n\n"
        f"Then run it with `tilth run {feature_dir}`.\n\n"
        f"--- {OVERVIEW_FILENAME} template ---\n{OVERVIEW_TEMPLATE}\n"
        f"--- task template ---\n{TASK_TEMPLATE}"
    )


def _parse_frontmatter(text: str, path: Path) -> tuple[dict[str, str], str]:
    """Split a leading ``--- ... ---`` frontmatter block from the body.

    Returns ``(fields, body)``. ``fields`` only collects ``key: value`` lines;
    unknown keys are ignored (forward-compatible). Raises ``TasksError`` when the
    block is absent or unterminated — the harness needs ``id``/``title`` and
    won't guess them from the filename.
    """
    stripped = text.lstrip("﻿")  # tolerate a BOM
    if not stripped.lstrip().startswith("---"):
        raise TasksError(
            f"{path.name}: missing frontmatter. A task file must begin with a\n"
            f"`---` block carrying `id` and `title`:\n\n{TASK_TEMPLATE}"
        )
    # Find the opening fence, then the closing one.
    lines = stripped.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "---")
    end = None
    for i in range(start + 1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise TasksError(
            f"{path.name}: frontmatter `---` block is not closed. "
            "Add a closing `---` line after the `id`/`title` fields."
        )
    fields: dict[str, str] = {}
    for ln in lines[start + 1 : end]:
        if not ln.strip() or ":" not in ln:
            continue
        key, _, value = ln.partition(":")
        fields[key.strip().lower()] = value.strip()
    body = "\n".join(lines[end + 1 :])
    return fields, body


_HEADING_RE = re.compile(r"^##\s+(.*\S)\s*$")


def _split_sections(body: str) -> dict[str, str]:
    """Map normalised ``## Heading`` text → the section's body text.

    Lower-cased heading keys; the text under each heading runs until the next
    ``## `` heading. Content before the first heading is keyed under ``""``.
    """
    sections: dict[str, list[str]] = {"": []}
    current = ""
    for ln in body.splitlines():
        m = _HEADING_RE.match(ln)
        if m:
            current = m.group(1).strip().lower()
            sections.setdefault(current, [])
            continue
        sections[current].append(ln)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def _bullets(text: str) -> list[str]:
    out: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if s[:2] in ("- ", "* "):
            item = s[2:].strip()
            if item:
                out.append(item)
    return out


def parse_task_file(path: Path) -> dict[str, Any]:
    """Parse one task markdown file into a task dict.

    Shape: ``{id, title, description, acceptance_criteria}`` — the same fields
    ``loop``/``memory`` consumed from a ``prd.json`` entry, minus ``status``
    (tracked harness-side). Lenient on body structure: ``description`` falls back
    to all non-AC body text when there's no explicit ``## Description`` heading.
    """
    text = path.read_text()
    fields, body = _parse_frontmatter(text, path)

    tid = fields.get("id", "").strip()
    if not tid:
        raise TasksError(f"{path.name}: frontmatter is missing `id` (e.g. `id: T-001`).")
    if not _ID_RE.match(tid):
        raise TasksError(
            f"{path.name}: id {tid!r} is not of the form `T-<NNN>` (e.g. `T-001`)."
        )
    title = fields.get("title", "").strip()
    if not title:
        raise TasksError(f"{path.name}: frontmatter is missing `title`.")

    sections = _split_sections(body)
    ac_text = sections.get("acceptance criteria", "")
    description = sections.get("description", "").strip()
    if not description:
        # No explicit `## Description` heading — take all body text that isn't
        # the acceptance-criteria section.
        description = sections.get("", "").strip()
    if not description:
        raise TasksError(
            f"{path.name}: no description. Add a `## Description` section the "
            "worker can act on."
        )

    return {
        "id": tid,
        "title": title,
        "description": description,
        "acceptance_criteria": _bullets(ac_text),
    }


def load_tasks(feature_dir: Path) -> list[dict[str, Any]]:
    """Parse every ``T-*.md`` in the feature directory, ordered by id.

    Raises ``TasksError`` (with the scaffold hint) when the directory or any
    task files are missing, or when ids collide.
    """
    d = feature_dir
    if not d.is_dir():
        raise TasksError(f"No feature directory at {d}.\n\n{_scaffold_hint(d)}")

    paths = sorted(d.glob(TASK_GLOB))
    if not paths:
        raise TasksError(f"No task files ({TASK_GLOB}) in {d}.\n\n{_scaffold_hint(d)}")

    tasks = [parse_task_file(p) for p in paths]
    tasks.sort(key=lambda t: t["id"])
    seen: set[str] = set()
    for t in tasks:
        if t["id"] in seen:
            raise TasksError(
                f"duplicate task id {t['id']!r} in {d} — each task file needs a unique id."
            )
        seen.add(t["id"])
    return tasks


def load_overview(feature_dir: Path) -> str:
    """Read the required ``overview.md``. Raises ``TasksError`` when absent/empty."""
    path = feature_dir / OVERVIEW_FILENAME
    if not path.is_file():
        raise TasksError(
            f"No {OVERVIEW_FILENAME} at {path} (required).\n\n"
            f"--- {OVERVIEW_FILENAME} template ---\n{OVERVIEW_TEMPLATE}"
        )
    text = path.read_text().strip()
    if not text:
        raise TasksError(
            f"{path} is empty. It must carry the feature's goal, scope, and "
            f"context.\n\n--- {OVERVIEW_FILENAME} template ---\n{OVERVIEW_TEMPLATE}"
        )
    return text


def load_feature(feature_dir: Path) -> tuple[str, list[dict[str, Any]]]:
    """Load ``(overview_text, tasks)`` for a feature directory. Fails fast on any gap."""
    overview = load_overview(feature_dir)
    tasks = load_tasks(feature_dir)
    return overview, tasks
