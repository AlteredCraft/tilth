"""Tool registry — each tool exports an OpenAI-style function schema and a callable.

Keep tools focused. Tool descriptions are *prompt text*: every character ships every turn.

Unlike the façade-style ``__init__.py`` files in ``tilth/hooks`` and ``tilth/visualize``,
this module intentionally owns logic (the ``REGISTRY`` and ``dispatch``) rather than just
re-exporting submodules. CLAUDE.md invariant 3 names this file as the canonical source
for "what tools exist" — don't relocate the registry into a sibling module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tilth.hooks import post_edit, pre_tool
from tilth.tools import bash as _bash
from tilth.tools import files as _files
from tilth.tools import search as _search

__all__ = ["REGISTRY", "Tool", "ToolOutcome", "dispatch", "schemas"]

POST_EDIT_TOOLS: frozenset[str] = frozenset({_files.NAME_WRITE, _files.NAME_EDIT})


@dataclass
class Tool:
    name: str
    schema: dict[str, Any]
    fn: Callable[[dict[str, Any], Path], str]


@dataclass
class ToolOutcome:
    blocked: bool
    result: str
    hook_runs: list[dict[str, Any]] = field(default_factory=list)


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

    hook_runs: list[dict[str, Any]] = []

    allow, reason = pre_tool(name, args, workspace)
    pre_run: dict[str, Any] = {"hook": "pre_tool", "outcome": "allow" if allow else "block"}
    if not allow:
        pre_run["reason"] = reason
    hook_runs.append(pre_run)
    if not allow:
        return ToolOutcome(True, reason, hook_runs)

    try:
        result = tool.fn(args, workspace)
    except Exception as exc:
        return ToolOutcome(False, f"ERROR: {type(exc).__name__}: {exc}", hook_runs)

    if name in POST_EDIT_TOOLS:
        notice = post_edit(name, args, workspace)
        hook_runs.append({"hook": "post_edit", "outcome": "warned" if notice else "silent"})
        if notice:
            result = result + notice
    return ToolOutcome(False, result, hook_runs)
