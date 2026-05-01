"""File operations: read_file, write_file, edit_file (anchor-based)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

MAX_READ_CHARS = 50_000
MAX_WRITE_CHARS = 200_000

NAME_READ = "read_file"
NAME_WRITE = "write_file"
NAME_EDIT = "edit_file"


SCHEMA_READ: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": NAME_READ,
        "description": (
            "Read a file's contents (up to 50KB). Path must be relative to the workspace root."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to workspace root."},
            },
            "required": ["path"],
        },
    },
}

SCHEMA_WRITE: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": NAME_WRITE,
        "description": (
            "Create or overwrite a file with the given contents. "
            "Use for new files. For edits to existing files, prefer edit_file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to workspace root."},
                "content": {"type": "string", "description": "Full file contents to write."},
            },
            "required": ["path", "content"],
        },
    },
}

SCHEMA_EDIT: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": NAME_EDIT,
        "description": (
            "Replace exactly one occurrence of old_string with new_string in an existing file. "
            "old_string must match the file's contents verbatim including whitespace and "
            "appear exactly once. Errors if it appears zero or multiple times."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to workspace root."},
                "old_string": {
                    "type": "string",
                    "description": "Exact substring currently in the file.",
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement substring.",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
}


def _resolve(path_str: str, workspace: Path) -> Path:
    if not isinstance(path_str, str) or not path_str.strip():
        raise ValueError("'path' must be a non-empty string")
    p = (workspace / path_str).resolve()
    ws = workspace.resolve()
    if ws not in p.parents and p != ws:
        raise ValueError(f"path escapes workspace: {path_str}")
    return p


def read(args: dict[str, Any], workspace: Path) -> str:
    p = _resolve(args["path"], workspace)
    if not p.is_file():
        return f"ERROR: not a file: {args['path']}"
    text = p.read_text()
    if len(text) > MAX_READ_CHARS:
        return text[:MAX_READ_CHARS] + f"\n... [truncated, total {len(text)} chars]"
    return text


def write(args: dict[str, Any], workspace: Path) -> str:
    if "content" not in args:
        return "ERROR: 'content' is required (pass an empty string for an empty file)"
    content = args["content"]
    if not isinstance(content, str):
        return "ERROR: 'content' must be a string"
    if len(content) > MAX_WRITE_CHARS:
        return f"ERROR: content exceeds {MAX_WRITE_CHARS} chars; split the work."
    p = _resolve(args["path"], workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"wrote {len(content)} chars to {args['path']}"


def edit(args: dict[str, Any], workspace: Path) -> str:
    old = args.get("old_string", "")
    new = args.get("new_string", "")
    if not isinstance(old, str) or not isinstance(new, str):
        return "ERROR: old_string and new_string must be strings"
    if old == new:
        return "ERROR: old_string and new_string are identical"
    p = _resolve(args["path"], workspace)
    if not p.is_file():
        return f"ERROR: not a file: {args['path']}"
    text = p.read_text()
    count = text.count(old)
    if count == 0:
        return f"ERROR: old_string not found in {args['path']}"
    if count > 1:
        return (
            f"ERROR: old_string matches {count} places in {args['path']}; "
            "expand it to a unique anchor."
        )
    p.write_text(text.replace(old, new, 1))
    return f"edited {args['path']} (-{len(old)} +{len(new)} chars)"
