"""Pre-tool gate: veto dangerous bash commands before they run.

Returns (allow, reason). When `allow` is False, the harness skips execution and
returns the reason as the tool result so the model knows why and can adjust.

Intentionally narrow — every rule should be earned by a real failure. The seed
list below is the conservative starting point.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Patterns that block execution outright. Order doesn't matter — first match wins
# in the loop, but every pattern is independent.
_BASH_DENY: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\s+-[rR]f?\b"), "destructive recursive delete (rm -rf)"),
    (re.compile(r"\brm\s+-[fF]r?\b"), "destructive force delete (rm -f variants)"),
    (re.compile(r"\bgit\s+push\b.*?(?:--force-with-lease|--force|(?<!\S)-f)\b"), "force push"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "git reset --hard"),
    (re.compile(r"\bgit\s+clean\s+-[fFdDxX]"), "git clean (force)"),
    (re.compile(r"^\s*sudo\b"), "sudo (privilege escalation)"),
    (re.compile(r"(^|;|\|\||&&|\|)\s*sudo\b"), "sudo (privilege escalation, chained)"),
    (re.compile(r"\bcurl\b[^|]*\|\s*(bash|sh|zsh)\b"), "piping curl into a shell"),
    (re.compile(r"\bwget\b[^|]*\|\s*(bash|sh|zsh)\b"), "piping wget into a shell"),
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*\}"), "fork bomb pattern"),
]


def pre_tool(tool_name: str, args: dict[str, Any], _workspace: Path) -> tuple[bool, str]:
    if tool_name != "bash":
        return True, ""
    command = args.get("command", "")
    if not isinstance(command, str):
        return True, ""
    for pattern, label in _BASH_DENY:
        if pattern.search(command):
            return False, f"BLOCKED by pre_tool hook ({label}). Try a safer alternative."
    return True, ""
