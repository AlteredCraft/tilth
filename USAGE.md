# Using Tilth on your own project

The honest version, not the marketing version.

This doc is for a reader who has cloned `tilth` and wants to run it against their own codebase. If you just want to try Tilth on a stand-in project first, clone the demo workspace from [`AlteredCraft/tilth-demo-todo-cli`](https://github.com/AlteredCraft/tilth-demo-todo-cli) and point Tilth at it (`uv run tilth /path/to/clone`).

---

## TL;DR

> This works well on a small Python project with 5–15 well-specified tasks and existing tests. Anything bigger or polyglot, you fork the harness.

---

## 1. Setup (one-time, ~5 minutes)

```bash
git clone git@github.com:AlteredCraft/tilth.git {{your projects folder}}/tilth
cd {{your projects folder}}/tilth
uv venv && uv pip install -e .
cp .env.example .env
# edit .env, paste your TILTH_API_KEY
```

The harness talks to **any OpenAI-compatible endpoint** via the `openai` Python SDK. The example `.env` points at Ollama Cloud; change `TILTH_BASE_URL` to use OpenRouter, Together, Groq, Anyscale, Fireworks, vLLM, LM Studio, or anything else with `/v1/chat/completions` semantics.

Required env vars (Tilth refuses to start without them):

- `TILTH_BASE_URL` — provider's OpenAI-compatible endpoint
- `TILTH_API_KEY` — bearer token for that provider
- `TILTH_WORKER_MODEL` — the model that does the work

Optional env vars:

- `TILTH_JUDGE_MODEL` — model that reviews finished tasks (default: same as worker)
- `TILTH_JUDGE_BASE_URL`, `TILTH_JUDGE_API_KEY` — point judge at a *different* provider for stronger independence (e.g. worker = open model on Ollama Cloud, judge = Claude on OpenRouter). See ["Picking a judge model"](#picking-a-judge-model) below.
- `TILTH_MAX_ITERATIONS_PER_TASK`, `TILTH_MAX_WALL_CLOCK_MINUTES`, `TILTH_MAX_TOKENS` — safety caps
- `TILTH_REASONING_ENABLED` (default `true`) — opts into [OpenRouter's normalised reasoning parameter](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens) so thinking-mode models emit reasoning content the harness echoes back across iterations (the worker's chain-of-thought is preserved across the tool-use cycle, not just within a single response). Required for routes that enforce round-tripping — without it, parallel-tool-call turns on e.g. SiliconFlow's DeepSeek can crash with HTTP 400 *"reasoning_content must be passed back to the API"*. Set `false` if your provider rejects unknown body fields.

### Provider strings

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

**Critical caveat:** not every model on every provider supports tool calling. OpenRouter in particular routes through many backends and some don't implement function calling. Pick a model whose card explicitly says `tools` is supported, or you'll get text responses where the loop expects tool calls. Test with the demo workspace first; it'll fail fast.

## 2. Prep your project (per-project, one-time)

Your project must be a **git repo with a clean `main` branch**. Add four files at the repo root:

### `prd.json` — the task list

This is the work. The harness does not plan; **you plan**.

```json
[
  {
    "id": "T-001",
    "title": "Short imperative title",
    "description": "What needs to be done. Be specific. Reference files if useful.",
    "acceptance_criteria": [
      "Concrete, checkable statement.",
      "Another concrete, checkable statement."
    ],
    "status": "pending"
  }
]
```

### `AGENTS.md` — your project's learned conventions

Short markdown. The self-improvement step appends learnings under named sections. Use these section headings exactly so updates land in the right place:

```markdown
# AGENTS.md

## Project
One paragraph describing what this codebase is.

## Language and tooling
Python version, frameworks, test runner, linter, etc.

## Layout
Where things live.

## Style
- Standard library first.
- Type hints on public functions.
- ...

## Patterns
_(empty — agent appends here)_

## Gotchas
_(empty — agent appends here)_

## Recent learnings
_(empty — agent appends here)_
```

If the headings don't exist or are named differently, learnings still land but in a new section appended to the end. The `_(empty — agent appends here)_` placeholder gets replaced by the first append.

### `progress.txt` — the journal

Start it empty. The harness appends one line per task outcome. The most recent ~30 lines are injected into each fresh task's prompt so the agent has rolling context.

### `tests/` — acceptance tests

At least one test per task in `prd.json`, asserting the acceptance criteria.

**Name files `test_<task-id-lower>_*.py`** — e.g., `test_t001_hello.py` for task `T-001`, `test_t002_package.py` for `T-002`. The harness filters pytest to only the files matching the *active* task, so failing tests for *future* tasks don't get fed back as failures of the current task. (Without this, the worker reads the failing future tests, builds them, and silently overflows scope.) A task with no matching test files skips pytest entirely and relies on the judge.

**Without tests the only objective validator is `ruff`**, which collapses verification down to "code looks clean and the judge model says it's fine." The judge is good but not bulletproof. Write tests upfront — that is the test ratchet.

### Commit all four to `main`

```bash
git add prd.json AGENTS.md progress.txt tests/
git commit -m "seed: prep for tilth"
```

That's the seed state the harness branches from.

## 3. Run it

```bash
cd {{your projects folder}}/tilth
uv run tilth /absolute/path/to/your/repo
```

What happens:

1. Harness verifies your repo is a git repo on a clean main.
2. Creates a worktree at `sessions/<id>/workspace/` on a new branch `session/<id>` in **your repo's `.git`**.
3. Loops through pending tasks in `prd.json`. For each task:
   - Reset context. Prompt = system + AGENTS.md + recent progress + this task.
   - Tool-loop with the worker model (bash, file ops, search) until it stops calling tools.
   - Run `ruff` + `pytest` in the worktree. Failures get fed back into the loop.
   - Judge model reviews the diff in a fresh context. Rejections get fed back.
   - Self-improvement prompt — the worker decides whether anything should land in `AGENTS.md`.
   - Commit on the worktree branch. Append to `progress.txt`. Mark task done in `prd.json`.
4. Stops on: all tasks done, iteration cap, wall-clock cap, token cap, or error.

You can interrupt at any point with Ctrl-C. To resume:

```bash
uv run tilth --resume               # default: most recent session in sessions/
uv run tilth --resume <session_id>  # or name one explicitly
```

What resume does:

- Skips tasks already marked `done` in `prd.json` (which lives on the worktree branch).
- **Retries the trailing failed task**, if any. Iter-cap, wall-clock-cap, token-cap, interrupt, and error stops all leave the in-flight task marked `failed`; resume flips that task back to `pending` and unwinds its `FAILED (...)` placeholder commit so the retry sees the partial work as uncommitted changes (and the judge will see a single cumulative diff, not just the new edits).
- **Resets the wall-clock budget** for this resume — otherwise a resume the next day would trip `TILTH_MAX_WALL_CLOCK_MINUTES` immediately.
- **Preserves the token total.** If the original run hit `TILTH_MAX_TOKENS`, bump it in `.env` before resuming or the new run will stop on the first token check.

The resume plan is printed up front (which task is being retried, which are pending) and logged as a `session_resume` event in `events.jsonl`.

### Resetting a session

If you want to throw a session away and start fresh — common when you're tuning prompts or iterating on the harness itself:

```bash
uv run tilth --reset               # most recent session
uv run tilth --reset <session_id>  # or name one explicitly
uv run tilth --reset --yes         # skip the y/N confirmation
```

This is the codified version of the three-step manual cleanup (`rm -rf sessions/<id>` + `git worktree prune` + `git branch -D session/<id>`). It:

1. Reads `sessions/<id>/checkpoint.json` to recover the worktree path and branch name; reads the `session_start` event for the source repo path.
2. Runs `git worktree remove --force <path>` in the source repo (force-removes the worktree even if dirty — `--reset`'s whole purpose is to discard a session's work, and the `[y/N]` prompt is the safety gate).
3. Runs `git branch -D session/<id>` in the source repo (force-delete is the right default for the `session/*` namespace, which is never auto-merged).
4. Removes `sessions/<id>/`.

Each step is idempotent — already-missing pieces are reported as skipped, not errored. `--reset` and `--resume` are mutually exclusive on one invocation.

### Resumable-session warning

If you run `uv run tilth <workspace>` (no flags) and there's already a resumable session for that same workspace under `sessions/`, the harness prints a heads-up and pauses 5 seconds before starting a new session:

```
heads up: sessions/20260430-121316-51ead4/ ended in iter_cap and is resumable
  → uv run tilth --resume       (continue that work)
  → uv run tilth --reset --yes  (discard it first)
starting fresh anyway in 5s... (Ctrl-C to abort)
```

"Resumable" means: same source path AND last `stop.reason` is anything other than `all_done` (or no stop event was logged). The detection is read-only — it doesn't touch the prior session. Hit Ctrl-C during the pause if the warning surprised you and you want to switch to `--resume` or `--reset` instead.

## 4. Review

When the run ends (or you stop it):

```bash
cd /path/to/your/repo
git log session/<id> --oneline
git diff main..session/<id>
```

Each task is one commit. If you like the work:

```bash
git checkout main
git merge session/<id>
# or open a PR if you push
```

If you don't like it: delete the branch. The harness never auto-merges.

The session log lives at `{{your projects folder}}/tilth/sessions/<id>/events.jsonl` — every model call, tool call, validator run, judge verdict, and AGENTS.md update is recorded. Useful for audit, blame, and future article writing.

For a more readable view, render the log as a chat-style HTML page:

```bash
uv run tilth --visualize               # most recent session
uv run tilth --visualize <session_id>  # or name one explicitly
```

Writes a single self-contained file (inline CSS, no JS) to `sessions/<id>/chat.html`. Events are grouped by task — model calls become meta strips with a collapsible reasoning block where the model emitted any, tool calls/results become bubbles, validator runs and judge verdicts become coloured cards. Read-only and runs over the saved `events.jsonl`, so it's safe to invoke against a finished or in-progress session.

## 5. Caveats worth being upfront about

- **It's Python-centric.** `post_edit` lints `.py` files. `validators` runs `pytest` and `ruff`. JavaScript / Rust / Go projects need `tilth/validators.py` and `tilth/hooks/post_edit.py` adapted to your toolchain — not deep work, but not zero.
- **Ruff config matters.** If your project doesn't already use ruff, the validator will fire constantly and the agent will spend iterations fixing things that aren't really broken. Either add a permissive `[tool.ruff]` block to your `pyproject.toml`, or swap the ruff validator for whatever linter you already use.
- **The planner is you.** Writing a good `prd.json` (small enough tasks, sharp acceptance criteria, tests upfront) is where most of the value is. Vague PRDs make the harness fail loudly and burn tokens.
- **Costs are real.** A 2-hour run can mean hundreds of thousands of tokens across worker + judge + self-improvement calls. The `TILTH_MAX_TOKENS` cap exists for a reason — set it on first run. Cost per token varies wildly across providers; pick your worker accordingly. Be careful about reaching for a smaller judge model to cut costs — see ["Picking a judge model"](#picking-a-judge-model) below.
- **AGENTS.md is yours forever.** It accumulates. Prune it periodically — old learnings that the model has clearly internalised should be removed (the ratchet works in both directions).
- **Tools are intentionally narrow.** No web fetch, no MCP, no curl-based downloads. If your tasks require external API access, you add a tool to `tilth/tools/` and register it. Keep tools focused — every tool description ships in the prompt every turn.
- **The harness commits to your repo's git db.** The worktree branch is in your repo, not the harness's. If you delete `{{your projects folder}}/tilth`, the branches in your project's repo remain. Clean up branches the same way you would for a normal feature branch.

## 6. Picking a judge model

The judge call is the single most consequential model decision in the harness. It's the only thing standing between "validators passed" and "this gets committed to a branch you'll merge."

### Default: judge ≥ worker

For correctness gating on code diffs, the judge should be **at least as capable as the worker, often more capable**. A weaker judge fails in the worst possible way: it accepts bad work because it didn't notice the problem.

This is the opposite of the intuition many people start with ("the worker did the hard work, the judge just rubber-stamps"). The judge has *less* context — no chain-of-thought, no tool history, just diff and criteria — so it needs more capability to compensate, not less.

Academic LLM-as-a-judge research bears this out: evaluators are typically run with GPT-4-class models judging GPT-3.5-class outputs, not the other way around. The point of separation is **independence**, not capability reduction.

### When dual-provider routing actually pays off

The `TILTH_JUDGE_BASE_URL` / `TILTH_JUDGE_API_KEY` feature is genuinely useful, but mostly for **cross-family independence**, not cost:

- **Worker = open model on Ollama Cloud, judge = Claude on OpenRouter.** Different model families catch different failure modes. Same-family judging shares the worker's blind spots.
- **Worker = capable open model, judge = frontier closed model.** When you need the strongest possible gate, route the judge to whatever's at the top of the leaderboard for code review.

Both of these are *upgrading* the judge, not downgrading it.

### When a smaller / cheaper judge is OK

There's a narrow band where a cheap judge works:

- **Shallow checks.** Binary outcomes ("did this string change?", "is this JSON?"), regex matches, simple format validation.
- **Policy gates.** "Did the response avoid the banned topics?", "Is this on-brand?" — small finetuned classifiers can do this for a fraction of the cost.
- **Worker is already top-tier and tasks are tightly bounded.** If the worker is Sonnet 4.5 doing well-specified PRD tasks, a Haiku-class judge catches the obvious failures cheaply.

For a Ralph loop doing real code review, none of these usually apply. Default to a judge that's at least as good as the worker. Only swap to a smaller judge after you've measured judge accept-rate on known-bad tasks and confirmed it's still catching them.

## 7. When this is the wrong tool

- **Closed-source-only tasks.** If you can't share code with whichever provider you're pointed at, don't use this. Point at a self-hosted vLLM or LM Studio if you need to keep code on-prem.

- **Models without tool-calling support.** Some OpenRouter routes, some smaller open models, and most "completion-only" endpoints will fail or hallucinate tool calls. Verify on the demo workspace first.
- **Polyglot codebases.** Adapt the validators, or accept that only the judge model is gating quality.
- **One-shot prompts.** If your work fits in one Claude Code or Cursor session, just use that.
- **Hours-long, mission-critical, or production-touching runs.** Use a managed runtime (Google Agent Platform, Claude Managed Agents) instead. This harness is for *learning the pattern* on small bounded work.

## 8. What to do on first run

1. Start with **two or three small, well-specified tasks** in `prd.json`. Get a clean run before adding more.
2. Watch the console — it streams every tool call. If the agent thrashes on one task, kill the run and rewrite the task description before retrying.
3. Inspect `sessions/<id>/events.jsonl` after the run. Look for unexpected patterns: tasks that took many iterations, judge rejections, validator failure loops. Each is a signal.
4. Read your `AGENTS.md` after the run. The first few learnings are usually noise — the model is calibrating to your project. Prune them.
5. Iterate the harness, not just the prompts. If a class of failure keeps recurring, add a hook (the ratchet pattern). Constraints are earned by failures.
