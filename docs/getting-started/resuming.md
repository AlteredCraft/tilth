# Resuming a session

Tilth sessions are append-only on disk: an `events.jsonl` log plus a `checkpoint.json` snapshot, both under `sessions/<id>/`. That's enough state to wake the harness on a fresh process and continue.

## How to resume

```bash
uv run tilth resume               # picks the most recent session
uv run tilth resume <session_id>  # or name one explicitly
```

Bare `tilth resume` selects the most recent session in `sessions/` by directory name (the timestamp prefix sorts chronologically).

Continuing the [cap-hit example from the caps deep-dive](../deep-dives/caps.md#what-hitting-a-cap-looks-like) — same session (`20260523-082151-45f0a5`), with `TILTH_MAX_ITERATIONS_PER_TASK` bumped from `8` to `16` in `.env` before resuming (this worked example was captured when the cap was set to `8`, below today's default of `32`):

![Terminal capture of `uv run tilth resume`. The harness prints "↻ resume plan: retrying T-003 (was: failed); then: T-004, T-005; last stop: iter_cap", then the session header (session id, branch, worktree, model deepseek/deepseek-v4-flash), then "task T-003 iter 1" with a read_file call, "task T-003 iter 2" with two glob calls, and "task T-003 iter 3" beginning.](../assets/resume-after-iter-cap.png)

*The plan banner spells out what's about to happen: T-003 retried with a fresh iteration budget, then T-004 and T-005 picked up in order. The iteration counter restarts at 1 — the bumped cap gives the retry 16 iterations to work with instead of the 8 that ran out last time.*
{: .caption }

## What resume does

- **Re-reads the feature from your repo.** The task content always comes from `<workspace>/.tilth/tasks/` (resume re-loads it from the session's recorded source path), so you can sharpen a task description between runs and the retry sees the new text. Per-task *status* lives in the harness's `sessions/<id>/task-status.json`; tasks already marked `done` there are skipped.
- **Retries the trailing failed task**, if any. Iter-cap, evaluator-cap, empty-response, and no-case stops mark the in-flight task `failed` and write a `FAILED (...)` placeholder commit; resume flips that task back to `pending` and unwinds the placeholder so the retry sees the partial work as uncommitted changes (and the evaluator will see a single cumulative diff, not just the new edits). Wall-clock and token caps stop *between* tasks, so there's no failed task to unwind; interrupt leaves the session `running`, error leaves it `failed` — neither marks a task failed.
- **Re-reads the per-task ledger.** Each task's `sessions/<id>/ledger/<task_id>.jsonl` (the evaluator's prior verdicts) survives on disk, so a resumed retry shows the worker and evaluator the verdict history from the run before — the live conversation is gone, the ledger is not. See [The worker↔evaluator dialogue](../deep-dives/worker-evaluator-dialogue.md).
- **Resets the wall-clock budget** for this resume — otherwise a resume the next day would trip `TILTH_MAX_WALL_CLOCK_MINUTES` immediately.
- **Preserves the token total.** If the original run hit `TILTH_MAX_TOKENS`, bump it in `.env` before resuming or the new run will stop on the first token check.

The resume plan is printed up front (which task is being retried, which are pending) and logged as a `session_resume` event in `events.jsonl`.

> **Diagram suggestion** — *timeline showing two runs of the same session glued together: first run hits a cap, then `tilth resume` resets the wall-clock baseline, retries the trailing failed task, then continues through pending tasks. Token total is shown as a single rising line across both runs; wall-clock is shown as two separate bars.*

For the under-the-hood story of what resume actually mutates, see [Resume mechanics](../deep-dives/resume-mechanics.md).
