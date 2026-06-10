# The task format

A feature is authored as markdown in the target repo, at `<workspace>/.tilth/tasks/` — a required `overview.md` plus one `T-NNN-<slug>.md` per task. It's small, but every stage of a run reads from it, and not every contract between the pieces is enforced by the harness. This page is the reference for the format, the parsing rules, the harness-owned status overlay, and which contracts are load-bearing-but-soft.

For *where to author it and why it's the high-leverage moment*, see [Using on your own project](../getting-started/your-own-project.md). For *where the harness keeps its own state*, see [Session layout](session-layout.md). This page is the data itself.

> The files are the contract. The worker's job is to satisfy them, and the evaluator judges the diff against them — there's no codified test gate underneath. Vague descriptions and weak acceptance criteria collapse Tilth's quality gate down to "the evaluator said it looked fine."

## Shape

```
.tilth/tasks/
├── overview.md            # required — the feature's "why"
├── T-001-<slug>.md        # one file per task, ordered by id
├── T-002-<slug>.md
└── ...
```

### `overview.md`

Required, must be non-empty. Free-form markdown — the template `tilth run` prints suggests **Goal**, **Context**, **Scope boundaries**, and **Notes for the reviewer** sections, but nothing parses the headings; the whole text is injected verbatim (capped) into both the worker's and the evaluator's prompts. The scope-boundaries part is the high-leverage one: the evaluator hard-rejects cross-task interference, and the out-of-scope list is what tells it (and the worker) where the lines are.

### Task files — `T-NNN-<slug>.md`

Each task file is frontmatter plus two body sections:

```markdown
---
id: T-001
title: Add the `add` subcommand
---

## Description
What to build, in the worker's voice. Real paths and symbols
(pkg/module.py:func()), not "the entrypoint". This block becomes the
user message the worker sees.

## Acceptance criteria
- An externally checkable behaviour
- Another one
```

Parsing (`tilth/tasks.py`) is deliberately forgiving — a hand-rolled `key: value` frontmatter reader, no YAML dependency — but validation fails fast with an actionable message when the required pieces are missing:

| Piece | Rule |
|---|---|
| Frontmatter block | Required (`---` … `---`). Unknown keys are ignored (forward-compatible); a missing or unterminated block is an error. |
| `id` | Required; must match `T-<digits>` (zero-padded by convention, e.g. `T-001`). Must be unique across files. |
| `title` | Required, non-empty. |
| `## Description` | The section's text becomes the task description. Lenient fallback: with no explicit `## Description` heading, all body text outside the AC section is used. Empty → error. |
| `## Acceptance criteria` | `- ` / `* ` bullets are collected as the criteria list. Heading match is case-insensitive. *Not required* — a task with no AC parses fine, but gives the evaluator nothing concrete to gate on. Write them. |

Files are discovered by the glob `T-*.md` and ordered by `id`. The filename's `<slug>` is for humans; only the frontmatter `id` matters to the harness.

`tilth run` loads the whole directory **before** creating any session or worktree — a missing or malformed feature fails fast with the templates printed inline, and no orphan state is left behind.

## Status lives elsewhere — `task-status.json`

The authored files are **read-only inputs**; there is no `status` field in them and the harness never writes to your repo. Per-task status is harness-owned, at `sessions/<id>/task-status.json` — a flat map:

```json
{
  "T-001": "done",
  "T-002": "failed"
}
```

A task absent from the map is `pending`. The loop overlays this map onto the static task list each pass (`loop.py:_overlay_status`) to get the runtime view; `_next_pending` picks the first `pending` entry — that's the whole scheduling algorithm. Two transitions: `pending → done` (evaluator accepted, commit landed) and `pending → failed` (a terminal task outcome: `iter_cap`, `evaluator_cap`, `empty_responses`, or `no_case`). Wall-clock and token caps and interrupts stop *between* tasks — they leave every task's status untouched and the session resumable.

This split has a practical consequence: **the task content is re-read from your repo on every pass and on every resume.** You can sharpen a task description between a failed run and its `tilth resume`, and the retry sees the new text — the harness state only remembers *which* tasks are done, not what they said.

## Per-field reader table

Who reads each piece, and what they do with it:

