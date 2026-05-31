# Installation

Tilth is a small Python package; setup is straight `uv` plumbing plus a `.env`.

## Prerequisites

- **Python 3.12** or newer.
- **`uv`** for env management ([installation guide](https://docs.astral.sh/uv/)).
- **`git`** — Tilth uses git worktrees as session sandboxes, so a working git is non-optional.
- **An OpenAI-compatible LLM endpoint and API key.** Tilth is actively tested against [OpenRouter](https://openrouter.ai); other OpenAI-flavour gateways should work via the OpenAI SDK but haven't been validated yet.

## Clone and install

Tilth and the project you point it at are **independent checkouts** — Tilth lives in its own directory, and the codebase it works on lives somewhere else. Clone Tilth wherever you keep code; the `tilth` command takes the target repo as a plain path argument (`uv run tilth <path>`), so any layout works.

```bash
git clone git@github.com:AlteredCraft/tilth.git
cd tilth
uv sync
cp .env.example .env
# edit .env, set TILTH_BASE_URL, TILTH_API_KEY, and TILTH_WORKER_MODEL
```

> **About the example paths in these docs.** Later pages show commands like `uv run tilth ~/projects/project-x` and reference paths such as `~/projects/tilth/sessions/<id>/`. That layout — Tilth and the target repo sitting side-by-side under `~/projects/` — is just one illustrative choice for the worked examples. Substitute whatever paths match your own setup.

All three of `TILTH_BASE_URL`, `TILTH_API_KEY`, and `TILTH_WORKER_MODEL` are **required** — Tilth refuses to start without them so a misconfigured run can't silently fall back to a provider/model your account doesn't have. The example `.env` points at OpenRouter.

## Required environment variables

| Variable | What it does |
|---|---|
| `TILTH_BASE_URL` | Provider's OpenAI-compatible endpoint (e.g. `https://openrouter.ai/api/v1`). |
| `TILTH_API_KEY` | Bearer token for that provider. |
| `TILTH_WORKER_MODEL` | The model that does the work. |

## Optional environment variables

| Variable | Default | What it does |
|---|---|---|
| `TILTH_JUDGE_MODEL` | same as worker | Model that reviews finished tasks. |
| `TILTH_JUDGE_BASE_URL` | inherits worker | Point the judge at a *different* provider for stronger independence. |
| `TILTH_JUDGE_API_KEY` | inherits worker | Bearer token for the judge provider. |
| `TILTH_PREP_MODEL` | same as worker | Model that runs the `tilth prep-feature` interview. Set to a frontier reasoning model independently of the worker. |
| `TILTH_PREP_BASE_URL` | inherits worker | Point the seeder at a *different* provider than the worker (e.g. worker on a budget provider, prep on a frontier one). |
| `TILTH_PREP_API_KEY` | inherits worker | Bearer token for the prep provider. |
| `TILTH_MAX_ITERATIONS_PER_TASK` | `8` | Tool-use iterations before a task is marked failed. |
| `TILTH_MAX_WALL_CLOCK_MINUTES` | `120` | Outer-loop wall-clock cap. |
| `TILTH_MAX_TOKENS` | `2000000` | Cumulative session token cap. |
| `TILTH_MAX_JUDGE_CALLS_PER_TASK` | `0` (off) | Optional cap on worker↔judge ping-pong. |

See [How the caps fit together](../deep-dives/caps.md) for the safety story behind the caps.

## Provider notes

Tilth talks to an OpenAI-compatible endpoint via the `openai` Python SDK. Today the only actively tested gateway is **[OpenRouter](https://openrouter.ai)** (`https://openrouter.ai/api/v1`); support for other OpenAI-flavour gateways is on the roadmap but unverified.

When the base URL points at OpenRouter, Tilth sends OpenRouter's normalised `reasoning: { enabled: true }` opt-in on every request so thinking-mode models populate `reasoning_details` reliably across parallel-tool-call turns. For non-OpenRouter base URLs the opt-in is omitted automatically — no configuration needed.

> **Tool-calling caveat.** Not every model on OpenRouter supports tool calling, and OpenRouter routes through many backends — some don't implement function calling. Pick a model whose card explicitly says `tools` is supported, or you'll get text responses where the loop expects tool calls. Test with the demo workspace first; it'll fail fast.

## Building these docs locally

The docs site itself is built with [MkDocs](https://www.mkdocs.org/). The `--extra docs` flag resolves the docs dependency group on the fly:

```bash
uv run --extra docs mkdocs serve
```

`mkdocs serve` opens a live-reload preview at `http://127.0.0.1:8000`.
