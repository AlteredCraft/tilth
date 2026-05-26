# Seeding a session

`tilth prep-feature` runs an anchored interview against your source repo and writes a **task seed** — the `prd.json` plus matching acceptance tests that the worker loop consumes. This page walks through the engine, the frontend protocol the engine talks to, and what you can swap.

> A run's quality ceiling is set by its seed. The worker's job is to satisfy the contract; the seed *is* the contract. Vague tasks and weak acceptance criteria collapse Tilth's quality gate down to "ruff passed and the judge said it looked fine." The seeding interview is the high-leverage moment.

## The architecture

`tilth/seed/` is the seeding package, parallel to `tilth/tools/` for the worker. The pieces:

- **`prompts.md`** — the interview system prompt. Describes the workflow (scan → interview → write_seed), the tool surface, and the coverage targets (motivation, observable contract, slicing, tests, scope, risks).
- **`interview.py`** — the engine. Drives a fresh tool-use loop against the configured prep-feature model; mirrors the worker loop's observability (`model_call` / `tool_call` / `tool_result` events) so a seeded session and a run render identically in the visualizer.
- **`tools.py`** — the schemas the engine advertises to the model. A *narrower* surface than the worker's: read-only inspection (`read_file`, `glob`, `grep` reused from `tilth/tools/`) plus two new tools — `ask_user` (routes to the frontend) and `write_seed` (the terminal call, routes to the sink).
- **`frontend.py`** — `InterviewFrontend` and `SeedSink` protocols. The engine knows nothing about TTY rendering or filesystem layouts; it talks to these.
- **`tty.py`** — the bundled `TTYFrontend`. Plain `input()` for free-form, numbered menu for options, prompt-line token totals.
- **`sink.py`** — the bundled `FileSeedSink`. Validates the seed bundle (task ID format, filename pattern, 1:1 task↔test coverage, no duplicates) and writes atomically: every file is staged under a `.tmp` sibling and `os.replace`'d into place. A crash mid-write leaves the prior state untouched; a rejected bundle writes nothing.

## What goes on the wire

```
sessions/<id>/
├── checkpoint.json     # status: prepared (until tilth run flips it to running)
├── events.jsonl        # model_call, tool_call, tool_result, session_prepared
├── prd.json            # written atomically by the sink
├── seed-meta.json      # interview audit trail — see below
└── (no worktree yet — that happens on tilth run)

<workspace>/tests/
├── test_t001_<slug>.py # one per task, named to match tilth's pytest filter
├── test_t002_<slug>.py
└── ...
```

The worker never sees `seed-meta.json` — it's the interview audit trail for the visualizer and the human reviewer. Contents:

```json
{
  "interviewer_model": "anthropic/claude-opus-4.7",
  "started_at": "2026-05-25T22:14:01Z",
  "ended_at":   "2026-05-25T22:17:43Z",
  "tokens": {"prompt": 8432, "completion": 1118, "total": 9550},
  "tldr": "- **T-001:** scaffold — ...\n- **T-002:** ...",
  "open_questions": ["should X also do Y?"],
  "blockers": [],
  "scope_notes": "Migrations are out of scope this seed."
}
```

The visualizer reads this file and renders a context panel above the chat scroll. Run `tilth visualize <id>` after a prep — the TL;DR, open questions, blockers, and scope notes are surfaced before any per-task events.

## The protocols

The engine is decoupled from the frontend and the sink — both via `typing.Protocol`. A test stub, a future TUI, or a web frontend can substitute for the TTY without engine changes.

```python
class InterviewFrontend(Protocol):
    def ask_user(self, question: str, options: list[str] | None = None) -> str: ...
    def show_summary(self, tldr: str, open_questions: list[str], blockers: list[str]) -> None: ...
    def update_tokens(self, prompt_total: int, completion_total: int) -> None: ...

class SeedSink(Protocol):
    def write_seed(
        self,
        session_dir: Path,
        workspace: Path,
        prd_entries: list[dict],
        test_files: dict[str, str],
        meta: dict,
    ) -> None: ...
```

The engine is called via `tilth.seed.run_interview(session, source, client, frontend, sink, feature_brief)`. Swapping the frontend is "write a class with those three methods and pass it in"; swapping the sink is the same for `write_seed`. The TTY implementation lives in ~80 lines; a stub frontend for tests lives in ~20.

