# What the agent sees (and what it doesn't)

The agent is deliberately walled off from most of the harness's machinery. It sees only the things it needs to do its job; the rest — audit logging, token accounting, resume state, the `prd.json` status machinery, the self-improvement step — is invisible.

The [Phase 4 visibility expansion](worker-evaluator-dialogue.md) softened where that wall sits: the worker now sees the whole feature plan *as context*, a curated slice of the seeding interview, and — when it has worked this task before — the evaluator's prior verdicts on it. It still never sees the harness files, the queue it's part of, or the cost accounting. This is a load-bearing design choice, not a convenience. Worth understanding before extending the harness.

> The reviewing role is the **evaluator**.

## Visibility table

| Artifact | Agent's view |
|---|---|
| **Current task** | Sees its task's full description and acceptance criteria, injected as the user message. Doesn't see the `prd.json` file it came from. |
| **Full feature plan** | Sees every task collapsed (id, title, status, description, AC), capped at `FULL_PRD_MAX_CHARS` (6 KB), framed as "context, not work to do" with the current task marked. So it understands the shape of the feature without pre-empting later tasks — but it sees the *plan*, not the mutable JSON state. |
| **`seed-meta.json`** | Sees a *curated slice* — the feature-shaping fields (TL;DR, scope notes, blockers, open questions), capped at `SEED_META_MAX_CHARS` (4 KB), injected as "seed context." The interview bookkeeping (model, tokens, timestamps) is excluded, and it never sees the file itself. |
| **Task ledger** (`ledger/<task_id>.jsonl`) | On a retry, sees its *own* task's ledger — the evaluator's prior verdicts (last 5), under "Prior iterations on this task (from the evaluator)." Empty on a task's first run; populated on resume. Doesn't see other tasks' ledgers or the file itself. |
| **`AGENTS.md`** | Sees the *content* (injected at task start). Could also `read_file` it. Tilth never writes to it — it stays user-owned. After each accepted task a separate `_self_improve` call asks whether the task surfaced a durable learning; a "yes" lands in `sessions/<id>/proposed-learnings.md` for the user to review, never back in AGENTS.md or the PR diff. |
| **`progress.txt`** | Sees the last ~30 lines, injected. Doesn't write to it — the harness appends after task done/fail. |
| **`prd.json`** | **Never sees the file or its structure.** Doesn't see it as JSON, doesn't see status fields or the queue machinery. It *does* see every task's title/status/AC as prose context (the full feature plan above), framed so it builds only its own task. |
| **`events.jsonl`** | **Never sees it.** Pure harness audit log. |
| **`checkpoint.json`** | **Never sees it.** Resume mechanics. |
| **Token counts and caps** | **Never sees them.** Caps enforce silently between tasks. |
| **Worktree branch** | Sees a workspace directory — doesn't know it's a worktree, doesn't know its branch name, doesn't know commits are made on its behalf. |
| **Evaluator / self-improvement** | The evaluator runs in a separate fresh-context call and self-improvement is a separate prompt — the worker never sees those mechanics. But it *does* see the evaluator's prior verdicts on *its own current task* (the ledger above), injected so it can act on the feedback. It never sees a generic cross-task reviewer, the session-wide rejection histogram, or the self-improve step. |
| **Validators** | Sees the *failure report* when validators fail (delivered as the `submit_case` tool_result). Sees nothing when they pass. |
| **Tool registry** | Sees the JSON schemas of all registered tools on every call (passed via the `tools=` parameter). The system prompt does *not* enumerate them — schemas are the canonical source. |
| **Reasoning blocks** | Sees its *own* prior reasoning, echoed across iterations in the in-memory message history (load-bearing for thinking-mode continuity — see `client.assistant_history_message`). The harness mirrors it into `events.jsonl` for the visualizer, but the agent never reads from there. |

