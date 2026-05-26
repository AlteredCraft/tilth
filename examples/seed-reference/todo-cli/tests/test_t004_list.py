"""Acceptance tests for T-004: list subcommand."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run_module(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, "-m", "todo_cli", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_list_with_items(tmp_path: Path):
    (tmp_path / "TODOS.md").write_text("- [ ] buy milk\n- [x] write tests\n")
    proc = _run_module(["list"], tmp_path)
    assert proc.returncode == 0, f"non-zero exit; stderr={proc.stderr!r}"
    assert proc.stdout == "1. [ ] buy milk\n2. [x] write tests\n", (
        f"unexpected: {proc.stdout!r}"
    )


def test_list_missing_file(tmp_path: Path):
    proc = _run_module(["list"], tmp_path)
    assert proc.returncode == 0, f"non-zero exit; stderr={proc.stderr!r}"
    assert proc.stdout == "(no todos)\n", f"unexpected: {proc.stdout!r}"


def test_list_file_with_no_valid_items(tmp_path: Path):
    (tmp_path / "TODOS.md").write_text("# header line\nrandom junk\n")
    proc = _run_module(["list"], tmp_path)
    assert proc.returncode == 0, f"non-zero exit; stderr={proc.stderr!r}"
    assert proc.stdout == "(no todos)\n", f"unexpected: {proc.stdout!r}"