## What the model can and can't do

The interview model is given exactly five tools:

| Tool | What it does | Routes to |
|---|---|---|
| `read_file` | Read a file from the source repo (up to 50KB) | `tilth.tools.files.read` against the source path |
| `glob` | Glob the source repo (e.g. `**/*.py`) | `tilth.tools.search.glob_` |
| `grep` | Regex-search file contents | `tilth.tools.search.grep` |
| `ask_user` | Pose a question; optional menu of options | `frontend.ask_user` |
| `write_seed` | TERMINAL — write the bundle atomically | `sink.write_seed`, then `session.set_status("prepared")` |

Conspicuously absent: `bash`, `write_file`, `edit_file`. The seeder is **read-only against your source repo** until the terminal write — there's no path for the model to mutate code outside of producing the `test_files` content in `write_seed`.

## How `tilth run` picks up a prepared session

`tilth run <workspace>` looks at `sessions/*/checkpoint.json` and finds those whose `source` matches `<workspace>` and `status == "prepared"`. The cases:

- **Exactly one prepared session.** Wake it, create the worktree at `sessions/<id>/workspace/` on branch `session/<id>`, flip status to `running`, log `session_start`, start the worker loop.
- **Multiple prepared sessions.** Refuse and list them — you discard the ones you don't want with `tilth reset <id>` until one remains.
- **Zero prepared sessions.** Falls back to the legacy "start fresh" path, which will fail at PRD-load with a clear pointer to `tilth prep-feature`.

This rule is enforced on the prep side too: `tilth prep-feature` refuses to start a new session if any session for this workspace is in `prepared`, `running`, or `failed` state. Discard or resume first. This stops the "I forgot I'd already prepped this" footgun.

## Configuration

The interview defaults to the same model the worker uses. Three optional env vars let you route the interview to a different provider, same pattern as the judge router:

| Var | Purpose | Default |
|---|---|---|
| `TILTH_PREP_MODEL` | Interview model name | `TILTH_WORKER_MODEL` |
| `TILTH_PREP_BASE_URL` | OpenAI-compatible base URL for prep | `TILTH_BASE_URL` |
| `TILTH_PREP_API_KEY` | API key for the prep provider | `TILTH_API_KEY` |

Practical use: route the interview to a frontier reasoning model (Claude Opus, GPT-5) even if your worker runs on a cheaper open model. A weak seed compounds across the entire run; a strong seed is the single highest-leverage spend.

The OpenRouter `reasoning.enabled` opt-in (sent for thinking-mode models) is keyed on the *routed* base URL, not the worker's — so a worker on OpenRouter routing prep through a different provider doesn't leak OpenRouter-specific syntax.

## Why two terminations and one cap

The interview loop has two normal exits:

1. **`write_seed` succeeds.** Status flips to `prepared`, `session_prepared` event logged, summary shown, return.
2. **Model stops calling tools.** `InterviewAbort` raised — the model "gave up" without writing a seed. Status stays `running` (so resume isn't tempting), the session is left for inspection or reset.

Plus one safety cap:

3. **`MAX_INTERVIEW_ITERATIONS` (60) hit without `write_seed`.** `InterviewAbort`. Generous on purpose; this should never fire under normal use, but it's the difference between a runaway interview and a stuck one.

There's no soft cap on tokens in v1 — the prompt-line token strip is the only signal. If a hard cap proves necessary in practice, the existing token-cap pattern (`TILTH_PREP_MAX_TOKENS`) is the precedent. See `proposals/prep-feature.md` §9.

## Reading further

- The proposal: `proposals/prep-feature.md` in the Tilth repo — Phase 1 (artifact moves), Phase 2 (the interview engine), Phase 3 (CLI verb router + this page).
- The original skill body: `~/.claude/skills/tilth-prd-seeder/SKILL.md` was the Claude Code skill version; `tilth/seed/prompts.md` is the in-harness port.
- A worked example seed: [`examples/seed-reference/todo-cli/`](https://github.com/AlteredCraft/tilth/tree/main/examples/seed-reference/todo-cli) in the Tilth repo — hand-crafted reference for a Python todo CLI.
