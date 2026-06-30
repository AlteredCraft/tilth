---
name: tilth-feature-author
description: Use Tilth to define a new feature for a codebase — produces a Tilth feature directory (an `overview.md` plus one `T-NNN-*.md` task file per slice, conventionally at `<repo>/.tilth/<feature>/`) from a feature or refactor request, via an interview anchored on the target app's code. Use whenever the user wants to define, scope, or seed a new feature for Tilth to build, break a feature into Tilth tasks, author an overview + tasks for Tilth, set up a `.tilth/<feature>/` directory, or start a new Tilth project (MVP). Do NOT use for: a bug fix small enough to fit a single task (write the one file directly), adding one more task to a feature directory that already exists in the same style (edit it directly), or running the harness itself (this only authors the feature).
argument-hint: [Feature or refactor description, plus the path to the target repo]
---

# Tilth Feature Author

Interview the user to define a new feature for **Tilth** to build, and write it out as a **feature directory** — a required `overview.md` (the feature's why + scope) plus one `T-NNN-<slug>.md` per task — conventionally at `<repo>/.tilth/<feature>/`. The user then hands it to Tilth with `tilth run <repo>/.tilth/<feature>`.

## What Tilth is (orient yourself first)

You may not have seen Tilth before — here's what these artifacts feed. Tilth is a long-running, autonomous agent harness. You give it a feature decomposed into small, ordered tasks, each with acceptance criteria. A **worker** agent builds the tasks one at a time on an isolated git branch; after each task an **evaluator** agent reviews that task's diff against its acceptance criteria and either accepts (commit, next task) or rejects with feedback (the worker retries). It runs unattended — no human in the loop during the run — and there is **no codified test gate**: nothing mechanically runs the repo's tests unless a task tells the worker to. That makes the files you author *the contract*. Vague descriptions or uncheckable criteria collapse the only quality gate to "the evaluator liked it," burn real tokens, and produce a branch the human rewrites. This skill exists to make that contract sharp before any code is written.

## Why this skill exists

Authoring is the high-leverage moment. The interview pulls the feature apart into small, sequenced, atomically-committable slices, pins each to externally-checkable criteria, and sets explicit scope boundaries the evaluator enforces. **Language-agnostic:** Tilth drives any toolchain — Python, JS/TS, Go, Rust, whatever — so anchor everything (paths, build, verification) on what the target repo actually uses; never assume a particular ecosystem. The invariant: **ground in the target code first, slice and agree on the task set in conversation, then author the overview + task files together.**

## Workflow

The sequence matters. Do not jump ahead.

### 1. Confirm the feature in one sentence

Get the user's intent: *what feature or refactor are we defining, and where is the repo?* Paraphrase if they already said it; ask one targeted question if it's vague or the path is missing. The target must be a **git repo with at least one commit** — Tilth's worktree machinery requires it (a brand-new project still needs `git init` + an initial commit). If it's an empty or near-empty repo, that's fine — see the MVP guidance in step 3.

### 2. Strategic codebase scan to seed context

The grounding read is **steered by the seed, not exhaustive.** Read what the feature makes relevant; ignore the rest. The goal is to ask sharp anchored questions, slice the work credibly, and recognize when an answer contradicts the code. Deep reading for the task descriptions happens after the slice is locked (step 7).

- **Check `<repo>/.tilth/` for existing feature directories.** Each feature is its own dir (`.tilth/<feature>/`). If one already covers this work, decide with the user whether to add tasks to it (keep its `overview.md`, continue `T-NNN` from the highest id) or start a new feature dir. Never silently overwrite.
- **Read the repo's `AGENTS.md` / `CLAUDE.md`** (or equivalent) — the conventions the worker will follow; your tasks should respect them.
- **Identify the toolchain.** The build/package manifest (`package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `Gemfile`, …), the test runner, the lint/format setup. This is what verification will hook into; you can't write runnable criteria without it.
- **Locate the area the feature touches.** Glob/grep for likely modules, entrypoints, handlers, models; read 2–4 of the most relevant files end-to-end. Skim a similar existing feature — slice the new work the way that one was sliced.
- Large/unfamiliar repo → spawn an **Explore** subagent in parallel. Tell it: "Inventory the repo at `<path>` for a Tilth feature on `<feature>`. Identify (a) where this work would live, (b) similar existing features and structure, (c) the toolchain + test patterns, (d) any existing `.tilth/<feature>/` dirs and their highest `T-NNN`. Report under 400 words."

After the scan, tell the user in *one paragraph* what you found — the area, conventions, toolchain, and whether a `.tilth/` feature already exists. Let them redirect.

### 3. Anchored interview

Drive it adaptively — match the tool to the question:

- **`AskUserQuestion`** for **decision-style** questions — 2–4 options the scan surfaced (e.g. "Extend `exporters/csv.*` or a new module?"). Always include `"Other (I'll specify)"`.
- **Free-form text** for **clarification-style** questions — where the code is vague, contradictory, or silent and you need the user's own words. Motivation, scope calls, and risks usually want free-form.

One question per turn; follow up freely; return to the code whenever an answer makes a new area relevant.

**Coverage targets** (cover before authoring; order is up to you):

- **Motivation & context** — why this feature/refactor, why now, what changes when it ships; the real modules/paths it touches. Becomes the overview's **Goal + Context**. Quote the user's framing when it nailed the why.
- **Task slicing & sequencing** — break the goal into **3–8 small tasks**, each (a) one atomic commit, (b) building on prior tasks' state, (c) checkable. Present your proposed slicing as a **numbered draft** and iterate with the user until you both agree — this collaborative loop is the heart of the skill. **Push back when the scope reads too big**: propose a concrete de-scope, not a vague "that's a lot." The first task is often a scaffold/sentinel; the last is usually the user-visible payoff. **For a new/empty project (an MVP — first feature, little or no code yet): proactively include the foundational tasks the user didn't name** — project scaffold, entry point, dependency/build setup, a runnable test harness, and a **README** (what the project is + how to build/run/test) — anchored on the chosen toolchain. The scaffold/test-harness tasks go up front; the README is usually the *last* task so it documents what actually shipped (the real build/run/test commands). Surface and confirm them ("a greenfield build needs a scaffold task and a test-harness task up front, and a README task to close it out — adding those") rather than silently inserting or omitting them.
- **Scope boundaries** — explicit *in* and *out of scope*. **The de-scope conversation feeds the out-of-scope list directly.** Highest-leverage area: the evaluator hard-rejects cross-task interference. Ask at least one out-of-scope question even if scope seems obvious.
- **Acceptance criteria (per task)** — for each agreed task, the 2–4 **externally-checkable behaviours** the evaluator can judge against a diff. Push for observable specifics ("`cli export --format json` writes valid JSON to stdout" beats "export works"). Ask whether the user wants a **TDD flow**; if yes, fold test-first detail into the criteria, naming the repo's *actual* test runner from the scan (pytest, jest/vitest, `go test`, `cargo test`, …). Keep every criterion outcome-level — *what's true after the change*, not *which API the worker used*; mechanism (a property wrapper, `try?`, a type name) belongs nowhere in the artifact. The runner a TDD task names lives in its criteria, never in the Description. The criteria are the only contract Tilth has.
- **Risks & reviewer notes** — assumptions made while slicing, open questions, things to sanity-check before merging the branch. Becomes the overview's **Notes for the reviewer**.

The test for an anchored question: *could this have been asked before the scan?* If yes, it's generic — sharpen it.

**Examples of good interview behavior:**

> User: "Add a `priority` field to the todo CLI so I can sort by it."
> *(After scanning: an `add`/`list`/`done` CLI; items stored as `- [ ] <text>` lines; tests assert exact file contents.)*
> Good question: "Items are stored as `- [ ] <text>` lines. Where should priority live? [Suffix `!high` / Prefix `[P1]` / Sidecar metadata file / Other]"
> Bad question: "How should priority work?" *(Generic — the scan surfaced three plausible encodings; ask which.)*

> User: "It's a brand-new repo — build me a URL-shortener service."
> *(The repo has only a README and a git init.)*
> Good move: "Greenfield, so before the shorten/redirect endpoints I'll add a scaffold task (project skeleton + entry point in your chosen stack) and a test-harness task, then build features on top. Which stack — Node/Express, Python/FastAPI, Go? — so the tasks name real paths and the right test runner."

### 4. Surface blockers and contradictions as you go

Flag these as soon as you spot them — don't bury them in the tasks:

- **Scope too big for one feature.** More than ~8 tasks, or tasks fanning across unrelated subsystems → suggest splitting into separate feature dirs, or de-scoping to a coherent first slice.
- **A slice contradicts the code.** "Task 3 assumes a `Renderer` class; the repo has `render()` as a free function. Refactor first, or rework the slice?"
- **An acceptance criterion isn't checkable.** Push for observable behaviour the evaluator can confirm on a diff.
- **A slice is too coarse** (touches many modules) → push to sub-slice.
- **Refactor with no existing tests** to ratchet against → with no codified gate, "behaviour preserved" has nothing to check it. Suggest a first task that captures current behaviour in tests, or name in each task's criteria the command that must still pass.

Frame contradictions as questions. If a blocker changes feasibility, pause and ask how to proceed.

### 5. Wrap up the interview

When you have enough, say so: "I think I have enough — let me write the feature. I'll flag anything I'm unsure about in the chat summary." Unknowns go in the overview's **Notes for the reviewer** and the chat summary, not in more turns.

### 6. Confirm the feature directory, IDs, and slugs

Via `AskUserQuestion`, confirm:

- **Feature directory path** — propose `<repo>/.tilth/<feature-slug>/` (derive the slug from the feature). This is the path the user will pass to `tilth run`. Any path works, but `.tilth/<feature>/` is the convention.
- **Task IDs + titles** — the proposed contiguous `T-NNN` list (continuing from the highest existing id if you're adding to a feature, else `T-001`).
- **Filename slugs** — `T-NNN-<slug>.md` (the slug is humans-only, from the title).

Don't silently pick the directory or ids — duplicate ids silently break Tilth's scheduling.

### 7. Author the artifacts

Use [`references/tilth-tasks-template.md`](references/tilth-tasks-template.md) verbatim — same structures, same conventions.

This is where deep reading happens — but be clear what it's *for*. Now that the slice is locked, re-read the load-bearing modules to (a) **verify the real anchors** you'll name — the file/module/type the work lands in (`pkg/module.ext`), not its functions — (b) confirm any test runner you'll cite in criteria actually exists, and (c) sharpen the **acceptance criteria**. It is **not** for transcribing the implementation you just read into the Description — knowing the exact solution is precisely the trap (see the altitude ceiling below). The step-2 scan was for the interview; this read is for the anchors and the criteria.

Rules:

- Write the files into the feature directory directly: `overview.md` + one `T-NNN-<slug>.md` per task. **No `tasks/` subdirectory** — the files sit directly in the feature dir.
- **`overview.md` is required and non-empty.** Sections: a `#` feature title, `## Goal`, `## Context` (real paths), `## Scope boundaries` (in + out), `## Notes for the reviewer`. The whole text is injected verbatim into **both** the worker's and the evaluator's prompts (capped) — keep it tight and load-bearing.
- **Each `T-NNN-<slug>.md`:** frontmatter `id: T-NNN` (zero-padded, unique, `T-<digits>`) + `title:`, then `## Problem` (the *why* — lead with it), `## Description` (the *what*: outcome + constraints + the real file/module/type it lands in, worker's voice), and `## Acceptance criteria` (`- ` bullets, externally checkable). IDs contiguous, ordered by id. (Parser caveat: today only `## Description` reaches the worker — keep it self-sufficient until [tilth#47](https://github.com/AlteredCraft/tilth/issues/47) makes `## Problem` first-class.)
- **Altitude ceiling — the *what* and the *where*, never the *how*.** You've just deep-read the code, so you know the solution; resist transcribing it. **The deepest you may go is naming the file, module, or type the work lands in** (`Second/Workspace.swift`; "a `Workspace` method"; "a node type for the tree"). **Never** spell out a *new* function or type signature, a property wrapper, a control-flow construct (`do/catch`, `guard`), or a literal string the worker should emit — those are the worker's calls. *Referencing existing* symbols as orientation is fine ("reuse `flushNow(file:folderURL:)`"); the ban is on dictating *new* code shapes. The evaluator gates on the diff-vs-criteria, never on the Description's steps, so transcribed implementation adds zero gate strength while stripping the worker's agency and making you the blind implementer. State the outcome and the constraints; let the worker, which can actually build and run, choose the approach.
- **Self-audit every Description before writing it.** Strike any line that names a *new* signature, a property wrapper, a control-flow keyword, or a literal string. If striking it loses something that must hold, it was never a how-detail — restate it as an observable outcome under **Acceptance criteria**. A Description that has turned into a numbered or bulleted list of steps is the tell; collapse it back to a short outcome statement.
- **Adding to an existing feature?** Keep its files and `overview.md` verbatim, continue numbering, don't overwrite tasks already built.
- **No `status` field** — task files are read-only inputs; Tilth tracks status under `~/.tilth/sessions/<id>/`. Never write harness state.
- **Don't touch `AGENTS.md` / `CLAUDE.md`** — read-only context.
- **Acceptance criteria are observable outcomes, not mechanisms.** Each is a behaviour a reviewer can confirm against the diff ("renaming to a name that already exists shows the user a dismissible message instead of failing silently"), not "uses `.alert`". Keep mechanism — `.alert`, `try?`, a type name — out of the criteria too; that only relocates the *how*. The single concrete element a criterion may name is a real verification **command**, and only where the outcome is genuinely "the tests pass" or "it builds" — that is where a TDD task's runner lives.

**Example — the altitude of a task Description (this is the whole point):**

> Over-prescribed (the *how* — don't write this):
> *"In `Second/ContentView.swift`, add `@State private var errorMessage: String?` and a `.alert` bound to it. Wrap the `Workspace` calls in `do/catch`; on `WorkspaceError` show its message, else show "Couldn't complete that action.""*
>
> PRD-level (the *what* + *where* — write this):
> *"In `Second/ContentView.swift`, the create / rename / delete actions currently fail silently. Surface those failures to the user as a clear, dismissible message, honouring the repo `CLAUDE.md` rule that user-facing copy stays generic and exposes no internal detail. How the alert is built and how errors are caught is the worker's call."*

The second names only the file and the desired behaviour; every mechanism (`@State`, `.alert`, `do/catch`, the exact string) is the worker's to choose — pinned, *as behaviour*, in the acceptance criteria.

After writing, surface the chat summary (do **not** write it to disk):

- **TL;DR.** One line per task: `T-NNN: <title> → <one-sentence outcome>`.
- **Open Questions.** Anything you guessed at or the user was unsure about.
- **Blockers / contradictions surfaced.** Only if you flagged any.

### 8. Suggest next steps and stop

In chat — *do not* create them as artifacts:

- "Want a dry-run pass — read the overview + tasks together and check each acceptance criterion is something a reviewer could confirm against a diff?"
- "Ready to kick it off: `tilth run <repo>/.tilth/<feature>`?"
- "Pick an evaluator model first? Tilth recommends evaluator ≥ worker — it's the only quality gate this format has."

Then stop. This skill authors the feature directory only.

## Behavior to avoid

- **Don't skip the codebase scan.** Ungrounded tasks reference "the entrypoint" instead of real paths and waste the worker's fresh-context budget.
- **Don't read every file end-to-end in step 2.** Strategic scan; the deep read is step 7.
- **Don't assume a language/toolchain.** Anchor verification on the repo's real tools.
- **Don't batch the interview into a megaprompt.** One question, then decide the next.
- **Don't fabricate acceptance criteria the user didn't agree to** — write Open Questions instead.
- **Don't write a task with no acceptance criteria** — that collapses Tilth's gate to "the evaluator vibed it."
- **Don't transcribe the implementation into the task.** Naming a *new* enum, type signature, property wrapper, `do/catch` block, or literal UI string the worker should emit means you've crossed from specifying to implementing — blind, since you haven't compiled it. The altitude ceiling is the file/module/type; stop there. Pull back to `## Problem` + the desired outcome + checkable criteria, and let the worker choose the how.
- **Don't overwrite an existing feature directory.** Confirm append-vs-new first; preserve tasks already built.
- **Don't write harness state or touch `AGENTS.md`.** The skill's surface is the feature directory only.
- **Don't run the harness.** `tilth run` is the user's to invoke.

## When to refuse / redirect

- **Bug fix that fits one task** → "A full interview is overkill — want me to just write the single `T-NNN` task file directly?"
- **An existing feature just needs one more task in the same style** → "Faster to add the file to that feature dir directly than to run the interview — want that as a one-shot?"
- **"Run Tilth for me"** → "This skill only authors the feature directory; running it is `tilth run <repo>/.tilth/<feature>`, which is yours to invoke. Want me to author the feature first?"

(Greenfield/MVP projects are *in* scope — handle them per the MVP guidance in step 3, not as a refusal.)

If the user pushes back, defer and proceed.

## Reference files

- [`references/tilth-tasks-template.md`](references/tilth-tasks-template.md) — the structure of `overview.md` and the `T-NNN-<slug>.md` task files, the parser/validation rules, and the chat-summary template.
