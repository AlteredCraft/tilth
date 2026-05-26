# Memory channels

Four memory channels live *outside* the agent. Some are project files the user owns; some are session-local artifacts the harness manages. Together they give a long-running run continuity by treating durable state as files on disk rather than messages the model has to carry between calls.

| Channel | Lives in | Written by | Read by |
|---|---|---|---|
| `AGENTS.md` | the workspace (user-owned) | the user | worker, judge (injected at task start / judge call, when present) |
| Git history | the worktree | the harness (one commit per task) | humans, judge (via diff) |
| `progress.txt` | `sessions/<id>/` (harness-owned) | the harness (one line per task outcome) | worker (last ~30 lines injected) |
| `prd.json` | `sessions/<id>/` (harness-owned) | `tilth prep-feature` (seed) and the harness (status flips) | the harness (task selection) |

The worker writes none of these. It writes code in the worktree, which the harness commits. The split is clean: **memory channels are inputs to the agents; session artifacts under `sessions/<id>/` (events.jsonl, summary.json, proposed-learnings.md, seed-meta.json) are outputs the harness produces during a run.**

`prd.json` and `progress.txt` used to live in the workspace itself, which leaked harness state into every PR. Phase 1 of the prep-feature work moved them under `sessions/<id>/`. Your workspace now only ships the things that genuinely belong in the PR — source changes and tests.

> **Diagram suggestion** — *four labelled "channels" feeding into a worker bubble at the centre with arrows annotated with the cadence of each: AGENTS.md ("at task start, one-way from user"), progress.txt ("last 30 lines, at task start"), git history ("via judge diff"), prd.json ("not visible to worker — harness only"). Reinforces the asymmetry of what the agent sees and the one-way directionality of AGENTS.md.*

## `AGENTS.md` — your project conventions

Short markdown. **User-owned, user-maintained.** Tilth reads it into the worker's user-prompt on every task and into the judge's user-prompt on every judge call, but never writes to it. Use whatever section headings make sense for your project; we suggest the ones below as a starting template:

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
- **Does *not* belong in AGENTS.md:** "record token counts in `events.jsonl`" (agent doesn't write that file), "update `prd.json` status when done" (agent doesn't manage prd), "stop after 8 iterations" (handled by `max_iterations_per_task`), "don't run dangerous commands" (handled by `pre_tool` hook), "the judge will evaluate your work" (see [Agent visibility](../deep-dives/agent-visibility.md)).

The cleanest test: if you removed a rule from AGENTS.md and the harness still enforced the underlying behaviour, the rule shouldn't be there.

### Where do learnings go?

After each task, Tilth runs a *self-improvement* step that asks the worker model whether the task surfaced anything durable worth capturing for later. The output of that step does **not** land in your AGENTS.md. It lands in `sessions/<id>/proposed-learnings.md` — a session-local file outside the worktree, never in the PR diff.

The user (and eventually an end-of-session findings hook) is the integrator: read the proposals at session end, decide which (if any) are worth promoting into your AGENTS.md, and merge them by hand. AGENTS.md stays in your voice, growing only when you decide it should.

## Git history — atomic commits per task

The worktree branch (`session/<id>`) gets one commit per completed task. The judge sees the cumulative diff against `main` for each finished task; humans see the same diff at review time.

A failed task lands a `FAILED (...)` placeholder commit so the partial work is preserved; `--resume` soft-unwinds that placeholder and the retry sees its own previous edits as uncommitted changes (so the judge gets a single cumulative diff, not just the new edits).

The branch is **never auto-merged**. Open a PR and review like any other branch.

## `progress.txt` — the chronological journal

Lives at `sessions/<id>/progress.txt`. Starts empty when the session is created; the harness appends one line per task outcome. The most recent ~30 lines are injected into each fresh task's prompt so the agent has rolling context — what was just done, what failed, what the cumulative shape of the run looks like.

The agent does *not* write to `progress.txt` directly; the harness writes after task done/fail.

## `prd.json` — the task list

This is the work. Lives at `sessions/<id>/prd.json`. The harness does not plan; the *seed* (interview output) plans, and `tilth prep-feature` runs that interview against your codebase to produce the file. See [Seeding a session](../deep-dives/seeding.md) for the full story.

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

The agent **never sees this file or its structure**. The harness reads it to pick the next pending task and writes it to flip status (`pending` → `done` / `failed`). The agent receives its current task as the user message at the start of a fresh context — it knows what it's working on, but it doesn't know the queue exists.

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
