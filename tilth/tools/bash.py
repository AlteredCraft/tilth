"""Allow-listed bash execution tool.

The agent gets a single `bash` tool. Output is truncated to keep context manageable.
Safety filtering is delegated to harness/hooks/pre_tool.py — this module only executes.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any

NAME = "bash"
TIMEOUT_SECONDS = 120
MAX_OUTPUT_CHARS = 8_000


SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": NAME,
        "description": (
            "Run a bash command in the current workspace. "
            "Returns combined stdout+stderr with the exit code on the last line. "
            "Output truncated past 8KB. Timeout 120s. "
            "Destructive commands (git push --force, sudo, curl piped to a shell) "
            "are blocked by a safety hook."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute.",
                },
            },
            "required": ["command"],
        },
    },
}


def run(args: dict[str, Any], workspace: Path) -> str:
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        return "ERROR: 'command' must be a non-empty string."

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {TIMEOUT_SECONDS}s\n$ {shlex.quote(command)}"

    out = (proc.stdout or "") + (proc.stderr or "")
    if len(out) > MAX_OUTPUT_CHARS:
        out = out[:MAX_OUTPUT_CHARS] + f"\n... [truncated, total {len(out)} chars]"
    return f"{out}\n[exit {proc.returncode}]"
