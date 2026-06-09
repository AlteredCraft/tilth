"""Loading a feature from <workspace>/.tilth/tasks/ (the prd.json successor).

Task files are read-only inputs authored in the source repo. The loader parses
frontmatter (id/title) + body sections into the same task-dict shape the loop
consumed from prd.json (minus status, which is tracked harness-side), and fails
fast with an actionable message when the required pieces are missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tilth import tasks


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / ".tilth" / "tasks").mkdir(parents=True)
    return ws


def _write_task(workspace: Path, name: str, body: str) -> Path:
    p = workspace / ".tilth" / "tasks" / name
    p.write_text(body)
    return p


def _write_overview(workspace: Path, text: str = "# Feature\n\nGoal here.") -> None:
    (workspace / ".tilth" / "tasks" / "overview.md").write_text(text)


VALID_TASK = """\
---
id: T-001
title: Add the add subcommand
---

## Description
Implement `todo add` in todo_cli/__main__.py:main().

## Acceptance criteria
- `todo add x` exits 0
- the item is persisted
"""


def test_parse_task_file_extracts_all_fields(workspace):
    p = _write_task(workspace, "T-001-add.md", VALID_TASK)
    task = tasks.parse_task_file(p)
    assert task["id"] == "T-001"
    assert task["title"] == "Add the add subcommand"
    assert "todo_cli/__main__.py:main()" in task["description"]
    assert task["acceptance_criteria"] == ["`todo add x` exits 0", "the item is persisted"]
    assert "status" not in task  # tracked harness-side, never parsed from the file


def test_description_falls_back_to_body_without_heading(workspace):
    body = "---\nid: T-002\ntitle: thing\n---\n\nJust do the thing, no heading.\n"
    p = _write_task(workspace, "T-002-thing.md", body)
    task = tasks.parse_task_file(p)
    assert task["description"] == "Just do the thing, no heading."
    assert task["acceptance_criteria"] == []


@pytest.mark.parametrize(
    "body, needle",
    [
        ("no frontmatter here\n", "missing frontmatter"),
        ("---\ntitle: no id\n---\n\n## Description\nx\n", "missing `id`"),
        ("---\nid: bogus\ntitle: t\n---\n\n## Description\nx\n", "not of the form"),
        ("---\nid: T-003\n---\n\n## Description\nx\n", "missing `title`"),
        ("---\nid: T-004\ntitle: t\n---\n\n## Acceptance criteria\n- a\n", "no description"),
        ("---\nid: T-005\ntitle: t\n\n## Description\nx\n", "not closed"),
    ],
)
def test_parse_task_file_rejects_malformed(workspace, body, needle):
    p = _write_task(workspace, "T-bad.md", body)
    with pytest.raises(tasks.TasksError) as exc:
        tasks.parse_task_file(p)
    assert needle in str(exc.value)


def test_load_tasks_orders_by_id(workspace):
    _write_task(workspace, "T-002-b.md", VALID_TASK.replace("T-001", "T-002"))
    _write_task(workspace, "T-001-a.md", VALID_TASK)
    _write_task(workspace, "T-010-c.md", VALID_TASK.replace("T-001", "T-010"))
    loaded = tasks.load_tasks(workspace)
    assert [t["id"] for t in loaded] == ["T-001", "T-002", "T-010"]


def test_load_tasks_ignores_overview_and_non_task_files(workspace):
    _write_overview(workspace)
    _write_task(workspace, "T-001-a.md", VALID_TASK)
    (workspace / ".tilth" / "tasks" / "README.md").write_text("not a task")
    loaded = tasks.load_tasks(workspace)
    assert [t["id"] for t in loaded] == ["T-001"]


def test_load_tasks_raises_when_dir_missing(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(tasks.TasksError) as exc:
        tasks.load_tasks(bare)
    assert ".tilth/tasks" in str(exc.value)


def test_load_tasks_raises_when_no_task_files(workspace):
    _write_overview(workspace)  # overview alone is not enough
    with pytest.raises(tasks.TasksError) as exc:
        tasks.load_tasks(workspace)
    assert "No task files" in str(exc.value)


def test_load_tasks_rejects_duplicate_ids(workspace):
    _write_task(workspace, "T-001-a.md", VALID_TASK)
    _write_task(workspace, "T-001-dup.md", VALID_TASK)
    with pytest.raises(tasks.TasksError) as exc:
        tasks.load_tasks(workspace)
    assert "duplicate task id" in str(exc.value)


def test_load_overview_required(workspace):
    with pytest.raises(tasks.TasksError) as exc:
        tasks.load_overview(workspace)
    assert "overview.md" in str(exc.value)


def test_load_overview_rejects_empty(workspace):
    _write_overview(workspace, "   \n")
    with pytest.raises(tasks.TasksError) as exc:
        tasks.load_overview(workspace)
    assert "empty" in str(exc.value)


def test_load_feature_returns_overview_and_tasks(workspace):
    _write_overview(workspace, "# Feature\n\nThe why.")
    _write_task(workspace, "T-001-a.md", VALID_TASK)
    overview, loaded = tasks.load_feature(workspace)
    assert "The why." in overview
    assert [t["id"] for t in loaded] == ["T-001"]
