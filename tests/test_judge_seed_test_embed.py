"""The judge needs to see the per-task seed test file.

It lives in HEAD after the seed commit, so it never appears in `task_diff`.
Without it the judge can't tell whether the assertions actually pin down the
acceptance criteria — and false-rejects on file-existence criteria. See #16.
"""

from __future__ import annotations

from pathlib import Path

from tilth import loop


def _task(task_id: str = "T-001") -> dict:
    return {
        "id": task_id,
        "title": "Scaffold the project",
        "description": "Bootstrap the package.",
        "acceptance_criteria": [
            "todo_cli/ exists",
            f"tests/test_{task_id.lower().replace('-', '')}_scaffold.py exists",
        ],
    }


def _write_test_file(worktree: Path, filename: str, body: str) -> Path:
    tests_dir = worktree / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    p = tests_dir / filename
    p.write_text(body)
    return p


def test_loader_returns_matching_test_file(tmp_path: Path):
    _write_test_file(
        tmp_path,
        "test_t001_scaffold.py",
        "def test_pkg_exists():\n    pass\n",
    )
    rel, content = loop._load_seed_test(tmp_path, "T-001")
    assert rel == "tests/test_t001_scaffold.py"
    assert "test_pkg_exists" in content


def test_loader_empty_when_no_tests_dir(tmp_path: Path):
    rel, content = loop._load_seed_test(tmp_path, "T-001")
    assert (rel, content) == ("", "")


def test_loader_empty_when_no_matching_file(tmp_path: Path):
    _write_test_file(tmp_path, "test_t002_other.py", "def test_x(): pass\n")
    rel, content = loop._load_seed_test(tmp_path, "T-001")
    assert (rel, content) == ("", "")


def test_loader_truncates_oversized_content(tmp_path: Path):
    body = "x" * (loop.JUDGE_TEST_FILE_MAX_CHARS + 500)
    _write_test_file(tmp_path, "test_t001_big.py", body)
    _, content = loop._load_seed_test(tmp_path, "T-001")
    assert len(content) < len(body)
    assert "truncated" in content


def test_judge_prompt_embeds_seed_test(tmp_path: Path):
    body = "def test_pkg_exists():\n    import todo_cli\n"
    _write_test_file(tmp_path, "test_t001_scaffold.py", body)
    msg = loop._build_judge_user_message(_task("T-001"), tmp_path, diff="+ new line\n")
    assert "## Seed acceptance test" in msg
    assert "tests/test_t001_scaffold.py" in msg
    assert "import todo_cli" in msg
    # The "already on disk, not in this diff" framing must reach the judge —
    # this is the rule that resolves the #16 file-existence false reject.
    assert "not in this diff" in msg


def test_judge_prompt_omits_section_when_no_test_file(tmp_path: Path):
    msg = loop._build_judge_user_message(_task("T-001"), tmp_path, diff="+ x\n")
    assert "## Seed acceptance test" not in msg


def test_judge_prompt_still_includes_diff_and_criteria(tmp_path: Path):
    _write_test_file(tmp_path, "test_t001_scaffold.py", "def test_x(): pass\n")
    msg = loop._build_judge_user_message(_task("T-001"), tmp_path, diff="+ added\n")
    assert "## Acceptance criteria" in msg
    assert "- todo_cli/ exists" in msg
    assert "+ added" in msg
    assert "Respond with strict JSON only." in msg
