# Using Tilth on your own project

The honest version, not the marketing version.

This page is for a reader who has finished the [demo walkthrough](running-the-demo.md) and now wants to point Tilth at their own codebase. [Installation](installation.md) and [Running the demo](running-the-demo.md) cover the harness mechanics — this page covers what's specific to applying it to your *own* repo: prepping the seed files, picking a judge, and the caveats that aren't obvious from a demo run.

> **TL;DR.** This works well on a small Python project with 5–15 well-specified tasks and existing tests. Anything bigger or polyglot, you fork the harness.

## 1. Prep your repo

Your project must be a **git repo with a clean `main` branch**. Add four files at the repo root.

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

The agent never sees `prd.json` directly — it receives one task at a time as the user message. See [Memory channels → `prd.json`](../architecture/memory-channels.md#prdjson-the-task-list) for the rationale.

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

`AGENTS.md` should stay project-focused — see [Memory channels → `AGENTS.md`](../architecture/memory-channels.md#agentsmd-the-agents-own-learned-conventions) for what does and doesn't belong in it.

### `progress.txt` — the journal

Start it empty. The harness appends one line per task outcome. The most recent ~30 lines are injected into each fresh task's prompt so the agent has rolling context.

### `tests/` — acceptance tests

At least one test per task in `prd.json`, asserting the acceptance criteria.

**Name files `test_<task-id-lower>_*.py`** — e.g., `test_t001_hello.py` for task `T-001`, `test_t002_package.py` for `T-002`. The harness filters pytest to only the files matching the *active* task plus all previously-completed tasks, so failing tests for *future* tasks don't get fed back as failures of the current task. (Without this, the worker reads the failing future tests, builds them, and silently overflows scope.) A task with no matching test files skips pytest entirely and relies on the judge.

**Without tests the only objective validator is `ruff`**, which collapses verification down to "code looks clean and the judge model says it's fine." The judge is good but not bulletproof. Write tests upfront — that is the test ratchet.

### Commit all four to `main`

```bash
git add prd.json AGENTS.md progress.txt tests/
git commit -m "seed: prep for tilth"
```

That's the seed state the harness branches from.

## 2. Run it

```bash
cd <your-tilth-clone>
uv run tilth /absolute/path/to/your/repo
```

The per-task lifecycle is identical to the demo — see [Running the demo → end-to-end flow](running-the-demo.md#run-a-session-against-the-demo) for the breakdown of what happens on each task. Follow-on operations:

- [Resuming a session](resuming.md) — `--resume` semantics, what survives across runs.
- [Resetting a session](resetting.md) — `--reset` tears down a session's worktree, branch, and `sessions/<id>/`.
- [Visualizing a session](visualizing.md) — `--visualize` renders `events.jsonl` as a chat-style HTML page.

## 3. Review

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

The session log lives at `{{tilth-clone-path}}/sessions/<id>/events.jsonl` — every model call, tool call, validator run, judge verdict, and AGENTS.md update is recorded. Alongside it, `sessions/<id>/summary.json` carries a rolled-up snapshot (token totals, per-task iteration counts, tool histogram, hook outcomes, judge accept/reject) refreshed at every task boundary — read that when you want a quick stat without `jq`-ing the full log. The schema is documented in `tilth/summary.py`'s module docstring.

## 4. Caveats worth being upfront about

- **It's Python-centric.** `post_edit` lints `.py` files. `validators` runs `pytest` and `ruff`. JavaScript / Rust / Go projects need `tilth/validators.py` and `tilth/hooks/post_edit.py` adapted to your toolchain — not deep work, but not zero.
- **Ruff config matters.** If your project doesn't already use ruff, the validator will fire constantly and the agent will spend iterations fixing things that aren't really broken. Either add a permissive `[tool.ruff]` block to your `pyproject.toml`, or swap the ruff validator for whatever linter you already use.
- **The planner is you.** Writing a good `prd.json` (small enough tasks, sharp acceptance criteria, tests upfront) is where most of the value is. Vague PRDs make the harness fail loudly and burn tokens.
- **Costs are real.** A 2-hour run can mean hundreds of thousands of tokens across worker + judge + self-improvement calls. The `TILTH_MAX_TOKENS` cap exists for a reason — set it on first run. Cost per token varies wildly across providers; pick your worker accordingly. Be careful about reaching for a smaller judge model to cut costs — see [Picking a judge model](#5-picking-a-judge-model) below.
- **AGENTS.md is yours forever.** It accumulates. Prune it periodically — old learnings that the model has clearly internalised should be removed (the ratchet works in both directions).
- **Tools are intentionally narrow.** No web fetch, no MCP, no curl-based downloads. If your tasks require external API access, you add a tool to `tilth/tools/` and register it. Keep tools focused — every tool description ships in the prompt every turn.
- **The harness commits to your repo's git db.** Tilth keeps the working tree under `sessions/<id>/workspace/` on its own side, but the branch `session/<id>` lives in *your* repo's `.git`. So if you delete your Tilth clone without resetting first, those branches remain in your project. Clean up branches the same way you would for a normal feature branch — or run `--reset` before you blow Tilth away. See [Session layout](../deep-dives/session-layout.md) for the full split.

## 5. Picking a judge model

The judge call is the single most consequential model decision in the harness. It's the only thing standing between "validators passed" and "this gets committed to a branch you'll merge."

### Default: judge ≥ worker

For correctness gating on code diffs, the judge should be **at least as capable as the worker, often more capable**. A weaker judge fails in the worst possible way: it accepts bad work because it didn't notice the problem.

This is the opposite of the intuition many people start with ("the worker did the hard work, the judge just rubber-stamps"). The judge has *less* context — no chain-of-thought, no tool history, just diff and criteria — so it needs more capability to compensate, not less.

Academic LLM-as-a-judge research bears this out: evaluators are typically run with GPT-4-class models judging GPT-3.5-class outputs, not the other way around. The point of separation is **independence**, not capability reduction.

### When dual-provider routing actually pays off

The `TILTH_JUDGE_BASE_URL` / `TILTH_JUDGE_API_KEY` feature is genuinely useful, but mostly for **cross-family independence**, not cost:

- **Worker = open model, judge = Claude (both on OpenRouter).** Different model families catch different failure modes. Same-family judging shares the worker's blind spots.
- **Worker = capable open model, judge = frontier closed model.** When you need the strongest possible gate, route the judge to whatever's at the top of the leaderboard for code review.

Both of these are *upgrading* the judge, not downgrading it.

### When a smaller / cheaper judge is OK

There's a narrow band where a cheap judge works:

- **Shallow checks.** Binary outcomes ("did this string change?", "is this JSON?"), regex matches, simple format validation.
- **Policy gates.** "Did the response avoid the banned topics?", "Is this on-brand?" — small finetuned classifiers can do this for a fraction of the cost.
- **Worker is already top-tier and tasks are tightly bounded.** If the worker is Sonnet 4.5 doing well-specified PRD tasks, a Haiku-class judge catches the obvious failures cheaply.

For a Ralph loop doing real code review, none of these usually apply. Default to a judge that's at least as good as the worker. Only swap to a smaller judge after you've measured judge accept-rate on known-bad tasks and confirmed it's still catching them.

## 6. When this is the wrong tool

- **Closed-source-only tasks.** If you can't share code with OpenRouter, this isn't the right tool today. A self-hosted OpenAI-compatible endpoint (vLLM, LM Studio) might work via the OpenAI SDK but hasn't been validated.
- **Models without tool-calling support.** Some OpenRouter routes, some smaller open models, and most "completion-only" endpoints will fail or hallucinate tool calls. Verify on the demo workspace first.
- **Polyglot codebases.** Adapt the validators, or accept that only the judge model is gating quality.
- **One-shot prompts.** If your work fits in one Claude Code or Cursor session, just use that.
- **Hours-long, mission-critical, or production-touching runs.** Use a managed runtime (Google Agent Platform, Claude Managed Agents) instead. This harness is for *learning the pattern* on small bounded work.

## 7. What to do on first run

1. Start with **two or three small, well-specified tasks** in `prd.json`. Get a clean run before adding more.
2. Watch the console — it streams every tool call. If the agent thrashes on one task, kill the run and rewrite the task description before retrying.
3. Inspect `sessions/<id>/events.jsonl` after the run. Look for unexpected patterns: tasks that took many iterations, judge rejections, validator failure loops. Each is a signal.
4. Read your `AGENTS.md` after the run. The first few learnings are usually noise — the model is calibrating to your project. Prune them.
5. Iterate the harness, not just the prompts. If a class of failure keeps recurring, add a hook (the ratchet pattern). Constraints are earned by failures.
