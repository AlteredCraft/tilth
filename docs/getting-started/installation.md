# Installation

Tilth is a small Python package; setup is straight `uv` plumbing plus a `.env`.

## Prerequisites

- **Python 3.12** or newer.
- **`uv`** for env management ([installation guide](https://docs.astral.sh/uv/)).
- **`git`** — Tilth uses git worktrees as session sandboxes, so a working git is non-optional.
- **An OpenAI-compatible LLM endpoint and API key.** Tilth is actively tested against [OpenRouter](https://openrouter.ai); other OpenAI-flavour gateways should work via the OpenAI SDK but haven't been validated yet.

## Install

Tilth publishes to PyPI, so the CLI installs like any other Python tool. Pick whichever runner you already use:

```bash
# uv (recommended — Tilth is uv-native)
uv tool install tilth          # `tilth` on your PATH, runnable from any directory
uvx tilth --help               # …or run it ephemerally, npx-style, with no install

# pipx
pipx install tilth             # persistent
pipx run tilth --help          # ephemeral
```

Tilth and the project you point it at are **independent checkouts** — Tilth lives wherever you install it, and the codebase it works on lives somewhere else. The command takes a feature directory as a plain path argument (`tilth run <repo>/.tilth/<feature>`) and derives the enclosing repo from it, so any layout works.

```bash
tilth init                     # scaffolds ~/.tilth/.env from the template
# edit ~/.tilth/.env, set TILTH_BASE_URL, TILTH_API_KEY, and TILTH_WORKER_MODEL
```

`tilth init` creates the per-user home (`~/.tilth/`) with a `sessions/` directory and a `.env` you fill in. It never overwrites an existing `.env`.

### Install from source (maintainers)

Working *on* the harness, or want to run an unreleased revision? Install it editable from a clone — `tilth` goes on your PATH but resolves to your working copy, so edits take effect without reinstalling:

```bash
git clone git@github.com:AlteredCraft/tilth.git
cd tilth
uv tool install --editable .   # `tilth` on your PATH; --editable tracks the clone
```

> **Contributor path.** If you're iterating on the code rather than using the installed tool, skip the tool install entirely: `uv sync` for the dev env, then run the CLI from the clone with `uv run tilth …`. Either way, state lands under `~/.tilth/` unless you override it (below). Cutting and shipping a release is documented in [Releasing to PyPI](../reference/releasing.md).

> **About the example paths in these docs.** Later pages show commands like `tilth run ~/projects/project-x/.tilth/<feature>` and reference paths such as `~/.tilth/sessions/<id>/`. The feature-directory path (and the feature name) is just one illustrative choice — substitute whatever matches your own setup.

All three of `TILTH_BASE_URL`, `TILTH_API_KEY`, and `TILTH_WORKER_MODEL` are **required** — Tilth refuses to start without them so a misconfigured run can't silently fall back to a provider/model your account doesn't have. The example `.env` points at OpenRouter.

## Where Tilth stores things

Everything Tilth writes for a user lives under one home directory, resolved at startup. Each location has an environment override:

| Path | Default | Override |
|---|---|---|
| Home directory | `~/.tilth/` | `$TILTH_HOME` |
| Sessions (one dir per run, including its worktree) | `<home>/sessions/` | `$TILTH_SESSIONS_DIR` |
| Provider config | `<home>/.env` | `$TILTH_ENV_FILE` |

The `.env` is discovered in order — `$TILTH_ENV_FILE`, then `~/.tilth/.env`, then a `.env` in the current directory (a convenience for running from a clone before `~/.tilth` is set up). The first one found wins; Tilth doesn't merge an unrelated project's `.env` just because you're standing in it. `$TILTH_HOME` and `$TILTH_SESSIONS_DIR` must be real shell variables to relocate the tree, since they decide where the `.env` itself is read from.

## Required environment variables

| Variable | What it does |
|---|---|
| `TILTH_BASE_URL` | Provider's OpenAI-compatible endpoint (e.g. `https://openrouter.ai/api/v1`). |
| `TILTH_API_KEY` | Bearer token for that provider. |
| `TILTH_WORKER_MODEL` | The model that does the work. |

## Optional environment variables

| Variable | Default | What it does |
|---|---|---|
| `TILTH_EVALUATOR_MODEL` | same as worker | Model that reviews finished tasks. |
| `TILTH_EVALUATOR_BASE_URL` | inherits worker | Point the evaluator at a *different* provider for stronger independence. |
| `TILTH_EVALUATOR_API_KEY` | inherits worker | Bearer token for the evaluator provider. |
| `TILTH_CONTEXT_FILES` | `AGENTS.md,CLAUDE.md` | Comma-separated project-context files read from the workspace root (in order, concatenated) into the worker and evaluator prompts. |
| `TILTH_MAX_ITERATIONS_PER_TASK` | `32` | Tool-use iterations before a task is marked failed. |
| `TILTH_MAX_WALL_CLOCK_MINUTES` | `120` | Outer-loop wall-clock cap. |
| `TILTH_MAX_TOKEN_DOLLAR_SPEND` | `10.00` | Cumulative session USD-spend cap, read from the provider's per-call `cost` (OpenRouter reports it; gateways that don't leave it uncapped — wall-clock is the backstop). |
| `MAX_EVALUATOR_CALLS_PER_TASK` | `0` (off) | Optional cap on worker↔evaluator ping-pong. |

See [What can stop a run](../deep-dives/two-loops.md#what-can-stop-a-run) for the safety story behind the caps.

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
