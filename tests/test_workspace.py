"""ensure_worktree is idempotent: create-then-recreate returns the same worktree
without raising. The "fresh" case still goes through create_worktree as before;
this test pins down the "already there" case that prep + run now both depend on.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tilth import workspace as ws


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("# fixture\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial", "--no-gpg-sign"],
        cwd=path,
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": __import__("os").environ.get("PATH", ""),
        },
    )


@pytest.fixture
def source(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    _init_repo(repo)
    return repo


def test_ensure_worktree_creates_when_missing(source: Path, tmp_path: Path) -> None:
    target = tmp_path / "sessions" / "20260526-100000-aaa" / "workspace"
    wt, branch = ws.ensure_worktree(source, "20260526-100000-aaa", target)
    assert wt == target
    assert branch == "session/20260526-100000-aaa"
    assert (target / "README.md").is_file()


def test_ensure_worktree_is_idempotent(source: Path, tmp_path: Path) -> None:
    """Second call against the same target returns the existing worktree without
    erroring on "branch already exists" or "path not empty"."""
    target = tmp_path / "sessions" / "20260526-100000-bbb" / "workspace"
    wt1, branch1 = ws.ensure_worktree(source, "20260526-100000-bbb", target)
    wt2, branch2 = ws.ensure_worktree(source, "20260526-100000-bbb", target)
    assert (wt1, branch1) == (wt2, branch2)


def test_ensure_worktree_preserves_session_branch_state(
    source: Path, tmp_path: Path
) -> None:
    """A second ensure_worktree against an existing worktree must not blow away
    files already there — prep writes tests into it before run picks it up."""
    target = tmp_path / "sessions" / "20260526-100000-ccc" / "workspace"
    ws.ensure_worktree(source, "20260526-100000-ccc", target)
    (target / "tests").mkdir()
    (target / "tests" / "test_t001_demo.py").write_text("def test_x():\n    assert True\n")

    ws.ensure_worktree(source, "20260526-100000-ccc", target)
    assert (target / "tests" / "test_t001_demo.py").is_file()
