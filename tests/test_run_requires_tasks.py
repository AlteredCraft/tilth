"""`tilth run` fails fast (no session/worktree created) when the feature
directory is missing or malformed, and prints the templates so the user can
author one.

There is no prep step — `run` reads the feature directory it's given directly.
"""

from __future__ import annotations

import pytest

from tilth import loop


@pytest.fixture(autouse=True)
def _isolate_sessions(monkeypatch, tmp_path):
    """Guard: if the code ever got past fail-fast it would create a session —
    point SESSIONS_DIR at a tmp dir so a regression can't litter the repo."""
    monkeypatch.setattr(loop, "SESSIONS_DIR", tmp_path / "sessions")


def test_run_fails_fast_when_feature_dir_missing(tmp_path, capsys):
    rc = loop.do_run_cmd(tmp_path / "repo" / ".tilth" / "feature-x")
    assert rc == 2
    out = capsys.readouterr().out
    assert "overview.md" in out  # the required-file message + template
    # no session was created
    assert not (loop.SESSIONS_DIR).exists() or not any(loop.SESSIONS_DIR.iterdir())


def test_run_fails_fast_when_overview_missing(tmp_path, capsys):
    feature = tmp_path / "repo" / ".tilth" / "feature-x"
    feature.mkdir(parents=True)
    (feature / "T-001-x.md").write_text(
        "---\nid: T-001\ntitle: x\n---\n\n## Description\nbuild x\n"
    )
    rc = loop.do_run_cmd(feature)
    assert rc == 2
    out = capsys.readouterr().out
    assert "overview.md" in out
