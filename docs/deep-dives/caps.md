# How the caps fit together

At any moment during a run, five things can stop it:

1. **All `prd.json` tasks done** — happy path; outer loop exits cleanly.
2. **`MAX_WALL_CLOCK_MINUTES` exceeded** — outer loop checks at the top of each task; the *current* task finishes first.
3. **`MAX_TOKENS` exceeded** — same enforcement granularity as wall-clock; inter-task only.
4. **`MAX_ITERATIONS_PER_TASK` exceeded inside a single task** — that task is marked `failed`, the run halts (does not continue to the next task — failures are halting events, not skip events). The next `--resume` flips the failed task back to `pending` and the agent retries it with a fresh iteration budget; partial work survives via a soft-reset of the FAILED placeholder commit.
5. **`MAX_JUDGE_CALLS_PER_TASK` exceeded inside a single task** *(optional; `0` = off, the default)* — same shape as cap 4: task marked `failed` with reason `judge_cap`, run halts, `--resume` retries with a fresh budget. Targets the worker↔judge ping-pong case where the iteration budget alone would let the worker keep retrying right up until `iter_cap`.

Caps 2 and 3 are **session-level**. Caps 4 and 5 are **task-level**. There is no per-call cap and no per-task token cap. That's the full safety story.

> **Diagram suggestion** — *a layered cap diagram: an outer ring labelled "Session-level caps (MAX_WALL_CLOCK_MINUTES, MAX_TOKENS)" containing an inner ring labelled "Task-level caps (MAX_ITERATIONS_PER_TASK, MAX_JUDGE_CALLS_PER_TASK)." A central node represents the worker model call. Annotate each ring with what happens on hit (run stops vs. task fails + run halts).*

## Cross-references

- See [The two loops](two-loops.md) for the loop structure that the caps gate, including the inner-loop flow chart that shows where `iter_cap` and `judge_cap` are emitted.
- See [Token recording](token-recording.md) for where `MAX_TOKENS` is read, where the running counter lives, and the trade-off behind between-task enforcement.
- See [Resume mechanics](resume-mechanics.md) for what `--resume` does to a cap-stopped run (and why a token-cap stop will re-trip immediately unless you bump the cap).
