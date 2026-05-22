"""tools.files._resolve must keep agent file access inside the workspace.

The agent's file tools (read, write, edit) all funnel through _resolve.
This is the only check that stops the agent from reading or writing outside
the workspace via crafted relative paths, absolute paths, or symlinks
that chain out.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tilth.tools.files import _resolve


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


def test_normal_relative_path_resolves_under_workspace(workspace):
    p = _resolve("subdir/file.txt", workspace)
    assert p.is_relative_to(workspace.resolve())


def test_workspace_root_itself_is_allowed(workspace):
    assert _resolve(".", workspace) == workspace.resolve()


@pytest.mark.parametrize(
    "escape_path",
    [
        "../../etc/passwd",
        "../sibling/secret",
        "subdir/../../escape",
        "/etc/passwd",
        "/tmp/absolute",
    ],
)
def test_escape_attempts_raise_value_error(workspace, escape_path):
    with pytest.raises(ValueError, match="escapes workspace"):
        _resolve(escape_path, workspace)


@pytest.mark.parametrize("bad_input", ["", "   ", "\n"])
def test_empty_or_whitespace_path_raises(workspace, bad_input):
    with pytest.raises(ValueError, match="must be a non-empty string"):
        _resolve(bad_input, workspace)


def test_symlink_pointing_outside_workspace_is_rejected(tmp_path):
    workspace = tmp_path / "ws"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret").write_text("hidden")
    (workspace / "link").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes workspace"):
        _resolve("link/secret", workspace)
