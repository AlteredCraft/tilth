# Resuming & resetting a session

A Tilth session is append-only on disk: an `events.jsonl` log plus a `checkpoint.json` snapshot under `~/.tilth/sessions/<id>/`. That's enough state to **resume** the run on a fresh process — or, when you're done with it, to **reset** (tear it down) in one command.

## Resuming

```bash
tilth resume               # picks the most recent session
tilth resume <session_id>  # or name one explicitly
```

Bare `tilth resume` selects the most recent session in `~/.tilth/sessions/` by directory name (the timestamp prefix sorts chronologically).

Continuing the [cap-hit example](../deep-dives/two-loops.md#what-can-stop-a-run) — same session (`20260523-082151-45f0a5`), with `TILTH_MAX_ITERATIONS_PER_TASK` bumped from `8` to `16` in `~/.tilth/.env` before resuming (this example was captured when the cap was `8`, below today's default of `32`):

![Terminal capture of `uv run tilth resume`. The harness prints "↻ resume plan: retrying T-003 (was: failed); then: T-004, T-005; last stop: iter_cap", then the session header (session id, branch, worktree, model deepseek/deepseek-v4-flash), then "task T-003 iter 1" with a read_file call, "task T-003 iter 2" with two glob calls, and "task T-003 iter 3" beginning.](../assets/resume-after-iter-cap.png)

*The plan banner spells out what's about to happen: T-003 retried with a fresh iteration budget, then T-004 and T-005 in order. The iteration counter restarts at 1 — the bumped cap gives the retry 16 iterations instead of the 8 that ran out.*
{: .caption }

### What resume does

- **Re-reads the feature from your repo.** Task content always comes from `<workspace>/.tilth/tasks/` (never cached in the session), so a description you sharpen between runs is live on the retry. Tasks already marked `done` in `sessions/<id>/task-status.json` are skipped.
- **Retries the trailing failed task**, if any. Iter-cap, evaluator-cap, and no-case stops mark the in-flight task `failed` and write a `FAILED (...)` placeholder commit; resume flips it back to `pending` and unwinds the placeholder so the retry sees the partial work as uncommitted changes — the evaluator then gets one cumulative diff, not just the new edits. Wall-clock and token caps stop *between* tasks, so there's no failed task to unwind.
- **Re-reads the per-task ledger.** The evaluator's prior verdicts survive on disk, so a retry shows both sides the verdict history from the run before — even though the conversation is gone. See [The worker↔evaluator dialogue](../deep-dives/worker-evaluator-dialogue.md#the-per-task-ledger-memory-across-iterations).
- **Resets the wall-clock budget** for this resume — otherwise a resume the next day would trip `TILTH_MAX_WALL_CLOCK_MINUTES` immediately.
- **Preserves the token total.** If the original run hit `TILTH_MAX_TOKENS`, bump it in `~/.tilth/.env` before resuming or the new run stops on the first token check.

### Under the hood

On wake, `Session.wake()` (`session.py`) reads `checkpoint.json` to reconstruct `tokens_used`, `source`, `workspace`, and `branch`, and resets `started_at` (wall-clock is per-resume). `do_resume_cmd` (`loop.py`) re-reads the feature, then `_prepare_resume()` reads the trailing `stop` event to learn how the last run ended: any `failed` task is flipped back to `pending` and `ws.unwind_failed_commit()` (`workspace.py`) soft-resets the `FAILED (...)` placeholder so the partial work returns to the index. A `session_resume` event records the structured plan (`last_stop`, `retried`, `pending`, `unwound_commit`) — the resume parallel of `session_start`, auditable from `events.jsonl` alone.

Resume doesn't loop endlessly: if a retried task hits a task-halting stop *again*, the outer loop halts with that reason just like the original run, and the next `tilth resume` would retry once more. Each resume is a fresh ride through the same loop.

## Resetting

```bash
tilth reset                  # most recent session
tilth reset <session_id>     # or name one explicitly
tilth reset --yes            # skip the y/N confirmation
```

`tilth reset` is **destructive by design** — it force-removes the worktree even if dirty, since its whole purpose is to discard a session's work. The `[y/N]` prompt (or `--yes`) is the only safety gate; once you confirm, the session is gone. There's no `--all` and no "keep events" mode.

A run lives on disk in two places (working tree under `~/.tilth/sessions/`; branch in the source repo's `.git` — see [Session layout](../deep-dives/session-layout.md)), and reset tears down both halves:

1. Reads `~/.tilth/sessions/<id>/checkpoint.json` for the worktree path and branch; reads the `session_start` event for the source repo path.
2. `git worktree remove --force <path>` in the source repo.
3. `git branch -D session/<id>` in the source repo (force-delete is correct for the `session/*` namespace, which is never auto-merged).
4. Removes `~/.tilth/sessions/<id>/`.

Each step is idempotent — already-missing pieces are reported as skipped, not errored, so you can run `tilth reset` against a half-cleaned-up state and it finishes the job.

### Under the hood

Reset runs entirely outside the normal loop — no model calls, no evaluator. `_read_checkpoint()` recovers `workspace` and `branch`; `_source_for_session()` scans `events.jsonl` for the `session_start` event to recover the source repo; `ws.reset_session_state()` (`workspace.py`) performs the three teardown operations above. `tilth reset` discards `~/.tilth/sessions/<id>/`, so it drops the ledgers too.

### Manual fallback

If `tilth reset` itself can't run (e.g. the session metadata is missing), the manual recipe still works:

```bash
cd <demo-clone-path>                  # e.g. ~/projects/tilth-demo
git worktree prune
git branch -D session/<id>            # if it still exists
rm -rf ~/.tilth/sessions/<id>/        # or $TILTH_SESSIONS_DIR/<id>/ if overridden
```
