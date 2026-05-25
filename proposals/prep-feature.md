# Proposal: `tilth prep-feature` and the seed-handoff cleanup

**Status:** Draft for review
**Author:** Sam
**Date:** 2026-05-25
**Related:** [#10](https://github.com/AlteredCraft/tilth/issues/10) (closed by this), [#13](https://github.com/AlteredCraft/tilth/issues/13) (complementary), [#14](https://github.com/AlteredCraft/tilth/issues/14) (supersedes)

## 1. Problem

Tilth's quality-of-output is dominated by **seed quality** — the prd.json + matching tests the harness consumes. Today that seed is produced by a Claude Code skill (`tilth-prd-seeder`) and committed by hand into the target repo's `main` branch. Two consequences:

1. **Pollution of the target repo.** `prd.json` is purely a harness artifact (mutated by tilth as tasks flip pending → done). `progress.txt` is a runtime journal. Both currently live in the workspace and ride the session branch into every PR. Tests are different — they're a legitimate repo artifact and *should* ship in the PR.
2. **Seeding gap for non-Claude-Code users.** The interview that produces a good seed only exists as a skill bound to one assistant runtime. A reader following `docs/getting-started/your-own-project.md` gets the *structure* of a prd.json but no help producing one. Seed quality collapses to "what the user remembered to put in by hand," and a weak seed collapses tilth's quality gate to "ruff passed + judge said OK."

These compound: the demo's `main` ships with a hand-crafted `prd.json` committed, so the demo path doesn't represent the path a real user would take on their own repo. That violates a goal we want to be honest about.

## 2. Goals

- **G1.** Target repo gains zero ephemeral tilth artifacts. After a tilth-driven feature ships, the only things in the PR are source changes + tests + (optionally) AGENTS.md updates.
- **G2.** Demo path == own-project path. The demo repo demonstrates the seeding workflow rather than skipping it.
- **G3.** Seeding works without Claude Code. The user needs a reasoning-capable model (configured via Tilth's existing API env vars). No other assistant prerequisite.
- **G4.** Preserve the "base tools strung together via the CLI" character. Seeding and running are independently invocable verbs, not fused.
- **G5.** Don't paint future orchestration (TUI / web UI) into a corner. The interview engine's contract should be reusable by a non-TTY frontend.

## 3. Non-goals

- **NG1.** Single-command UX ("`tilth go`"). Sub-commands are fine; orchestration can come later.
- **NG2.** Process-isolating the worker from the harness's session dir. That's [#13](https://github.com/AlteredCraft/tilth/issues/13). This proposal makes the structural arrangement that makes #13 possible later.
- **NG3.** Auto-merging the session branch. Still human-reviewed.
- **NG4.** Polyglot support. Same Python-centric scope as today.

## 4. Design overview

Two coupled changes:

**A. Move harness-owned artifacts out of the workspace.** `prd.json` and `progress.txt` live under `sessions/<id>/`. The worktree never contains them. This is structural cleanup — closes #10, eliminates PR pollution — and ships independently of B.

**B. Add `tilth prep-feature` as a peer subcommand of `tilth run`.** It runs an interview against the target codebase using the configured reasoning model, writes the seed bundle into `sessions/<id>/`, and writes the matching test files into `<workspace>/tests/`. The interview engine is built around a frontend protocol so a future TUI/web UI can reuse the prompt without modification.

The current bare invocation `uv run tilth <workspace>` becomes `uv run tilth run <workspace>`. We accept this as a small breaking change — the user surface is small and pre-1.0.

## 5. Detailed design

### 5.1 Session dir layout

```
sessions/<id>/
├── checkpoint.json     # existing; gains `status: prepared | running | all_done | failed`
├── events.jsonl        # existing
├── summary.json        # existing
├── prd.json            # MOVED here (was: <workspace>/prd.json)
├── progress.txt        # MOVED here (was: <workspace>/progress.txt)
├── seed-meta.json      # NEW: { interviewer_model, started_at, ended_at,
│                       #        open_questions: [...], blockers: [...],
│                       #        scope_notes: "..." }
└── workspace/          # existing worktree mount; no harness state inside
```

`seed-meta.json` is written once by the seeder and read only by the visualizer (worker never sees it).

### 5.2 Code touch points in tilth

- `tilth/loop.py`
  - `_load_prd(worktree)` → `_load_prd(session_dir)` reading `session_dir / "prd.json"`
  - `_save_prd(worktree, prd)` → `_save_prd(session_dir, prd)`
  - Worktree construction does not copy prd.json in
- `tilth/memory.py`
  - `_load_progress_tail`, `append_progress` key on `session_dir` not `workspace`
- `tilth/session.py`
  - `checkpoint.json` gains `status` field
  - Add `find_prepared_session(workspace_path: Path) -> str | None`
- `tilth/visualize/`
  - Render `seed-meta.json` (open questions, blockers, scope notes) as a context panel above the chat scroll

### 5.3 Seed bundle contract

The seeder writes, atomically per session:

- **`sessions/<id>/prd.json`** — canonical tilth task list. JSON array, each entry: `{id, title, description, acceptance_criteria, status: "pending"}`.
- **`sessions/<id>/seed-meta.json`** — interview metadata.
- **`<workspace>/tests/test_t0NN_<slug>.py`** — one per task, named to match tilth's pytest filter. Written to the user's repo because they are legitimate test files that ship in the PR.
- **`session_prepared` event** appended to `sessions/<id>/events.jsonl` so the timeline is continuous from interview through execution.

The seeder **never writes to `<workspace>/AGENTS.md`**. If it exists, the seeder reads it for grounding context — project conventions there should inform task slicing and test style. If it doesn't exist, the seeder leaves it that way; it may surface "you don't have an AGENTS.md, your worker will fly blind on conventions" as a chat-summary suggestion, but doesn't create one. See §10 for the broader AGENTS.md follow-on.

Tilth `run` then consumes `sessions/<id>/prd.json` and `sessions/<id>/progress.txt` (initially empty) as the per-task input and journal.

### 5.4 Interview engine (`tilth/seed/`)

```
tilth/seed/
├── __init__.py
├── interview.py        # the engine — drives the model conversation
├── prompts.md          # the interview system prompt (port of the skill body)
├── frontend.py         # Protocol: ask_user, show_summary
└── tty.py              # TTY implementation of the frontend
```

The engine runs a tool-use loop against the configured reasoning model. The model is given a system prompt describing the seeding job (port of `tilth-prd-seeder/SKILL.md`) plus two tools:

```python
class InterviewFrontend(Protocol):
    def ask_user(
        self,
        question: str,
        options: list[str] | None = None,
    ) -> str:
        """Pose a question to the user. Options trigger menu-style input;
        None triggers free-form input. Returns the user's answer verbatim."""

    def show_summary(
        self,
        tldr: str,
        open_questions: list[str],
        blockers: list[str],
    ) -> None:
        """Render the closing summary. No return value."""

class SeedSink(Protocol):
    def write_seed(
        self,
        session_dir: Path,
        workspace: Path,
        prd_entries: list[dict],
        test_files: dict[str, str],
        meta: dict,
    ) -> None:
        """Atomically persist the seed bundle."""
```

The model emits tool calls for `ask_user`, `read_file` (for codebase grounding, reusing tilth's existing `tools/files.py:read`), `search` (reusing `tools/search.py`), and finally `write_seed` once. The TTY frontend implements `ask_user` with `input()` for free-form and a numbered menu for options. A future TUI implements the same protocol with a modal; a web UI with a WebSocket round-trip. **The interview prompt doesn't change across frontends** — that's the portable asset.

Reading from the existing `tilth/tools/` registry (files, search) keeps the interview engine grounded the same way the worker is, and avoids duplicating read primitives.

### 5.5 CLI surface

`tilth/cli.py` becomes a verb-routed entry point (argparse subparsers — adding `click` for this alone isn't worth a dependency):

```
tilth prep-feature <workspace>          # NEW: interview, produce seed
tilth run          <workspace>          # rename of current default
                   [--session <id>]     #   explicit session to consume
tilth resume       [<id>]               # promoted from --resume
tilth reset        [<id>] [--yes]       # promoted from --reset
tilth visualize    [<id>]               # promoted from --visualize
```

Resolution rules for `tilth run`:

- `--session <id>` given → use that session (must be `status: prepared` for this workspace).
- Otherwise, look for exactly one `prepared` session keyed to this workspace path. If found, use it.
- Multiple prepared sessions for this workspace → list them and refuse, ask the user to choose with `--session`.
- Zero prepared sessions → error: "No prepared session for this workspace. Run `uv run tilth prep-feature <workspace>` first."

Back-compat for bare `uv run tilth <workspace>`: detect the positional-only form and emit a deprecation warning routing to `tilth run`, then dispatch as `run`. Remove after one minor version.

### 5.6 Model configuration for the interview

- Defaults to the same model the harness already uses (`TILTH_API_KEY` / `TILTH_BASE_URL` / `TILTH_MODEL`).
- Optional overrides: `TILTH_PREP_MODEL`, `TILTH_PREP_BASE_URL`, `TILTH_PREP_API_KEY` — same pattern as the judge router.
- Interview model needs tool-calling; same constraint as the worker. Same model is the safe default.

### 5.7 Demo repo migration

`AlteredCraft/tilth-demo-todo-cli` `main`:

- Delete `prd.json`.
- Delete `progress.txt`.
- Delete `tests/test_t001_*.py` through `tests/test_t005_*.py`.
- Keep `tests/__init__.py`, `AGENTS.md`, source code, `pyproject.toml`, README.
- Update README to "Run `tilth prep-feature` against this, then `tilth run`. Don't seed manually — the demo demonstrates the seeding path."

The committed `prd.json` and tests are valuable as a reference for "what a good seed looks like." Move them into `examples/seed-reference/todo-cli/` in the **tilth** repo (not the demo repo) before deleting them from the demo:

```
examples/
└── seed-reference/
    └── todo-cli/
        ├── README.md         # what this is, why it's here, how to read it alongside the demo
        ├── prd.json          # the 5-task seed as it existed pre-migration
        └── tests/
            ├── test_t001_hello.py
            ├── test_t002_package.py
            ├── test_t003_add.py
            ├── test_t004_list.py
            └── test_t005_done.py
```

Reference is the right framing — it's a teaching artifact, not a fixture the harness reads. The docs page `docs/getting-started/your-own-project.md` should link to it as "here's what a good seed looks like when the interview is done."

Anticipate further `examples/seed-reference/<project>/` entries over time as more reference seeds get captured.

### 5.8 Docs migration

- `docs/getting-started/your-own-project.md` rewrites around `prep-feature`. The "Prep your repo" section shrinks to: clean git repo, optional AGENTS.md skeleton, that's it. The seed comes from the interview.
- `docs/getting-started/running-the-demo.md` updates to two commands (prep, then run).
- `docs/deep-dives/` gains a new page: `seeding.md` — explains the interview engine, the frontend protocol, how to swap the frontend.
- `docs/architecture/memory-channels.md#prdjson-the-task-list` updates to clarify prd.json lives in `sessions/<id>/`, never the workspace.

## 6. Phased landing

Tilth has no external users yet, so temporary breakage between phases is acceptable. Work methodically; ship each phase when it's correct, not when it's backwards-compatible.

### Phase 1 — Artifact moves (no new features)

Move `prd.json` and `progress.txt` to `sessions/<id>/`. Touch the load/save call sites in `loop.py` and `memory.py`. Add `status` to `checkpoint.json`. Capture the existing demo `prd.json` + tests into `examples/seed-reference/todo-cli/` in the tilth repo. Delete them from the demo repo.

**Visible change:** the demo (and any own-project user) needs phase 2 to be runnable again. That's fine — phase 1 is a pure structural cleanup that lands before the seeder exists, and we accept the gap.

After phase 1, #10 is closed and PR pollution is gone.

### Phase 2 — Interview engine + `tilth prep-feature`

Build `tilth/seed/`. Port `tilth-prd-seeder/SKILL.md` → `tilth/seed/prompts.md`. Implement the TTY frontend. Wire `tilth prep-feature` into the CLI. Deprecate the standalone Claude Code skill in favor of the in-harness command.

After phase 2, demo and own-project paths converge and the harness is runnable end-to-end again.

### Phase 3 — CLI verb router

Promote `--resume`, `--reset`, `--visualize` from flags to subcommands. Rename bare invocation to `tilth run`.

Phase 3 can land anytime after phase 2; it's a polish step, not load-bearing.

## 7. Open questions

1. **`AskUserQuestion`-equivalent UX in a TTY.** Plain `input()` plus numbered menus is enough for v1 and adds no dependency. Worth considering `questionary` (already pure-Python, ~600 LOC) if the TTY UX feels rough — but defer until we've actually felt the friction.
2. **Re-prep on an existing prepared session.** If the user runs `prep-feature` and there's already a prepared session for that workspace: refuse with `--reset` hint? Append to the existing PRD as a follow-on feature? Refuse-by-default seems right — `--reset <id>` is the escape hatch.
3. **Interview budget.** A 20-turn interview against a frontier model is real money. Worth surfacing a running token total in the TTY and a soft cap (`TILTH_PREP_MAX_TOKENS` defaulting to ~100k)?
4. **Empty `tests/` directory.** If the project has no `tests/` at all, the seeder needs to create it. Confirm that's the right location (single-question check, no-op if it exists).
5. **Surfacing a missing AGENTS.md to the user.** The seeder never writes it (see §5.3, §10). But if the workspace doesn't have one, the worker will fly blind on conventions. Should the seeder mention this in the chat summary as a suggestion ("you have no AGENTS.md — the worker won't have project context beyond what's in task descriptions; consider writing one before running")? Probably yes, as a non-blocking note.

## 8. Risks

- **Interview engine becomes a second hairball.** The harness already runs one tool-use loop (the worker). Adding a second one risks duplicating infrastructure. Mitigation: reuse `tilth.client.LLMClient` and the existing `tools/files.py` / `tools/search.py` registry — don't fork. The seed-specific surface is the frontend protocol and the prompt; everything else routes through existing code.
- **`prepared` sessions accumulate.** Users will prep, get distracted, prep again. We need a `tilth list` verb eventually, but for v1 the existing `sessions/` directory is grep-able and that's fine.
- **Interview cost surprises a user.** A user expecting "set up tilth" to be free runs an unbounded conversation against a frontier model. Mitigation: surface running cost in the TTY; soft cap via env var.
- **The seed-meta.json contract drifts.** It's read only by the visualizer today, but is tempting to lean on later. Document it as "interview audit trail, not load-bearing for the worker" and don't let the worker or judge read it.

## 9. Out of scope (later)

- TUI / web UI frontend for the interview (the protocol is the foothold).
- Multi-feature prepared sessions (one session = one feature for v1).
- Auto-update of an in-progress session's prd.json mid-run (the worker still doesn't plan).
- Cross-workspace shared seed library.
- Cost dashboard for the interview model.

## 10. Follow-on: revisit Tilth's stance on the target repo's AGENTS.md

The seeder change above is small and scoped: it reads AGENTS.md but never writes it. The bigger question this surfaces — and which this proposal **does not resolve** — is whether Tilth's *harness* should be mutating AGENTS.md at all.

### What happens today

`tilth/loop.py:158-216` runs a self-improvement step after each task. It asks the worker model whether anything from the just-completed task should land in AGENTS.md, validates the suggested section against `memory.VALID_AGENTS_MD_SECTIONS`, and if so calls `memory.append_to_agents_md(worktree, section, "- {entry}")`. The append happens inside the worktree, so it ends up in the session branch's diff and rides into the PR like any other change.

The framing in `docs/architecture/memory-channels.md` calls this "the agent's own learned conventions" — a cross-session ratchet.

### Why this needs another look

AGENTS.md is increasingly a **user-owned project artifact** under modern conventions (mirrored by tools far beyond Tilth — it's how Claude Code, Codex, Cursor, and others read project context). When Tilth mutates it:

- **Mid-run mutations land in the PR.** A line about "always use `argparse.BooleanOptionalAction`" appears in the same diff as the feature work and reviewers have to evaluate two unrelated changes.
- **The agent is writing into the user's documentation.** Even with section validation and length capping, the user's voice in their own conventions file gets diluted by model output of variable quality.
- **AGENTS.md is also load-bearing as worker context.** Every fresh task injects the file. If the model has been appending to it, the prompt grows; appended lines that are obvious in hindsight stay in the working set forever. This is the "AGENTS.md is yours forever; prune it periodically" caveat in `your-own-project.md` — a sign the current design pushes hygiene onto the user.

### Options to weigh (not decide here)

1. **Status quo, better documented.** Keep the append behavior; clarify in docs that AGENTS.md is a Tilth-mutated file the user is expected to prune.
2. **Read-only on AGENTS.md.** Harness reads it for context but never writes. Self-improvement insights land somewhere else — perhaps `sessions/<id>/learnings.md` (private to the session) or a separate `LEARNINGS.md` that the user explicitly opts in to.
3. **Opt-in mutation.** Env var or `prd.json`-level flag turns the append behavior on; default is off. Users who want the ratchet behavior get it; users who want AGENTS.md as their own doc get that.
4. **Proposal-based.** Worker emits a learning, but it lands in `sessions/<id>/agents-md-proposals.md` for the human to review and merge by hand. Same ratchet, no surprise mutations.

Option 2 is the cleanest read on "AGENTS.md is the user's." Option 4 preserves the original ratchet idea without the surprise. Option 3 is the chickenshit compromise that doubles the surface area.

### Why this is a separate proposal

The seeder change is *one* read-only consumer of AGENTS.md and is uncontroversial. The self-improvement loop is the load-bearing case and touches `loop.py`, `memory.py`, `session.py` (event types), the docs, and the article narrative around "memory channels." It deserves its own proposal where we can lay out the options, decide, and migrate cleanly.

→ See [`agents-md-stance.md`](agents-md-stance.md) for the sibling proposal.
