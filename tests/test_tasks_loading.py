"""Loading a feature from a feature directory (the prd.json successor).

Task files are read-only inputs authored in the source repo (conventionally
`<repo>/.tilth/<feature>/`). The loader parses frontmatter (id/title) + body
sections into the same task-dict shape the loop consumed from prd.json (minus
status, which is tracked harness-side), and fails fast with an actionable
message when the required pieces are missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tilth import tasks


@pytest.fixture
def feature_dir(tmp_path: Path) -> Path:
    d = tmp_path / "repo" / ".tilth" / "demo"
    d.mkdir(parents=True)
    return d


def _write_task(feature_dir: Path, name: str, body: str) -> Path:
    p = feature_dir / name
    p.write_text(body)
    return p


def _write_overview(feature_dir: Path, text: str = "# Feature\n\nGoal here.") -> None:
    (feature_dir / "overview.md").write_text(text)


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


def test_parse_task_file_extracts_all_fields(feature_dir):
    p = _write_task(feature_dir, "T-001-add.md", VALID_TASK)
    task = tasks.parse_task_file(p)
    assert task["id"] == "T-001"
    assert task["title"] == "Add the add subcommand"
    assert "todo_cli/__main__.py:main()" in task["description"]
    assert task["acceptance_criteria"] == ["`todo add x` exits 0", "the item is persisted"]
    assert "status" not in task  # tracked harness-side, never parsed from the file


def test_description_falls_back_to_body_without_heading(feature_dir):
    body = "---\nid: T-002\ntitle: thing\n---\n\nJust do the thing, no heading.\n"
    p = _write_task(feature_dir, "T-002-thing.md", body)
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
def test_parse_task_file_rejects_malformed(feature_dir, body, needle):
    p = _write_task(feature_dir, "T-bad.md", body)
    with pytest.raises(tasks.TasksError) as exc:
        tasks.parse_task_file(p)
    assert needle in str(exc.value)


def test_load_tasks_orders_by_id(feature_dir):
    _write_task(feature_dir, "T-002-b.md", VALID_TASK.replace("T-001", "T-002"))
    _write_task(feature_dir, "T-001-a.md", VALID_TASK)
    _write_task(feature_dir, "T-010-c.md", VALID_TASK.replace("T-001", "T-010"))
    loaded = tasks.load_tasks(feature_dir)
    assert [t["id"] for t in loaded] == ["T-001", "T-002", "T-010"]


def test_load_tasks_ignores_overview_and_non_task_files(feature_dir):
    _write_overview(feature_dir)
    _write_task(feature_dir, "T-001-a.md", VALID_TASK)
    (feature_dir / "README.md").write_text("not a task")
    loaded = tasks.load_tasks(feature_dir)
    assert [t["id"] for t in loaded] == ["T-001"]


def test_load_tasks_raises_when_dir_missing(tmp_path):
    bare = tmp_path / "nope"
    with pytest.raises(tasks.TasksError) as exc:
        tasks.load_tasks(bare)
    assert "No feature directory" in str(exc.value)


def test_load_tasks_raises_when_no_task_files(feature_dir):
    _write_overview(feature_dir)  # overview alone is not enough
    with pytest.raises(tasks.TasksError) as exc:
        tasks.load_tasks(feature_dir)
    assert "No task files" in str(exc.value)


def test_load_tasks_rejects_duplicate_ids(feature_dir):
    _write_task(feature_dir, "T-001-a.md", VALID_TASK)
    _write_task(feature_dir, "T-001-dup.md", VALID_TASK)
    with pytest.raises(tasks.TasksError) as exc:
        tasks.load_tasks(feature_dir)
    assert "duplicate task id" in str(exc.value)


def test_load_overview_required(feature_dir):
    with pytest.raises(tasks.TasksError) as exc:
        tasks.load_overview(feature_dir)
    assert "overview.md" in str(exc.value)


def test_load_overview_rejects_empty(feature_dir):
    _write_overview(feature_dir, "   \n")
    with pytest.raises(tasks.TasksError) as exc:
        tasks.load_overview(feature_dir)
    assert "empty" in str(exc.value)


def test_load_feature_returns_overview_and_tasks(feature_dir):
    _write_overview(feature_dir, "# Feature\n\nThe why.")
    _write_task(feature_dir, "T-001-a.md", VALID_TASK)
    overview, loaded = tasks.load_feature(feature_dir)
    assert "The why." in overview
    assert [t["id"] for t in loaded] == ["T-001"]
