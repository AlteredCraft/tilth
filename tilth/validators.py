"""Objective validators: pytest + ruff. Pass/fail is mechanical.

Subjective evaluation lives in the judge call (see prompts/judge.md, slice 5).
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

VALIDATOR_TIMEOUT = 180


@dataclass
class ValidatorResult:
    name: str
    passed: bool
    output: str

    def short(self) -> str:
        out = self.output.strip()
        if len(out) > 2000:
            out = out[:2000] + f"\n... [truncated, total {len(self.output)} chars]"
        return f"[{self.name}] {'PASS' if self.passed else 'FAIL'}\n{out}".rstrip()


def _run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=VALIDATOR_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {VALIDATOR_TIMEOUT}s: {' '.join(cmd)}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def run_pytest(workspace: Path) -> ValidatorResult:
    if not (workspace / "tests").is_dir():
        return ValidatorResult("pytest", True, "(no tests/ dir; skipping)")
    rc, out = _run([sys.executable, "-m", "pytest", "-x", "-q"], workspace)
    return ValidatorResult("pytest", rc == 0, out)


def run_ruff(workspace: Path) -> ValidatorResult:
    rc, out = _run([sys.executable, "-m", "ruff", "check", "."], workspace)
    return ValidatorResult("ruff", rc == 0, out)


def run_all(workspace: Path) -> list[ValidatorResult]:
    return [run_ruff(workspace), run_pytest(workspace)]


def all_passed(results: list[ValidatorResult]) -> bool:
    return all(r.passed for r in results)


def combined_report(results: list[ValidatorResult]) -> str:
    return "\n\n".join(r.short() for r in results)
