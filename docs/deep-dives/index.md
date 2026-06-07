# Deep dives

Honest, code-level walk-throughs of mechanics that aren't load-bearing for *using* the harness, but matter when you want to extend, debug, or reason about the safety story.

The [Getting started](../getting-started/installation.md) and [Architecture](../architecture/overview.md) sections cover "how do I run this." This section covers "how does it actually work inside."

- **[Hyper-observability](hyper-observability.md)** — Tilth's standing goal: every prompt the harness sends is recorded and every run replays end-to-end from its `events.jsonl`. What the observability surface gives you today, what it doesn't yet, and why feeding a run's log to a co-dev agent to hunt anomalies has been one of the more useful debugging moves.
- **[The two loops](two-loops.md)** — Ralph (outer) vs. tool-use (inner), iteration accounting, the inner-loop flowchart, the worker↔evaluator dialogue (the worker submits a *case*, the evaluator returns a structured *verdict*), evaluator-rejection accounting, worst-case tokens per task.
- **[The worker↔evaluator dialogue](worker-evaluator-dialogue.md)** — the structured `case` / `verdict` exchange the inner loop ends in: `submit_case`, `submit_verdict`, the six rejection categories, and the per-task ledger that gives the evaluator memory across iterations.
- **[Token recording and enforcement](token-recording.md)** — where the cap is set, where the running counter lives, the three call sites that record tokens, where enforcement happens, and the gaps worth knowing.
- **[Agent visibility](agent-visibility.md)** — the visibility table: what the worker sees (its task, the plan as context, the seed slice, its own task's verdicts) and what it doesn't, why the separation is deliberate, and when you'd break the rule.
- **[Seeding a session](seeding.md)** — `tilth prep-feature`'s interview engine: the seed bundle, the frontend/sink protocols, the atomic terminal write.
- **[The PRD format](prd-json.md)** — the `prd.json` schema, lifecycle, and the soft 1:1 convention between acceptance criteria and test assertions.
- **[How the caps fit together](caps.md)** — the things that can stop a run (the four caps plus the empty-response / no-case backstops), and which level (session vs. task) each one operates at.
- **[Resume mechanics](resume-mechanics.md)** — what `tilth resume` actually mutates on wake; how per-task ledgers survive; resumable-session detection.
- **[Reset mechanics](reset-mechanics.md)** — what `tilth reset` tears down, idempotency, the manual fallback.
- **[Session layout](session-layout.md)** — where a run lives on disk: working tree under Tilth's `sessions/<id>/`, the session's durable state, branch in the source repo's `.git`, and a reference table of every event type.
