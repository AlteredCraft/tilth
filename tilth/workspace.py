"""Per-session git worktree management.

The user-facing `workspace` argument points at the *source* project (must be a git
repo with a clean main branch). On run start, we create a worktree at
`sessions/<id>/workspace/` on a fresh branch `session/<id>`. The agent operates only
on that worktree. The branch is never auto-merged — humans review like any other
branch.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


class WorkspaceError(RuntimeError):
    pass


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def repo_root(path: Path) -> Path:
    """Return the git repo root that contains `path` (a feature dir inside it).

    `path` must exist. Raises `WorkspaceError` when it isn't inside a git repo —
    Tilth derives the worktree source from the feature directory the user runs.
    """
    if not path.exists():
        raise WorkspaceError(f"path does not exist: {path}")
    start = path if path.is_dir() else path.parent
    proc = _git(["rev-parse", "--show-toplevel"], start)
    if proc.returncode != 0:
        raise WorkspaceError(
            f"{path} is not inside a git repo. Tilth needs the feature directory "
            "to live in a git repo with at least one commit."
        )
    return Path(proc.stdout.strip())


def ensure_git_repo(source: Path) -> None:
    """Verify `source` is a git repo with at least one commit on a default branch."""
    if not source.is_dir():
        raise WorkspaceError(f"workspace does not exist: {source}")
    if not (source / ".git").exists():
        raise WorkspaceError(
            f"{source} is not a git repo. Initialise it first:\n"
            f"  cd {source} && git init -b main && git add -A && "
            f'git commit -m "initial"'
        )
    proc = _git(["rev-parse", "HEAD"], source)
    if proc.returncode != 0:
        raise WorkspaceError(
            f"{source} has no commits. Add an initial commit:\n"
            f'  cd {source} && git add -A && git commit -m "initial"'
        )


def create_worktree(source: Path, session_id: str, target: Path) -> tuple[Path, str]:
    """Create a worktree of `source` at `target` on a new branch `session/<id>`."""
    target.parent.mkdir(parents=True, exist_ok=True)
    branch = f"session/{session_id}"
    proc = _git(["worktree", "add", str(target), "-b", branch], source)
    if proc.returncode != 0:
        raise WorkspaceError(
            f"failed to create worktree at {target}: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return target, branch


def ensure_worktree(source: Path, session_id: str, target: Path) -> tuple[Path, str]:
    """Return the worktree at `target` if it already exists, else create it.

    Idempotent wrapper over `create_worktree` — `tilth run` calls it once per
    session; if the worktree already exists (e.g. a re-entered run) it's reused
    rather than re-created.
    """
    branch = f"session/{session_id}"
    proc = _git(["worktree", "list", "--porcelain"], source)
    if proc.returncode == 0:
        target_resolved = target.resolve() if target.exists() else target
        for record in proc.stdout.split("\n\n"):
            wt_line = next(
                (ln for ln in record.splitlines() if ln.startswith("worktree ")),
                None,
            )
            if wt_line is None:
                continue
            existing = Path(wt_line[len("worktree "):]).resolve()
            if existing == target_resolved:
                return target, branch
    return create_worktree(source, session_id, target)


def worktree_gitdir(worktree: Path) -> Path | None:
    """Resolve a linked worktree's git admin dir from its `.git` pointer file.

    A linked worktree has a `.git` *file* (not a dir) holding
    `gitdir: <source>/.git/worktrees/<name>`. Returns that path, or None when the
    worktree is missing or `.git` isn't the expected pointer shape. This is the
    map `tilth info <id>` shows alongside the worktree folder.
    """
    dotgit = worktree / ".git"
    if not dotgit.is_file():
        return None
    text = dotgit.read_text(errors="replace").strip()
    prefix = "gitdir:"
    if not text.startswith(prefix):
        return None
    return Path(text[len(prefix):].strip())


def worktree_registered(source: Path, worktree: Path) -> bool | None:
    """Whether `worktree` is a live entry in `source`'s worktree registry.

    None when the registry can't be read (source gone / not a repo); True if
    `git worktree list` includes the path; False if it's stale (the directory
    was removed by hand and only an admin entry lingers — `git worktree prune`
    would clear it).
    """
    proc = _git(["worktree", "list", "--porcelain"], source)
    if proc.returncode != 0:
        return None
    target = worktree.resolve() if worktree.exists() else worktree
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            existing = Path(line[len("worktree "):].strip())
            existing = existing.resolve() if existing.exists() else existing
            if existing == target:
                return True
    return False


def commit_task(worktree: Path, task_id: str, title: str) -> str | None:
    """Stage and commit. Returns short SHA, or None if there was nothing to commit."""
    _git(["add", "-A"], worktree)
    status = _git(["status", "--porcelain"], worktree)
    if not status.stdout.strip():
        return None
    msg = f"{task_id}: {title}\n\nGenerated by tilth."
    proc = _git(["commit", "-m", msg], worktree)
    if proc.returncode != 0:
        raise WorkspaceError(f"commit failed: {proc.stderr.strip() or proc.stdout.strip()}")
    sha = _git(["rev-parse", "--short", "HEAD"], worktree).stdout.strip()
    return sha or None


# --- remote / publishing ----------------------------------------------------
#
# Used by the user-invoked `tilth push` / `tilth pr` to get a session branch out
# to a remote. They run against the *source* repo (which holds the remote and
# shares its object store with the worktree's branch), never inside the loop.


def remote_url(source: Path, remote: str = "origin") -> str | None:
    """The configured URL of `remote` in `source`, or None if it isn't set."""
    proc = _git(["remote", "get-url", remote], source)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def branch_on_remote(source: Path, branch: str, remote: str = "origin") -> bool:
    """Whether `branch` already exists on `remote` (a `git ls-remote --heads` hit)."""
    proc = _git(["ls-remote", "--heads", remote, branch], source)
    return proc.returncode == 0 and bool(proc.stdout.strip())


