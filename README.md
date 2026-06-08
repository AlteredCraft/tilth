# Tilth

> *Prepare the ground, let the agent grow the work.*

A minimal long-running agent harness against an **OpenAI-compatible** LLM endpoint. Tested today against [OpenRouter](https://openrouter.ai); the OpenAI SDK underneath means other OpenAI-flavour gateways should work, but support for them is on the roadmap rather than validated. Built to learn (and demonstrate) the Brain/Hands/Session split, the Ralph loop, and the four memory channels described in Addy Osmani's [long-running agents](https://addyosmani.com/blog/long-running-agents/), [agent harness engineering](https://addyosmani.com/blog/agent-harness-engineering/), and [self-improving agents](https://addyosmani.com/blog/self-improving-agents/) posts.

![Brain / Hands / Session split — three boxes connected by flow arrows, with the files that implement each piece](docs/assets/brain-hands-session.png)

**Audience:** This is an active research project for my work in [Altered Craft](https://alteredcraft.com). I do activly use it for real work, so I would advise it for single-dev / few-dev teams who want to *understand* what a long-running agent harness actually does. That is today (May-2026), in the future, we shall see.

**Target run:** I test with 10-60 minutes of autonomous work against an open model (default `deepseek/deepseek-v4-flash` on OpenRouter for the worker; the evaluator and prep interview default to `deepseek/deepseek-v4-pro`). Completing a task list against a small project on a per-session git worktree.

## How Tilth differs

Many minimal coding agents are *interactive* — a developer watches the output and course-corrects, kills a bad run, or re-prompts. Tilth runs *autonomously* for the length of a run, with no one watching mid-task. That single difference is why it carries machinery a pair-programming agent can skip: an **evaluator** that judges whether a change is a *proper* solution (not just green tests), **between-task caps** that stand in for the budget ceiling a human would otherwise impose, a per-task **evaluator ledger**, **state kept out of the model's context**, and **offline-first observability** (detailed just below). None of this is a knock on interactive agents; it's a different shape for a different job.

### Hyper-observability

If no one is watching a run mid-flight, the recording *is* the supervision. Tilth's standing goal is **hyper-observability** — *every prompt the harness sends is accessible, and every run is fully inspectable after the fact.* Every assembled prompt, memory load, model call, validator run, and evaluator verdict lands in an append-only `events.jsonl`, and `tilth visualize` replays the whole thing end-to-end as a self-contained `chat.html` — no live TUI to babysit, no state hidden out of reach. Need the *exact bytes* a model received on a given turn? Set `TILTH_PROMPT_DUMP=1` and Tilth writes each call's full request (system + history + tool schemas) to `sessions/<id>/prompts/`, cross-referenced from the `model_call` events (off by default).

![A finished Tilth run rendered as chat-style HTML: session header, task divider, a model-call meta strip with an expanded reasoning block, and a bash tool call with its result](docs/assets/session-render.png)

*A finished run, rendered by `tilth visualize`.*

It's an early example of the goal, not a finished product — the [Hyper-observability deep dive](docs/deep-dives/hyper-observability.md) covers what the surface gives you now and what it doesn't yet.

For the full product story — the Brain/Hands/Session split in detail, the memory channels, the two loops, the worker↔evaluator dialogue, token recording and enforcement, the agent-visibility boundary, and the safety guards — see the **[docs site](https://alteredcraft.github.io/tilth/)**. This README is the elevator pitch.

## Quickstart

```bash
git clone git@github.com:AlteredCraft/tilth.git
cd tilth
uv sync
cp .env.example .env
# edit .env — TILTH_BASE_URL, TILTH_API_KEY, TILTH_WORKER_MODEL are all required
# (Tilth refuses to start without them so a misconfigured run can't silently
# fall back to a provider/model your account doesn't have)
```

Run the demo against a small todo-CLI workspace (a tiny Python project with an `AGENTS.md` and an empty `tests/`). Tilth's task list and matching tests come from an interview the harness runs against your code:

```bash
git clone git@github.com:AlteredCraft/tilth-demo-todo-cli.git tilth-demo
uv run tilth prep-feature ./tilth-demo   # interview, produce the seed
uv run tilth run          ./tilth-demo   # run the seeded session
```

For tested provider/model combinations, the full `TILTH_*` env-var table, the `resume` / `reset` / `visualize` subcommands, and the honest guide to using Tilth on your own non-demo project, see the **[docs](./docs/index.md)**.

## Working with the codebase

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
