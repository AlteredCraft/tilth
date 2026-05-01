You just completed a development task. Before context is reset and the next task begins, decide whether anything you learned should be persisted to `AGENTS.md` so future iterations don't have to relearn it.

Good additions are short, durable, and project-specific:

- **Patterns** — a convention or technique that worked well and should be reused.
- **Gotchas** — a non-obvious pitfall a future agent would step on.
- **Style** — a stylistic choice that should be consistent across files.

Bad additions:

- Generic advice (`"write tests"`).
- Restatements of what's already in AGENTS.md.
- Anything that is just "I did task X."

## How to respond

Respond with **strict JSON only**, no prose around it:

```json
{
  "update": "no" | "yes",
  "section": "Patterns" | "Gotchas" | "Style" | "Recent learnings",
  "entry": "<one bullet, ≤2 sentences, omitted if update=no>"
}
```

If nothing meaningful was learned that's worth persisting, respond `{"update": "no"}`. Most tasks will not produce an update — that's fine.
