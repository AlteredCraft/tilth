# Using Tilth on your own project

> **Early-stage research project.** Tilth is a research harness, not a hardened product. Running it should be *safe*: every change lands on an isolated `session/<id>` worktree branch, never on your `main`, and the harness never auto-merges — you review the diff like any other branch. What isn't guaranteed yet is *quality*. In these early stages the branch Tilth hands back may be rough or incomplete, and the tokens spent getting there are real. Treat runs as spend-at-your-own-risk and keep the first ones small.

This page is for a reader who has finished the [demo walkthrough](running-the-demo.md) and now wants to point Tilth at their own codebase. [Installation](installation.md) and [Running the demo](running-the-demo.md) cover the harness mechanics — this page covers what's specific to applying it to your *own* repo: authoring the feature, picking an evaluator, and the caveats that aren't obvious from a demo run.

## 1. Prep your repo

Your project must be a **git repo with at least one commit**. That's it for hard prerequisites. One thing is worth having but optional:

- **`AGENTS.md` (or `CLAUDE.md`) at the repo root.** User-owned, user-maintained — Tilth reads it as project context for the worker and the evaluator but never writes to it. Even a short one helps the worker understand your conventions. By default Tilth reads both `AGENTS.md` and `CLAUDE.md` (in that order, concatenated); override the list with `TILTH_CONTEXT_FILES`. A starting template lives at [Memory channels → `AGENTS.md`](../architecture/memory-channels.md#agentsmd-your-project-conventions); the same page covers what does and doesn't belong there.

You do **not** hand-manage any harness state. Per-task status, the progress journal, and the evaluator ledgers are harness-owned and live under `~/.tilth/sessions/<id>/` — they never enter your repo's working tree. The only artifacts in your source repo are the `.tilth/tasks/` directory you author (below) and the `session/<id>` branch in `.git`.

## 2. Author the feature

The work is markdown you write in your repo, at `<repo>/.tilth/tasks/`: a required `overview.md` (the feature's goal, context, and — the high-leverage part — explicit scope boundaries) plus one `T-NNN-<slug>.md` per task (frontmatter `id`/`title`, a `## Description` in the worker's voice, and `## Acceptance criteria` as externally checkable bullets). The format reference is [The task format](../deep-dives/task-format.md); `tilth run` prints ready-to-fill templates when the directory is missing.

> The task files *are* the contract. The worker's job is to satisfy them, and the evaluator judges the diff against them — there's no codified test gate underneath. Vague descriptions and weak acceptance criteria collapse the quality gate down to "the evaluator said it looked fine," burn tokens, and produce branches you'll rewrite. Authoring is the high-leverage moment — slow down here, not in the run.

A few authoring habits that pay off:

- **Real paths and symbols** in descriptions (`pkg/module.py:func()`), not "the entrypoint". The worker starts each task from a fresh context; specificity is what it navigates by.
- **Acceptance criteria the evaluator can check against a diff.** "Running `cli export --format json` writes valid JSON to stdout" beats "export works correctly."
- **Tight scope boundaries in `overview.md`.** The "Out of scope" list is what keeps slices from growing — the evaluator hard-rejects cross-task interference, so tell it where the lines are.

You can draft the files with any agent you like (the templates are designed to be model-fillable) — but read what it wrote before running; you're signing the contract.

## 3. Run it

```bash
tilth run /absolute/path/to/your/repo
```

`tilth run` loads `.tilth/tasks/`, creates a fresh session + worktree, and starts the worker loop. With Tilth installed as a tool, run it from anywhere — no `cd` into a clone. (Working from a clone instead? `uv run tilth run …`.) The per-task lifecycle is identical to the demo — see [Running the demo → end-to-end flow](running-the-demo.md#run-a-session-against-the-demo) for the breakdown. Follow-on operations:

- [Resuming & resetting](resuming-and-resetting.md) — `tilth resume` to continue a stopped run; `tilth reset` to tear one down.
- [Visualizing a session](visualizing.md) — `tilth visualize` renders `events.jsonl` as a chat-style web app, live or replayed.

## 4. Review

Each task is one commit on `session/<id>`. Inspect and merge exactly as in the demo — [Running the demo → After the run](running-the-demo.md#after-the-run) covers the `git log` / `git diff` / merge recipe and what the session's `events.jsonl` and `summary.json` hold. If you don't like the work, delete the branch; the harness never auto-merges. For a readable pass over the run, render it with [`tilth visualize`](visualizing.md).

## 5. Caveats worth being upfront about

- **There is no mechanical quality floor.** The prompt-driven core has no codified lint/test gate — the worker is *told* to verify its work via `bash` before presenting it, and the evaluator judges the diff, but nothing runs your test suite deterministically between iterations. If your repo has a good test suite, say so in the task descriptions ("run `pytest tests/test_export.py` and make it pass") so verification is part of the contract.
- **Costs are real.** A run spends real money across worker + evaluator. The `TILTH_MAX_TOKEN_DOLLAR_SPEND` cap (USD) exists for a reason — set it on first run. If you set it too low, you can simply raise it and `tilth resume` the session. The cap reads the provider's reported per-call cost, so it only bites on providers that report one (OpenRouter does); elsewhere lean on the wall-clock cap. Cost per token varies wildly across providers; pick your worker accordingly. Be careful about reaching for a smaller evaluator model to cut costs — see [Picking an evaluator model](#6-picking-an-evaluator-model) below.
- **AGENTS.md is yours.** Tilth reads it, never writes it. It only grows when you decide it should.
- **Tools are intentionally narrow.** No web fetch, no MCP, no curl-based downloads. If your tasks require external API access, you add a tool to `tilth/tools/` and register it. Keep tools focused — every tool description ships in the prompt every turn.
- **The harness commits to your repo's git db.** Tilth keeps the working tree under `~/.tilth/sessions/<id>/workspace/` on its own side, but the branch `session/<id>` lives in *your* repo's `.git`. So if you uninstall Tilth (or wipe `~/.tilth/`) without resetting first, those branches remain in your project. Clean up branches the same way you would for a normal feature branch — or run `tilth reset` before you blow Tilth away. See [Session layout](../deep-dives/session-layout.md) for the full split.

## 6. Picking an evaluator model

The evaluator call is the single most consequential model decision in the harness. With no codified validator step, it's the only thing standing between the worker's own claim of "done" and a commit on a branch you'll merge.

### Default: evaluator ≥ worker

For correctness gating on code diffs, the evaluator should be **at least as capable as the worker, often more capable**. A weaker evaluator fails in the worst possible way: it accepts bad work because it didn't notice the problem.

The evaluator sees the task and its acceptance criteria, the feature overview, your project-context files, the worker's structured case, the diff, and its own prior verdicts on this task — but not the worker's chain-of-thought or tool history. It's reviewing an artifact, not retracing the work — so it needs more capability to compensate, not less.

### When dual-provider routing actually pays off

The `TILTH_EVALUATOR_BASE_URL` / `TILTH_EVALUATOR_API_KEY` feature is genuinely useful, but mostly for **cross-family independence**, not cost:

- **Worker = open model, evaluator = Claude (both on OpenRouter).** Different model families catch different failure modes. Same-family judging shares the worker's blind spots.
- **Worker = capable open model, evaluator = frontier closed model.** When you need the strongest possible gate, route the evaluator to whatever's at the top of the leaderboard for code review.

## 7. When this is the wrong tool

- **Closed-source-only tasks.** If you can't share code with OpenRouter, this isn't the right tool today. A self-hosted OpenAI-compatible endpoint (vLLM, LM Studio) might work via the OpenAI SDK but hasn't been validated.
- **Models without tool-calling support.** Some OpenRouter routes, some smaller open models, and most "completion-only" endpoints will fail or hallucinate tool calls. Verify on the demo workspace first.
- **One-shot prompts.** If your work fits in one Claude Code or Cursor session, just use that.
- **Hours-long, mission-critical, or production-touching runs.** Use a managed runtime (Google Agent Platform, Claude Managed Agents) instead. This harness is for *learning the pattern* on small bounded work.

## 8. What to do on first run

1. Scope the feature **narrowly**. Two or three tasks' worth of work — a feature with a clear contract, not an open-ended refactor. "Add `--format json` to the export CLI" beats "improve the export system."
2. Write the acceptance criteria as if you were the evaluator. For each one, ask: *could a reviewer check this against a diff?* If not, sharpen it. The contract compounds — early vagueness means later iterations.
3. Watch the console during `tilth run` — it streams every tool call. If the agent thrashes on one task, kill the run, reset, rewrite the task file with a sharper description.
4. Inspect `~/.tilth/sessions/<id>/events.jsonl` after the run. Look for unexpected patterns: tasks that took many iterations, strings of evaluator rejections on the same category, the worker re-reading the same files. Each is a signal — usually about the task file, sometimes about the harness. For a readable pass over the same data, render the run with [`tilth visualize`](visualizing.md).
