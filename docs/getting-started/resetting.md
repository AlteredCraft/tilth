# Resetting a session

Drop a session's worktree, delete its `session/<id>` branch from the source repo, and remove `sessions/<id>/` — in a single command.

## How to reset

```bash
uv run tilth --reset                  # most recent session
uv run tilth --reset <session_id>     # or name one explicitly
uv run tilth --reset --yes            # skip the y/N confirmation
```

`--reset` is **destructive by design** — it force-removes the worktree even if dirty, since its whole purpose is to discard a session's work. The `[y/N]` prompt (or `--yes` to skip) is the safety gate.

`--reset` and `--resume` are mutually exclusive on a single invocation.

## What reset removes

This is the codified version of the three-step manual cleanup (`rm -rf sessions/<id>` + `git worktree prune` + `git branch -D session/<id>`). It:

1. Reads `sessions/<id>/checkpoint.json` to recover the worktree path and branch name; reads the `session_start` event for the source repo path.
2. Runs `git worktree remove --force <path>` in the source repo.
3. Runs `git branch -D session/<id>` in the source repo (force-delete is the right default for the `session/*` namespace, which is never auto-merged).
4. Removes `sessions/<id>/`.

Each step is idempotent — already-missing pieces are reported as skipped, not errored. You can run `--reset` against a half-cleaned-up state and it'll finish the job.

> **Diagram suggestion** — *three-column "before / after" of the filesystem and the source-repo refs: left column shows `sessions/<id>/`, the worktree directory, and the `session/<id>` branch all present; right column shows all three gone. A small annotation in between names the three operations performed.*

For the implementation walk-through and idempotency story, see [Reset mechanics](../deep-dives/reset-mechanics.md).

## Manual fallback

If `--reset` itself can't run (e.g. the session metadata is missing), the manual recipe still works:

```bash
cd <demo-clone-path>                  # e.g. {{your projects folder}}/tilth-demo
git worktree prune
git branch -D session/<id>            # if it still exists
rm -rf <tilth-clone-path>/sessions/<id>/
```
