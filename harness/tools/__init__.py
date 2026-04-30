"""Tool registry — each tool exports an OpenAI-style function schema and a callable.

Keep tools focused. Tool descriptions are *prompt text*: every character ships every turn.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.hooks import post_edit, pre_tool
from harness.tools import bash as _bash
from harness.tools import files as _files
from harness.tools import search as _search


@dataclass
class Tool:
    name: str
    schema: dict[str, Any]
    fn: Callable[[dict[str, Any], Path], str]


@dataclass
class ToolOutcome:
    blocked: bool
    result: str


def _registry() -> dict[str, Tool]:
    return {
        _bash.NAME: Tool(_bash.NAME, _bash.SCHEMA, _bash.run),
        _files.NAME_READ: Tool(_files.NAME_READ, _files.SCHEMA_READ, _files.read),
        _files.NAME_WRITE: Tool(_files.NAME_WRITE, _files.SCHEMA_WRITE, _files.write),
        _files.NAME_EDIT: Tool(_files.NAME_EDIT, _files.SCHEMA_EDIT, _files.edit),
        _search.NAME_GLOB: Tool(_search.NAME_GLOB, _search.SCHEMA_GLOB, _search.glob_),
        _search.NAME_GREP: Tool(_search.NAME_GREP, _search.SCHEMA_GREP, _search.grep),
    }


REGISTRY: dict[str, Tool] = _registry()


def schemas() -> list[dict[str, Any]]:
    return [t.schema for t in REGISTRY.values()]


def dispatch(name: str, args: dict[str, Any], workspace: Path) -> ToolOutcome:
    tool = REGISTRY.get(name)
    if tool is None:
        return ToolOutcome(False, f"ERROR: unknown tool '{name}'. Available: {sorted(REGISTRY)}")

    allow, reason = pre_tool(name, args, workspace)
    if not allow:
        return ToolOutcome(True, reason)

    try:
        result = tool.fn(args, workspace)
    except Exception as exc:
        return ToolOutcome(False, f"ERROR: {type(exc).__name__}: {exc}")

    notice = post_edit(name, args, workspace)
    if notice:
        result = result + notice
    return ToolOutcome(False, result)
