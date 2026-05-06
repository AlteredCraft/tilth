# Resuming a session

Tilth sessions are append-only on disk: an `events.jsonl` log plus a `checkpoint.json` snapshot, both under `sessions/<id>/`. That's enough state to wake the harness on a fresh process and continue.

## How to resume

```bash
uv run tilth --resume               # picks the most recent session
uv run tilth --resume <session_id>  # or name one explicitly
```

Bare `--resume` selects the most recent session in `sessions/` by directory name (the timestamp prefix sorts chronologically).

## What resume does

- Skips tasks already marked `done` in `prd.json` (which lives on the worktree branch).
- **Retries the trailing failed task**, if any. Iter-cap, wall-clock-cap, token-cap, interrupt, and error stops all leave the in-flight task marked `failed`; resume flips that task back to `pending` and unwinds its `FAILED (...)` placeholder commit so the retry sees the partial work as uncommitted changes (and the judge will see a single cumulative diff, not just the new edits).
- **Resets the wall-clock budget** for this resume — otherwise a resume the next day would trip `TILTH_MAX_WALL_CLOCK_MINUTES` immediately.
- **Preserves the token total.** If the original run hit `TILTH_MAX_TOKENS`, bump it in `.env` before resuming or the new run will stop on the first token check.

The resume plan is printed up front (which task is being retried, which are pending) and logged as a `session_resume` event in `events.jsonl`.

> **Diagram suggestion** — *timeline showing two runs of the same session glued together: first run hits a cap, then `--resume` resets the wall-clock baseline, retries the trailing failed task, then continues through pending tasks. Token total is shown as a single rising line across both runs; wall-clock is shown as two separate bars.*

For the under-the-hood story of what resume actually mutates, see [Resume mechanics](../deep-dives/resume-mechanics.md).

## Resumable-session detection

If you run `uv run tilth <workspace>` (no flags) and there's already a resumable session for that same workspace under `sessions/`, the harness prints a heads-up and pauses 5 seconds before starting a new session:

```
heads up: sessions/20260430-121316-51ead4/ ended in iter_cap and is resumable
  → uv run tilth --resume       (continue that work)
  → uv run tilth --reset --yes  (discard it first)
starting fresh anyway in 5s... (Ctrl-C to abort)
```

"Resumable" means: same source path AND last `stop.reason` is anything other than `all_done` (or no stop event was logged at all — covers crashes that died before logging). The detection is read-only — it doesn't touch the prior session. Hit Ctrl-C during the pause if the warning surprised you and you want to switch to `--resume` or [`--reset`](resetting.md) instead.
