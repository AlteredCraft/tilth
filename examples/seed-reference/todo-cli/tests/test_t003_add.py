"""Acceptance tests for T-003: add subcommand."""

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


def test_add_creates_todos_file(tmp_path: Path):
    proc = _run_module(["add", "buy", "milk"], tmp_path)
    assert proc.returncode == 0, f"non-zero exit {proc.returncode}; stderr={proc.stderr!r}"
    todos = tmp_path / "TODOS.md"
    assert todos.is_file(), "TODOS.md was not created"
    assert todos.read_text() == "- [ ] buy milk\n", f"unexpected: {todos.read_text()!r}"


def test_add_with_no_args_errors(tmp_path: Path):
    proc = _run_module(["add"], tmp_path)
    assert proc.returncode == 2, f"expected exit 2, got {proc.returncode}"
    assert proc.stderr.strip() != "", "expected usage on stderr"
    assert not (tmp_path / "TODOS.md").exists(), "TODOS.md should not be created on error"


def test_add_appends_to_existing(tmp_path: Path):
    todos = tmp_path / "TODOS.md"
    todos.write_text("- [ ] one\n- [x] two\n")
    proc = _run_module(["add", "three"], tmp_path)
    assert proc.returncode == 0, f"non-zero exit; stderr={proc.stderr!r}"
    assert todos.read_text() == "- [ ] one\n- [x] two\n- [ ] three\n", (
        f"unexpected: {todos.read_text()!r}"
    )
