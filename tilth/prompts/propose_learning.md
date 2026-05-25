You just completed a development task. Before context resets and the next task begins, decide whether this task surfaced any durable observation about *this codebase* worth capturing for later.

What you propose lands in a **session-local file the user will review after the run** — not in any file the next task reads. Your proposal does **not** influence subsequent tasks in this run. A downstream consumer (the user, or a future hook) decides whether and where to apply it.

This framing matters. Don't propose within-run reminders to yourself (you won't see them). Don't propose where the learning should live or how it should be filed (that's not your call). Just state the observation, plainly.

Good proposals are short, durable, and grounded in *this codebase*:

- A non-obvious convention this codebase uses that future work should follow.
- A pitfall that cost you iterations and would cost a future agent the same.
- A structural fact about the project (layout, fixture pattern, build quirk) that a fresh agent would benefit from knowing.

Bad proposals:

- Generic advice ("write tests", "use type hints").
- Restatements of what's already in AGENTS.md.
- A summary of "I did task X" — that's a journal entry, not a learning.
- Suggestions about where or how the learning should be filed.

Most tasks will not produce a worthwhile proposal. That's fine — respond `{"propose": "no"}` and move on.

## How to respond

Respond with **strict JSON only**, no prose around it:

```json
{
  "propose": "no" | "yes",
  "learning": "<one bullet, ≤2 sentences; omitted if propose=no>"
}
```
