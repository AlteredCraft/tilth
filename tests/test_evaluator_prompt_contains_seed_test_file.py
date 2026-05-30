"""Phase 4 visibility: the evaluator sees this task's seed test inlined (#16).

It reads the worktree-current version — the exact file pytest filtered on and
ran (validators.task_test_glob is the shared source of truth). Tampering is
already caught via the diff; here the evaluator can read what the passing test
actually asserts, to ground `weak_test`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tilth.loop import SEED_TEST_INJECT_CAP, _format_seed_test_section


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "worktree"
    (wt / "tests").mkdir(parents=True)
    return wt


def test_inlines_the_matching_seed_test(worktree):
    body = "def test_add_appends_a_line():\n    assert add_todo('x')\n"
    (worktree / "tests" / "test_t001_scaffold.py").write_text(body)
    out = _format_seed_test_section(worktree, "T-001")
    assert "test_t001_scaffold.py" in out
    assert "test_add_appends_a_line" in out
    assert "Seed acceptance test" in out


def test_uses_the_same_glob_as_pytest_so_unrelated_tasks_are_skipped(worktree):
    (worktree / "tests" / "test_t001_scaffold.py").write_text("# t1\n")
    (worktree / "tests" / "test_t002_core.py").write_text("# t2\n")
    out = _format_seed_test_section(worktree, "T-001")
    assert "test_t001_scaffold.py" in out
    assert "test_t002_core.py" not in out


def test_no_tests_dir_yields_empty_string(tmp_path: Path):
    out = _format_seed_test_section(tmp_path / "worktree", "T-001")
    assert out == ""


def test_no_matching_test_yields_empty_string(worktree):
    (worktree / "tests" / "test_t002_core.py").write_text("# t2\n")
    assert _format_seed_test_section(worktree, "T-001") == ""


def test_oversized_test_is_truncated(worktree):
    (worktree / "tests" / "test_t001_big.py").write_text("y" * (SEED_TEST_INJECT_CAP + 500))
    out = _format_seed_test_section(worktree, "T-001")
    assert "truncated" in out
