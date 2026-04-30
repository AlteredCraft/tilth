"""Search tools: glob and grep."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

NAME_GLOB = "glob"
NAME_GREP = "grep"

MAX_RESULTS = 200


SCHEMA_GLOB: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": NAME_GLOB,
        "description": (
            "Find files in the workspace matching a glob pattern (e.g. '**/*.py'). "
            "Returns up to 200 matching paths, relative to the workspace root."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern."},
            },
            "required": ["pattern"],
        },
    },
}

SCHEMA_GREP: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": NAME_GREP,
        "description": (
            "Search file contents for a regex. Returns up to 200 'path:lineno: line' matches. "
            "Use 'path_glob' to scope (default: '**/*'). Hidden dirs (.git, .venv) are skipped."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Python regex."},
                "path_glob": {
                    "type": "string",
                    "description": "Glob to limit which files are searched. Default '**/*'.",
                },
            },
            "required": ["pattern"],
        },
    },
}


SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache"}


def _iter_files(workspace: Path, pattern: str) -> list[Path]:
    matches: list[Path] = []
    for p in workspace.glob(pattern):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(workspace).parts):
            continue
        matches.append(p)
    matches.sort()
    return matches


def glob_(args: dict[str, Any], workspace: Path) -> str:
    pattern = args.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        return "ERROR: 'pattern' must be a non-empty string"
    matches = _iter_files(workspace, pattern)[:MAX_RESULTS]
    if not matches:
        return "(no matches)"
    rels = [str(p.relative_to(workspace)) for p in matches]
    return "\n".join(rels)


def grep(args: dict[str, Any], workspace: Path) -> str:
    pattern = args.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        return "ERROR: 'pattern' must be a non-empty string"
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return f"ERROR: invalid regex: {exc}"
    path_glob = args.get("path_glob") or "**/*"
    if not isinstance(path_glob, str):
        return "ERROR: 'path_glob' must be a string"

    hits: list[str] = []
    for p in _iter_files(workspace, path_glob):
        try:
            for i, line in enumerate(p.read_text().splitlines(), start=1):
                if rx.search(line):
                    rel = p.relative_to(workspace)
                    hits.append(f"{rel}:{i}: {line.rstrip()}")
                    if len(hits) >= MAX_RESULTS:
                        return "\n".join(hits) + f"\n... [stopped at {MAX_RESULTS} matches]"
        except (UnicodeDecodeError, OSError):
            continue

    return "\n".join(hits) if hits else "(no matches)"