> **Diagram suggestion** — *a "wall" diagram: on the left, a single bubble labelled "the worker agent" with what it sees (its task, the full feature plan as context, seed context, AGENTS.md content, progress.txt tail, the evaluator's prior verdicts on this task, tool schemas, prior reasoning). On the right, behind a vertical wall labelled "harness mechanics," what it doesn't (the `prd.json` file + status machinery, `events.jsonl`, `checkpoint.json`, token counts, the cross-task evaluator + rejection histogram, the self-improvement step, worktree, branch). Arrows from the right cross the wall only as carefully-shaped injections — the plan-as-context and the worker's own-task verdicts cross; the mutable JSON state and the wider evaluation machinery do not.*

## Why this separation is deliberate

Three failure modes prevented by hiding mechanics from the agent:

1. **Gaming the evaluator.** The worker sees the evaluator's verdicts on *its own task* — by design, so it can fix what was flagged — but not that a generic reviewer evaluates every task, nor the rejection categories aggregated across the session. Keeping the wider evaluation machinery hidden stops the worker padding commits *for the reviewer* instead of writing working code.
2. **Token shortcuts.** If the agent knew the token cap, it would cut corners ("I'll skip writing the test to save tokens"). The only objective the worker is given is "do the task." Cost accounting is the harness's problem, not the agent's.
3. **Trying to manage state itself.** If the agent saw the mutable `prd.json` *state* — status fields, the queue machinery — it would try to mark its own task done, skip ahead, or rewrite the queue. Both real failure modes seen in earlier hand-built loops. The plan it sees is read-only prose; the state management belongs in code, and the agent works on one task at a time and stops.

## Corollary: AGENTS.md should stay project-focused

A natural follow-on. AGENTS.md is for *project* conventions, not harness mechanics:

- **Belongs in AGENTS.md:** language version, test framework, file layout, style rules, project-specific gotchas, accumulated learnings.
- **Does *not* belong in AGENTS.md:** "record token counts in `events.jsonl`" (agent doesn't write that file), "update `prd.json` status when done" (agent doesn't manage prd), "stop after 32 iterations" (handled by `max_iterations_per_task`), "don't run dangerous commands" (handled by `pre_tool` hook), "the evaluator will evaluate your work" (see "gaming the evaluator" above).

The cleanest test: if you removed a rule from AGENTS.md and the harness still enforces the underlying behaviour, the rule shouldn't be there. Harness machinery handles the mechanics; AGENTS.md handles the project.

## What the agent *is* explicitly told (in `prompts/system.md`)

- Its role: "focused worker agent operating inside a long-running harness."
- Each task starts a fresh conversation; no memory of prior tasks beyond the loaded context — which now includes the full feature plan, the seed context, and (on a retry) the evaluator's prior verdicts on this task.
- How to use tools (prefer narrow over broad — `read_file` over `bash cat`, `grep` over `bash grep -r`).
- How it finishes: it's an *advocate* — when the work is done and verified it calls `submit_case`, mapping every acceptance criterion to the `file:symbol` that satisfies it (plus declared work-arounds and uncertainties). Going quiet is not "done"; the harness just asks it to submit one.
- Constraints: no destructive commands, no force pushes, no out-of-workspace edits.
- A self-review reminder: before calling `submit_case`, ask "for each acceptance criterion, what's the `file:symbol` that satisfies it, and did I run the test that proves it?"

That's the entire visible contract. Everything else is the harness's job, in code.

## When you'd break this rule

Two cases where it can be right to expose harness internals:

1. **Debugging.** During development, expose the iteration counter to the agent (e.g. inject "you have 3 iterations left" on each turn) to see if behaviour changes. Useful as a probe; remove before production.
2. **Explicit budget signalling.** If you want the agent to plan its work against a known budget — "you have 50K tokens for this task, plan accordingly" — that's a different pattern than the hidden cap, and it requires changing the system prompt's framing (the agent is now a planner, not just a worker). The MVP intentionally doesn't do this; the iteration cap is a hard stop, not a soft hint.
