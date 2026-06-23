# Deep dives

Honest, code-level walk-throughs of the mechanics that matter when you want to extend, debug, or reason about the safety story — not needed just to *use* the harness. Each page explains a mechanic at a high level and points to the `file.py:function` that implements it, so you can read the source for the rest.

The [Getting started](../getting-started/installation.md) and [Architecture](../architecture/overview.md) sections cover "how do I run this" and "who does what." This section covers "how does it actually work inside."

- **[Hyper-observability](hyper-observability.md)** — Tilth's standing goal and headline feature: every prompt the harness sends is recorded, and every run replays end-to-end from its `events.jsonl` via [`tilth visualize`](../getting-started/visualizing.md). What the observability surface gives you today, what it doesn't yet, and why feeding a run's log to a co-dev agent to hunt anomalies has been one of the more useful debugging moves.
- **[The two loops](two-loops.md)** — Ralph (outer) vs. tool-use (inner), iteration accounting, the inner-loop flowchart, the worker↔evaluator dialogue in loop position, and **what can stop a run** (the session- and task-level caps plus the provider-failure / no-case backstops).
- **[The worker↔evaluator dialogue](worker-evaluator-dialogue.md)** — the structured `case` / `verdict` exchange the inner loop ends in: `submit_case`, `submit_verdict`, the six rejection categories, and the per-task ledger that gives the evaluator memory across iterations.
- **[Token recording and enforcement](token-recording.md)** — the canonical usage record (prompt/eval/cached/reasoning/cost), the single call site that records it, where enforcement happens (between tasks), and what's display-only (cost, worker/evaluator split) vs capped (tokens).
- **[The task format](task-format.md)** — the authored markdown under `.tilth/<feature>/`: frontmatter and section parsing, the templates, the harness-owned status overlay, and who reads each field.
- **[Session layout](session-layout.md)** — where a run lives on disk: working tree under `~/.tilth/sessions/<id>/`, the session's durable state, the branch in the source repo's `.git`, and a reference table of every `events.jsonl` event type.

The design-rationale companion — **what the worker can and can't see** — lives in Architecture as [Agent visibility](../architecture/agent-visibility.md).
