"""tools.dispatch must report hook telemetry.

We need to distinguish "hook ran, said nothing" from "hook didn't run". The
ToolOutcome carries a `hook_runs` list — one entry per hook that was invoked
for this dispatch — so loop.py can emit a `hook_run` event for each.

`pre_tool` is invoked on every dispatch (it's the gate). `post_edit` is only
invoked when the tool is write_file/edit_file; for other tools its absence
from `hook_runs` is the signal that it was not applicable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tilth import tools


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


def test_dispatch_returns_hook_runs(workspace):
    out = tools.dispatch("bash", {"command": "echo hi"}, workspace)
    assert hasattr(out, "hook_runs")
    assert isinstance(out.hook_runs, list)


def test_pre_tool_logged_with_allow_outcome(workspace):
    out = tools.dispatch("bash", {"command": "echo safe"}, workspace)
    pre = [h for h in out.hook_runs if h["hook"] == "pre_tool"]
    assert len(pre) == 1
    assert pre[0]["outcome"] == "allow"


def test_pre_tool_logged_with_block_outcome(workspace):
    out = tools.dispatch("bash", {"command": "sudo rm -rf /"}, workspace)
    assert out.blocked is True
    pre = [h for h in out.hook_runs if h["hook"] == "pre_tool"]
    assert len(pre) == 1
    assert pre[0]["outcome"] == "block"
    assert "reason" in pre[0]


def test_post_edit_not_logged_for_non_edit_tools(workspace):
    out = tools.dispatch("bash", {"command": "echo hi"}, workspace)
    post = [h for h in out.hook_runs if h["hook"] == "post_edit"]
    assert post == []


def test_post_edit_logged_silent_for_clean_python(workspace):
    target = workspace / "good.py"
    out = tools.dispatch(
        "write_file",
        {"path": "good.py", "content": "x = 1\n"},
        workspace,
    )
    assert target.is_file()
    post = [h for h in out.hook_runs if h["hook"] == "post_edit"]
    assert len(post) == 1
    assert post[0]["outcome"] == "silent"


def test_post_edit_logged_warned_for_lint_failure(workspace):
    out = tools.dispatch(
        "write_file",
        {"path": "bad.py", "content": "import os\n"},  # unused import → ruff warns
        workspace,
    )
    post = [h for h in out.hook_runs if h["hook"] == "post_edit"]
    assert len(post) == 1
    assert post[0]["outcome"] == "warned"
