# Tilth

> *Prepare the ground, let the agent grow the work.*

A minimal long-running agent harness against any **OpenAI-compatible** LLM endpoint — Ollama Cloud, OpenRouter, Together, Groq, Anyscale, Fireworks, vLLM, LM Studio, you name it. Built to learn (and demonstrate) the Brain/Hands/Session split, the Ralph loop, and the four memory channels described in Addy Osmani's [long-running agents](https://addyosmani.com/blog/long-running-agents/), [agent harness engineering](https://addyosmani.com/blog/agent-harness-engineering/), and [self-improving agents](https://addyosmani.com/blog/self-improving-agents/) posts.

![Brain / Hands / Session split — three boxes connected by flow arrows, with the files that implement each piece](docs/assets/brain-hands-session.png)

**Audience:** single-dev / few-dev teams who want to *understand* what a long-running agent harness actually does — without consuming a managed pattern.

**Target run:** 1–2 hours autonomous against an open model (default `deepseek/deepseek-v4-pro` on Ollama Cloud), completing a task list against a small toy project on a per-session git worktree.

For the full product story — the Brain/Hands/Session split in detail, the four memory channels, the two loops, token recording and enforcement, the agent-visibility boundary, and the safety guards — see the **[docs site](./docs/index.md)**. This README is the elevator pitch.

## Quickstart

```bash
git clone git@github.com:AlteredCraft/tilth.git
cd tilth
uv venv && uv sync
cp .env.example .env
# edit .env — TILTH_BASE_URL, TILTH_API_KEY, TILTH_WORKER_MODEL are all required
# (Tilth refuses to start without them so a misconfigured run can't silently
# fall back to a provider/model your account doesn't have)
```

Run the demo against a small todo-CLI workspace, pre-seeded with the four files Tilth expects (`prd.json`, `AGENTS.md`, `progress.txt`, `tests/`):

```bash
git clone git@github.com:AlteredCraft/tilth-demo-todo-cli.git tilth-demo
uv run tilth ./tilth-demo
```

For tested provider/model combinations, the full `TILTH_*` env-var table, `--resume` / `--reset` / `--visualize` semantics, and the honest guide to using Tilth on your own non-demo project, see the **[docs](./docs/index.md)**.

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
