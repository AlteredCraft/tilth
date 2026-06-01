# How the caps fit together

The caps exist because Tilth runs unattended. An interactive agent doesn't need a hard ceiling — a human watching the scrollback notices a runaway and stops it. Tilth has no such human for the length of a run, so the caps *are* that human: a budget the harness enforces on its behalf. They're set deliberately loose (a stuck task should be the exception), and a hit is always loud and resumable rather than silent.

At any moment during a run, seven things can stop it:

1. **All `prd.json` tasks done** — happy path; outer loop exits cleanly.
2. **`MAX_WALL_CLOCK_MINUTES` exceeded** — outer loop checks at the top of each task; the *current* task finishes first.
3. **`MAX_TOKENS` exceeded** — same enforcement granularity as wall-clock; inter-task only.
4. **`MAX_ITERATIONS_PER_TASK` exceeded inside a single task** — that task is marked `failed`, the run halts (does not continue to the next task — failures are halting events, not skip events). The next `tilth resume` flips the failed task back to `pending` and the agent retries it with a fresh iteration budget; partial work survives via a soft-reset of the FAILED placeholder commit.
5. **`MAX_EVALUATOR_CALLS_PER_TASK` exceeded inside a single task** *(optional; `0` = off, the default)* — same shape as cap 4: task marked `failed` with reason `evaluator_cap`, run halts, `tilth resume` retries with a fresh budget. Targets the worker↔evaluator ping-pong case where the iteration budget alone would let the worker keep retrying right up until `iter_cap`.
6. **3 consecutive empty model responses inside a single task** — a turn with no tool calls, no content, no reasoning (a provider hiccup; observed: an endpoint that 200s with zero usage tokens). Same shape as cap 4: task marked `failed` with reason `empty_responses`, run halts, `tilth resume` retries. Empty calls cost no tokens, so the token cap can't catch a stuck endpoint — this backstop does. Fixed at 3 retries (with backoff); no env knob.
7. **3 consecutive worker turns without a case inside a single task** — the worker went quiet but never called `submit_case`. After 3 nudges the task is marked `failed` with reason `no_case`, run halts, `tilth resume` retries. Fixed at 3; no env knob.

Caps 2 and 3 are **session-level**. Caps 4–7 are **task-level**. There is no per-call cap and no per-task token cap. Caps 6 and 7 aren't env-tunable — they're fixed circuit-breakers against a dead endpoint or a worker that never presents its case. That's the full safety story.

## What hitting a cap looks like

A cap-4 (iter_cap) hit, with the post-run summary the harness prints on the way out:

![Terminal capture: task T-003 reaches iter 8, the harness logs "task T-003 hit iteration cap [TILTH_MAX_ITERATIONS_PER_TASK=8]" and then "× T-003 failed (iter_cap); halting run". A run summary block follows: session 20260523-082151-45f0a5, branch session/20260523-082151-45f0a5, duration 2m27s (2.0% of TILTH_MAX_WALL_CLOCK_MINUTES=120), tokens 75,387 (3.8% of TILTH_MAX_TOKENS=2,000,000), tasks done=2 failed=1 pending=2.](../assets/iter-cap-and-summary.png)

*Two things to read here. Top: the cap fires and the run halts mid-task list (T-003 of five). Bottom: the run summary surfaces every cap as a percentage, so it's obvious which one bit — duration and tokens are both well under, only iterations were tight. (This capture predates the default bump — the cap was set to `8` for this run, below today's default of `32` — which is why it halts this early.)*
{: .caption }

The `failed=1 pending=2` line in the summary is what `tilth resume` reads to plan its retry — see [Resuming a session](../getting-started/resuming.md) for what picks up from this exact point (same session id).

> **Diagram suggestion** — *a layered cap diagram: an outer ring labelled "Session-level caps (MAX_WALL_CLOCK_MINUTES, MAX_TOKENS)" containing an inner ring labelled "Task-level stops (MAX_ITERATIONS_PER_TASK, MAX_EVALUATOR_CALLS_PER_TASK, plus the fixed empty_responses / no_case backstops)." A central node represents the worker model call. Annotate each ring with what happens on hit (run stops vs. task fails + run halts).*

## Cross-references

- See [The two loops](two-loops.md) for the loop structure that the caps gate, including the inner-loop flow chart that shows where `iter_cap`, `evaluator_cap`, `empty_responses`, and `no_case` are emitted.
- See [Token recording](token-recording.md) for where `MAX_TOKENS` is read, where the running counter lives, and the trade-off behind between-task enforcement.
- See [Resume mechanics](resume-mechanics.md) for what `tilth resume` does to a cap-stopped run (and why a token-cap stop will re-trip immediately unless you bump the cap).
