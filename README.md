# Tilth

> *Prepare the ground, let the agent grow the work.*

A minimal long-running agent harness against any **OpenAI-compatible** LLM endpoint — Ollama Cloud, OpenRouter, Together, Groq, Anyscale, Fireworks, vLLM, LM Studio, you name it. Built to learn (and demonstrate) the Brain/Hands/Session split, the Ralph loop, and the four memory channels described in Addy Osmani's [long-running agents](https://addyosmani.com/blog/long-running-agents/), [agent harness engineering](https://addyosmani.com/blog/agent-harness-engineering/), and [self-improving agents](https://addyosmani.com/blog/self-improving-agents/) posts.

**Audience:** single-dev / few-dev teams who want to *understand* what a long-running agent harness actually does — without consuming a managed pattern.

**Target run:** 1–2 hours autonomous against an open model (default `gpt-oss:120b-cloud` on Ollama Cloud), completing a task list against a small toy project on a per-session git worktree.

## Architecture

Three independently-replaceable components:

- **Brain** — `tilth/client.py` + `tilth/loop.py`. Ralph loop calling any OpenAI-compatible endpoint via the `openai` Python SDK. Worker and judge can sit on different providers.
- **Hands** — `tilth/workspace.py` (per-session git worktree) + `tilth/tools/` (allow-listed bash, file ops, search) + `tilth/hooks/` (pre-tool veto, post-edit lint).
- **Session** — `tilth/session.py`. Append-only `events.jsonl` + checkpoint, enough to `wake(session_id)` on a fresh process.

Four memory channels live outside the agent:

- `AGENTS.md` — the agent's own learned conventions and gotchas (in the *workspace*).
- Git history — atomic commits per task (in the *worktree*).
- `progress.txt` — chronological journal of task attempts (in the *workspace*).
- `prd.json` — task list with status flags (in the *workspace*).

Generator/evaluator separation: a separate **judge** call (`tilth/prompts/judge.md`) reviews each finished task in a fresh context — diff + acceptance criteria, nothing else.

## Setup

```bash
cd ~/Projects/tilth
uv venv
uv sync
cp .env.example .env
# edit .env, set TILTH_API_KEY (and optionally TILTH_BASE_URL / TILTH_WORKER_MODEL)
```

Defaults point at Ollama Cloud (`https://ollama.com/v1`, model `gpt-oss:120b-cloud`). To use a different provider, change `TILTH_BASE_URL`, `TILTH_API_KEY`, and `TILTH_WORKER_MODEL`. See [USAGE.md](./USAGE.md#provider-strings) for known-good provider/model combinations.

## Running the demo

```bash
uv run tilth examples/todo-cli
```

Resume an interrupted run:

```bash
uv run tilth --resume               # picks the most recent session
uv run tilth --resume <session_id>  # or name one explicitly
```

Resume retries the trailing failed task (if any) by flipping it back to `pending` and unwinding the `FAILED (...)` placeholder commit so partial work blends into the retry. The wall-clock budget resets per resume; the token total is preserved (bump `TILTH_MAX_TOKENS` first if you blew the cap).

Reset a session (drop its worktree, delete its `session/<id>` branch from the source repo, remove `sessions/<id>/`):

```bash
uv run tilth --reset                  # most recent session
uv run tilth --reset <session_id>     # or name one explicitly
uv run tilth --reset --yes            # skip the y/N confirmation
```

Refuses if the worktree has uncommitted changes — investigate, commit/stash, then retry. Reset and resume are mutually exclusive on a single invocation.

If you run `uv run tilth <workspace>` (no flags) and a resumable session exists for that same workspace, the harness prints a heads-up listing your `--resume` / `--reset` options and pauses 5 seconds before starting fresh — Ctrl-C during the pause to switch course.

## Using it on your own project

See **[USAGE.md](./USAGE.md)** for the full logistics: how to prep your repo (`prd.json`, `AGENTS.md`, `progress.txt`, `tests/`), what happens during a run, how to review and merge, provider/model selection, and the caveats worth knowing up front.

## Going deeper

See **[deep-dives.md](./deep-dives.md)** for code-level walk-throughs of the mechanics — the two loops (Ralph vs. tool-use), what counts as an iteration, judge-rejection accounting, and end-to-end token recording and enforcement. Useful if you're extending or debugging the harness rather than just running it.

## Safety guards

- Iteration cap per task (default 8)
- Wall-clock cap per run (default 120 min)
- Token cap (configurable)
- `pre_tool` hook blocks `rm -rf`, `git push --force`, `sudo`, etc.
- Worktree branch is **never auto-merged** — open a PR and review like any other branch.

## Status

Early MVP. See [the Notes folder in `_PRIMARY_VAULT`](../../_PRIMARY_VAULT/AlteredCraft/Altered%20Craft%20Publications/Notes/Long%20running%20agents/) for the article and design rationale.
