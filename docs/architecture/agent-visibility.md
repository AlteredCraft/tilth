# What the agent sees (and what it doesn't)

The worker is deliberately walled off from most of the harness's machinery. It sees only what it needs to do its job; the rest — audit logging, token accounting, resume state, the task-status machinery — is invisible. This is [invariant 2](overview.md#architecture-invariants-worth-preserving) made concrete, and it's worth understanding before extending the harness.

The wall is softer than it once was, by design: the worker sees the feature overview and the whole plan *as context*, and — when it has worked this task before — the evaluator's prior verdicts on it. It still never sees the harness files, the queue it's part of, or the cost accounting.

> The reviewing role is the **evaluator**.

## Visibility table

| Artifact | Worker's view |
|---|---|
| **Current task** | Sees its full description and acceptance criteria, injected as the user message. Not the file path or frontmatter — just the content. |
| **Feature overview** | Sees the text of `.tilth/tasks/overview.md` (capped at `OVERVIEW_MAX_CHARS`, 4 KB), so it understands the whole before building one slice. |
| **Full feature plan** | Sees every task collapsed (id, title, status, description, AC), capped at `FULL_PRD_MAX_CHARS` (6 KB), framed as "context, not work to do" with the current task marked. It sees the *plan*, not the mutable status store. |
| **Task ledger** (`ledger/<task_id>.jsonl`) | On a retry, sees its *own* task's ledger — the evaluator's prior verdicts (last 5). Empty on a first run; populated on later iterations and across resume. Not other tasks' ledgers or the file itself. |
| **`AGENTS.md` / `CLAUDE.md`** | Sees the *content* (injected at task start; file list via `TILTH_CONTEXT_FILES`). Could also `read_file` it. Tilth never writes to these. |
| **`progress.txt`** | Sees the last ~30 lines, injected. Doesn't write to it — the harness appends after task done/fail. |
| **`.tilth/tasks/` files** | The *content* reaches it as the overview + plan + task injections. The directory is physically present in the worktree, so it could `read_file` them — the system prompt marks `.tilth/` read-only, and the evaluator hard-rejects diffs that edit it. |
| **`task-status.json`** | **Never sees the file.** Per-task status is harness-owned; the worker only sees each task's status rendered into the plan-as-context. |
| **`events.jsonl`** | **Never sees it.** Pure harness audit log. |
| **`checkpoint.json`** | **Never sees it.** Resume mechanics. |
| **Token counts and caps** | **Never sees them.** Caps enforce silently between tasks. |
| **Worktree branch** | Sees a workspace directory — doesn't know it's a worktree, its branch name, or that commits are made on its behalf. |
| **The evaluator** | Runs in a separate fresh-context call the worker never sees. But it *does* see the evaluator's prior verdicts on *its own current task* (the ledger above), and each reject arrives as the structured `submit_case` tool_result. Never a generic cross-task reviewer or the session-wide rejection histogram. |
| **Tool registry** | Sees the JSON schemas of all registered tools on every call (via `tools=`). The system prompt does *not* enumerate them — schemas are the canonical source. |
| **Reasoning blocks** | Sees its *own* prior reasoning, echoed across iterations in the in-memory history (load-bearing for thinking-mode continuity — see `client.assistant_history_message`). The harness mirrors it into `events.jsonl` for the visualizer, but the agent never reads from there. |

## Why this separation is deliberate

Three failure modes — all seen in earlier hand-built loops — are prevented by hiding mechanics from the agent:

1. **Gaming the evaluator.** The worker sees the evaluator's verdicts on *its own task* — by design, so it can fix what was flagged — but not the rejection categories aggregated across the session or the evaluation machinery itself. Keeping the wider machinery hidden stops the worker padding commits *for the reviewer* instead of writing working code.
2. **Token shortcuts.** If the agent knew the token cap, it would cut corners ("I'll skip verifying to save tokens"). The only objective it's given is "do the task." Cost accounting is the harness's problem.
3. **Trying to manage state itself.** If the agent saw the mutable status *state* — `task-status.json`, the queue machinery — it would try to mark its own task done, skip ahead, or rewrite the queue. The plan it sees is read-only prose; state management belongs in code, and the agent works on one task at a time and stops.

A corollary follows for `AGENTS.md`: it's for *project* conventions, not harness mechanics — the [Memory channels](memory-channels.md#agentsmd-your-project-conventions) page carries the belongs/doesn't-belong rule.

## What the agent *is* explicitly told (in `prompts/system.md`)

- Its role: "focused worker agent operating inside a long-running harness."
- Each task starts a fresh conversation; no memory of prior tasks beyond the loaded context — the overview, the full plan, and (on a retry) the evaluator's prior verdicts on this task.
- The overview and plan are context, **not a worklist** — build only the task under "Your task."
- Prefer the narrowest tool that does the job (`read_file` over `bash cat`, `grep` over `bash grep -r`).
- **Verify before claiming done** — if the project has a way to exercise the behaviour, run it via `bash`; "the code looks right" is not verification.
- How it finishes: it's an *advocate* — when the work is done and verified it calls `submit_case`, mapping every acceptance criterion to the `file:symbol` that satisfies it (plus declared work-arounds and uncertainties). Going quiet is not "done."
- `.tilth/` is read-only context; no destructive commands, no force pushes, no out-of-workspace edits.

That's the entire visible contract. Everything else is the harness's job, in code.

## When you'd break this rule

Two cases where exposing internals can be right:

1. **Debugging.** During development, inject the iteration counter ("you have 3 iterations left") to see if behaviour changes. Useful as a probe; remove before production.
2. **Explicit budget signalling.** If you want the agent to plan against a known budget ("you have 50K tokens for this task, plan accordingly"), that's a different pattern than the hidden cap — it reframes the agent as a planner and requires changing the system prompt. Tilth intentionally doesn't do this; the iteration cap is a hard stop, not a soft hint.
