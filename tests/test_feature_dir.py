"""The feature-directory model: `tilth run` is given the feature dir directly,
derives the enclosing git repo, and records the dir on the session so `resume`
reloads the same feature.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tilth import workspace as ws
from tilth.session import Session


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("# fixture\n")
    env = {
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "PATH": __import__("os").environ.get("PATH", ""),
    }
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial", "--no-gpg-sign"],
        cwd=path,
        check=True,
        capture_output=True,
        env=env,
    )


# --- repo_root --------------------------------------------------------------

def test_repo_root_finds_enclosing_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    feature = repo / ".tilth" / "feature-x"
    feature.mkdir(parents=True)
    assert ws.repo_root(feature).resolve() == repo.resolve()


def test_repo_root_raises_outside_a_repo(tmp_path: Path) -> None:
    loose = tmp_path / "loose" / ".tilth" / "feature-x"
    loose.mkdir(parents=True)
    with pytest.raises(ws.WorkspaceError):
        ws.repo_root(loose)


def test_repo_root_raises_when_path_missing(tmp_path: Path) -> None:
    with pytest.raises(ws.WorkspaceError):
        ws.repo_root(tmp_path / "does-not-exist")


# --- session feature_dir round-trip -----------------------------------------

def test_feature_dir_round_trips_through_wake(tmp_path: Path) -> None:
    feature = tmp_path / "repo" / ".tilth" / "feature-x"
    s = Session.new(tmp_path / "sessions")
    s.feature_dir = feature
    s.save_checkpoint()

    woken = Session.wake(tmp_path / "sessions", s.session_id)
    assert woken.feature_dir == feature


def test_feature_dir_in_checkpoint_json(tmp_path: Path) -> None:
    feature = tmp_path / "repo" / ".tilth" / "feature-x"
    s = Session.new(tmp_path / "sessions")
    s.feature_dir = feature
    s.save_checkpoint()
    cp = json.loads(s.checkpoint_path.read_text())
    assert cp["feature_dir"] == str(feature)


def test_wake_defaults_missing_feature_dir_to_none(tmp_path: Path) -> None:
    s = Session.new(tmp_path / "sessions")
    cp = json.loads(s.checkpoint_path.read_text())
    cp.pop("feature_dir", None)
    s.checkpoint_path.write_text(json.dumps(cp))
    woken = Session.wake(tmp_path / "sessions", s.session_id)
    assert woken.feature_dir is None
