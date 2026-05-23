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
    - Self-improvement prompt — the worker decides whether anything should land in `AGENTS.md`.
    - Commit on the worktree branch. Append to `progress.txt`. Mark the task `done` in `prd.json`.
4. Stops on: all tasks done, iteration cap, wall-clock cap, token cap, or error.

> **Diagram suggestion** — *a left-to-right flow diagram of one task's lifecycle inside the harness: prompt assembly → tool-use loop → validators → judge → self-improvement → commit. Annotate which steps the agent sees and which are pure harness machinery.*

You can interrupt at any point with Ctrl-C. See [Resuming a session](resuming.md) to pick up where it stopped.

## What you should expect to see

The console streams every tool call as it happens. A clean run scrolls by like this:

![Terminal capture of `uv run tilth ~/projects/tilth-demo` in progress. Header lines show the worktree path, session id, branch `session/<id>`, and worker model `deepseek/deepseek-v4-flash`. Below, task T-001 runs through two iterations of bash and write_file tool calls, prints a "Done" summary, passes validators, gets accepted by the judge with a brief verdict, and is committed (`8a8839b`). Task T-002 iteration 1 begins at the bottom of the frame.](../assets/tilth-demo-terminal.png)

*One session in flight. The header is what the harness picks up at start; the body is the per-task loop — tool calls, validators, judge verdict, commit — repeating until the task list is exhausted.*
{: .caption }

A clean run ends with every task in `prd.json` marked `done` and a commit-per-task on the `session/<id>` branch. When the loop doesn't track this cleanly, watch for these patterns:

- **A task spinning** is signalled by the same files being read and re-written across iterations. If it happens, kill the run and rewrite the task description before retrying.
- **Validator feedback loops** show as repeated `validator_failed → next iteration` patterns. A handful is normal; a long string usually means the test suite or the lint config is misaligned with the agent's idea of "done."

## After the run

```bash
cd ~/projects/tilth-demo
git log session/<id> --oneline
git diff main..session/<id>
```

Each task is one commit. If you like the work, merge it into `main` like any other branch; if not, delete the branch. The harness never auto-merges. (You can also use [Resetting a session](resetting.md) to throw away the worktree, branch, and the harness's session directory in one shot.)

The session log lives at `<tilth-clone>/sessions/<id>/events.jsonl` — every model call, tool call, validator run, judge verdict, and AGENTS.md update is recorded. Alongside it, `sessions/<id>/summary.json` carries a rolled-up snapshot (token totals, per-task iteration counts, tool histogram, hook outcomes, judge accept/reject) refreshed at every task boundary — read that when you want a quick stat without `jq`-ing the full log.

For a more readable view of a finished run, see [Visualizing a session](visualizing.md).
