# What the agent sees (and what it doesn't)

The agent is deliberately walled off from most of the harness's machinery. It sees only the things it needs to do its job; the rest — audit logging, token accounting, resume state, the task-status machinery — is invisible.

The wall is softer than it once was, by design: the worker sees the feature overview and the whole plan *as context*, and — when it has worked this task before — the evaluator's prior verdicts on it. It still never sees the harness files, the queue it's part of, or the cost accounting. This is a load-bearing design choice, not a convenience. Worth understanding before extending the harness.

> The reviewing role is the **evaluator**.

## Visibility table

| Artifact | Agent's view |
|---|---|
| **Current task** | Sees its task's full description and acceptance criteria, injected as the user message. Doesn't see the authored file's path or frontmatter — just the content. |
| **Feature overview** | Sees the text of `.tilth/tasks/overview.md` (capped at `OVERVIEW_MAX_CHARS`, 4 KB), injected as "Feature overview (why this feature, what's in/out of scope)" — so it understands the whole before building one slice. |
| **Full feature plan** | Sees every task collapsed (id, title, status, description, AC), capped at `FULL_PRD_MAX_CHARS` (6 KB), framed as "context, not work to do" with the current task marked. So it understands the shape of the feature without pre-empting later tasks — but it sees the *plan*, not the mutable status store. |
| **Task ledger** (`ledger/<task_id>.jsonl`) | On a retry, sees its *own* task's ledger — the evaluator's prior verdicts (last 5), under "Prior iterations on this task (from the evaluator)." Empty on a task's first run; populated on later iterations and across resume. Doesn't see other tasks' ledgers or the file itself. |
| **`AGENTS.md` / `CLAUDE.md`** | Sees the *content* (injected at task start; file list configurable via `TILTH_CONTEXT_FILES`). Could also `read_file` it. Tilth never writes to these — they stay user-owned. |
| **`progress.txt`** | Sees the last ~30 lines, injected. Doesn't write to it — the harness appends after task done/fail. |
| **`.tilth/tasks/` files** | The *content* reaches it as the overview + plan + task injections above. The directory is also physically present in the worktree, so it could `read_file` them — the system prompt tells it to treat `.tilth/` as read-only context, and the evaluator hard-rejects diffs that edit it. |
| **`task-status.json`** | **Never sees the file.** Per-task status is harness-owned state under `sessions/<id>/`; the worker only sees each task's status rendered into the plan-as-context. |
| **`events.jsonl`** | **Never sees it.** Pure harness audit log. |
| **`checkpoint.json`** | **Never sees it.** Resume mechanics. |
| **Token counts and caps** | **Never sees them.** Caps enforce silently between tasks. |
| **Worktree branch** | Sees a workspace directory — doesn't know it's a worktree, doesn't know its branch name, doesn't know commits are made on its behalf. |
| **The evaluator** | The evaluator runs in a separate fresh-context call — the worker never sees that mechanism. But it *does* see the evaluator's prior verdicts on *its own current task* (the ledger above), injected so it can act on the feedback, and each reject arrives as the structured `submit_case` tool_result. It never sees a generic cross-task reviewer or the session-wide rejection histogram. |
| **Tool registry** | Sees the JSON schemas of all registered tools on every call (passed via the `tools=` parameter). The system prompt does *not* enumerate them — schemas are the canonical source. |
| **Reasoning blocks** | Sees its *own* prior reasoning, echoed across iterations in the in-memory message history (load-bearing for thinking-mode continuity — see `client.assistant_history_message`). The harness mirrors it into `events.jsonl` for the visualizer, but the agent never reads from there. |

