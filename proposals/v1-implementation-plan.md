# Implementation plan: worker–evaluator dialogue (v1)

**Status:** In progress — **Phases 1–2 landed (2026-05-29).** Phases 3–6 not started.
**Author:** Sam (with Claude conversational pass)
**Date:** 2026-05-27 (last updated 2026-05-29)
**Related:** [`frictions-2026-05-26.md`](frictions-2026-05-26.md) (the inventory), [`v1-worker-evaluator-dialogue.md`](v1-worker-evaluator-dialogue.md) (the sketch this plan implements).

## Core-goal reminders

These ride above every phase. When a design choice trades against one of them, name the trade and decide explicitly — don't let it slip by.

- **Hyper observability.** This is the thing that differentiates Tilth from other agent harnesses. `tilth visualize` is the start of it. The working proof point so far: pointing an agent at a session's logs after a trial run and asking *"review this session, call out any anomalies or friction points"* has been invaluable in troubleshooting the harness itself. Every new structured artifact in v1 (verdicts, ledger entries, cases, `prompt_assembled` events) is also an observability artifact — design it to be readable after the fact, not just consumed in the loop. If a phase adds state that the loop reads but a post-run reviewer can't, that's a regression on the core goal regardless of what else it ships.

## Goal

Ship the v1 dialogue in **five independently shippable phases**. After each phase the harness still runs end-to-end on the demo; if we stop mid-plan, no phase leaves the loop in a broken intermediate state. Each phase is paired with a validation criterion and a concrete demo expectation so we can tell whether it earned its keep before moving on.

## Phasing principles

- **Every phase ends with a green demo run.** `uv run tilth ~/projects/tilth-demo` against the seeded demo must complete (or fail in the same shapes it fails today). No phase introduces a regression in the loop's basic ability to drive a session.
- **No backwards compatibility with v0 sessions.** A session started under Phase N can't necessarily be resumed under Phase N+1. The contract is "reset and re-seed across phase boundaries." This was decided in the sketch (*Not backwards-compatible with v0 sessions*) and applies to phases within v1 too.
- **Tests-for-Tilth-itself land with each phase.** Every phase that touches the loop, prompts, or session state ships with `tilth/tests/` coverage for the new behavior. Existing tests stay green.
- **Prompts and schemas are versioned but not migrated.** When we change the verdict schema or `system.md`, we bump a constant (`VERDICT_SCHEMA_VERSION`, etc.) and document the break; we don't write migrations.
- **Demo signals over synthetic tests.** Unit tests prove the wiring; the demo run proves the *behavior*. A phase isn't done until a fresh demo run shows the expected shift (e.g., richer rejection feedback, evaluator citing prior iterations). Synthetic tests are necessary but not sufficient.

## Prerequisites / out-of-scope

None of these block the dialogue; each is its own workstream.