def push_branch(source: Path, branch: str, remote: str = "origin") -> None:
    """Push `branch` to `remote`, setting upstream. Raises WorkspaceError on failure."""
    proc = _git(["push", "-u", remote, branch], source)
    if proc.returncode != 0:
        raise WorkspaceError(
            f"failed to push {branch} to {remote}: {proc.stderr.strip() or proc.stdout.strip()}"
        )


def default_remote_branch(source: Path, remote: str = "origin") -> str | None:
    """Best-effort default branch of `remote` (e.g. 'main') from its tracked HEAD.

    Returns None when the remote's HEAD isn't known locally (never fetched, or
    `set-head` not run) — the caller falls back to 'main'.
    """
    proc = _git(["symbolic-ref", f"refs/remotes/{remote}/HEAD"], source)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout.strip().rsplit("/", 1)[-1] or None


def remote_web_url(url: str) -> str | None:
    """Normalise a GitHub remote URL to its `https://github.com/<owner>/<repo>` web base.

    Handles the scp-like (`git@github.com:O/r.git`), https, and ssh forms, with or
    without a trailing `.git`. Returns None for non-GitHub or unparseable remotes —
    the caller then can't offer a compare link.
    """
    url = (url or "").strip()
    if not url:
        return None
    m = re.match(r"^git@github\.com:(?P<path>.+?)(?:\.git)?/?$", url)
    if not m:
        m = re.match(
            r"^(?:https?|ssh)://(?:[^@/]+@)?github\.com/(?P<path>.+?)(?:\.git)?/?$", url
        )
    if not m:
        return None
    return f"https://github.com/{m.group('path')}"


# --- cleanse (retire a finished session: worktree + branch gone, dir kept) -----


def branch_exists(source: Path, branch: str) -> bool:
    """Whether `branch` is a live local head in `source`."""
    return (
        _git(["rev-parse", "--verify", "-q", f"refs/heads/{branch}"], source).returncode == 0
    )


def branch_integrated(source: Path, branch: str) -> bool:
    """True if `branch`'s tip is reachable from another local or remote ref — its
    work is merged into another branch (e.g. main) or pushed to a remote.

    The safety gate for `tilth cleanse`: deleting the branch only loses nothing
    when the commits survive elsewhere. The branch's own head is excluded; any
    other local head or remote-tracking ref containing the tip counts. Reflects
    local knowledge only — a merge that happened solely on the remote needs a
    `git fetch` first to be seen.
    """
    tip = _git(["rev-parse", "--verify", "-q", branch], source).stdout.strip()
    if not tip:
        return False
    proc = _git(
        ["for-each-ref", "--contains", tip, "--format=%(refname)",
         "refs/heads/", "refs/remotes/"],
        source,
    )
    if proc.returncode != 0:
        return False
    own = f"refs/heads/{branch}"
    return any(r.strip() and r.strip() != own for r in proc.stdout.splitlines())


