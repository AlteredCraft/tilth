# Running the demo

The demo workspace is deliberately almost empty — just an `AGENTS.md` (the project's conventions) and a `.gitignore`. It exists mainly as a *git repo*, which is all Tilth needs to do what it always does: branch off a worktree and build inside it. The path mirrors what a real first-time user does — author the feature as markdown in a feature directory under `.tilth/`, then point `tilth run` at it. Nothing is pre-baked; the todo CLI gets built from scratch during the run.

## Clone the demo workspace

> **Path used on this page.** Commands below use `~/projects/tilth-demo` as an illustrative location, and `todo-cli` as an illustrative feature name. Tilth doesn't care where the workspace lives or what you call the feature — you pass the feature directory's path and it derives the enclosing repo — so substitute any directory that matches your setup. Treat the demo repo as a stand-in for your own.

```bash
git clone git@github.com:AlteredCraft/tilth-demo-todo-cli.git ~/projects/tilth-demo
```

## Author the feature

There is no interview or prep step: the work is a directory of markdown files you write (by hand, or with whatever agent you like) in the target repo, at `<repo>/.tilth/<feature>/` — you name the feature directory, so one repo can hold several features side by side:

```
.tilth/todo-cli/
├── overview.md            # the feature's goal + scope boundaries (required)
├── T-001-<slug>.md        # one file per task, ordered by id
├── T-002-<slug>.md
└── ...
```

Each task file is small frontmatter plus two sections — a description in the worker's voice, and externally checkable acceptance criteria:

```markdown
---
id: T-001
title: Add the `add` subcommand
---

## Description
What to build, in the worker's voice. Real paths/symbols
(todo_cli/__main__.py:main()), not "the entrypoint".

## Acceptance criteria
- An externally checkable behaviour
- Another one
```

If the directory is missing (or malformed), `tilth run` fails fast *before* creating any session and prints ready-to-fill templates for `overview.md` and a task file — so the cheapest way to learn the shape is to just run it once. The full format reference (parsing rules, what's required, who reads each field) is [The task format](../deep-dives/task-format.md).

For the demo, try a feature like *"a minimal todo CLI with add, list, and done subcommands, on-disk format `- [ ] item` in `TODOS.md`"*, sliced into three or four tasks.

![Filesystem trees for one Tilth run: HARNESS SIDE under ~/.tilth/sessions/<id>/ holds workspace/, events.jsonl, summary.json, checkpoint.json; TARGET REPO SIDE under ~/projects/tilth-demo/.git/ holds refs/heads/session/<id> and worktrees/workspace/. A sage-green arrow labeled 'git worktree binds these' connects the workspace/ on the left to the worktrees/workspace/ admin entry on the right.](../assets/session-layout.png)

*Where a session's state lives. Everything the harness writes — the event log, the status overlay, the summary — sits under Tilth's per-user data dir (`~/.tilth/sessions/<id>/`); only the `session/<id>` branch and its worktree admin entry live in the demo repo's `.git`. The task markdown you authored stays in your repo, where you put it. Full breakdown in [Session layout](../deep-dives/session-layout.md).*
{: .caption }

## Run a session against the demo

```bash
tilth run ~/projects/tilth-demo/.tilth/todo-cli
```

What happens, end-to-end:

1. Tilth reads the feature directory you pointed it at and derives the enclosing git repo for the worktree (failing fast with templates if the directory has no feature).
2. Creates a fresh session and a worktree of the demo repo. The working tree lives at `~/.tilth/sessions/<id>/workspace/` (in Tilth's per-user data dir, outside the demo repo); the new branch `session/<id>` is registered in the demo repo's `.git`. The two halves live in different places by design — see [Session layout](../deep-dives/session-layout.md) for the why.
3. Loops through pending tasks in order. For each task:
    - Reset context. Prompt = system + project context (`AGENTS.md`/`CLAUDE.md`) + recent progress + the feature overview + the full plan (as context) + this task (and, on a retry, the evaluator's prior verdicts on it).
    - Tool-loop with the worker model (bash, file ops, search) until it calls `submit_case` to present its finished work.
    - Evaluator model reviews the case + diff in a fresh context (it also sees this task's prior verdicts). Rejections get fed back as structured feedback. **The evaluator is the only gate** — there is no codified test/lint step; the worker is told to verify its own work via `bash` before presenting it.
    - On accept: commit on the worktree branch, append to `progress.txt`, mark the task `done` in the harness's status overlay.
4. Stops on: all tasks done, iteration cap, wall-clock cap, dollar-spend cap, evaluator-call cap, or a terminal failure (e.g. a provider returning empty responses, or the worker never presenting a case).

You can interrupt at any point with Ctrl-C. Ctrl-C and cap hits (iteration, wall-clock, dollar-spend) all leave the run in a resumable state — see [Resuming & resetting](resuming-and-resetting.md) to pick it back up. Of the three caps, only the **dollar-spend** cap needs attention before you resume: the cumulative spend carries across resumes, so if `TILTH_MAX_TOKEN_DOLLAR_SPEND` is what stopped the run, raise it in `~/.tilth/.env` first or `tilth resume` trips it again on the first check. The wall-clock budget resets per resume, and the iteration cap is per-task (a retried task starts counting from one), so neither blocks a resume unless the work genuinely needs a bigger budget — see [What resume does](resuming-and-resetting.md#what-resume-does).

## What you should expect to see

The console streams every tool call as it happens. The per-task loop has the shape below:

![Four rounded boxes arranged left to right depicting one task's lifecycle inside Tilth's harness: PROMPT (a stack of document icons; caption "fresh context built from disk"); TOOL LOOP (a wrench-and-file glyph encircled by a loop arrow, with monospace tool labels bash, read_file, edit_file, grep; caption "worker iterates, then presents its case"); EVALUATOR (a balance scale; caption "the only gate, fresh context"); COMMIT (a git-branch glyph with a single new-commit dot; caption "one task = one commit"). Two label-bars span the top: "WORKER SEES" over PROMPT and TOOL LOOP, "HARNESS ONLY" over the remaining boxes. Sage-green forward arrows connect each box; a thinner sage-green feedback curve returns to TOOL LOOP from EVALUATOR (labelled evaluator_rejected).](../assets/per-task-lifecycle.png)

*One task's lifecycle inside the harness. The worker sees the Prompt and the Tool Loop; the evaluation machinery stays harness-side. A rejected evaluator verdict feeds back into the Tool Loop for another iteration.*
{: .caption }

A clean run ends with every task marked `done` and a commit-per-task on the `session/<id>` branch. When the loop doesn't track this cleanly, watch for these patterns:

- **A task spinning** is signalled by the same files being read and re-written across iterations. If it happens, kill the run and rewrite the task description before retrying.
- **Evaluator rejection loops** show as repeated `evaluator rejects → next iteration` patterns. A handful is normal; a long string usually means the acceptance criteria are misaligned with the task description — the worker keeps satisfying one reading while the evaluator holds the other. Sharpen the task file.

## After the run

Once every task is `done`, the harness closes out the final task and prints `all tasks complete` followed by a run summary:

![A three-region diagram of Tilth's end-of-session state. Left region, under the label "ON THE SESSION BRANCH": a vertical stack of five rounded rectangles, each a monospace task id with a checkmark — T-001 through T-005 — with the italic caption "tasks done · one commit each". Centre region: a rounded panel titled "RUN SUMMARY" in bold sans-serif all caps, with four monospace key/value rows — session 20260525-103149-3800ea, duration 6m10s, tokens 412,800, tasks total=5 done=5 failed=0 pending=0 — followed by a trailing ellipsis row (•••) abridging the remaining summary lines, and the italic caption "harness reports out". Right region, under the label "WRITTEN UNDER sessions/<id>/": a vertical stack of document-icon chips, each a monospace filename with a short italic role note — events.jsonl ("full audit trail"), summary.json ("rolled-up snapshot"), checkpoint.json ("resume footing"). A sage-green arrow runs from the task stack into the RUN SUMMARY panel; a second sage-green arrow curves from the panel up into the right-hand stack, labelled "everything one run leaves on disk".](../assets/session-end.png)

*A clean ending. Every task is committed on the session branch (left); the run summary tallies what happened (centre); and the artifacts the run wrote under `~/.tilth/sessions/<id>/` — the event log, the rolled-up summary, the resume checkpoint — sit on the right, outside the worktree for you to read or resume from. Your `AGENTS.md` and `.tilth/<feature>/` are never touched by the run.*
{: .caption }

To inspect what just got committed:

```bash
cd ~/projects/tilth-demo
git log session/<id> --oneline
git diff main..session/<id>
```

Each task is one commit. If you like the work, merge it into `main` like any other branch; if not, delete the branch. The harness never auto-merges. (You can also use [`tilth reset`](resuming-and-resetting.md#resetting) to throw away the worktree, branch, and the harness's session directory in one shot.)

The session log lives at `~/.tilth/sessions/<id>/events.jsonl` — every model call, tool call, and evaluator verdict is recorded (see [Session layout → Event types](../deep-dives/session-layout.md#event-types) for the full taxonomy). Alongside it, `~/.tilth/sessions/<id>/summary.json` carries a rolled-up snapshot (token totals, per-task iteration counts, tool histogram, hook outcomes, evaluator accepts/rejects with rejection categories) refreshed at every task boundary — read that when you want a quick stat without `jq`-ing the full log.

For a more readable view of a finished run, see [Visualizing a session](visualizing.md).
