# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repo.

## What this is

A minimal long-running agent harness against any OpenAI-compatible LLM endpoint. It implements the Brain / Hands / Session split, the Ralph loop, and the four memory channels from Addy Osmani's posts on long-running agents. Built as both a working tool and the practical centerpiece of an Altered Craft article.

The repo is **not** a framework. It's an artefact. ~600 lines of Python, kept deliberately small.

## Where to look first

- **`README.md`** — high-level architecture and setup.
- **`USAGE.md`** — how a reader uses it on their own project (preparing `prd.json`, `AGENTS.md`, `progress.txt`, `tests/`; provider/model selection; caveats).
- **`deep-dives.md`** — code-level walk-throughs of the two loops, iteration accounting, token recording/enforcement, and the agent-visibility boundary. Read this before changing any of those mechanics.
- **The demo workspace** — lives in its own repo at [`AlteredCraft/tilth-demo-todo-cli`](https://github.com/AlteredCraft/tilth-demo-todo-cli). Conventional clone path is `{{your projects folder}}/tilth-demo` (sibling to Tilth, matches README/USAGE). Tilth treats the path as just an argument, so any path works.

## Don't confuse the three "agent instruction" files

The repo has *three* files that look like agent instructions but speak to different audiences:

| File | Audience | Purpose |
|---|---|---|
| `CLAUDE.md` (this file) | Claude Code working on the harness itself | Conventions for editing this codebase |
| `tilth/prompts/system.md` | The worker agent inside the harness loop | Role, tool guidance, "done" criteria |
| `<demo-workspace>/AGENTS.md` | The worker agent operating on the demo workspace | Project conventions for the toy todo-cli |

When the user says "update the agent's instructions," ask which one — they're not the same thing.

## Repo layout

```
tilth/
├── README.md, USAGE.md, deep-dives.md, CLAUDE.md
├── pyproject.toml, .env.example, .gitignore
├── tilth/
│   ├── loop.py            # Ralph loop CLI + the inner tool-use loop
│   ├── client.py          # OpenAI-compat wrapper, dual-client routing
│   ├── session.py         # events.jsonl + checkpoint.json + wake()
│   ├── memory.py          # AGENTS.md / progress.txt loading + injection
│   ├── workspace.py       # git worktree create / commit / diff
│   ├── validators.py      # ruff + pytest runners
│   ├── tools/             # bash, files, search — registered in __init__.py
│   ├── hooks/             # pre_tool, post_edit
│   └── prompts/           # system.md, judge.md, agents_update.md
└── sessions/              # per-run state (gitignored)
```

The demo workspace is a separate repo (`AlteredCraft/tilth-demo-todo-cli`) cloned alongside Tilth — by convention at `{{your projects folder}}/tilth-demo`. It is not part of the Tilth repo.

## Conventions

- **Python 3.12.** `from __future__ import annotations` everywhere.
- **`uv` for env management.** `uv venv && uv pip install -e .`
- **`ruff` for lint.** Config in `pyproject.toml`. Run `ruff check tilth/` before declaring work done.
- **Type hints on public functions.** Internal helpers can skip them.
- **No comments unless the WHY is non-obvious.** Don't narrate WHAT the code does.
- **Standard library first.** Third-party deps live in `pyproject.toml`; resist adding more.

## Architecture invariants worth preserving

These are load-bearing. Read `deep-dives.md` before breaking any of them.

1. **Brain / Hands / Session split.** Don't blur the three. New code goes in the module whose job it is — model calls in `client.py`, sandbox/tool ops in `workspace.py` and `tools/`, durable state in `session.py`.
2. **The agent doesn't see harness mechanics.** No `prd.json` structure, no `events.jsonl`, no token counts, no judge, no checkpoints. Hiding these prevents gaming, shortcutting, and self-managed state. New features should preserve this boundary unless the user explicitly asks otherwise.
3. **Tool registry is the canonical source for "what tools exist".** `tilth/tools/__init__.py` defines the registry; system.md should *not* enumerate tools (it gets stale).
4. **Hook contract: "success silent, failures verbose."** Pass states inject nothing into the loop. Failures inject a feedback message that the next worker iteration sees.
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

## Common commands

```bash
# Setup
uv venv && uv pip install -e .

# Lint
.venv/bin/python -m ruff check tilth/

# Demo (needs TILTH_API_KEY set in .env, and a local clone of the demo repo
# at AlteredCraft/tilth-demo-todo-cli — conventional path is sibling to Tilth)
git clone git@github.com:AlteredCraft/tilth-demo-todo-cli.git {{your projects folder}}/tilth-demo
uv run tilth {{your projects folder}}/tilth-demo

# Resume an interrupted session (latest in sessions/, or by id)
uv run tilth --resume
uv run tilth --resume <session_id>

# Reset a session — removes the worktree, deletes session/<id>, drops sessions/<id>/
uv run tilth --reset
uv run tilth --reset <session_id>
uv run tilth --reset --yes  # skip the confirmation prompt

# Inspect a session log
jq -c . sessions/<session_id>/events.jsonl | head -40
```

## Working with the demo

The demo lives in its own repo at [`AlteredCraft/tilth-demo-todo-cli`](https://github.com/AlteredCraft/tilth-demo-todo-cli). Clone it as a sibling of Tilth (canonical path: `{{your projects folder}}/tilth-demo`) before running it. The path is just an argument to `uv run tilth`, so any path works.

The demo has to be a git repo because Tilth's worktree machinery requires it. To tear down a session's artifacts (worktree, `session/<id>` branch, `sessions/<id>/`), use `--reset` rather than the manual recipe:

```bash
uv run tilth --reset                # most recent session
uv run tilth --reset <session_id>   # explicit
```

`--reset` reads the session's checkpoint and `session_start` event to recover the source repo + worktree path + branch, runs `git worktree remove --force` and `git branch -D` against the source repo, and deletes `sessions/<id>/`. Force-removes a dirty worktree by design — its whole purpose is to discard a session's work; the `[y/N]` prompt is the safety gate.

If `--reset` itself can't run (e.g., session metadata missing), the manual fallback is:

```bash
cd <demo-clone-path>                  # e.g. {{your projects folder}}/tilth-demo
git worktree prune
git branch -D session/<id>            # if it still exists
rm -rf <tilth-clone-path>/sessions/<id>/
```

Don't commit changes the agent made on `session/*` branches into the demo clone's `main`. Those are run artefacts; the demo's `main` should stay seeded-state-only.

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
