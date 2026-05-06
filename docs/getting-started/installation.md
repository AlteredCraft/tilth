# Installation

Tilth is a small Python package; setup is straight `uv` plumbing plus a `.env`.

## Prerequisites

- **Python 3.12** or newer.
- **`uv`** for env management ([installation guide](https://docs.astral.sh/uv/)).
- **`git`** — Tilth uses git worktrees as session sandboxes, so a working git is non-optional.
- **An OpenAI-compatible LLM endpoint and API key.** Tested combinations live in [Provider strings](#provider-strings).

## Clone and install

```bash
git clone git@github.com:AlteredCraft/tilth.git {{your projects folder}}/tilth
cd {{your projects folder}}/tilth
uv venv
uv sync
cp .env.example .env
# edit .env, set TILTH_BASE_URL, TILTH_API_KEY, and TILTH_WORKER_MODEL
```

All three of `TILTH_BASE_URL`, `TILTH_API_KEY`, and `TILTH_WORKER_MODEL` are **required** — Tilth refuses to start without them so a misconfigured run can't silently fall back to a provider/model your account doesn't have. The example `.env` points at Ollama Cloud.

## Required environment variables

| Variable | What it does |
|---|---|
| `TILTH_BASE_URL` | Provider's OpenAI-compatible endpoint (e.g. `https://ollama.com/v1`). |
| `TILTH_API_KEY` | Bearer token for that provider. |
| `TILTH_WORKER_MODEL` | The model that does the work. |

## Optional environment variables

| Variable | Default | What it does |
|---|---|---|
| `TILTH_JUDGE_MODEL` | same as worker | Model that reviews finished tasks. |
| `TILTH_JUDGE_BASE_URL` | inherits worker | Point the judge at a *different* provider for stronger independence. |
| `TILTH_JUDGE_API_KEY` | inherits worker | Bearer token for the judge provider. |
| `TILTH_MAX_ITERATIONS_PER_TASK` | `8` | Tool-use iterations before a task is marked failed. |
| `TILTH_MAX_WALL_CLOCK_MINUTES` | `120` | Outer-loop wall-clock cap. |
| `TILTH_MAX_TOKENS` | `2000000` | Cumulative session token cap. |
| `TILTH_MAX_JUDGE_CALLS_PER_TASK` | `0` (off) | Optional cap on worker↔judge ping-pong. |
| `TILTH_REASONING_ENABLED` | `true` | Opts into OpenRouter's normalised reasoning parameter so thinking-mode models emit reasoning content the harness echoes back across iterations. Set `false` if your provider rejects unknown body fields. |

See [How the caps fit together](../deep-dives/caps.md) for the safety story behind the caps.

## Provider strings

The harness talks to **any OpenAI-compatible endpoint** via the `openai` Python SDK. Change `TILTH_BASE_URL` to use OpenRouter, Together, Groq, Anyscale, Fireworks, vLLM, LM Studio, or anything else with `/v1/chat/completions` semantics.

Tested / expected-to-work combinations:

| Provider | `TILTH_BASE_URL` | Example `TILTH_WORKER_MODEL` |
|---|---|---|
| Ollama Cloud | `https://ollama.com/v1` | `deepseek/deepseek-v4-pro` |
| OpenRouter | `https://openrouter.ai/api/v1` | `anthropic/claude-sonnet-4.5`, `openai/gpt-4o`, `meta-llama/llama-3.1-405b-instruct` |
| Together AI | `https://api.together.xyz/v1` | `meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo` |
| Groq | `https://api.groq.com/openai/v1` | `llama-3.1-70b-versatile` |
| Anyscale | `https://api.endpoints.anyscale.com/v1` | `meta-llama/Meta-Llama-3-70B-Instruct` |
| Fireworks | `https://api.fireworks.ai/inference/v1` | `accounts/fireworks/models/llama-v3p1-70b-instruct` |
| vLLM (self-hosted) | `http://localhost:8000/v1` | whatever you served |
| LM Studio (self-hosted) | `http://localhost:1234/v1` | whatever you loaded |

> **Critical caveat.** Not every model on every provider supports tool calling. OpenRouter in particular routes through many backends and some don't implement function calling. Pick a model whose card explicitly says `tools` is supported, or you'll get text responses where the loop expects tool calls. Test with the demo workspace first; it'll fail fast.

## Building these docs locally

The docs site itself is built with [MkDocs](https://www.mkdocs.org/). Once the docs optional dependency group is installed:

```bash
uv pip install -e ".[docs]"
mkdocs serve
```

`mkdocs serve` opens a live-reload preview at `http://127.0.0.1:8000`.
