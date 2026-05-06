# Deep dives

Honest, code-level walk-throughs of mechanics that aren't load-bearing for *using* the harness, but matter when you want to extend, debug, or reason about the safety story.

The [Getting started](../getting-started/installation.md) and [Architecture](../architecture/overview.md) sections cover "how do I run this." This section covers "how does it actually work inside."

- **[The two loops](two-loops.md)** — Ralph (outer) vs. tool-use (inner), iteration accounting, the inner-loop flowchart, judge-rejection accounting, worst-case tokens per task.
- **[Token recording and enforcement](token-recording.md)** — where the cap is set, where the running counter lives, the three call sites that record tokens, where enforcement happens, and the gaps worth knowing.
- **[Agent visibility](agent-visibility.md)** — the visibility table: what the agent sees and what it doesn't, why the separation is deliberate, and when you'd break the rule.
- **[How the caps fit together](caps.md)** — the five things that can stop a run, and which level (session vs. task) each one operates at.
- **[Resume mechanics](resume-mechanics.md)** — what `--resume` actually mutates on wake; resumable-session detection.
- **[Reset mechanics](reset-mechanics.md)** — what `--reset` tears down, idempotency, the manual fallback.
