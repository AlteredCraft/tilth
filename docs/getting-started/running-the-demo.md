# Running the demo

The demo is a small todo-CLI workspace, pre-seeded with `prd.json`, `AGENTS.md`, `progress.txt`, and `tests/` — exactly the shape you'd give Tilth for your own project. It lives in its own repo so it's a realistic example, not a special case.

## Clone the demo workspace

> **Path used on this page.** Commands below use `~/projects/tilth-demo` as an illustrative location. Tilth doesn't care where the workspace lives — the path is just a CLI argument — so substitute any directory that matches your setup. Treat the demo repo as a stand-in for your own.

```bash
git clone git@github.com:AlteredCraft/tilth-demo-todo-cli.git ~/projects/tilth-demo
```

## Run a session against the demo

```bash
uv run tilth ~/projects/tilth-demo
```

What happens, end-to-end:

1. Tilth verifies the path is a git repo on a clean main.
2. Creates a worktree of the demo repo. The working tree lives at `<tilth-clone>/sessions/<id>/workspace/` (inside Tilth, gitignored); the new branch `session/<id>` is registered in the demo repo's `.git`. The two halves live in different places by design — see [Session layout](../deep-dives/session-layout.md) for the why.
3. Loops through pending tasks in `prd.json`. For each task:
    - Reset context. Prompt = system + AGENTS.md + recent progress + this task.
    - Tool-loop with the worker model (bash, file ops, search) until it stops calling tools.
    - Run `ruff` + `pytest` in the worktree. Failures get fed back into the loop.
    - Judge model reviews the diff in a fresh context. Rejections get fed back.
    - Self-improvement prompt — the worker considers whether the task surfaced a durable observation worth proposing. Any proposal lands in `sessions/<id>/proposed-learnings.md` (not in your repo) for end-of-run review.
    - Commit on the worktree branch. Append to `progress.txt`. Mark the task `done` in `prd.json`.
4. Stops on: all tasks done, iteration cap, wall-clock cap, token cap, or error.

You can interrupt at any point with Ctrl-C. Ctrl-C and cap hits (iteration, wall-clock, token) both leave the run in a resumable state — see [Resuming a session](resuming.md) to pick it back up. If a cap was what stopped you, bump it in `.env` first or `--resume` will trip it again.

## What you should expect to see

The console streams every tool call as it happens. The per-task loop has the shape below:

![Six rounded boxes arranged left to right depicting one task's lifecycle inside Tilth's harness: PROMPT (a stack of three document icons representing AGENTS.md, progress.txt, and the task; caption "fresh context built from disk"); TOOL LOOP (a wrench-and-file glyph encircled by a loop arrow, with monospace tool labels bash, read_file, edit_file, grep; caption "worker iterates until it stops"); VALIDATORS (a checkmark over a terminal prompt, labels ruff and pytest; caption "objective gate"); JUDGE (a balance scale; caption "subjective gate, fresh context"); SELF-IMPROVE (a notebook with a sage-green bookmark ribbon; caption "propose a learning (optional)"); COMMIT (a git-branch glyph with a single new-commit dot; caption "one task = one commit"). Two label-bars span the top: "WORKER SEES" over PROMPT and TOOL LOOP, "HARNESS ONLY" over the remaining four boxes. Sage-green forward arrows connect each box to the next; two thinner sage-green feedback curves return to TOOL LOOP from VALIDATORS (labelled validator_failed) and from JUDGE (labelled judge_rejected).](../assets/per-task-lifecycle.jpg)

*One task's lifecycle inside the harness. The worker only sees the Prompt and the Tool Loop; everything from Validators onward is harness machinery the worker is unaware of. Failed validators or a rejected judge verdict feed back into the Tool Loop for another iteration.*
{: .caption }

A clean run ends with every task in `prd.json` marked `done` and a commit-per-task on the `session/<id>` branch. When the loop doesn't track this cleanly, watch for these patterns:

- **A task spinning** is signalled by the same files being read and re-written across iterations. If it happens, kill the run and rewrite the task description before retrying.
- **Validator feedback loops** show as repeated `validator_failed → next iteration` patterns. A handful is normal; a long string usually means the test suite or the lint config is misaligned with the agent's idea of "done."

## After the run

Once every task in `prd.json` is `done`, the harness closes out the final task and prints `all tasks complete` followed by a run summary:

![A three-region diagram of Tilth's end-of-session state. Left region: a vertical stack of five small rounded rectangles, each containing a monospace task id (T-001 through T-005) and a checkmark; italic caption beneath reads "tasks done · one commit each". Centre region: a larger rounded panel titled "RUN SUMMARY" in bold sans-serif all caps, with four monospace key/value rows — session 20260525-103149-3800ea, duration 6m10s, tokens 412,800, tasks total=5 done=5 failed=0 pending=0 — and an italic caption beneath reading "harness reports out". Right region: a document icon labelled "proposed-learnings.md" with three short bullet lines inside, and a monospace path beneath: sessions/<id>/proposed-learnings.md. Two label-bars span the top: "ON THE SESSION BRANCH" over the task stack and run summary; "OUTSIDE THE WORKTREE" over the proposed-learnings document. A sage-green arrow curves from the bottom of the RUN SUMMARY panel up into the document icon, labelled alongside the curve "→ N proposed learnings written — review when ready".](../assets/session-end.png)

*A clean ending. Every task is committed on the session branch; the run summary tallies what happened; proposed learnings (if any) land outside the worktree for the user to review and merge by hand. AGENTS.md is never touched by the run.*
{: .caption }

To inspect what just got committed:

```bash
cd ~/projects/tilth-demo
git log session/<id> --oneline
git diff main..session/<id>
```

Each task is one commit. If you like the work, merge it into `main` like any other branch; if not, delete the branch. The harness never auto-merges. (You can also use [Resetting a session](resetting.md) to throw away the worktree, branch, and the harness's session directory in one shot.)

The session log lives at `<tilth-clone>/sessions/<id>/events.jsonl` — every model call, tool call, validator run, judge verdict, and proposed-learning verdict is recorded. Alongside it, `sessions/<id>/summary.json` carries a rolled-up snapshot (token totals, per-task iteration counts, tool histogram, hook outcomes, judge accept/reject) refreshed at every task boundary — read that when you want a quick stat without `jq`-ing the full log.

For a more readable view of a finished run, see [Visualizing a session](visualizing.md).