| Piece | Worker sees | Evaluator sees | Harness uses |
|---|---|---|---|
| `overview.md` | injected as "Feature overview" (capped 4 KB) | injected as "Feature overview (the why + scope boundaries)" | — |
| `id` | task header in user prompt; in the plan-as-context | task header in evaluator prompt | scheduling key in `task-status.json`; commit message; ledger filename |
| `title` | task header | task header | commit message (`<id>: <title>`) |
| `description` | full text in user prompt; collapsed in the plan-as-context | full text in evaluator prompt | — |
| `acceptance_criteria` | bulleted in user prompt; the worker maps each to a `file:symbol` in its `submit_case` `ac_coverage` | bulleted in evaluator prompt; most of the six rejection categories (`acceptance_gap` above all) reference them | — |
| status (overlay) | each task's status appears in the plan-as-context (read-only) | — | `_next_pending` reads it; the loop mutates it after each task |

A few notes worth pulling out of the table:

- **The worker never receives the files or the status store directly.** [`memory.build_user_prompt`](https://github.com/AlteredCraft/tilth/blob/main/tilth/memory.py) formats the current task as a user message and injects the *whole* plan — every task collapsed (id/title/status/description/AC), framed as "context, not work to do" and capped at 6 KB — so the worker understands the surrounding tasks but never sees the mutable state or the queue it manages (see [Agent visibility](agent-visibility.md)). The directory does physically sit in the worktree; the system prompt marks `.tilth/` read-only and the evaluator hard-rejects edits to it.
- **The evaluator sees one task at a time, fresh across tasks but with per-task memory.** It carries no memory across tasks, but within a task it reads the last 5 entries of that task's ledger (`sessions/<id>/ledger/<task_id>.jsonl`) — its own prior verdicts — so it can confirm a concern was resolved instead of re-litigating it. It gates on the diff plus the worker's structured case, anchored on `description` + `acceptance_criteria` + the overview's scope boundaries.

> **The acceptance criteria are conventions for a *reviewer*, not assertions for a test runner.** Nothing mechanical checks them — no test suite runs, no assertion count is compared. A criterion the evaluator can't check against a diff ("user is happy") gives the run no real contract anywhere; a criterion phrased as observable behaviour ("`cli export --format json` writes valid JSON to stdout") gives the worker something to verify with `bash` and the evaluator something to gate on. Write the criteria as if you were the evaluator.

## What the task directory is *not*

- **Not harness state.** The harness never writes under `.tilth/` — status, progress, and ledgers all live under `sessions/<id>/`. Deleting a session (`tilth reset`) leaves your authored files untouched.
- **Not a queue the agent manages.** The worker sees the plan as read-only prose; scheduling is `_next_pending` in code.
- **Not versioned.** There's no format-version field, and the schema isn't formally pinned. The frontmatter reader ignores unknown keys, so additive extensions (e.g. task dependencies, per-task budget hints) have room — but nothing of the sort exists yet.
- **Not generated.** Earlier Tilth had an interview step (`prep-feature`) that produced a machine-written `prd.json`; the prompt-driven refactor replaced it with this hand-authored format. Draft the files with whatever agent you like — the templates are designed to be model-fillable — but you sign the contract.

## Where to look in the code

| Want to understand… | Read… |
|---|---|
| Parsing + validation rules, the templates | [`tilth/tasks.py`](https://github.com/AlteredCraft/tilth/blob/main/tilth/tasks.py) — `parse_task_file`, `load_feature`, `OVERVIEW_TEMPLATE` / `TASK_TEMPLATE`. |
| The status overlay | [`tilth/loop.py`](https://github.com/AlteredCraft/tilth/blob/main/tilth/loop.py) — `_load_status` / `_save_status` / `_overlay_status` / `_next_pending`. |
| How the worker sees a task | [`tilth/memory.py:build_user_prompt`](https://github.com/AlteredCraft/tilth/blob/main/tilth/memory.py) — current task + overview + full plan as context + own ledger. |
| How the evaluator sees a task | [`tilth/loop.py:_evaluator_task`](https://github.com/AlteredCraft/tilth/blob/main/tilth/loop.py) — description + AC + overview + project context + ledger + case + diff. |
