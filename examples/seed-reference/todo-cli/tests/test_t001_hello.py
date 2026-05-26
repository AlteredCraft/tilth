"""Acceptance tests for T-001: hello.py prints fixed greeting."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_hello_file_exists():
    assert (ROOT / "hello.py").is_file(), "hello.py must exist at the workspace root"


def test_hello_prints_greeting():
    proc = subprocess.run(
        [sys.executable, "hello.py"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert proc.returncode == 0, f"non-zero exit: {proc.returncode}; stderr={proc.stderr!r}"
    assert proc.stdout == "hello from the harness\n", f"unexpected stdout: {proc.stdout!r}"
    assert proc.stderr == "", f"unexpected stderr: {proc.stderr!r}"
