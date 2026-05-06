# Reset mechanics

`--reset [<session_id>]` tears down a session's artifacts. It runs entirely outside the normal loop — no model calls, no validators, no judge.

## The flow

1. **Resolve the session.** Bare `--reset` picks the latest by directory name (parallel to `--resume`); explicit `--reset <id>` targets that one.
2. **Recover paths.** `_read_checkpoint()` gives `workspace` (worktree) and `branch`. `_source_for_session()` scans `events.jsonl` for the `session_start` event to recover the source repo (the path is already in the log, so no checkpoint schema change was needed for this).
3. **Confirm.** `input("Continue? [y/N] ")` unless `--yes` is passed. The prompt is the default; `--yes` is the override.
4. **Tear down via `ws.reset_session_state()`:**
    - `git worktree remove --force <worktree>` against the source. Force is always passed: `--reset`'s whole purpose is to discard a session's work, and the user already confirmed via the `[y/N]` prompt (or `--yes`). Refusing on dirty would defeat the user's stated intent. A failure here now indicates a true filesystem-level problem (locks, perms) rather than uncommitted changes.
    - `git branch -D session/<id>` against the source (force-delete is correct for the `session/*` namespace, which is never auto-merged).
    - `shutil.rmtree(sessions/<id>/)` for whatever's left on the harness side.
5. Each step is **idempotent** — already-missing pieces are reported as skipped, not errored. You can run `--reset` against a half-cleaned-up state and it'll finish the job.

> **Diagram suggestion** — *step-by-step flow diagram of the four side effects, each with a green "ok" branch and a yellow "already gone, skipped" branch. Reinforces idempotency as a property of the whole command, not just one step.*

There's no `--reset --all` and no "keep events" mode. The `[y/N]` prompt is the only safety gate; once you confirm, the session is gone.
