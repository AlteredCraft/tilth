"""tools.dispatch must report hook telemetry.

We need to distinguish "hook ran, said nothing" from "hook didn't run". The
ToolOutcome carries a `hook_runs` list — one entry per hook that was invoked
for this dispatch — so loop.py can emit a `hook_run` event for each.

`pre_tool` is invoked on every dispatch (it's the gate). The prompt-driven
refactor removed the post_edit ruff hook, so no dispatch reports a `post_edit`
hook run anymore.
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


def test_no_post_edit_hook_run_on_any_tool(workspace):
    """post_edit was removed — even a write_file dispatch reports no post_edit."""
    target = workspace / "good.py"
    out = tools.dispatch(
        "write_file",
        {"path": "good.py", "content": "x = 1\n"},
        workspace,
    )
    assert target.is_file()
    assert [h for h in out.hook_runs if h["hook"] == "post_edit"] == []
    bash_out = tools.dispatch("bash", {"command": "echo hi"}, workspace)
    assert [h for h in bash_out.hook_runs if h["hook"] == "post_edit"] == []