> **Diagram suggestion** — *a "wall" diagram: on the left, a single bubble labelled "the worker agent" with what it sees (its task, the feature overview, the full plan as context, project-context files, progress.txt tail, the evaluator's prior verdicts on this task, tool schemas, prior reasoning). On the right, behind a vertical wall labelled "harness mechanics," what it doesn't (`task-status.json`, `events.jsonl`, `checkpoint.json`, token counts, the wider evaluation machinery, worktree, branch). Arrows from the right cross the wall only as carefully-shaped injections — the plan-as-context and the worker's own-task verdicts cross; the mutable status state and the session-wide evaluation machinery do not.*

## Why this separation is deliberate

Three failure modes prevented by hiding mechanics from the agent:

1. **Gaming the evaluator.** The worker sees the evaluator's verdicts on *its own task* — by design, so it can fix what was flagged — but not the rejection categories aggregated across the session or the evaluation machinery itself. Keeping the wider machinery hidden stops the worker padding commits *for the reviewer* instead of writing working code.
2. **Token shortcuts.** If the agent knew the token cap, it would cut corners ("I'll skip verifying to save tokens"). The only objective the worker is given is "do the task." Cost accounting is the harness's problem, not the agent's.
3. **Trying to manage state itself.** If the agent saw the mutable status *state* — `task-status.json`, the queue machinery — it would try to mark its own task done, skip ahead, or rewrite the queue. Both real failure modes seen in earlier hand-built loops. The plan it sees is read-only prose; the state management belongs in code, and the agent works on one task at a time and stops.

## Corollary: AGENTS.md should stay project-focused

A natural follow-on. AGENTS.md is for *project* conventions, not harness mechanics:

- **Belongs in AGENTS.md:** language version, test framework, file layout, style rules, project-specific gotchas, accumulated learnings.
- **Does *not* belong in AGENTS.md:** "record token counts in `events.jsonl`" (agent doesn't write that file), "mark your task done when finished" (the harness manages status), "stop after 32 iterations" (handled by `max_iterations_per_task`), "don't run dangerous commands" (handled by `pre_tool` hook), "the evaluator will evaluate your work" (see "gaming the evaluator" above).

The cleanest test: if you removed a rule from AGENTS.md and the harness still enforces the underlying behaviour, the rule shouldn't be there. Harness machinery handles the mechanics; AGENTS.md handles the project.

## What the agent *is* explicitly told (in `prompts/system.md`)

- Its role: "focused worker agent operating inside a long-running harness."
- Each task starts a fresh conversation; no memory of prior tasks beyond the loaded context — which includes the feature overview, the full plan, and (on a retry) the evaluator's prior verdicts on this task.
- The overview and plan are context, **not a worklist** — build only the task under "Your task."
- How to use tools (prefer the narrowest tool that does the job — `read_file` over `bash cat`, `grep` over `bash grep -r`).
- **Verify before claiming done** — if the project has a way to exercise the behaviour, run it via `bash`; "the code looks right" is not verification.
- How it finishes: it's an *advocate* — when the work is done and verified it calls `submit_case`, mapping every acceptance criterion to the `file:symbol` that satisfies it (plus declared work-arounds and uncertainties). Going quiet is not "done"; the harness just asks it to submit one.
- `.tilth/` is read-only context — don't edit it.
- Constraints: no destructive commands, no force pushes, no out-of-workspace edits.
- A self-review reminder: before calling `submit_case`, ask "for each acceptance criterion, what's the `file:symbol` that satisfies it, and did I actually run something to confirm it?"

That's the entire visible contract. Everything else is the harness's job, in code.

## When you'd break this rule

Two cases where it can be right to expose harness internals:

1. **Debugging.** During development, expose the iteration counter to the agent (e.g. inject "you have 3 iterations left" on each turn) to see if behaviour changes. Useful as a probe; remove before production.
2. **Explicit budget signalling.** If you want the agent to plan its work against a known budget — "you have 50K tokens for this task, plan accordingly" — that's a different pattern than the hidden cap, and it requires changing the system prompt's framing (the agent is now a planner, not just a worker). Tilth intentionally doesn't do this; the iteration cap is a hard stop, not a soft hint.
