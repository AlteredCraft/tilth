"""Objective validators: pytest + ruff. Pass/fail is mechanical.

Subjective evaluation lives in the judge call (see prompts/judge.md, slice 5).
"""

from __future__ import annotations

import re
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


def _task_test_glob(task_id: str) -> str:
    """Translate a task ID like 'T-001' into a test-file glob: 'test_t001_*.py'."""
    slug = re.sub(r"[^a-z0-9]", "", task_id.lower())
    return f"test_{slug}_*.py"


def run_pytest(
    workspace: Path, task_ids: list[str] | None = None
) -> ValidatorResult:
    tests_dir = workspace / "tests"
    if not tests_dir.is_dir():
        return ValidatorResult("pytest", True, "(no tests/ dir; skipping)")

    if task_ids:
        patterns = [_task_test_glob(tid) for tid in task_ids]
        matches: set[str] = set()
        for pattern in patterns:
            matches.update(
                str(p.relative_to(workspace)) for p in tests_dir.glob(pattern)
            )
        if not matches:
            return ValidatorResult(
                "pytest",
                True,
                f"(no tests matching {', '.join(patterns)}; skipping)",
            )
        cmd = [sys.executable, "-m", "pytest", "-x", "-q", *sorted(matches)]
    else:
        cmd = [sys.executable, "-m", "pytest", "-x", "-q"]

    rc, out = _run(cmd, workspace)
    return ValidatorResult("pytest", rc == 0, out)


def run_ruff(workspace: Path) -> ValidatorResult:
    rc, out = _run([sys.executable, "-m", "ruff", "check", "."], workspace)
    return ValidatorResult("ruff", rc == 0, out)


def run_all(
    workspace: Path, task_ids: list[str] | None = None
) -> list[ValidatorResult]:
    return [run_ruff(workspace), run_pytest(workspace, task_ids)]


def all_passed(results: list[ValidatorResult]) -> bool:
    return all(r.passed for r in results)


def combined_report(results: list[ValidatorResult]) -> str:
    return "\n\n".join(r.short() for r in results)