def cleanse_session_state(
    source: Path, worktree: Path | None, branch: str | None
) -> list[str]:
    """Remove a session's worktree (+ its admin entry) and branch, KEEPING the
    session dir. The `tilth cleanse` counterpart to `reset_session_state`, which
    also drops the dir. Raises WorkspaceError if the worktree can't be removed.
    """
    notes: list[str] = []
    if worktree is not None:
        if worktree.exists():
            proc = _git(["worktree", "remove", "--force", str(worktree)], source)
            if proc.returncode != 0:
                raise WorkspaceError(
                    f"worktree remove failed: {proc.stderr.strip() or proc.stdout.strip()}"
                )
            notes.append(f"removed worktree {worktree}")
        else:
            _git(["worktree", "prune"], source)
            notes.append(f"worktree already gone (pruned admin entries) {worktree}")
    if branch:
        proc = _git(["branch", "-D", branch], source)
        if proc.returncode == 0:
            notes.append(f"deleted branch {branch}")
        else:
            err = (proc.stderr.strip() or proc.stdout.strip()) or "unknown error"
            notes.append(
                f"branch {branch} already gone"
                if "not found" in err.lower()
                else f"branch delete warning: {err}"
            )
    return notes


def reset_session_state(
    source: Path | None,
    worktree: Path | None,
    branch: str | None,
    session_dir: Path,
) -> list[str]:
    """Tear down a session's git artifacts and remove its directory.

    Idempotent — already-missing pieces are reported as skipped, not errored.
    Forces worktree removal even if dirty: --reset's whole purpose is to discard
    a session's work, and the caller has already confirmed via the CLI prompt.
    """
    notes: list[str] = []

    if worktree is not None and source is not None:
        if worktree.exists():
            proc = _git(["worktree", "remove", "--force", str(worktree)], source)
            if proc.returncode == 0:
                notes.append(f"removed worktree {worktree}")
            else:
                err = (proc.stderr.strip() or proc.stdout.strip()) or "unknown error"
                notes.append(f"worktree remove FAILED: {err}")
                return notes
        else:
            _git(["worktree", "prune"], source)
            notes.append(f"worktree already gone (pruned admin entries) {worktree}")

    if branch and source is not None:
        proc = _git(["branch", "-D", branch], source)
        if proc.returncode == 0:
            notes.append(f"deleted branch {branch}")
        else:
            err = (proc.stderr.strip() or proc.stdout.strip()) or "unknown error"
            if "not found" in err.lower():
                notes.append(f"branch {branch} already gone")
            else:
                notes.append(f"branch delete warning: {err}")

    if session_dir.exists():
        shutil.rmtree(session_dir)
        notes.append(f"removed session dir {session_dir}")
    else:
        notes.append(f"session dir already gone {session_dir}")

    return notes


def unwind_failed_commit(worktree: Path) -> bool:
    """If HEAD is a FAILED placeholder, soft-reset it so the work returns to the index.

    The failure path commits with a `FAILED (<reason>): ...` message to mark progress
    on the session branch. On a retry, we want the next task_diff to capture the full
    body of work, not just changes since the placeholder. Returns True on a reset.
    """
    proc = _git(["log", "-1", "--pretty=%s"], worktree)
    if proc.returncode != 0:
        return False
    if not proc.stdout.lstrip().startswith("FAILED ("):
        return False
    reset = _git(["reset", "--soft", "HEAD^"], worktree)
    return reset.returncode == 0


def diff_since_main(worktree: Path) -> str:
    """Diff the worktree branch against the source repo's main branch."""
    proc = _git(["diff", "main...HEAD"], worktree)
    return proc.stdout


def task_diff(worktree: Path) -> str:
    """Diff of the current (uncommitted) task work, against HEAD.

    Includes both staged and unstaged changes. Used by the evaluator to evaluate
    a task's diff before it's committed.
    """
    proc = _git(["add", "-N", "."], worktree)  # mark untracked so they appear in diff
    if proc.returncode != 0:
        return f"(failed to add intent-to-add: {proc.stderr})"
    proc = _git(["diff", "HEAD"], worktree)
    return proc.stdout


def task_diff_summary(worktree: Path) -> str:
    """Compact one-line summary of the current task diff: 'path (+a -d); ...'.

    For ledger entries — enough for the evaluator to see *what* changed at a
    prior iteration without re-reading the whole diff. Binary files render as
    'path (binary)'.
    """
    proc = _git(["add", "-N", "."], worktree)
    if proc.returncode != 0:
        return f"(failed to add intent-to-add: {proc.stderr.strip()})"
    proc = _git(["diff", "--numstat", "HEAD"], worktree)
    parts: list[str] = []
    for line in proc.stdout.splitlines():
        cols = line.split("\t")
        if len(cols) != 3:
            continue
        added, deleted, path = cols
        if added == "-" or deleted == "-":
            parts.append(f"{path} (binary)")
        else:
            parts.append(f"{path} (+{added} -{deleted})")
    return "; ".join(parts) if parts else "(no changes)"
