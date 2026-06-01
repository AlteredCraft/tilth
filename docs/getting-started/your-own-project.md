# Using Tilth on your own project

> **Early-stage research project.** Tilth is a research harness, not a hardened product. Running it should be *safe*: every change lands on an isolated `session/<id>` worktree branch, never on your `main`, and the harness never auto-merges — you review the diff like any other branch. What isn't guaranteed yet is *quality*. In these early stages the branch Tilth hands back may be rough or incomplete, and the tokens spent getting there are real. Treat runs as spend-at-your-own-risk and keep the first ones small.

This page is for a reader who has finished the [demo walkthrough](running-the-demo.md) and now wants to point Tilth at their own codebase. [Installation](installation.md) and [Running the demo](running-the-demo.md) cover the harness mechanics — this page covers what's specific to applying it to your *own* repo: seeding the task list with `tilth prep-feature`, picking an evaluator, and the caveats that aren't obvious from a demo run.

## 1. Prep your repo

Your project must be a **git repo with a clean `main` branch**. That's it for hard prerequisites. Two things are worth having but optional:

- **`AGENTS.md`** at the repo root. User-owned, user-maintained — Tilth reads it as project context for the worker and the evaluator but never writes to it. Even a short one helps both the seeding interview and the worker understand your conventions. A starting template lives at [Memory channels → `AGENTS.md`](../architecture/memory-channels.md#agentsmd-your-project-conventions); the same page covers what does and doesn't belong there.
- **An existing `tests/` directory with at least one example file.** The seeder samples your test style during the interview and mirrors it in the new test files. With no examples, you'll be asked to confirm the convention.

You do **not** write `prd.json`, `progress.txt`, or the acceptance tests by hand. `prd.json` and `progress.txt` are harness-owned and live under `sessions/<id>/` — they never enter your repo's working tree. The acceptance tests come out of `tilth prep-feature` and land in `sessions/<id>/workspace/tests/` (the session worktree, on branch `session/<id>`), not your source repo's `tests/`. Your working tree stays clean across both prep and run; the only artifact in your source repo is the `session/<id>` branch in `.git`.

## 2. Seed a task list

`tilth prep-feature` interviews you against your codebase to produce the seed — `prd.json` (task list) and one matching acceptance test per task. Each test lands at `<worktree>/tests/test_t<NNN>_<slug>.py` (matching its `T-NNN` task id) — the convention pytest runs against and the evaluator reads to ground its review; the sink rejects a seed that doesn't follow it. The interview is anchored: the model reads your code as it asks questions, so the slices are grounded in what's actually there.

If you've already written a spec / RFC / design doc / ticket for this feature, point at it in your initial brief (e.g. *"add a CSV exporter — full spec at `docs/proposals/csv-exporter.md`"*). The seeder reads the doc first and shifts the interview into confirmation + gap-filling mode rather than starting from scratch — usually fewer turns, fewer tokens, and the seed anchored on text you already vetted. The doc must live inside the repo (the seeder's file access is sandboxed); for external docs, paste the load-bearing sections inline into your brief. See [Interview shapes: cold start vs. existing-PRD anchor](../deep-dives/seeding.md#interview-shapes-cold-start-vs-existing-prd-anchor) for the engine-side story.

```bash
cd <your-tilth-clone>
uv run tilth prep-feature /absolute/path/to/your/repo
```

You'll be prompted once for a one-line brief (the feature or refactor you want), then driven through ~5–15 turns of decision-style menus and free-form questions. The token total for the interview is surfaced on every prompt so you can abort if it drifts long. After the terminal write, the session is in `prepared` state and the harness tells you what's next.

For the full interview-engine story — frontend protocol, write-seed atomicity, how to swap the TTY for a different frontend — see [Seeding a session](../deep-dives/seeding.md). For a worked example of what a finished seed looks like, browse [`examples/seed-reference/todo-cli/`](https://github.com/AlteredCraft/tilth/tree/main/examples/seed-reference/todo-cli) in the Tilth repo.

If a `prepared`, `running`, or `failed` session for the same workspace already exists, `prep-feature` opens an interactive picker: *run/resume the existing one, discard and re-prep, start a new session alongside it, or cancel*. The "start alongside" option is for when you want to try a different angle without losing in-flight work — but the next `tilth run` will refuse until exactly one prepared session remains, so you'll need to `tilth reset` the extras eventually. On a non-TTY (CI/scripts) the picker is skipped and you get the previous refuse-and-hint behavior; pass `--force` to auto-discard blockers, or `--keep-existing` to start fresh alongside them.

## 3. Run it

```bash
uv run tilth run /absolute/path/to/your/repo
```

If exactly one prepared session for this workspace exists, the harness wakes it (worktree already exists from prep), flips status to `running`, and starts the worker loop. The per-task lifecycle is identical to the demo — see [Running the demo → end-to-end flow](running-the-demo.md#run-a-session-against-the-demo) for the breakdown.

If no prepared session exists, `tilth run` shows a picker rather than crashing — *resume a prior session* (when one's resumable), *prep one now*, or *cancel*. Non-TTY callers get a clean exit-2 pointer instead. See [Resumable-session detection](resuming.md#resumable-session-detection) for the picker shape. Follow-on operations:

- [Resuming a session](resuming.md) — `tilth resume` semantics, what survives across runs.
- [Resetting a session](resetting.md) — `tilth reset` tears down a session's worktree, branch, and `sessions/<id>/`.
- [Visualizing a session](visualizing.md) — `tilth visualize` renders `events.jsonl` (and `seed-meta.json` if present) as a chat-style HTML page with a seed-context panel above the timeline.

## 4. Review

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

The session log lives at `{{tilth-clone-path}}/sessions/<id>/events.jsonl` — every model call, tool call, validator run, evaluator verdict, and self-improvement proposal is recorded. Alongside it, `sessions/<id>/summary.json` carries a rolled-up snapshot (token totals, per-task iteration counts, tool histogram, hook outcomes, evaluator accept/reject plus a rejection-category histogram) refreshed at every task boundary — read that when you want a quick stat without `jq`-ing the full log. The schema is documented in `tilth/summary.py`'s module docstring.

## 5. Caveats worth being upfront about

- **It's Python-only today — polyglot is the intent.** Right now `post_edit` lints `.py` files and `validators` runs `pytest` and `ruff`, so the mechanical floor assumes a Python toolchain. Making that floor language-pluggable — so Tilth can drive JavaScript, Rust, or Go projects on their own toolchains — is a tracked goal ([#20](https://github.com/AlteredCraft/tilth/issues/20)), not a finished feature. Until it lands, pointing Tilth at a non-Python project means adapting `tilth/validators.py` and `tilth/hooks/post_edit.py` to your toolchain yourself — not deep work, but not zero.
- **The interview drives the seed; you drive the interview.** `prep-feature` interviews against your code, but the answers come from you. Vague briefs and rushed answers produce vague seeds and weak acceptance criteria, which burn tokens and produce branches you'll rewrite. The interview is the high-leverage moment — slow down here, not in the run.
- **Costs are real, in two places.** The interview itself is a real spend (a frontier-tier reasoning model across many turns); the prompt-line token strip surfaces it so you can abort if it drifts. Then the run itself spends hundreds of thousands of tokens across worker + evaluator + self-improvement. The `TILTH_MAX_TOKENS` cap exists for a reason — set it on first run. If you set it too low, you can simply raise it and `--resume` the session. Cost per token varies wildly across providers; pick your worker accordingly. Be careful about reaching for a smaller evaluator model to cut costs — see [Picking a evaluator model](#6-picking-an-evaluator-model) below.
- **AGENTS.md is yours.** Tilth reads it, never writes it. The self-improvement step's proposals land in `sessions/<id>/proposed-learnings.md` for you to review and (optionally) promote into AGENTS.md by hand. The file only grows when you decide it should.
- **Tools are intentionally narrow.** No web fetch, no MCP, no curl-based downloads. If your tasks require external API access, you add a tool to `tilth/tools/` and register it. Keep tools focused — every tool description ships in the prompt every turn.
- **The harness commits to your repo's git db.** Tilth keeps the working tree under `sessions/<id>/workspace/` on its own side, but the branch `session/<id>` lives in *your* repo's `.git`. So if you delete your Tilth clone without resetting first, those branches remain in your project. Clean up branches the same way you would for a normal feature branch — or run `tilth reset` before you blow Tilth away. See [Session layout](../deep-dives/session-layout.md) for the full split.

## 6. Picking an evaluator model

The evaluator call is the single most consequential model decision in the harness. It's the only thing standing between "validators passed" and "this gets committed to a branch you'll merge."

### Default: evaluator ≥ worker

For correctness gating on code diffs, the evaluator should be **at least as capable as the worker, often more capable**. A weaker evaluator fails in the worst possible way: it accepts bad work because it didn't notice the problem.

The evaluator sees the diff, the acceptance criteria, the full validator output, the inlined seed test, the worker's structured case, and its own prior verdicts on this task — but not the worker's chain-of-thought or tool history. It's reviewing an artifact, not retracing the work — so it needs more capability to compensate, not less.

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

1. Brief the interview **narrowly**. Two or three tasks' worth of work — a feature with a clear contract, not an open-ended refactor. "Add `--format json` to the export CLI" beats "improve the export system."
2. Drive the interview honestly. When asked an out-of-scope question, push back rather than nod through it; when the model proposes a slice that looks wrong, redirect with words rather than `Other` defaults. The seed compounds — early shortcuts mean later iterations.
3. Watch the console during `tilth run` — it streams every tool call. If the agent thrashes on one task, kill the run, reset, re-prep with a sharper brief.
4. Inspect `sessions/<id>/events.jsonl` after the run. Look for unexpected patterns: tasks that took many iterations, evaluator rejections, validator failure loops. Each is a signal. For a readable pass over the same data, render the run with [`tilth visualize`](visualizing.md) — it lays the event log out as a chat-style timeline with the seed context panelled above it.
5. Read `sessions/<id>/proposed-learnings.md` and decide what (if anything) belongs in your `AGENTS.md`. The first few proposals are often noise — prune ruthlessly.
