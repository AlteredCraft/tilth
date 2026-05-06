# Memory channels

Four memory channels live *outside* the agent, in the workspace itself. The agent reads and writes some of them; the harness reads and writes others. Together they give a long-running run continuity without inflating the per-call context.

| Channel | Lives in | Written by | Read by |
|---|---|---|---|
| `AGENTS.md` | the workspace | self-improvement step (worker, separate call) | worker (injected at task start) |
| Git history | the worktree | the harness (one commit per task) | humans, judge (via diff) |
| `progress.txt` | the workspace | the harness (one line per task outcome) | worker (last ~30 lines injected) |
| `prd.json` | the workspace | the harness (status flips) | the harness (task selection) |

> **Diagram suggestion** — *four labelled "channels" feeding into a worker bubble at the centre with arrows annotated with the cadence of each: AGENTS.md ("at task start"), progress.txt ("last 30 lines, at task start"), git history ("via judge diff"), prd.json ("not visible to worker — harness only"). Reinforces the asymmetry of what the agent sees.*

## `AGENTS.md` — the agent's own learned conventions

Short markdown. The self-improvement step appends learnings under named sections. Use these section headings exactly so updates land in the right place:

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
_(empty — agent appends here)_

## Gotchas
_(empty — agent appends here)_

## Recent learnings
_(empty — agent appends here)_
```

If the headings don't exist or are named differently, learnings still land but in a new section appended to the end. The `_(empty — agent appends here)_` placeholder gets replaced by the first append.

**AGENTS.md should stay project-focused.** It's for *project* conventions, not harness mechanics:

- **Belongs in AGENTS.md:** language version, test framework, file layout, style rules, project-specific gotchas, accumulated learnings.
- **Does *not* belong in AGENTS.md:** "record token counts in `events.jsonl`" (agent doesn't write that file), "update `prd.json` status when done" (agent doesn't manage prd), "stop after 8 iterations" (handled by `max_iterations_per_task`), "don't run dangerous commands" (handled by `pre_tool` hook), "the judge will evaluate your work" (see [Agent visibility](../deep-dives/agent-visibility.md)).

The cleanest test: if you removed a rule from AGENTS.md and the harness still enforced the underlying behaviour, the rule shouldn't be there.

## Git history — atomic commits per task

The worktree branch (`session/<id>`) gets one commit per completed task. The judge sees the cumulative diff against `main` for each finished task; humans see the same diff at review time.

A failed task lands a `FAILED (...)` placeholder commit so the partial work is preserved; `--resume` soft-unwinds that placeholder and the retry sees its own previous edits as uncommitted changes (so the judge gets a single cumulative diff, not just the new edits).

The branch is **never auto-merged**. Open a PR and review like any other branch.

## `progress.txt` — the chronological journal

Start it empty. The harness appends one line per task outcome. The most recent ~30 lines are injected into each fresh task's prompt so the agent has rolling context — what was just done, what failed, what the cumulative shape of the run looks like.

The agent does *not* write to `progress.txt` directly; the harness writes after task done/fail.

## `prd.json` — the task list

This is the work. The harness does not plan; **you plan**.

```json
[
  {
    "id": "T-001",
    "title": "Short imperative title",
    "description": "What needs to be done. Be specific. Reference files if useful.",
    "acceptance_criteria": [
      "Concrete, checkable statement.",
      "Another concrete, checkable statement."
    ],
    "status": "pending"
  }
]
```

The agent **never sees this file or its structure**. The harness reads it to pick the next pending task and writes it to flip status (`pending` → `in_progress` → `done` / `failed`). The agent receives its current task as the user message at the start of a fresh context — it knows what it's working on, but it doesn't know the queue exists.

Hiding `prd.json` from the agent prevents three real failure modes seen in earlier hand-built loops:

1. The agent marks its own task done.
2. The agent skips ahead to a "more interesting" task.
3. The agent rewrites the queue.

State management belongs in code; the agent works on one task at a time and stops.

## Why the channels live outside the agent

You could imagine baking all of this into the system prompt and letting the agent juggle it. Why not?

- **Context budget.** Re-injecting the whole task list every turn gets expensive fast and crowds out the model's working memory for the actual code.
- **Resumability.** State outside the agent survives across sessions and provider switches. State *inside* the agent is gone the moment the conversation resets.
- **Auditability.** The four channels are flat files in the workspace. You can `git log` them, diff them, hand them to teammates, version them. Anything inside the model is opaque.
- **Independence of the judge.** The judge runs in a fresh context; without external memory channels, it would have nothing to look at except what the worker chose to expose.

See [Agent visibility](../deep-dives/agent-visibility.md) for the full story of which artefacts the worker can and can't see.
