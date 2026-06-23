# Memory channels

Five memory channels live *outside* the agent. Some are project files the user owns; some are session-local artifacts the harness manages. Together they give a long-running run continuity by treating durable state as files on disk rather than messages the model has to carry between calls. (The fifth — the per-task evaluator ledger — arrived with the structured [worker↔evaluator dialogue](../deep-dives/worker-evaluator-dialogue.md).)

| Channel | Lives in | Written by | Read by |
|---|---|---|---|
| `AGENTS.md` / `CLAUDE.md` | the workspace (user-owned) | the user | worker, evaluator (injected when present) |
| Git history | the worktree | the harness (one commit per task) | humans, evaluator (via diff) |
| `progress.txt` | `sessions/<id>/` (harness-owned) | the harness (one line per task outcome) | worker (last ~30 lines injected) |
| Task markdown (`.tilth/<feature>/`) | the workspace (user-authored, read-only to Tilth) | the user | the harness (task selection); worker (its task + the overview + the *plan* as injected prose context); evaluator (the task under review) |
| Evaluator ledger | `sessions/<id>/ledger/<task_id>.jsonl` | the harness (one entry per evaluator call) | evaluator (its prior verdicts on this task); worker (the same, on a retry) |

> The reviewing role is the **evaluator**.

The worker writes none of these. It writes code in the worktree, which the harness commits. The split is clean: **memory channels are inputs to the agents; session artifacts under `~/.tilth/sessions/<id>/` (`events.jsonl`, `summary.json`, `checkpoint.json`, `task-status.json`) are outputs the harness produces during a run.** The full read-it-once picture — every input, every output, and the three artifacts that are *both* — is laid out in [Anatomy of a run](anatomy-of-a-run.md); this page zooms in on the input channels.

## `AGENTS.md` — your project conventions

Short markdown. **User-owned, user-maintained.** Tilth reads it into the worker's user-prompt on every task and into the evaluator's prompt on every evaluator call — but never writes to it. Use whatever section headings make sense for your project; we suggest the ones below as a starting template:

```markdown
# AGENTS.md

## Project
One paragraph describing what this codebase is.

## Language and tooling
Python version, frameworks, test runner, linter, etc.

## Layout
Where things live.

## Style
- Standard library first.
- Type hints on public functions.
- ...

## Patterns
- (Add as you learn what works for this codebase.)

## Gotchas
- (Add as you trip over them.)
```

**AGENTS.md should stay project-focused.** It's for *project* conventions, not harness mechanics:

- **Belongs in AGENTS.md:** language version, test framework, file layout, style rules, project-specific gotchas, accumulated learnings.
- **Does *not* belong in AGENTS.md:** "record token counts in `events.jsonl`" (agent doesn't write that file), "mark your task done when finished" (the harness manages status), "stop after 32 iterations" (handled by `max_iterations_per_task`), "don't run dangerous commands" (handled by `pre_tool` hook), "the evaluator will evaluate your work" (see [Agent visibility](agent-visibility.md)).

The cleanest test: if you removed a rule from AGENTS.md and the harness still enforced the underlying behaviour, the rule shouldn't be there.

### Which file(s) Tilth reads

The channel isn't tied to a single filename. By default Tilth reads `AGENTS.md` **and** `CLAUDE.md` from the workspace root — in that order, concatenated — so a repo that keeps its conventions in `CLAUDE.md` (Claude Code's convention) is picked up out of the box, not left invisible. Override the list with `TILTH_CONTEXT_FILES` (comma-separated, first-listed highest priority); only files that exist are injected, and the combined text is capped so the prompt stays legible. Tilth never writes any of them.

## Git history — atomic commits per task

The worktree branch (`session/<id>`) gets one commit per completed task. The evaluator sees the working-tree diff against the branch's HEAD for the task under review; humans see the cumulative branch diff at review time.

A failed task lands a `FAILED (...)` placeholder commit so the partial work is preserved; `tilth resume` soft-unwinds that placeholder and the retry sees its own previous edits as uncommitted changes (so the evaluator gets a single cumulative diff, not just the new edits).

The branch is **never auto-merged**. Open a PR and review like any other branch.

## `progress.txt` — the chronological journal

Lives at `sessions/<id>/progress.txt`. Starts empty when the session is created; the harness appends one line per task outcome. The most recent ~30 lines are injected into each fresh task's prompt so the agent has rolling context — what was just done, what failed, what the cumulative shape of the run looks like.

The agent does *not* write to `progress.txt` directly; the harness writes after task done/fail.

## Task markdown — the work itself

This is the work. You author it in your repo in a feature directory you name at `<repo>/.tilth/<feature>/` — an `overview.md` (the feature's goal, context, and scope boundaries) plus one `T-NNN-<slug>.md` per task (frontmatter `id`/`title`, a description, acceptance criteria). The harness does not plan; you (or whatever agent you draft with) plan, and the files are the contract. The format reference is [The task format](../deep-dives/task-format.md).

The files are **read-only inputs** to Tilth — the harness never mutates your authored docs. Per-task *status* lives separately, in the harness-owned `sessions/<id>/task-status.json` (a flat `{task_id: status}` map; a task absent from the map is `pending`). The loop overlays status onto the static task list to pick the next pending task.

The agent **never sees the status store or the queue-management machinery.** It does see the *whole task list* as prose context (every task collapsed, the current one marked) plus the feature overview — framed as "context, not work to do" so it understands the shape of the feature without pre-empting later tasks. What stays hidden is the mutable state, not the plan.

Hiding the mutable status *state* from the agent prevents a cluster of real failure modes seen in earlier hand-built loops — the agent marking its own task done, skipping ahead, or rewriting the queue. State management belongs in code; the agent works on one task at a time and stops. (The worker is also told to treat `.tilth/` as read-only context, and the evaluator hard-rejects diffs that edit it.) The enumerated rationale is in [Agent visibility → why this separation is deliberate](agent-visibility.md#why-this-separation-is-deliberate).

## Evaluator ledger — the evaluator's per-task memory

Lives at `sessions/<id>/ledger/<task_id>.jsonl`. One append-only entry per evaluator call (`{ts, iter, diff_summary, case, verdict}`). It gives the evaluator memory across iterations of a single task — so it can confirm a prior concern was resolved instead of re-litigating it, and escalate when the same rejection category recurs. The last 5 entries are injected into each evaluator call, and into the worker's prompt as the reviewer's prior verdicts on its current task. Task-scoped and session-local; never crosses sessions; read straight off disk on resume. See [The worker↔evaluator dialogue](../deep-dives/worker-evaluator-dialogue.md) for the full mechanism.

## Why the channels live outside the agent

You could imagine baking all of this into the system prompt and letting the agent juggle it. Why not?

- **Context budget.** Re-injecting everything every turn gets expensive fast and crowds out the model's working memory for the actual code.
- **Resumability.** State outside the agent survives across sessions and provider switches. State *inside* the agent is gone the moment the conversation resets.
- **Auditability.** The channels are flat files in the workspace or session directory. You can `git log` them, diff them, hand them to teammates, version them. Anything inside the model is opaque.
- **Independence of the evaluator.** The evaluator runs in a fresh context across tasks (it carries per-task memory only via the ledger); without external memory channels, it would have nothing to look at except what the worker chose to expose.

See [Agent visibility](agent-visibility.md) for the full story of which artefacts the worker can and can't see.
