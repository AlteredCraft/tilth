"""Acceptance tests for T-002: todo_cli package scaffold."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_package_layout():
    pkg = ROOT / "todo_cli"
    assert pkg.is_dir(), "todo_cli/ directory must exist"
    assert (pkg / "__init__.py").is_file(), "todo_cli/__init__.py must exist"
    assert (pkg / "__main__.py").is_file(), "todo_cli/__main__.py must exist"


def test_module_runs_with_no_args():
    proc = subprocess.run(
        [sys.executable, "-m", "todo_cli"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert proc.returncode == 2, f"expected exit code 2, got {proc.returncode}"
    assert "usage: todo" in proc.stderr, f"missing usage on stderr: {proc.stderr!r}"


def test_package_importable():
    proc = subprocess.run(
        [sys.executable, "-c", "import todo_cli; print(todo_cli.__name__)"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert proc.returncode == 0, f"import failed: {proc.stderr!r}"
    assert proc.stdout.strip() == "todo_cli", f"unexpected: {proc.stdout!r}"