- **[#20](https://github.com/AlteredCraft/tilth/issues/20) — Validator pluggability.** Required for non-Python workspaces; the demo is Python so v1 runs against the hard-coded ruff+pytest floor. Land #20 in parallel; the dialogue plan is mechanism-agnostic.
- **[#19](https://github.com/AlteredCraft/tilth/issues/19) — Hook lifecycle research.** Each v1 seam is wired directly (no general lifecycle). Revisit alongside v2.
- **[#13](https://github.com/AlteredCraft/tilth/issues/13) — Process isolation.** Orthogonal; v1 runs on the existing worktree model.
- **Halt authority.** Deferred (see sketch). Phases below don't add a halt verdict or terminal state.

---

## Phase 1 — Structured verdict

**Status: ✅ Landed 2026-05-29.** What shipped (and where it diverged from this plan):

- **Verdict as a tool call**, not prompt-only JSON — `tilth/verdict.py` owns `SUBMIT_VERDICT_TOOL`, `VERDICT_SCHEMA_VERSION`, `parse_verdict` (defensive: first valid `submit_verdict` call wins, corrupted siblings skipped), and `format_reject_feedback`. `LLMClient.chat` gained a `tool_choice` passthrough. Decided via [`probes/phase1_verdict_tool_call_probe.py`](probes/phase1_verdict_tool_call_probe.py); uses `tool_choice="auto"` to dodge the DeepSeek/OpenRouter double-emit quirk ([#21](https://github.com/AlteredCraft/tilth/issues/21)).
- **Events:** `evaluator_verdict` (replaces v0 `judge_verdict`), `evaluator_parse_error` (carries capped `raw_tool_calls` for faithful failure capture), `prompt_assembled` (worker + evaluator, the cross-cutting capture seam). `summary.py` → v2: `judge`→`evaluator`, structured `rejection_categories`, plus `prep_started_at` vs run `started_at`.
- **Two scope additions discovered during demo runs** (both folded into Phase 1):
    1. **Judge-prompt softening** — `judge.md`'s scope-creep rule was redrawn from "any file outside the AC → reject" to *cross-task interference → hard reject; everything else → use judgement*. See the *Scope addition* note below.
    2. **Observability parity** — the evaluator and self-improve steps now emit `model_call` events like the worker/interviewer (they previously emitted none — ~14% of model calls were invisible). Driven by a real diagnostic failure: a parse error's offending payload wasn't recoverable from the logs. Hyper-observability core-goal in action.
- **Tests:** 251 green (`test_evaluator_verdict_parsing`, `test_loop_structured_feedback`, `test_summary_rejection_category_counts`, `test_evaluator_raw_capture`, + updated `test_summary`).
- **Demo runs:** run 1 (`20260527-174931`) surfaced the strict-scope friction → fixed; run 2 (`20260528-074315`) cleared all 3 tasks, 0 rejects, working app.
- **Issues filed:** [#21](https://github.com/AlteredCraft/tilth/issues/21) (OpenRouter quirk, FYI), [#22](https://github.com/AlteredCraft/tilth/issues/22) (F1 demo-seed tracker, deferred to v2).
- **Known recurrence, not blocking:** `uv init` inside a session worktree still mutates the parent `pyproject.toml`'s `[tool.uv.workspace] members` despite `exclude = ["sessions"]` — F10, tracked separately ([#13](https://github.com/AlteredCraft/tilth/issues/13) is the isolation fix). Restore `pyproject.toml` after demo runs until then.

**Goal:** Replace the judge's free-text verdict with a parseable JSON object containing `verdict`, `rejection_category`, `concern`, `evidence`, `next_step`. No ledger yet; no case yet; no visibility changes.

**Why this phase first:** It's the smallest change with a real signal in the demo. Rejection feedback today is prose the worker has to interpret; structured `next_step` is the highest-leverage single change. It also forces the judge prompt rewrite and parsing infrastructure that later phases build on.

**Mechanism — verdict as a tool call, not a free-form JSON response.** Settled by probing the configured judge (`deepseek/deepseek-v4-pro` via OpenRouter — see [`probes/phase1_verdict_tool_call_probe.py`](probes/phase1_verdict_tool_call_probe.py)). Findings:

- The model reliably emits a `submit_verdict` tool call with schema-valid JSON in all probed scenarios (accept, reject, with and without `tool_choice` forcing).
- `tool_choice="auto"` (default) returned cleaner results than forcing — the system prompt + tool definition is sufficient to elicit the call. Forcing produced a clean first call *plus* a second call corrupted by DeepSeek's internal template syntax leaking through OpenRouter's normalisation (tracked in [#21](https://github.com/AlteredCraft/tilth/issues/21), FYI-only).
- Schema enums (`verdict`, `rejection_category`) were respected at the API layer.

So Phase 1 uses tool-call-shaped emission, not prompt-only JSON. This reuses the worker's existing tool-call/tool-result recovery pattern (`interview.py:138-184` for the seeder, the equivalent path in `loop.py` for the worker) and shows up in `events.jsonl` as a `tool_call` event for free — hits the hyper-observability goal without extra wiring.

**Scope:**
- `tilth/prompts/judge.md` — rewrite as the system prompt for a tool-using judge: "submit your verdict via `submit_verdict`; the tool call is the only acceptable response."
- `tilth/client.py` — extend `LLMClient.chat` to thread a `tool_choice` parameter through (the existing wrapper doesn't expose it). Default stays absent / "auto".
- `tilth/loop.py` — replace the prompt-only judge call with a tool-call call; defensively pick the first `submit_verdict` tool call whose arguments parse as JSON *and* schema-validate, ignoring any siblings. On no qualifying call, feed the parse error back as a `tool_result` and retry once; if a second attempt also fails, treat as a rejected verdict with `rejection_category: null` and a `concern` naming the parse failure so the run continues.
- `tilth/loop.py` — when verdict is `reject`, build the worker-visible feedback message from `concern + evidence + next_step` (structured template, not free prose).
- `tilth/session.py` — add `evaluator_verdict` event with structured payload; update the module docstring's event-type list.
- `tilth/summary.py` — surface `rejection_category` counts in the rollup so we can see patterns across the session.
- Verdict schema lives in code (where the tool definition is built), not in the prompt — `VERDICT_SCHEMA_VERSION` constant alongside it. Bump on shape changes.

**New event types:**
- `evaluator_verdict` — `{ verdict, rejection_category, concern, evidence, next_step }` payload.

**Tests for Tilth:**
- `tests/test_judge_verdict_parsing.py` — happy path, missing field, wrong enum, extra fields.
- `tests/test_loop_structured_feedback.py` — given a `reject` verdict, the next worker iteration sees the structured feedback template.
- `tests/test_summary_rejection_category_counts.py` — rollup includes counts.

**Validation:**
- Unit tests pass.
- A demo run that triggers at least one rejection logs an `evaluator_verdict` event with a populated `next_step`.
- The worker's next iteration after a rejection visibly acts on `next_step` (read the events.jsonl after a session — the worker's next message references the named file or symbol).

**Demo expectation:**
- Rejection feedback in `events.jsonl` is concrete file:symbol pointers, not "the implementation doesn't satisfy the acceptance criteria."
- Iteration count after a rejection drops modestly (hypothesis: 2–4 fewer iterations on average for tasks that involve at least one rejection).

**Risks:**
- Judge model has trouble producing valid JSON. Mitigation: tool-call enforcement at the API layer plus defensive parsing across `message.tool_calls` (described in *Scope* above). One retry via `tool_result` feedback on total parse failure; on second failure, the call is treated as a "reject with parse-failure concern" so the loop continues — never silently passes.
- Provider quirks beyond our control. The probe surfaced one: DeepSeek-via-OpenRouter emits a corrupted second tool call when `tool_choice` is forced. We avoid it by not forcing. If a different judge model on a different provider has its own quirk, the defensive-parse contract should catch it; if not, the probe script is retained under `proposals/probes/` so a new judge can be re-tested before adoption.

**Scope addition discovered during the first demo run (judge-prompt softening):**

The first Phase 1 demo run (`20260527-174931-25fa4c`) surfaced a friction the written scope didn't anticipate. Phase 1 upgraded the verdict *shape* (free-text → structured tool call) but inherited v0's **scope-creep rule unexamined**: *any file in the diff not enumerated in the AC → hard reject*. On the demo's T-001 (which authorises `uv init`), that rule false-positived on `README.md` and `.python-version` — universal project-hygiene artefacts a human reviewer would never flag — bundling them with a legitimate reject (a cross-task edit to `tests/test_t003_persist.py`). The worker couldn't separate the signal from the noise and thrashed for ~12 iterations (incl. an attempted `ruff` exclude — F12 gaming).

The fix shipped as part of Phase 1: **`tilth/prompts/judge.md` redraws the bright line at *cross-task interference*** (modifying another task's `test_t<NNN>_*.py` or named files — the gaming backstop the strict rule actually existed for) and **delegates everything else to the model's judgement** (hygiene/tooling/scaffolding collateral is accepted unless its *content* is wrong; genuinely unrelated work and dead scaffolding stubs are still rejected). The anchoring test in the prompt: *"would a careful human reviewer be bothered by this file being here?"* — not *"is this file enumerated in the AC?"*.

**Meta-lesson worth carrying forward (applies to every phase that writes a prompt rule):** every constraint we add to a prompt is a piece of judgement we are declining to delegate to the model. The strict scope rule was added when worker *pre-emption* was the live failure; by Phase 1 the live failure had shifted to over-strict reject loops, and the rule was mis-calibrated for the new situation. When the failure shape shifts, re-examine the constraints — don't just add more. (Same spirit as the Anthropic harness piece's *"re-examine harnesses with new models."*) The seed-side half of this friction (the demo T-001 contract is underspecified — F1) is tracked separately in [#22](https://github.com/AlteredCraft/tilth/issues/22) and deferred to v2 contract negotiation; v1 compensates at run time via the judge softening above, it does not make the seed self-consistent.

---

## Phase 2 — Per-task ledger

**Status: ✅ Landed 2026-05-29.** What shipped:

- **Ledger I/O on `Session`** — `ledger_dir`, `append_ledger_entry` (auto-stamps `ts`), `read_ledger(task_id, limit)`. Plain files at `sessions/<id>/ledger/<task_id>.jsonl`; a resumed session (`Session.wake`) reads the prior run's ledger transparently. Each append mirrored by a lightweight `ledger_appended` event (audit trail); the files are the evaluator's read path.
- **`workspace.task_diff_summary`** — compact `path (+a -d); ...` from `git diff --numstat`, stored per entry so the evaluator sees what changed at a prior iteration without re-reading the diff.
- **`verdict.format_ledger_section`** — pure renderer, oldest-first; empty input → `""` (no section on the first call).
- **`loop._judge_task`** — reads last-`LEDGER_INJECT_LIMIT`(=5) entries *before* this call's verdict is appended, so a call only sees iterations that preceded it. `case` stays `null` until Phase 3.
- **`judge.md`** — "Prior iterations on this task" guidance (focus on what's new, escalate don't repeat, don't anchor on a prior reject).
- The injected section rides the existing `prompt_assembled` capture, so a post-run reviewer sees exactly what memory the evaluator had each call — observability for free.
- **Tests:** 266 green (`test_ledger_io`, `test_evaluator_sees_ledger`, `test_ledger_resume`).
- **Demo validation** (`20260529-113158`): two tasks rejected then recovered; the evaluator's accept verdict on the retry explicitly referenced the resolved prior reject (*"the prior scope_creep issue is resolved…"*) — the memory feature demonstrably used, not just written. The same run surfaced a **seed contradiction** (T-001 pins `main([])==0` as a permanent regression test; T-002 makes `main([])` non-zero → the validator ratchet forced the worker to rewrite T-001's seed test, which the evaluator accepted by improvising a completed-vs-future distinction over its own hard rule). Filed as [#23](https://github.com/AlteredCraft/tilth/issues/23); root cause is seeder-side (F1/F2/F9), deliberately *not* fixed by weakening the cross-task rule. Clean illustration of the sketch's prediction: v1 *surfaces* F1 via the ledger/verdicts but has no authority to halt it.

**Goal:** Persist evaluator iterations to `sessions/<id>/ledger/<task_id>.jsonl` and inject the recent history into the evaluator's user message on every call. Evaluator gains memory; worker still doesn't see the ledger.

**Why this phase second:** The structured verdict from Phase 1 is the unit the ledger stores. Building the ledger before Phase 1's schema would be premature; building it after Phase 3 means the worker would see an empty ledger on its first read.

**Scope:**
- `tilth/session.py` — `ledger_dir(session_id)` helper; `append_ledger_entry(task_id, entry)`; `read_ledger(task_id, limit)`.
- `tilth/loop.py` — after each evaluator call (Phase 1 already produces the verdict), append entry `{ iter, ts, diff_summary, case: null, verdict }`. `case` stays `null` until Phase 3.
- `tilth/loop.py` — at the start of each evaluator call, read last N ledger entries and inject them under `## Prior iterations on this task` in the evaluator's user message.
- `tilth/prompts/judge.md` — add a paragraph telling the evaluator how to use the prior-iterations section ("don't re-litigate, escalate feedback shape if the same `rejection_category` appears repeatedly").

**Ledger size cap (OQ #1 from the sketch):** Start with **last 5 entries** as the default. Token-budgeted truncation is a follow-up if we see prompt size issues — defer until the demo gives us a real signal.

**New event types:**
- `ledger_appended` — `{ task_id, iter, verdict_summary }` payload. Lightweight; the full content is in the ledger file.

**Tests for Tilth:**
- `tests/test_ledger_io.py` — append, read, ordering, cap-at-N.
- `tests/test_evaluator_sees_ledger.py` — given a ledger with 3 prior entries, the next evaluator call's user message contains the prior-iterations section.
- `tests/test_ledger_resume.py` — after a `tilth resume`, ledger reads pick up where the prior run left off.

**Validation:**
- Unit tests pass.
- A demo run with at least one task that rejects ≥2 times produces a ledger file with the expected entries.
- Inspecting the second-or-later evaluator user message (via `events.jsonl` payload capture, see *Cross-cutting* below) shows the prior-iterations section is populated.

**Demo expectation:**
- On a task that takes 3+ iterations: the evaluator's verdicts after iter 2 reference prior iterations in `concern` ("as in iter 1, the same file is being touched outside scope...") rather than restating from scratch.
- The "judge re-verifies after the fix is already in" pattern (issue [#11](https://github.com/AlteredCraft/tilth/issues/11)) should ease — evaluator can see "this is the third attempt at the same surface" and shift its feedback.

**Risks:**
- Prompt size grows uncontrollably on tasks with many iterations. Mitigation: 5-entry cap is the floor; if size becomes a problem, drop `diff_summary` from older entries first.
- Evaluator over-anchors on early rejections and refuses to update when the worker has actually fixed the issue. Mitigation: the prompt explicitly says "focus on what's new in this iteration"; watch for this in the demo and tighten if needed.

---

## Phase 3 — Worker `submit_case` and advocate framing

**Goal:** Replace "worker stops calling tools and responds with a summary" with "worker calls `submit_case` with structured fields." Worker's `system.md` reframes from tool-user-that-stops to advocate-presenting-a-case.

**Why this phase third:** The case is what the evaluator wants to see; building the evaluator side first (Phases 1–2) means we can compare evaluator behavior with and without the case. It also means Phase 3 is the only phase that touches the worker.

**Scope:**
- `tilth/tools/submit_case.py` — new tool. Schema:
    ```
    summary: str (1–3 sentences)
    ac_coverage: list[{ criterion, addressed_by, evidence }]
    work_arounds: list[str]
    uncertainties: list[str]
    ```
    Schema validation on call; malformed cases return a parse-error tool result (existing pattern).
- `tilth/tools/__init__.py` — register `submit_case`.
- `tilth/prompts/system.md` — rewrite. "Done" is "I submitted a case", not "no more tool calls". Tool list is still owned by the registry (per CLAUDE.md invariant) — system.md describes the *meaning* of `submit_case`, not the schema.
- `tilth/loop.py` — the inner tool-use loop exits when `submit_case` is called (replaces the "no more tool calls" termination).
- `tilth/loop.py` — pass the submitted case to the evaluator alongside the diff and ledger.
- `tilth/loop.py` — ledger entries now include the full `case` (no longer `null`).
- `tilth/prompts/judge.md` — expanded to read the case; the verdict's `evidence` field is expected to cite the case's claims where applicable.

**Tests for Tilth:**
- `tests/test_submit_case_schema.py` — happy path, missing field, wrong shape (e.g., `addressed_by` as free prose instead of `file:symbol`).
- `tests/test_loop_exits_on_submit_case.py` — worker calling `submit_case` terminates the iteration cleanly.
- `tests/test_ledger_includes_case.py` — Phase 2's ledger entries now have populated `case`.

**Validation:**
- Unit tests pass.
- A demo run shows the worker calling `submit_case` at task end (not before). Inspecting the call payload, the four fields are populated meaningfully.
- The evaluator's verdict in the same iteration references at least one claim from `ac_coverage` or `work_arounds`.

**Demo expectation:**
- The F4 disambiguation case (uv-init side-effect files) gets a named `work_arounds` entry from the worker, and the evaluator's verdict accepts or pushes back on that specific claim rather than re-deriving "is this scope creep?" from the diff.
- Worker `system.md` is shorter or about the same length, not longer. Advocate framing is a *reframe*, not a *padding*.

**Risks:**
- Worker treats `work_arounds` as a free permission slip, listing everything as a "work-around." Mitigation: evaluator prompt is explicit ("treat `work_arounds` claims skeptically"); cap entries at a sensible number (e.g., 5); revisit OQ #2 if abuse shows up.
- Worker submits malformed cases repeatedly. Mitigation: parse-error-as-tool-result is the existing pattern; the model will recover. If a worker can't produce a valid case in 5 attempts, that's a signal something deeper is wrong (escalate to the iter cap as today).

---

## Phase 4 — Visibility expansion

**Goal:** Implement the visibility table from the sketch. Worker sees full PRD, `seed-meta.json`, own task's ledger. Evaluator sees full validator output content and matching seed test file content.

**Why this phase fourth:** Phases 1–3 build the *mechanism*; Phase 4 expands what *flows through* it. Doing visibility before the mechanism exists means adding context the loop has nowhere to use yet.

**Scope:**
- `tilth/memory.py` (or `tilth/loop.py` — whichever owns prompt assembly today) — worker's user message now includes:
    - Full PRD (collapsed format — task descriptions + ACs for each task, including completed and not-yet-started).
    - `seed-meta.json` content under a `## Seed context (authored for humans)` section.
    - Own task's ledger (read from Phase 2's read path) under `## Prior iterations on this task (from the evaluator)`.
- Evaluator's user message now includes:
    - Full validator output content (stdout/stderr), not just pass/fail bool.
    - Matching seed test file content (the `tests/test_t<NNN>_*.py` glob) inlined.
- `tilth/prompts/system.md` and `tilth/prompts/judge.md` — small additions describing the new context sections and how to use them.

**Tests for Tilth:**
- `tests/test_worker_prompt_contains_full_prd.py`.
- `tests/test_worker_prompt_contains_seed_meta.py`.
- `tests/test_worker_prompt_contains_own_ledger.py`.
- `tests/test_evaluator_prompt_contains_validator_output.py`.
- `tests/test_evaluator_prompt_contains_seed_test_file.py`.

**Validation:**
- Unit tests pass.
- A demo run shows the worker's user message size growing as expected; spot-check that the new sections are present and well-formed.
- F5/F9 (pre-empting future tasks) should noticeably decrease — worker now has reason to understand why it shouldn't.

**Demo expectation:**
- Scope-creep accusations on side-effect files drop further (combined with Phase 3's `work_arounds`).
- The "judge can't review seed test files" failure shape ([#16](https://github.com/AlteredCraft/tilth/issues/16)) should resolve — evaluator now has the seed test inline.
- Worker iteration count overall trends down (hypothesis: 20–40% reduction on multi-rejection tasks).

**Risks:**
- Prompt size explodes. Mitigation: `seed-meta.json` and other-task ACs are short by construction; the ledger is capped from Phase 2; validator output may need truncation if a test dumps a lot — add a per-field cap with a "(truncated)" marker if needed.
- Worker reads ahead into future tasks and pre-builds. Mitigation: the system prompt is explicit that the full PRD is *context*, not *work to do*; the evaluator catches scope creep into future-task territory via the existing `rejection_category == "scope_creep"`.

---

## Phase 5 — Self-improver reads the ledger

**Goal:** The existing self-improve step (`tilth/prompts/propose_learning.md` → `proposed-learnings.md`) reads the per-task ledger and the session-wide `rejection_category` counts to propose better-grounded learnings.

**Why this phase last:** Self-improve is a leaf in the loop's flow. It benefits from everything Phases 1–4 produce. Doing it last means we know the signal it's working from is real.

**Scope:**
- `tilth/loop.py` — when assembling the self-improve user message, include:
    - All ledger files from the session (read-only).
    - The `rejection_category` rollup from `summary.json`.
- `tilth/prompts/propose_learning.md` — small update: "you now have access to per-task rejection ledgers. If a pattern shows up across tasks (e.g., repeated `scope_creep` rejections in the same kind of code), propose a learning grounded in that pattern."

**Tests for Tilth:**
- `tests/test_self_improve_sees_ledger.py` — the self-improve prompt's user message contains the ledger summary.

**Validation:**
- Unit tests pass.
- A demo run that produces ≥2 rejections of the same `rejection_category` produces a `proposed-learnings.md` entry that names the pattern, not just one symptom.

**Demo expectation:**
- `proposed-learnings.md` after a session reads as *grounded suggestions* rather than *generic platitudes*. Useful smell-test: would a human reading the entry know which iteration in the session triggered it?

**Risks:**
- Low — this phase is additive context to an existing step.

---

## Phase 6 — Visualizer ledger panel (deferred)

Flagged in the sketch as out-of-scope-for-v1-implementation. Worth a follow-up issue once Phases 1–5 land — the ledger is rich data and the visualizer is the natural place to render it. Not on the v1 critical path.

---

## Cross-cutting concerns

### Capturing prompts in `events.jsonl` for validation

Several of the validation criteria above require inspecting the *user message* the worker or evaluator received in a given iteration. Today `events.jsonl` logs tool calls and high-level loop transitions, not the full prompt payloads. Add a `prompt_assembled` event (worker and evaluator) with the assembled user message under a size cap — enough to see structure, not enough to bloat the log. This lands as part of Phase 1 (the first phase that needs it).

### Demo-run protocol per phase

After completing each phase, run:
```bash
uv run tilth reset                          # clean slate
uv run tilth prep-feature ~/projects/tilth-demo
uv run tilth run          ~/projects/tilth-demo
```
on a fresh seed. Capture `events.jsonl`, `summary.json`, and the ledger directory. Compare against the prior phase's capture. The phase isn't done until the *behavior shift* described in *Demo expectation* is visible in the capture.

### Open questions, assigned to phases

The sketch's open questions land here:

| Open question | Phase | Note |
|---|---|---|
| OQ #1 — Ledger size cap | Phase 2 | Start with last-N=5; revisit if prompt size grows uncomfortably |
| OQ #2 — `work_arounds` discipline | Phase 3 | Cap at 5 entries; evaluator-prompt skepticism; revisit after demo |
| OQ #3 — AC coverage gaps | Phase 3 | Auto-reject if `ac_coverage` is incomplete vs. the PRD entry |
| OQ #4 — Self-improver and ledger | Phase 5 | Direct implementation target |
| OQ #5 — Cross-task evaluator memory | Out of scope | v1.5 question; flagged in sketch |
| OQ #6 — Token budget | Continuous | Measure per phase; no hard target |
| OQ #7 — `submit_case` failure modes | Phase 3 | Parse-error-as-tool-result, existing pattern |
| OQ #8 — Seeder updates | Phase 3 evaluation | Run the demo; if AC phrasing breaks the dialogue, revisit |
| OQ #9 — Visualizer | Phase 6 | Deferred |

### Tilth self-tests

Today `tilth/tests/` is light. v1 doubles the surface area. Aim to land each phase with tests that would catch a regression in its specific behavior, not tests that re-verify the existing code. Use parameterized tests where input shapes vary (e.g., the verdict-parsing tests).

---

## What we'll have after Phase 5

- A worker that submits a structured case naming what it did, why, and what's uncertain.
- An evaluator with memory across iterations of a task, producing structured rejections with concrete remediation pointers.
- Visibility for both roles that lets them reason about the work, with the gaming-defense wall preserved against harness-mechanics state.
- A per-task ledger that is both runtime memory for the evaluator and the **data instrument** for the deferred halt decision.
- A grounded self-improve loop that proposes learnings from observed patterns, not from priors.

## What's left for v1.5 / v2

- **Halt authority.** Designed against real ledger shapes from v1 sessions.
- **Contract negotiation at seed time.** Anthropic-style; prevents F1 instead of surfacing it.
- **Richer mechanical checks.** OpenAI-style custom-lints-with-remediation; depends on [#20](https://github.com/AlteredCraft/tilth/issues/20).
- **Hook lifecycle.** Tracked in [#19](https://github.com/AlteredCraft/tilth/issues/19); revisit when v2 mechanical checks make the natural shape obvious.
- **Cross-session evaluator memory.** OQ #5; needs more thought.
- **Process isolation.** Tracked in [#13](https://github.com/AlteredCraft/tilth/issues/13).
- **Visualizer panel for the ledger.** Phase 6 above.

## Rough sequencing note

Each phase is small enough to land as 1–3 PRs. Phases 1 and 2 can probably be one PR each. Phase 3 is the biggest (worker `system.md` rewrite + new tool + loop changes) and may want to be 2–3 PRs. Phase 4 is multiple small PRs (one per visibility surface). Phase 5 is one PR. No phase needs to land as a single mega-PR.
