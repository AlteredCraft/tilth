# Resuming a session

Tilth sessions are append-only on disk: an `events.jsonl` log plus a `checkpoint.json` snapshot, both under `sessions/<id>/`. That's enough state to wake the harness on a fresh process and continue.

## How to resume

```bash
uv run tilth resume               # picks the most recent session
uv run tilth resume <session_id>  # or name one explicitly
```

Bare `tilth resume` selects the most recent session in `sessions/` by directory name (the timestamp prefix sorts chronologically). The pre-Phase-3 flag form `--resume` still works for one minor version.

Continuing the [cap-hit example from the caps deep-dive](../deep-dives/caps.md#what-hitting-a-cap-looks-like) — same session (`20260523-082151-45f0a5`), with `TILTH_MAX_ITERATIONS_PER_TASK` bumped from `8` to `16` in `.env` before resuming:

![Terminal capture of `uv run tilth resume`. The harness prints "↻ resume plan: retrying T-003 (was: failed); then: T-004, T-005; last stop: iter_cap", then the session header (session id, branch, worktree, model deepseek/deepseek-v4-flash), then "task T-003 iter 1" with a read_file call, "task T-003 iter 2" with two glob calls, and "task T-003 iter 3" beginning.](../assets/resume-after-iter-cap.png)

*The plan banner spells out what's about to happen: T-003 retried with a fresh iteration budget, then T-004 and T-005 picked up in order. The iteration counter restarts at 1 — the bumped cap gives the retry 16 iterations to work with instead of the 8 that ran out last time.*
{: .caption }

## What resume does

- Skips tasks already marked `done` in `prd.json` (which lives in `sessions/<id>/`, not on the worktree branch).
- **Retries the trailing failed task**, if any. Iter-cap, judge-cap, empty-response, and no-case stops mark the in-flight task `failed` and write a `FAILED (...)` placeholder commit; resume flips that task back to `pending` and unwinds the placeholder so the retry sees the partial work as uncommitted changes (and the evaluator will see a single cumulative diff, not just the new edits). Wall-clock and token caps stop *between* tasks, so there's no failed task to unwind; interrupt leaves the session `running`, error leaves it `failed` — neither marks a task failed.
- **Re-reads the per-task ledger.** Each task's `sessions/<id>/ledger/<task_id>.jsonl` (the evaluator's prior verdicts) survives on disk, so a resumed retry shows the worker and evaluator the verdict history from the run before — the live conversation is gone, the ledger is not. See [The worker↔evaluator dialogue](../deep-dives/worker-evaluator-dialogue.md).
- **Resets the wall-clock budget** for this resume — otherwise a resume the next day would trip `TILTH_MAX_WALL_CLOCK_MINUTES` immediately.
- **Preserves the token total.** If the original run hit `TILTH_MAX_TOKENS`, bump it in `.env` before resuming or the new run will stop on the first token check.

The resume plan is printed up front (which task is being retried, which are pending) and logged as a `session_resume` event in `events.jsonl`.

> **Diagram suggestion** — *timeline showing two runs of the same session glued together: first run hits a cap, then `tilth resume` resets the wall-clock baseline, retries the trailing failed task, then continues through pending tasks. Token total is shown as a single rising line across both runs; wall-clock is shown as two separate bars.*

For the under-the-hood story of what resume actually mutates, see [Resume mechanics](../deep-dives/resume-mechanics.md).

## Resumable-session detection

If you run `uv run tilth run <workspace>` and there's no prepared session for this workspace, the harness checks for a resumable prior session and offers an interactive picker rather than silently starting a new session:

```
No prepared session for this workspace, but a prior session is resumable:
  sessions/20260430-121316-51ead4/  failed     T-001: Scaffold CSVExporter  [last stop: iter_cap]

What would you like to do?
  1) resume that session
  2) discard it and prep a new one
  0) cancel
>
```

"Resumable" means: same source path AND last `stop.reason` is anything other than `all_done` (or no stop event was logged at all — covers crashes that died before logging) AND the session is *not* in `prepared` state (those get picked up directly by `tilth run` without a warning). The detection is read-only — it doesn't touch the prior session until you pick option 2.

When stdin isn't a TTY (CI, scripts, piped invocations) the picker is skipped — the harness exits 2 with a single-line pointer at `tilth resume <sid>` or `tilth prep-feature <ws>` so callers fail loudly instead of hanging on a prompt that nobody will ever answer.
