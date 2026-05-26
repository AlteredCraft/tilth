"""Acceptance tests for T-005: done subcommand."""

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


def test_done_marks_item_complete(tmp_path: Path):
    todos = tmp_path / "TODOS.md"
    todos.write_text("- [ ] buy milk\n- [ ] write tests\n")
    proc = _run_module(["done", "1"], tmp_path)
    assert proc.returncode == 0, f"non-zero exit; stderr={proc.stderr!r}"
    assert todos.read_text() == "- [x] buy milk\n- [ ] write tests\n", (
        f"unexpected: {todos.read_text()!r}"
    )


def test_done_already_done_is_idempotent(tmp_path: Path):
    todos = tmp_path / "TODOS.md"
    todos.write_text("- [x] buy milk\n- [ ] other\n")
    proc = _run_module(["done", "1"], tmp_path)
    assert proc.returncode == 0, f"non-zero exit; stderr={proc.stderr!r}"
    assert todos.read_text() == "- [x] buy milk\n- [ ] other\n", (
        f"unexpected: {todos.read_text()!r}"
    )


def test_done_no_index_errors(tmp_path: Path):
    proc = _run_module(["done"], tmp_path)
    assert proc.returncode == 2, f"expected exit 2, got {proc.returncode}"
    assert proc.stderr.strip() != "", "expected usage on stderr"


def test_done_non_integer_errors(tmp_path: Path):
    (tmp_path / "TODOS.md").write_text("- [ ] only one\n")
    proc = _run_module(["done", "abc"], tmp_path)
    assert proc.returncode == 2, f"expected exit 2, got {proc.returncode}"
    assert proc.stderr.strip() != ""


def test_done_out_of_range_errors(tmp_path: Path):
    (tmp_path / "TODOS.md").write_text("- [ ] only one\n")
    proc = _run_module(["done", "5"], tmp_path)
    assert proc.returncode == 2, f"expected exit 2, got {proc.returncode}"
    assert proc.stderr.strip() != ""


def test_done_when_file_missing_errors(tmp_path: Path):
    proc = _run_module(["done", "1"], tmp_path)
    assert proc.returncode == 2, f"expected exit 2, got {proc.returncode}"
    assert proc.stderr.strip() != ""
