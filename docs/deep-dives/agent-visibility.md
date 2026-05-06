# What the agent sees (and what it doesn't)

The agent is deliberately walled off from most of the harness's machinery. It sees only the things it needs to do its job; everything else — task queueing, audit logging, token accounting, resume state, the judge, the self-improvement step — is invisible.

This is a load-bearing design choice, not a convenience. Worth understanding before extending the harness.

## Visibility table

| Artifact | Agent's view |
|---|---|
| **Current task** | Sees the task description and acceptance criteria, injected as the user message. Doesn't know the task came from `prd.json`. |
| **`AGENTS.md`** | Sees the *content* (injected at task start). Could also `read_file` it. Updates happen via the `_self_improve` prompt — a separate model call in a different context — that asks "what should I append?" The worker answering that question doesn't know it's writing to AGENTS.md, just answers a structured JSON question. |
| **`progress.txt`** | Sees the last ~30 lines, injected. Doesn't write to it — the harness appends after task done/fail. |
| **`prd.json`** | **Never sees the file or its structure.** Doesn't know it exists as JSON, doesn't know about status fields, doesn't know other tasks are queued. |
| **`events.jsonl`** | **Never sees it.** Pure harness audit log. |
| **`checkpoint.json`** | **Never sees it.** Resume mechanics. |
| **Token counts and caps** | **Never sees them.** Caps enforce silently between tasks. |
| **Worktree branch** | Sees a workspace directory — doesn't know it's a worktree, doesn't know its branch name, doesn't know commits are made on its behalf. |
| **Judge / self-improvement** | **Never sees them.** The judge is a separate fresh-context call; self-improvement is a separate prompt. The worker has no idea its work is being evaluated or reflected on. |
| **Validators** | Sees the *failure report* when validators fail (injected as the next user message). Sees nothing when they pass. |
| **Tool registry** | Sees the JSON schemas of all registered tools on every call (passed via the `tools=` parameter). The system prompt does *not* enumerate them — schemas are the canonical source. |
| **Reasoning blocks** | Sees its *own* prior reasoning, echoed across iterations in the in-memory message history (load-bearing for thinking-mode continuity — see `_assistant_history_message`). The harness mirrors it into `events.jsonl` for the visualizer, but the agent never reads from there. |

> **Diagram suggestion** — *a "wall" diagram: on the left, a single bubble labelled "the worker agent" with a list of what it sees (task, AGENTS.md content, progress.txt tail, tool schemas, prior reasoning). On the right, behind a vertical wall labelled "harness mechanics," a list of what it doesn't (`prd.json`, `events.jsonl`, `checkpoint.json`, token counts, judge, self-improvement, worktree, branch). Arrows from the right cross the wall only as carefully-shaped injections (task description, AGENTS.md content, progress tail, validator failure reports).*

## Why this separation is deliberate

Three failure modes prevented by hiding mechanics from the agent:

1. **Gaming the judge.** If the agent knew a judge was reading the diff, it would pad commits with explanatory comments aimed at the judge instead of writing working code. Independent evaluation only works if the worker doesn't optimise for it.
2. **Token shortcuts.** If the agent knew the token cap, it would cut corners ("I'll skip writing the test to save tokens"). The only objective the worker is given is "do the task." Cost accounting is the harness's problem, not the agent's.
3. **Trying to manage state itself.** If the agent saw `prd.json`, it would try to mark its own task done, or skip ahead, or rewrite the queue. Both real failure modes seen in earlier hand-built loops. State management belongs in code; the agent works on one task at a time and stops.

## Corollary: AGENTS.md should stay project-focused

A natural follow-on. AGENTS.md is for *project* conventions, not harness mechanics:

- **Belongs in AGENTS.md:** language version, test framework, file layout, style rules, project-specific gotchas, accumulated learnings.
- **Does *not* belong in AGENTS.md:** "record token counts in `events.jsonl`" (agent doesn't write that file), "update `prd.json` status when done" (agent doesn't manage prd), "stop after 8 iterations" (handled by `max_iterations_per_task`), "don't run dangerous commands" (handled by `pre_tool` hook), "the judge will evaluate your work" (see "gaming the judge" above).

The cleanest test: if you removed a rule from AGENTS.md and the harness still enforces the underlying behaviour, the rule shouldn't be there. Harness machinery handles the mechanics; AGENTS.md handles the project.

## What the agent *is* explicitly told (in `prompts/system.md`)

- Its role: "focused worker agent operating inside a long-running harness."
- Each task starts a fresh conversation; no memory of prior tasks beyond the loaded context.
- How to use tools (prefer narrow over broad — `read_file` over `bash cat`, `grep` over `bash grep -r`).
- "Done" criteria: acceptance criteria met, tests pass, workspace committable.
- Constraints: no destructive commands, no force pushes, no out-of-workspace edits.
- A self-review reminder: ask "what evidence do I have?" before declaring done.

That's the entire visible contract. Everything else is the harness's job, in code.

## When you'd break this rule

Two cases where it can be right to expose harness internals:

1. **Debugging.** During development, expose the iteration counter to the agent (e.g. inject "you have 3 iterations left" on each turn) to see if behaviour changes. Useful as a probe; remove before production.
2. **Explicit budget signalling.** If you want the agent to plan its work against a known budget — "you have 50K tokens for this task, plan accordingly" — that's a different pattern than the hidden cap, and it requires changing the system prompt's framing (the agent is now a planner, not just a worker). The MVP intentionally doesn't do this; the iteration cap is a hard stop, not a soft hint.
