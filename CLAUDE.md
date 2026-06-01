# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repo.

## What this is

A minimal long-running agent harness against any OpenAI-compatible LLM endpoint. It implements the Brain / Hands / Session split, the Ralph loop, and the four memory channels from Addy Osmani's posts on long-running agents (plus a fifth Tilth adds — the per-task evaluator ledger). Built as both a working tool and the practical centerpiece of an Altered Craft article.

The ultimate goal is a minimal, productive agent harness with **hyper-observability**: every prompt the harness sends is accessible and adaptable, and every run is fully inspectable after the fact. The [Visualizing a session](docs/getting-started/visualizing.md) page is an early example of that extended observability — a finished run rendered end-to-end from its `events.jsonl`.

## Where to look first

- **`mkdocs.yml`** — **the canonical map of the documentation set**, and your primary entry point when looking for docs by topic. The `nav:` block has a one- to four-line comment above each leaf entry summarising what the linked `.md` covers and when you'd reach for it; skim those comments first, then open the page that fits. Everything that matters for users and contributors lives under `docs/`; `README.md` is the GitHub landing page and points into `docs/` for anything beyond the elevator pitch.
- **`README.md`** — terse GitHub landing page: product elevator pitch (with the Brain/Hands/Session image), a minimal quickstart, and the working-with-the-codebase commands (lint, tests, docs). **Not a mirror** of `docs/index.md` — for any product detail beyond the pitch, README points readers into `docs/`. Edit the two files independently.
- **`docs/getting-started/your-own-project.md`** — the "honest version" of using Tilth on a non-demo codebase: what prep your repo actually needs (a clean git repo, optionally an `AGENTS.md`), seeding with `tilth prep-feature` (you no longer hand-write the seed), the test-filename convention, caveats, evaluator-model picking, when it's the wrong tool. (Successor to the old root-level `USAGE.md`.)
- **`docs/deep-dives/`** — code-level walk-throughs of the two loops, the worker↔evaluator dialogue (case / verdict / ledger), iteration accounting, token recording/enforcement, and the agent-visibility boundary. Read this before changing any of those mechanics. (Successor to the old root-level `deep-dives.md`.)
- **The demo workspace** — lives in its own repo at [`AlteredCraft/tilth-demo-todo-cli`](https://github.com/AlteredCraft/tilth-demo-todo-cli). The docs use `~/projects/tilth-demo` as an illustrative path, but Tilth treats the path as just an argument so any layout works.
- **`docs/assets/IMAGE_STYLE.md`** — the prompt scaffold for generating new docs *images*, anchored to the canonical `brain-hands-session.png`. Use this whenever you generate a new diagram or illustration so the visual voice stays consistent across pages. Not in the published nav (excluded via `not_in_nav` in `mkdocs.yml`).
- **`docs/assets/SITE_STYLE.md`** — the visual identity for the rendered docs *site* (Material for MkDocs + custom CSS). Documents the provenance of the theme (Hex, from [refero.design](https://refero.design)), the load-bearing tokens, and the do's-and-don'ts to follow when editing `docs/stylesheets/extra.css` or `mkdocs.yml`. Companion to `IMAGE_STYLE.md`; also excluded from the published nav.

## Don't confuse the three "agent instruction" files

The repo has *four* files that look like agent instructions but speak to different audiences:

| File | Audience | Purpose |
|---|---|---|
| `CLAUDE.md` (this file) | Claude Code working on the harness itself | Conventions for editing this codebase |
| `tilth/prompts/system.md` | The worker agent inside the harness loop | Role, tool guidance, "done" criteria |
| `tilth/seed/prompts.md` | The seeder agent inside `tilth prep-feature` | Anchored interview workflow, tool surface, write_seed contract |
| `<demo-workspace>/AGENTS.md` | The worker agent operating on the demo workspace | Project conventions for the toy todo-cli |

When the user says "update the agent's instructions," ask which one — they're not the same thing.

## Repo layout

```
tilth/
├── README.md, CLAUDE.md, mkdocs.yml
├── docs/                  # MkDocs source (annotated nav in mkdocs.yml is the topic index)
├── pyproject.toml, .env.example, .gitignore
├── tilth/
│   ├── cli.py             # verb-routed entry: prep-feature / run / resume / reset / visualize
│   ├── loop.py            # Ralph loop + inner tool-use loop + subcommand handlers
│   ├── client.py          # OpenAI-compat wrapper, dual-client routing (worker / evaluator / prep)
│   ├── session.py         # events.jsonl + checkpoint.json + per-task ledger + wake()
│   ├── summary.py         # roll events.jsonl into summary.json (denormalised view)
│   ├── memory.py          # AGENTS.md / progress.txt / full-plan / seed-context injection
│   ├── workspace.py       # git worktree create / commit / diff
│   ├── validators.py      # ruff + pytest runners
│   ├── case.py            # worker submit_case schema / parse / render
│   ├── verdict.py         # evaluator submit_verdict schema / parse / ledger format
│   ├── tools/             # bash, files, search — registered in __init__.py (worker)
│   ├── hooks/             # pre_tool, post_edit
│   ├── prompts/           # system.md, evaluator.md, propose_learning.md
│   ├── seed/              # tilth prep-feature: interview engine + frontend / sink protocols
│   └── visualize/         # tilth visualize: events.jsonl + seed-meta.json → chat.html
├── examples/seed-reference/  # frozen example seeds (teaching artifacts, not runtime)
└── sessions/              # per-run state (gitignored)
```

The demo workspace is a separate repo (`AlteredCraft/tilth-demo-todo-cli`) — not part of the Tilth repo. Clone it wherever you keep code; the docs use `~/projects/tilth-demo` as an illustrative path, but the location is arbitrary.

## Conventions

- **Python 3.12.** `from __future__ import annotations` everywhere.
- **`uv` for env management.** `uv sync`
- **`ruff` for lint.** Config in `pyproject.toml`. Run `ruff check tilth/` before declaring work done.
- **Type hints on public functions.** Internal helpers can skip them.
- **No comments unless the WHY is non-obvious.** Don't narrate WHAT the code does.
- **Standard library first.** Third-party deps live in `pyproject.toml`; resist adding more.
- **External interfaces — verify, don't guess.** For provider APIs (OpenRouter, OpenAI SDK, Ollama, etc.), library specs, or any third-party wire format: consult the official docs first (use Context7, WebFetch, or the provider's sitemap to find them), and probe the live response shape with a tiny one-shot script before writing the fix. Don't infer field names from error messages — providers often surface their *upstream* internal field names in errors (e.g. SiliconFlow says `reasoning_content` but the OpenRouter wire field is `reasoning_details`). Don't infer from training data — these surfaces churn. A synthetic unit test built on a guessed shape gives false confidence; the test passes against your made-up contract while the real bug stays. Probe → write the test against the real shape → fix.

## Architecture invariants worth preserving

These are load-bearing. Read the relevant page under `docs/deep-dives/` before breaking any of them.

1. **Brain / Hands / Session split.** Don't blur the three. New code goes in the module whose job it is — model calls in `client.py`, sandbox/tool ops in `workspace.py` and `tools/`, durable state in `session.py`.
2. **The agent doesn't see harness mechanics.** No `prd.json` *file* or status fields, no `events.jsonl`, no `summary.json`, no token counts, no checkpoints, no cross-task evaluator. Hiding these prevents gaming, shortcutting, and self-managed state. (Phase 4 deliberately softened this: the worker now sees the whole task list *as prose context*, a curated `seed-meta.json` slice, and the evaluator's prior verdicts on its *current* task — so it can act on review feedback. The mutable JSON state, the harness files, and the wider evaluation machinery stay hidden.) New features should preserve this boundary unless the user explicitly asks otherwise.

    **Honest scope.** This is a *design goal*, not an enforcement guarantee in default mode. The worker has `bash` and the worktree is mounted at `sessions/<id>/workspace/`, so a determined model can reach harness state via relative paths — `events.jsonl`, `summary.json`, `checkpoint.json`, `prd.json`, `progress.txt` all live one directory up (`../`). The invariant's near-term purpose is to keep new code from making harness state *more* obviously surfaced to the worker; real enforcement is opt-in process isolation, planned in [#13](https://github.com/AlteredCraft/tilth/issues/13). (Phase 1 of `proposals/completed/prep-feature.md` moved `prd.json` and `progress.txt` out of the worktree and under `sessions/<id>/`, closing [#10](https://github.com/AlteredCraft/tilth/issues/10) — they're no longer inside the worktree, but they're still reachable via `../` from a determined worker.)
3. **Tool registry is the canonical source for "what tools exist".** `tilth/tools/__init__.py` defines the registry; system.md should *not* enumerate tools (it gets stale).
4. **Hook contract: "success silent, failures verbose" — to the *agent*.** Pass states inject nothing into the loop's message history; failures inject a feedback message that the next worker iteration sees. **Telemetry is separate.** Every hook invocation should emit a `hook_run` event regardless of outcome — observability is for the developer reading `events.jsonl`, not the agent. "Silent to the agent" must not mean "invisible in the log".
5. **The worktree branch is never auto-merged.** `commit_task` commits to the session branch; humans review and merge. Don't add an "auto-merge on success" feature without an explicit ask.
6. **Token cap enforcement is between tasks, not mid-task.** The "always finish the current task cleanly" property matters; preserve it.

## Where to file new things

| Adding... | Lives in... | Don't forget... |
|---|---|---|
| A tool | `tilth/tools/{name}.py` | Register in `tools/__init__.py:_registry()` |
| A hook | `tilth/hooks/{name}.py` | Wire into `tools/__init__.py:dispatch()` |
| A validator | `tilth/validators.py:run_*()` | Add to `run_all()` |
| A prompt | `tilth/prompts/{name}.md` | Add a loader in `loop.py` |
| A session event type | Use it in `session.log("...", {...})` | Document the type in `session.py`'s module docstring |
| A summary metric | `tilth/summary.py:build_from_events()` | Update the schema in the module docstring; bump `SUMMARY_VERSION` if shape breaks |

## Common commands

```bash
# Setup
uv sync

# Lint
.venv/bin/python -m ruff check tilth/

# Docs — strict build (catches broken nav refs, missing files, dead relative
# links). Run after editing mkdocs.yml or anything under docs/. This is the
# command CI will run when docs validation gets wired in; keep it green.
uv run --extra docs mkdocs build --strict --site-dir /tmp/tilth-site

# Docs — live preview at http://127.0.0.1:8000
uv run --extra docs mkdocs serve

# Demo (needs TILTH_API_KEY set in .env, and a local clone of the demo repo
# at AlteredCraft/tilth-demo-todo-cli — clone it wherever; path below is illustrative)
git clone git@github.com:AlteredCraft/tilth-demo-todo-cli.git ~/projects/tilth-demo
uv run tilth prep-feature ~/projects/tilth-demo   # interview → seed
uv run tilth run          ~/projects/tilth-demo   # run the seeded session

# Resume an interrupted session (latest in sessions/, or by id)
uv run tilth resume
uv run tilth resume <session_id>

# Reset a session — removes the worktree, deletes session/<id>, drops sessions/<id>/
uv run tilth reset
uv run tilth reset <session_id>
uv run tilth reset --yes  # skip the confirmation prompt

# Render a session's events.jsonl + seed-meta.json as chat-style HTML (sessions/<id>/chat.html)
uv run tilth visualize
uv run tilth visualize <session_id>

# Legacy single-dash flag forms (--resume / --reset / --visualize / --prep-feature)
# still work for one minor version; prefer the verbs above.

# Inspect a session log
jq -c . sessions/<session_id>/events.jsonl | head -40
```

## Working with the demo

The demo lives in its own repo at [`AlteredCraft/tilth-demo-todo-cli`](https://github.com/AlteredCraft/tilth-demo-todo-cli). Clone it wherever you keep code before running it. The path is just an argument to `uv run tilth`, so any layout works; the docs use `~/projects/tilth-demo` as an illustrative example.

The demo has to be a git repo because Tilth's worktree machinery requires it. To tear down a session's artifacts (worktree, `session/<id>` branch, `sessions/<id>/`), use `tilth reset` rather than the manual recipe:

```bash
uv run tilth reset                # most recent session
uv run tilth reset <session_id>   # explicit
```

`tilth reset` reads the session's checkpoint and `session_start` event to recover the source repo + worktree path + branch, runs `git worktree remove --force` and `git branch -D` against the source repo, and deletes `sessions/<id>/`. Force-removes a dirty worktree by design — its whole purpose is to discard a session's work; the `[y/N]` prompt is the safety gate.

If `tilth reset` itself can't run (e.g., session metadata missing), the manual fallback is:

```bash
cd <demo-clone-path>                  # e.g. ~/projects/tilth-demo
git worktree prune
git branch -D session/<id>            # if it still exists
rm -rf <tilth-clone-path>/sessions/<id>/
```

Don't commit changes the agent made on `session/*` branches into the demo clone's `main`. Those are run artefacts; the demo's `main` should stay unsealed (no `prd.json`, no per-task tests — those are produced by `prep-feature` into `sessions/<id>/` and `<workspace>/tests/` at run time).

## Things not to do without asking

- Commit changes (per the user's standing instruction — only commit when explicitly asked).
- Push to a remote, create PRs, or do anything network-side beyond running the harness itself.
- Change the architecture invariants above.
- Add a new dependency to `pyproject.toml` for convenience — justify the addition.
- Rewrite the system prompts to be more verbose. They are short on purpose; every character ships every turn.
- Auto-fix the demo workspace to pass tests yourself if a demo run fails — that defeats the point of the demo. Investigate why the harness didn't.

## Article context

This codebase is the practical centerpiece of an article in the user's PKM vault at:

```
~/_PRIMARY_VAULT/AlteredCraft/Altered Craft Publications/Notes/Long running agents/
```

That folder has `research-findings.md`, `research-links.md`, `mvp-spec.md`, and `draft.md`. When changes here are likely to be article-worthy (e.g. surprising findings from a demo run, new lessons from extending a slice), surface them so the user can update the draft. Don't edit those files unless asked.

### Session-start sweep for article-worthy learnings

A separate running notes file lives at:

```
~/_PRIMARY_VAULT/AlteredCraft/Altered Craft Publications/Notes/tilth-learnings.md/notes.md
```

It's a bulleted, themed corpus of transferable lessons from Tilth development (provider quirks, robustness patterns, multi-agent failure modes, observability wins) with commit-SHA links into [`AlteredCraft/tilth`](https://github.com/AlteredCraft/tilth). At the start of a new session, spawn a general-purpose subagent to:

1. `git log --since="3 days ago"` — see what's landed.
2. Read the notes file above.
3. For any commit surfacing a non-obvious lesson that *isn't* already represented, append a bullet under the right themed section (or open a new section) with the commit SHA linked and the bullet's date tagged on the section's `*Observed:*` line.
4. Skip docs-sync / lint / surface-polish commits — the file is for transferable lessons, not changelog.
5. Report back a short summary of what was added (and what was already there).

Match the existing voice: terse bullets, themed sections (not chronological), date range under each section header, lesson framed as a transferable principle plus a concrete commit anchor.
