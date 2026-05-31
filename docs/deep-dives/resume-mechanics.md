# Resume mechanics

`tilth resume` wakes a session and re-enters the outer loop (the legacy `--resume` flag still works for one minor version). Three things happen on wake:

1. **`Session.wake()` reads `checkpoint.json`** and reconstructs `tokens_used`, `workspace`, `branch`. `started_at` is reset to `time.time()` (wall-clock budget is per-resume).
2. **`_prepare_resume()` reads the trailing `stop` event** from `events.jsonl` to learn how the previous run ended, then:
    - If `last_stop == "all_done"`, no-op (besides logging).
    - Otherwise, any task in `prd.json` with `status == "failed"` is flipped back to `"pending"` and `ws.unwind_failed_commit()` soft-resets the `FAILED (...)` placeholder commit so the partial work returns to the index. Without that soft-reset, the evaluator's `task_diff` (HEAD vs working tree) would only see *new* edits on the retry, not the cumulative work — incorrect evaluation.
3. **A `session_resume` event** is logged with the structured plan: `last_stop`, `retried`, `pending`, `unwound_commit`, and a one-line summary. This is the parallel of `session_start` for resumes; both transitions are auditable from `events.jsonl` alone.

**Per-task ledgers survive resume.** `sessions/<id>/ledger/<task_id>.jsonl` are plain append-only files under the session root, not in the worktree or the live conversation. `Session.wake()` re-roots and `read_ledger` reads off disk, so a resume picks up the prior run's evaluator verdicts — re-injected into both the evaluator prompt and the worker prompt under "## Prior iterations on this task". The first run after a resume therefore sees what was rejected before, even though the conversation is gone. `tilth reset` drops `sessions/<id>/`, so it discards ledgers too. See [The worker↔evaluator dialogue](worker-evaluator-dialogue.md).

Bare `tilth resume` (no session ID) selects the most recent session in `sessions/` by directory name (the timestamp prefix sorts chronologically). Explicit `tilth resume <session_id>` is unchanged.

> **Diagram suggestion** — *sequence diagram: `tilth resume` invocation → `Session.wake()` reads checkpoint → `_prepare_resume()` reads trailing `stop` event → unwinds FAILED placeholder if any → flips failed task back to pending → logs `session_resume` event → outer loop starts. Lifeline lanes for `checkpoint.json`, `events.jsonl`, `prd.json`, and the worktree git database.*

Resume does not loop endlessly. If a retried task hits a terminal-failure stop *again* — `iter_cap`, `judge_cap`, `empty_responses`, or `no_case` — the outer loop halts with that `stop {reason}` just like the original run; the next `tilth resume` would retry once more. The retries are recursive in invocation, not in mechanism — each one is just a fresh ride through the same loop.

## Resumable-session detection

When you run `uv run tilth run <workspace>` and there's no prepared session to pick up, `_find_resumable_session()` scans `sessions/` newest-first and looks for a directory whose `session_start.source` matches `<workspace>`, whose last `stop.reason` is anything other than `all_done` (or has no `stop` event at all — covers crashes that died before logging), and whose checkpoint status is *not* `prepared` (prepared sessions are picked up directly by `tilth run` without a warning). If a resumable session exists, the harness prints a heads-up listing the `tilth resume` / `tilth reset` recovery commands and pauses 5 seconds before calling `Session.new()`. Ctrl-C during the pause returns 130 cleanly.

The detection is read-only — no files modified, no state mutated. It exists purely to surface that a fresh run will silently abandon resumable progress, which is the failure mode the iteration loop ("halt → tweak → continue") inadvertently optimises for.
