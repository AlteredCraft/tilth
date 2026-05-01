"""Post-edit follow-up: lint files the agent just wrote/edited.

Runs after `write_file` and `edit_file`. If the touched path is a Python file,
runs `ruff check <path>` against it. Success is silent (returns ""). Failure
returns a string that the dispatcher appends to the tool result so the model
sees the lint failure on its next turn.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

LINT_TIMEOUT = 30


def post_edit(tool_name: str, args: dict[str, Any], workspace: Path) -> str:
    if tool_name not in {"write_file", "edit_file"}:
        return ""
    path_str = args.get("path")
    if not isinstance(path_str, str):
        return ""
    target = (workspace / path_str).resolve()
    if not target.is_file() or target.suffix != ".py":
        return ""

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "ruff", "check", str(target)],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=LINT_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"\n\n[post_edit] ruff timed out after {LINT_TIMEOUT}s"
    except FileNotFoundError:
        return ""

    if proc.returncode == 0:
        return ""  # silent success
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return f"\n\n[post_edit] ruff found issues in {path_str}:\n{out}"
