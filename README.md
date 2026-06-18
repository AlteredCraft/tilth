# Tilth

> *Prepare the ground, let the agent grow the work.*

A minimal long-running agent harness against an **OpenAI-compatible** LLM endpoint. Tested today against [OpenRouter](https://openrouter.ai); the OpenAI SDK underneath means other OpenAI-flavour gateways should work, but support for them is on the roadmap rather than validated. Built to learn (and demonstrate) the Brain/Hands/Session split, the Ralph loop, and the file-backed memory channels described in Addy Osmani's [long-running agents](https://addyosmani.com/blog/long-running-agents/) and [agent harness engineering](https://addyosmani.com/blog/agent-harness-engineering/) posts.

![Brain / Hands / Session split — three boxes connected by flow arrows, with the files that implement each piece](docs/assets/brain-hands-session.png)

**Audience:** This is an active research project for my work in [Altered Craft](https://alteredcraft.com). I do actively use it for real work, so I'd suggest it for single-dev / few-dev teams who want to *understand* what a long-running agent harness actually does. That's today (June 2026); the future, we shall see.

**Target run:** 10–60 minutes of autonomous work against an open model (default `deepseek/deepseek-v4-flash` on OpenRouter for the worker; the evaluator defaults to `deepseek/deepseek-v4-pro`), completing a short task list against a small project on a per-session git worktree.

> **Status — prompt-driven core.** Tilth is deliberately small and currently being driven *down* to its essentials: a worker and an independent evaluator, the base file/search/bash tools, and full observability. There is **no codified test/lint gate** — the evaluator is the only gate — and **no interview step**: you author the work as markdown and run it. Capabilities get added back only as testing shows they're needed.

## How Tilth differs

Many minimal coding agents are *interactive* — a developer watches the output and course-corrects, kills a bad run, or re-prompts. Tilth runs *autonomously* for the length of a run, with no one watching mid-task. That single difference is why it carries machinery a pair-programming agent can skip: an **evaluator** — a second model that judges whether a change is a *proper* solution against the task's acceptance criteria, not just whether the code runs; **between-task caps** that stand in for the budget ceiling a human would otherwise impose; a per-task **evaluator ledger** so a retried task sees the reviewer's prior verdicts; **state kept out of the model's context**; and **offline-first observability** (detailed just below). None of this is a knock on interactive agents; it's a different shape for a different job.

### Hyper-observability

If no one is watching a run mid-flight, the recording *is* the supervision. Tilth's standing goal is **hyper-observability** — *every prompt the harness sends is accessible, and every run is fully inspectable after the fact.* Every assembled prompt, memory load, model call, and evaluator verdict lands in an append-only `events.jsonl`, and `tilth visualize` serves the whole thing as a local chat-style web app — tail an active run in near-realtime or replay a finished one end-to-end, with no state hidden out of reach.

![A finished Tilth run rendered as chat-style HTML: session header, task divider, a model-call meta strip with an expanded reasoning block, and a bash tool call with its result](docs/assets/session-render.png)

*A finished run, rendered by `tilth visualize`.*

It's an early example of the goal, not a finished product. For the full product story — the Brain/Hands/Session split in detail, the memory channels, the two loops, and the worker↔evaluator dialogue — see the **[docs site](https://alteredcraft.github.io/tilth/)**. (The docs are mid-revision for the prompt-driven core; the README is the current source of truth for the run flow.)

## Quickstart

```bash
git clone git@github.com:AlteredCraft/tilth.git
cd tilth
# This will put `tilth` on your PATH, runnable from anywhere, but point to the local code
#   here. This enalbes you to iterate on the codebase without having to reinstall the tool.
uv tool install --editable .   # puts `tilth` on your PATH, runnable from anywhere

tilth init                     # scaffolds ~/.tilth/.env
# edit ~/.tilth/.env — TILTH_BASE_URL, TILTH_API_KEY, TILTH_WORKER_MODEL are all
# required (Tilth refuses to start without them so a misconfigured run can't
# silently fall back to a provider/model your account doesn't have)
```

Tilth keeps all per-user state under `~/.tilth/` — the `.env` above and every run's `sessions/<id>/`. Relocate it with `$TILTH_HOME` (whole tree) or `$TILTH_SESSIONS_DIR` (just the runs).

You author the feature as markdown in the target repo, then run it — there's no interview step. The work lives under `<repo>/.tilth/tasks/`:

```
.tilth/tasks/
├── overview.md            # the feature's goal + scope boundaries (required)
├── T-001-<slug>.md        # one file per task, ordered by id
├── T-002-<slug>.md
└── ...
```

Each task file is small frontmatter plus two sections:

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

Then point Tilth at the repo:

```bash
git clone git@github.com:AlteredCraft/tilth-demo-todo-cli.git tilth-demo
# author tilth-demo/.tilth/tasks/  (run prints ready-to-fill templates if it's missing)
tilth run ./tilth-demo
```

For each pending task, Tilth resets context from disk, lets the worker work with the file/search/bash tools until it calls `submit_case`, hands the case + diff to the evaluator in a fresh context, and on `accept` commits one task = one commit on the `session/<id>` branch (humans review and merge — Tilth never auto-merges). A run stops on all-tasks-done or a cap (iterations / wall-clock / tokens / evaluator calls). Interrupt with Ctrl-C; resume with `tilth resume`.

```bash
tilth resume                 # continue the latest session
tilth reset                  # tear down a session's worktree + branch + dir
tilth visualize              # serve the live session viewer (127.0.0.1:8765)
```

The `TILTH_*` env-var table (caps, evaluator routing, context-file selection) is documented in the generated `~/.tilth/.env` (copied from `.env.example`).

## Working with the codebase

Working *on* Tilth itself rather than using it? `uv sync` for the dev env, then run the CLI straight from the clone with `uv run tilth …` (no install needed — sessions still land in `~/.tilth/` unless you set `$TILTH_HOME`).

```bash
# Lint
.venv/bin/python -m ruff check tilth/

# Tests
.venv/bin/python -m pytest

# Docs — live preview at http://127.0.0.1:8000
uv run --extra docs mkdocs serve

# Docs — strict build (the CI gate; catches broken nav refs, missing files, dead links)
uv run --extra docs mkdocs build --strict --site-dir /tmp/tilth-site
```

See [`CLAUDE.md`](./CLAUDE.md) for repo conventions and the architecture invariants worth preserving when editing the harness itself.
