# Tilth feature template

A feature is a directory (conventionally `<repo>/.tilth/<feature>/`) holding
exactly one `overview.md` and one `T-NNN-<slug>.md` per task, **directly** — no
`tasks/` subdirectory. The user runs it with `tilth run <repo>/.tilth/<feature>`.
Use the shapes below verbatim; Tilth's parser is forgiving but **validates**, and
fails the run fast (before any session/worktree) if a required piece is missing.

Replace everything in `{{ braces }}` with content from the interview. Delete the
inline guidance once a section is filled.

## Validation rules the parser enforces

| Piece | Rule |
|---|---|
| `overview.md` | **Required, non-empty.** Free-form markdown; injected verbatim (capped) into both the worker's and the evaluator's prompts. Headings aren't parsed — use the four below; they're what readers expect. |
| Task frontmatter | **Required** `---` … `---` block. Unknown keys ignored; a missing or unterminated block is an error. |
| `id` | **Required**, must match `T-<digits>` (zero-padded: `T-001`). **Unique** across files; ordered by id. A collision silently breaks scheduling. |
| `title` | **Required**, non-empty. |
| `## Problem` | The *why* — what's wrong/missing and the impact. **Lead the task with this.** Parser caveat: today's parser passes only `## Description` (its fallback is the text *before the first heading*, **not** all non-AC body), so a discrete `## Problem` is silently dropped until [tilth#47](https://github.com/AlteredCraft/tilth/issues/47) lands. Keep `## Description` self-sufficient meanwhile. |
| `## Description` | The *what* the worker acts on. **Only this section** (or, with no `## Description` heading, the pre-first-heading text) reaches the worker. Empty → error. |
| `## Acceptance criteria` | `- ` / `* ` bullets collected as the criteria list (heading match case-insensitive). Not strictly required by the parser — but a task with no AC gives the evaluator nothing to gate on. **Always write them.** This is where binding precision lives. |

---

## `overview.md`

```markdown
# {{ Feature name }}

## Goal
{{ 1–3 sentences: what this feature/refactor delivers and why now. Quote the
user's framing if it captured the why. }}

## Context
{{ Which modules/files this touches — REAL paths (pkg/module.ext), not "the
entrypoint". What the worker needs to understand the whole before building one
slice. Name the load-bearing conventions from AGENTS.md/CLAUDE.md if they
matter. }}

## Scope boundaries
- In scope: {{ what this feature includes }}
- Out of scope: {{ the things the de-scope conversation cut; related features
  deferred; integrations not happening this round. High-leverage — the
  evaluator hard-rejects cross-task interference. }}

## Notes for the reviewer
{{ Risks, assumptions to sanity-check, open questions — what a human should
look at before merging the session branch. }}
```

---

## `T-NNN-<slug>.md` (one file per task, ordered by id)

```markdown
---
id: T-001
title: {{ short imperative title }}
---

## Problem
{{ The *why*, in 1–3 sentences: what's wrong or missing today and the impact.
Lead with this so the worker has the intent, not just instructions. (Until
tilth#47 ships, the parser drops this section — so keep `## Description` able to
stand on its own.) }}

## Description
{{ The *what* "done" looks like — the outcome plus constraints, in the worker's
voice, kept to a short statement (NOT a numbered/bulleted list of steps).
Anchor only as deep as the real file/module/type it lands in (pkg/module.ext;
"a Workspace method"), not "the entrypoint" — and stop there. **Never** name a
*new* function or type signature, a property wrapper, a control-flow construct
(do/catch, guard), or a literal string the worker should emit; those are the
worker's calls. Referencing *existing* symbols as orientation is fine. Name
cross-task boundaries and repo conventions that must hold. The worker has fresh
context and can build/run — leave it the agency to choose the approach. Every
detail that must hold goes in Acceptance criteria as an observable outcome, not
here. }}

## Acceptance criteria
- {{ observable outcome the evaluator can confirm against a diff — *what's true after*, not which API/mechanism was used }}
- {{ another }}
{{ If the user chose a TDD flow, phrase one criterion test-first, naming the
real test runner/path from the codebase scan — e.g. for the repo's toolchain:
   "tests/test_export.py::test_json_shape passes" (pytest) /
   "npm test -- export.test.ts passes" (jest/vitest) /
   "go test ./export passes" / "cargo test export passes". }}
```

---

## Notes on filling this in

- **Files sit directly in the feature directory** — `overview.md` and the
  `T-NNN-<slug>.md` files. No `tasks/` subdirectory.
- **IDs are zero-padded and contiguous** (`T-001`, `T-002`, …), unique, ordered
  by id. The `<slug>` in the filename is humans-only; only the frontmatter `id`
  matters to the harness.
- **`overview.md` ships every turn** — injected into both the worker and
  evaluator prompts. Keep it tight and load-bearing, not a design doc. The
  scope-boundaries section is the highest-leverage part.
- **No `status` field, ever.** Task files are read-only inputs; Tilth tracks
  status under `~/.tilth/sessions/<id>/`. Re-running re-reads the files, so you
  can sharpen a description between a failed run and `tilth resume`.
- **Acceptance criteria are conventions for a reviewer, not assertions for a
  test runner.** Write each as observable behaviour the evaluator can confirm on
  a diff, in terms of the repo's real toolchain — not a subjective state.
- **Problem first, then *what* not *how*.** Lead with `## Problem` (the why),
  put the outcome + constraints + file/module/type anchors (never new
  signatures, property wrappers, control-flow, or literal strings) in
  `## Description`, and the
  binding precision in `## Acceptance criteria`. Don't write the code the worker
  should type — if you're dictating exact type definitions or line edits you've
  become the implementer (blind, since you haven't run it). Specify outcomes and
  let the worker choose the approach; **every bit of precision you pull out of
  the steps must reappear as an observable criterion, or the gate weakens** — this
  is a transfer from steps → criteria, not a deletion. (Parser note: `## Problem`
  reaches the worker only once tilth#47 ships; until then keep `## Description`
  self-sufficient.)
- **3–8 tasks is the healthy range** for one feature. More than that → probably
  two features; give each its own `.tilth/<feature>/` directory.
- **Greenfield/MVP:** include the foundational tasks the user didn't name —
  scaffold, entry point, dependency/build setup, a runnable test harness, and a
  **README** (what it is + how to build/run/test) — anchored on the chosen
  stack. Scaffold/test-harness go up front as the early `T-NNN`s; the README is
  usually the *last* task, so it documents what actually shipped.
- **Adding to an existing feature:** keep the existing files and `overview.md`
  verbatim, continue numbering from the highest id.

## Chat summary (print to chat after writing — do NOT save to disk)

```
**Authored `<repo>/.tilth/<feature>/`:**
- T-001: <title> → <one-sentence outcome>
- T-002: <title> → <one-sentence outcome>
- …

**Open questions:** <decisions you guessed at or the user was unsure about>
**Blockers surfaced:** <only if you flagged any during the interview>

Run it: tilth run <repo>/.tilth/<feature>
```
