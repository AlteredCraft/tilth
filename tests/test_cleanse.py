"""tilth cleanse — retire a finished, integrated session while keeping its record.

Covers the integration gate (workspace helpers), the worktree+branch teardown
that preserves the session dir, the handler (refuse / happy / idempotent), and
that `tilth info` reads an archived session calmly rather than as "missing".
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tilth import loop
from tilth import workspace as ws


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    _git("init", "-b", "main", cwd=src)
    _git("config", "user.email", "t@t.t", cwd=src)
    _git("config", "user.name", "t", cwd=src)
    (src / "f.txt").write_text("hi\n")
    _git("add", "-A", cwd=src)
    _git("commit", "-m", "initial", "--no-gpg-sign", cwd=src)
    return src


def _make_session_branch(src: Path, sid: str, sessions_dir: Path) -> Path:
    """A session worktree + branch with one commit, plus a checkpoint to wake from."""
    (sessions_dir / sid).mkdir(parents=True)
    wt = sessions_dir / sid / "workspace"
    _git("worktree", "add", str(wt), "-b", f"session/{sid}", cwd=src)
    (wt / "work.txt").write_text("agent work\n")
    _git("add", "-A", cwd=wt)
    _git("commit", "-m", "T-001 work", "--no-gpg-sign", cwd=wt)
    (sessions_dir / sid / "checkpoint.json").write_text(json.dumps({
        "status": "all_done", "source": str(src), "workspace": str(wt),
        "branch": f"session/{sid}", "feature_dir": str(src / ".tilth" / "demo"),
    }))
    (sessions_dir / sid / "events.jsonl").write_text("")
    return wt


def _merge_into_main(src: Path, sid: str) -> None:
    _git("merge", f"session/{sid}", "--no-edit", "--no-gpg-sign", cwd=src)


# --- gate helpers -----------------------------------------------------------


def test_branch_exists(repo: Path, tmp_path: Path) -> None:
    _make_session_branch(repo, "s1", tmp_path / "sessions")
    assert ws.branch_exists(repo, "session/s1") is True
    assert ws.branch_exists(repo, "session/nope") is False


def test_branch_integrated_false_when_unmerged_unpushed(repo: Path, tmp_path: Path) -> None:
    _make_session_branch(repo, "s1", tmp_path / "sessions")
    assert ws.branch_integrated(repo, "session/s1") is False


def test_branch_integrated_true_when_merged(repo: Path, tmp_path: Path) -> None:
    _make_session_branch(repo, "s1", tmp_path / "sessions")
    _merge_into_main(repo, "s1")
    assert ws.branch_integrated(repo, "session/s1") is True


def test_branch_integrated_true_when_pushed(repo: Path, tmp_path: Path) -> None:
    _make_session_branch(repo, "s1", tmp_path / "sessions")
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git("init", "--bare", "-b", "main", cwd=origin)
    _git("remote", "add", "origin", str(origin), cwd=repo)
    _git("push", "origin", "session/s1", cwd=repo)
    assert ws.branch_integrated(repo, "session/s1") is True


# --- teardown that keeps the session dir ------------------------------------


def test_cleanse_state_removes_worktree_and_branch_keeps_dir(repo: Path, tmp_path: Path) -> None:
    sdir = tmp_path / "sessions"
    wt = _make_session_branch(repo, "s1", sdir)
    ws.cleanse_session_state(repo, wt, "session/s1")
    assert not wt.exists()                                        # worktree gone
    assert ws.branch_exists(repo, "session/s1") is False         # branch gone
    assert not (repo / ".git" / "worktrees" / "workspace").exists()  # admin entry gone
    assert (sdir / "s1" / "checkpoint.json").exists()            # record KEPT


# --- handler ----------------------------------------------------------------


@pytest.fixture
def sessioned(repo: Path, tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    sdir = tmp_path / "sessions"
    monkeypatch.setattr(loop, "SESSIONS_DIR", sdir)
    return repo, sdir


def test_cleanse_refuses_unintegrated(sessioned, capsys) -> None:
    repo, sdir = sessioned
    _make_session_branch(repo, "s1", sdir)
    rc = loop.do_cleanse_cmd("s1")
    assert rc == 2
    assert "isn't merged" in capsys.readouterr().out
    assert ws.branch_exists(repo, "session/s1") is True          # nothing removed
    assert (sdir / "s1" / "workspace").exists()


def test_cleanse_happy_path_keeps_record_and_marks_archived(sessioned, capsys) -> None:
    repo, sdir = sessioned
    _make_session_branch(repo, "s1", sdir)
    _merge_into_main(repo, "s1")
    rc = loop.do_cleanse_cmd("s1", assume_yes=True)
    assert rc == 0
    assert "cleansed" in capsys.readouterr().out
    assert not (sdir / "s1" / "workspace").exists()
    assert ws.branch_exists(repo, "session/s1") is False
    assert (sdir / "s1" / "checkpoint.json").exists()
    cp = json.loads((sdir / "s1" / "checkpoint.json").read_text())
    assert cp["archived"] is True
    assert cp["status"] == "all_done"                            # outcome preserved
    assert '"archived"' in (sdir / "s1" / "events.jsonl").read_text()


def test_cleanse_prompts_and_aborts_on_no(sessioned, capsys, monkeypatch) -> None:
    repo, sdir = sessioned
    _make_session_branch(repo, "s1", sdir)
    _merge_into_main(repo, "s1")
    monkeypatch.setattr("builtins.input", lambda _="": "n")
    rc = loop.do_cleanse_cmd("s1")
    assert rc == 1
    assert "aborted" in capsys.readouterr().out
    assert ws.branch_exists(repo, "session/s1") is True          # nothing removed
    assert (sdir / "s1" / "workspace").exists()


def test_cleanse_prompts_and_proceeds_on_yes(sessioned, capsys, monkeypatch) -> None:
    repo, sdir = sessioned
    _make_session_branch(repo, "s1", sdir)
    _merge_into_main(repo, "s1")
    monkeypatch.setattr("builtins.input", lambda _="": "y")
    rc = loop.do_cleanse_cmd("s1")
    assert rc == 0
    assert "cleansed" in capsys.readouterr().out
    assert ws.branch_exists(repo, "session/s1") is False


def test_cleanse_idempotent_after_branch_gone(sessioned) -> None:
    repo, sdir = sessioned
    _make_session_branch(repo, "s1", sdir)
    _merge_into_main(repo, "s1")
    assert loop.do_cleanse_cmd("s1", assume_yes=True) == 0
    # second run: already archived -> a no-op, not a refuse or a prompt
    assert loop.do_cleanse_cmd("s1") == 0


def test_info_reads_archived_session_calmly(sessioned, capsys) -> None:
    repo, sdir = sessioned
    _make_session_branch(repo, "s1", sdir)
    _merge_into_main(repo, "s1")
    loop.do_cleanse_cmd("s1", assume_yes=True)
    capsys.readouterr()
    loop.do_info_cmd("s1")
    out = capsys.readouterr().out
    assert "archived" in out
    assert "missing" not in out
