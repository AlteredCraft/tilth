# Implementation plan: worker–evaluator dialogue (v1)

**Status:** **Phases 1–5 landed (Phases 1–3 2026-05-29; Phases 4–5 2026-05-30)** — the v1 worker–evaluator dialogue is implemented end-to-end. Phase 6 (visualizer ledger panel) is split out to [#26](https://github.com/AlteredCraft/tilth/issues/26); it was always off the v1 critical path.
**Author:** Sam (with Claude conversational pass)
**Date:** 2026-05-27 (last updated 2026-05-30)
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

## Conventions established in Phases 1–2 (the remaining phases inherit these)

Phases 1–2 set patterns the later phases should reuse rather than reinvent. These are load-bearing now, not aspirational:

- **Structured model output is a tool call, parsed defensively.** The settled shape (Phase 1, `tilth/verdict.py`): a schema lives in code as an OpenAI tool definition with a `*_SCHEMA_VERSION` constant; a parser iterates `message.tool_calls`, takes the first that names the tool, parses JSON, value-local-normalizes (e.g. `""`→`null`, *never* a cross-field heuristic), validates with a single focused error, and on failure returns an error string the loop feeds back as a `tool_result` for one retry. **Phase 3's `submit_case` must mirror `parse_verdict`, not invent a new path.** Don't reach back to the interview's older `_parse_args` recovery as the template — `verdict.py` is the canonical one now.
- **Observability parity is an invariant, not a phase.** Every model-calling site emits a `model_call` event (worker, evaluator, self_improve, interview — `phase` tags non-worker calls). Every structured artifact the loop *acts on* must be reconstructable from `events.jsonl` after the fact: failing payloads captured raw (capped) on the error event, assembled prompts captured via `prompt_assembled`. A new phase that adds a model call or a consumed artifact without this is a regression on the core goal. (When Phase 5 adds context to self-improve, also emit a `prompt_assembled` for it — today only worker + evaluator do.)
- **Soften rules toward model judgement; keep only the gaming backstop hard.** Phase 1's judge-prompt lesson: a blanket constraint ("any file outside the AC → reject") false-positived; we narrowed the hard reject to *cross-task interference* and delegated the rest to judgement. Every constraint a prompt adds is judgement we're declining to delegate — when a later phase writes prompt rules (Phase 3's `system.md` rewrite, Phase 4's visibility guidance), prefer "here's the context, use judgement" over enumerated allow/deny lists, and keep hard rules only where gaming is the documented risk.
- **`tilth/verdict.py` is the home for evaluator-side schemas; the worker-side `case` is a sibling.** Recommendation for Phase 3: a new `tilth/case.py` mirroring `verdict.py`'s shape (`SUBMIT_CASE_TOOL`, `CASE_SCHEMA_VERSION`, `parse_case`, `_validate`, `_normalize`, `format_*`), keeping the two actors' artifacts in separate modules.

## Prerequisites / out-of-scope

None of these block the dialogue; each is its own workstream.

- **[#20](https://github.com/AlteredCraft/tilth/issues/20) — Validator pluggability.** Required for non-Python workspaces; the demo is Python so v1 runs against the hard-coded ruff+pytest floor. Land #20 in parallel; the dialogue plan is mechanism-agnostic.
- **[#19](https://github.com/AlteredCraft/tilth/issues/19) — Hook lifecycle research.** Each v1 seam is wired directly (no general lifecycle). Revisit alongside v2.
- **[#13](https://github.com/AlteredCraft/tilth/issues/13) — Process isolation.** Orthogonal; v1 runs on the existing worktree model.
- **Halt authority.** Deferred (see sketch). Phases below don't add a halt verdict or terminal state.

---

## Phase 1 — Structured verdict

**Status: ✅ Landed 2026-05-29.** What shipped (and where it diverged from this plan):

- **Verdict as a tool call**, not prompt-only JSON — `tilth/verdict.py` owns `SUBMIT_VERDICT_TOOL`, `VERDICT_SCHEMA_VERSION`, `parse_verdict` (defensive: first valid `submit_verdict` call wins, corrupted siblings skipped), and `format_reject_feedback`. `LLMClient.chat` gained a `tool_choice` passthrough. Decided via [`probes/phase1_verdict_tool_call_probe.py`](../probes/phase1_verdict_tool_call_probe.py); uses `tool_choice="auto"` to dodge the DeepSeek/OpenRouter double-emit quirk ([#21](https://github.com/AlteredCraft/tilth/issues/21)).
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

**Mechanism — verdict as a tool call, not a free-form JSON response.** Settled by probing the configured judge (`deepseek/deepseek-v4-pro` via OpenRouter — see [`probes/phase1_verdict_tool_call_probe.py`](../probes/phase1_verdict_tool_call_probe.py)). Findings:

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

**Status: ✅ Landed 2026-05-29.** What shipped (matches the scope below; deltas noted):

- **`tilth/case.py`** — worker-side mirror of `verdict.py`: `SUBMIT_CASE_TOOL`, `CASE_SCHEMA_VERSION`, `parse_case` (defensive, first-valid-wins, value-local normalize), `_validate`/`_normalize`, `format_case_section`. `work_arounds` capped at 5 (OQ #2). `addressed_by` uses a *lenient* pointer check — rejects only clear prose (terse pointers like `main() in __main__` pass), per the soften-toward-judgement convention.
- **`tilth/loop.py`** — `SUBMIT_CASE_TOOL` offered in the worker's `tools=` list and **intercepted** in `_run_task` (not a `REGISTRY`/`dispatch` tool). Done-signal flipped: "no tool calls" → valid `submit_case`. `_partition_worker_tool_calls` runs worktree tools first; `_answer_case_calls` answers every tool call before the next model call; validator-fail and reject feedback delivered as the `submit_case` tool_result; stop-without-case → `WORKER_NO_CASE_NUDGE`. Case threaded into `_judge_task` (injected as `## Worker's case`) and stored in the ledger via `_build_ledger_entry` (`case` no longer null). **Bonus:** guarded the worker's worktree tool-arg `json.loads` (was an unguarded latent crash).
- **Prompts** — `system.md` rewritten to advocate framing; `judge.md` gained a "Worker's case" section (verify `ac_coverage` against the diff, engage `work_arounds` skeptically, case never overrides the floor).
- **Events** — new `case_parse_error` (raw capture, parity with `evaluator_parse_error`); `submit_case` logged as a `tool_call`. `session.py` docstring updated. Stale "stops calling tools" termination text fixed in the loop docstrings + `docs/architecture/overview.md` + `docs/getting-started/running-the-demo.md`.
- **Tests:** 292 green (`test_case_parsing`, `test_worker_case_loop`, `test_ledger_includes_case`). The two `test_loop_exits_on_submit_case`-style behaviours are covered via the factored helpers (`_partition_worker_tool_calls`, `WORKER_NO_CASE_NUDGE`), matching the suite's altitude (loop tests exercise factored decisions, not a live model).
- **Demo validation** (`20260529-134013`, a *fresh* seed — not the literal #23 seed, but the same F4 shape arose organically): on T-001 the worker submitted a case naming a cross-task test edit as a `work_around` → evaluator **hard-rejected** `scope_creep` (named-but-illegitimate); the worker then found a `per-file-ignore` in its *own* `pyproject.toml`, named *that* as a work-around → evaluator **accepted**. The F4/#23 disambiguation became an explicit, logged negotiation — the headline win. Zero `case_parse_error`s; premature submits (iters 12, 20) caught by validators **without burning a judge call**.
- **Cost finding (orthogonal, filed as [#25](https://github.com/AlteredCraft/tilth/issues/25)):** T-001 burned 27 iters / 226k tokens, ~15 on a ruff dance — `run_ruff` lints workspace-wide while `run_pytest` is task-filtered (F5), forcing the per-file-ignore. Phase 3 made the workaround *declared and adjudicated* rather than hidden, but the root cause is validator-side, not the dialogue. Inflates OQ #6.

**Goal:** Replace "worker stops calling tools and responds with a summary" with "worker calls `submit_case` with structured fields." Worker's `system.md` reframes from tool-user-that-stops to advocate-presenting-a-case.

**Why this phase third:** The case is what the evaluator wants to see; building the evaluator side first (Phases 1–2) means we can compare evaluator behavior with and without the case. It also means Phase 3 is the only phase that touches the worker.

**`submit_case` is a control-flow tool, not a workspace tool.** This is the subtlety the original scope under-stated. The existing registry (`tilth/tools/__init__.py`) is for tools that *operate on the worktree* and return a string result via `dispatch`. `submit_case` is a *terminal signal* — calling it ends the worker's turn and hands off to validators+evaluator. It must be recognised in the loop *before* normal `dispatch`, validated as a structured payload, and either accepted (→ terminate) or fed back as a parse error (→ retry, same turn). So it does **not** go in the `REGISTRY`; its schema is offered to the worker via the `tools=` list but it's intercepted in `loop._run_task`, parallel to how the evaluator's `submit_verdict` is intercepted, not dispatched.

**Scope:**
- `tilth/case.py` — **new module mirroring `verdict.py`** (per the conventions block above): `SUBMIT_CASE_TOOL` (OpenAI tool def), `CASE_SCHEMA_VERSION`, `parse_case(msg) -> (case|None, err|None)`, `_validate`, `_normalize`. Schema fields:
    ```
    summary: str (1–3 sentences)
    ac_coverage: list[{ criterion, addressed_by, evidence }]   # addressed_by is a file:symbol pointer, not prose — validate the shape
    work_arounds: list[str]                                     # cap at 5 (OQ #2)
    uncertainties: list[str]
    ```
    Malformed cases return a parse-error string the loop feeds back as a `tool_result` — the **`parse_verdict` pattern**, not the interview's `_parse_args`.
- `tilth/loop.py` — offer `SUBMIT_CASE_TOOL` in the worker's `tools=` list (alongside the registry schemas); intercept a `submit_case` tool call in `_run_task` and treat it as the done-signal. **Termination flips:** today "done" = the model emits *no* tool calls (`loop.py:~1000`); under Phase 3 "done" = `submit_case` parses cleanly. Handle the two new edge cases explicitly: (a) model stops calling tools *without* `submit_case` → nudge it to submit one (mirror the interview's "stopped before write_seed" abort, but as a recoverable feedback message, not a hard abort); (b) `submit_case` arrives *alongside* other tool calls in the same turn → run the others first, then treat the case as terminal.
- `tilth/prompts/system.md` — rewrite to the advocate framing. "Done" is "I submitted a case", not "no more tool calls". Registry still owns the tool list (CLAUDE.md invariant 3) — system.md describes the *meaning* of `submit_case`, not its schema. Keep it short (see Demo expectation).
- `tilth/loop.py` — pass the parsed case to `_judge_task` alongside the diff and ledger; inject it into the evaluator prompt under a `## Worker's case` section (it rides the existing `prompt_assembled` capture for free).
- `tilth/loop.py` — the ledger entry's `case` field (null since Phase 2) now carries the parsed case.
- `tilth/prompts/judge.md` — expanded to read the case; the verdict's `evidence` should cite the case's claims where applicable, and `ac_coverage` completeness is checkable against the PRD entry (see OQ #3).

**Tests for Tilth:**
- `tests/test_case_parsing.py` — mirror `test_evaluator_verdict_parsing.py`: happy path, missing field, `addressed_by` as free prose instead of `file:symbol`, `work_arounds` over the cap, empty-string normalization, DSML-leakage sibling.
- `tests/test_loop_exits_on_submit_case.py` — `submit_case` terminates the turn; stopping *without* it nudges rather than silently completing.
- `tests/test_ledger_includes_case.py` — ledger entries now have a populated `case` (extends the Phase 2 ledger tests).

**Validation:**
- Unit tests pass.
- A demo run shows the worker calling `submit_case` at task end (not before). Inspecting the call payload, the four fields are populated meaningfully.
- The evaluator's verdict in the same iteration references at least one claim from `ac_coverage` or `work_arounds`.

**Demo expectation:**
- The F4 disambiguation gets a named `work_arounds` entry from the worker, and the evaluator's verdict accepts or pushes back on that specific claim rather than re-deriving "is this scope creep?" from the diff. **Two concrete fixtures we already have from Phase 1–2 demo runs:** [#22](https://github.com/AlteredCraft/tilth/issues/22) (uv-init collateral — `README.md`/`.python-version`) and [#23](https://github.com/AlteredCraft/tilth/issues/23) (the worker rewriting T-001's seed test because T-002 supersedes its `main([])` behaviour). #23 is the sharper test: under Phase 3 the worker should *name* "I edited `test_t001_scaffold.py` because T-002's AC changes `main([])` from the T-001 stub behaviour" as a `work_arounds`/`uncertainties` entry, and the evaluator should engage that specific claim — surfacing the seed contradiction as an explicit, logged disagreement instead of silently waving the edit through (which is what happened in `20260529-113158`).
- Worker `system.md` is shorter or about the same length, not longer. Advocate framing is a *reframe*, not a *padding*.

**Risks:**
- Worker treats `work_arounds` as a free permission slip, listing everything as a "work-around." Mitigation: evaluator prompt is explicit ("treat `work_arounds` claims skeptically"); cap entries at a sensible number (e.g., 5); revisit OQ #2 if abuse shows up.
- Worker submits malformed cases repeatedly. Mitigation: parse-error-as-tool-result is the existing pattern; the model will recover. If a worker can't produce a valid case in 5 attempts, that's a signal something deeper is wrong (escalate to the iter cap as today).

---

## Phase 4 — Visibility expansion

**Status: ✅ Landed 2026-05-30** ([`29c6f40`](https://github.com/AlteredCraft/tilth/commit/29c6f40)). All five visibility surfaces shipped in one pass; the robustness fix below was folded in after the demo surfaced it. What shipped (and where it diverged from this plan):

- **Worker visibility (`memory.build_user_prompt`)** — gained keyword params `prd=` and `own_ledger=` (defaults keep existing callers valid). Three new sections, all *about the work*, all riding the existing `prompt_assembled` capture:
    - **Full feature plan**, collapsed, framed as *context not a worklist*. **Divergence:** the current task is rendered as a one-line placement marker pointing at the detailed `## Your task` block below, rather than repeating its full description + ACs — caught while eyeballing the assembled prompt (it duplicated ~1 KB).
    - **Seed context** curated from `seed-meta.json` — only the feature-shaping fields (`tldr`, `scope_notes`, `blockers`, `open_questions`); interview bookkeeping (model, tokens, timestamps) is deliberately excluded as mechanics, not work.
    - **Own-task ledger** via the *reused* `verdict.format_ledger_section` with a header that names the source (`from the evaluator`). Payoff is on resume — empty on a task's first run, as predicted.
    - Manifest gained `full_prd` / `seed_meta` / `own_ledger` channels (observability parity). Per-field caps (`FULL_PRD_MAX_CHARS=6k`, `SEED_META_MAX_CHARS=4k`) are separate from the 16k `prompt_assembled` *log* cap.
- **Evaluator visibility (`loop._judge_task`)** — gained `results=`; the static "All objective validators PASSED" string is replaced with the **real validator output** (`_format_validator_section`, capped 4k), and **this task's seed test is inlined** (`_format_seed_test_section`, capped 6k). **Decision (with the user):** the seed test is read **worktree-current** — the exact file pytest ran — not the pristine seed commit; tampering is already caught via the diff, and the worktree version is what makes the `weak_test` judgement meaningful. Reads via the now-public `validators.task_test_glob` — one source of truth shared with the pytest filter. Resolves [#16](https://github.com/AlteredCraft/tilth/issues/16).
- **Prompts** — `system.md` gained a short "the full plan and seed context are *context*, not a worklist; address prior verdicts directly" paragraph; `judge.md` gained a "Reading the seed acceptance test" section (use it to ground `weak_test`) and an updated "you see only" list. Both kept judgement-framed, no enumerated rules.
- **Fixed in passing:** a stale Phase-3 leftover — the per-task worker prompt tail still said *"Stop calling tools and respond with a brief summary when done"*, directly contradicting the `submit_case` advocate framing. Now aligned.
- **Robustness fix folded in (the headline demo finding).** The first Phase 4 demo run (`20260530-073150-761226`) stalled on T-004: the worker model (deepseek-v4-flash via OpenRouter) began returning **empty 200s — no content, no tool calls, zero prompt+completion tokens** — at iter 7. The loop mistook each for "worker stopped without `submit_case`" and nudged a dead endpoint ~15× until manual interrupt (3/4 tasks done). This was a *latent Phase-3 robustness gap*, not a Phase-4 regression (iters 1–6 worked; the prompt was ~6k tokens). Empty calls cost 0 tokens, so the token cap never tripped; only the iteration cap (60) would have. Fix (same commit, mirroring how Phase 1 folded in judge-prompt softening discovered during *its* demo): `_is_empty_response` detection that **skips the history append** (an empty turn became a role-less `{}` message that poisoned every later request), retry-with-backoff then abort with a distinct `empty_responses` reason + an `empty_model_response` event, and a consecutive-nudge circuit breaker (`no_case`) for the genuinely-quiet-worker case. New terminal-failure reasons `empty_responses` / `no_case`.
- **Tests:** 329 green (was 292). New: `test_worker_prompt_contains_full_prd`, `_seed_meta`, `_own_ledger`; `test_evaluator_prompt_contains_validator_output`, `_seed_test_file`; `test_loop_empty_response_abort` (the empty-response and no-case backstops, driving `_run_task` with a fake client).
- **Demo validation:** the first run surfaced the empty-response stall (above); a re-run **after** the fix completed end-to-end. The wiring is proven on a real run — worker saw the full plan + seed context + (on resume) its own ledger; evaluator saw real validator output + the inlined seed test. The plan's F9-reduction and token-cost hypotheses were **not separately quantified** this session (F9 needs a pre-emption-prone seed; token cost stays confounded by the F5 ruff dance until [#25](https://github.com/AlteredCraft/tilth/issues/25)) — claim the wiring + #16, not the cost win, until measured on a controlled seed.

**Goal:** Implement the visibility table from the sketch. Worker sees full PRD, `seed-meta.json`, own task's ledger. Evaluator sees full validator output content and matching seed test file content.

**Why this phase fourth:** Phases 1–3 build the *mechanism*; Phase 4 expands what *flows through* it. Doing visibility before the mechanism exists means adding context the loop has nowhere to use yet.

**Ownership resolved (was a hedge in the original draft):** the worker's user message is assembled by `memory.build_user_prompt(task, worktree, session.root)` — Phase 4's worker-visibility additions go there. The evaluator's user message is assembled inline in `loop._judge_task` — its additions go there. Both already flow through `_log_prompt_assembled`, so the new context is captured for post-run review automatically.

**Scope:**
- `tilth/memory.py` (`build_user_prompt`) — worker's user message now includes:
    - Full PRD (collapsed format — task descriptions + ACs for each task, including completed and not-yet-started). Source: `_load_prd(session.root)` shape.
    - `seed-meta.json` content under a `## Seed context (authored for humans)` section.
    - Own task's ledger under `## Prior iterations on this task (from the evaluator)` — read via Phase 2's `session.read_ledger(task_id, limit)` (the read path already exists; reuse it, same cap as the evaluator). Note this widens the visibility wall: the worker now sees the evaluator's verdicts on its own task. That's "about the work," not "harness mechanics" — consistent with the sketch's F8 line — but call it out in the demo review.
- Evaluator's user message (`loop._judge_task`) now includes:
    - Full validator output content (stdout/stderr), not just pass/fail bool. (Today `validator_run` logs only `{name, passed}` — the content needs threading through from `validators.run_all`.)
    - Matching seed test file content (the `tests/test_t<NNN>_*.py` glob) inlined — resolves [#16](https://github.com/AlteredCraft/tilth/issues/16).
- `tilth/prompts/system.md` and `tilth/prompts/judge.md` — small additions describing the new context sections and how to use them. (Per the conventions block: "context + use judgement," not enumerated rules.)
- **Respect the capture cap.** `PROMPT_ASSEMBLED_CHAR_CAP`/`MODEL_RAW_ARGS_CHAR_CAP` are 16k today; validator output and inlined test files can be large, so apply a per-field cap with a `(truncated)` marker before injection (the *prompt* cap is separate from the *log* cap — don't let a noisy pytest dump blow the worker's context).

**Tests for Tilth:**
- `tests/test_worker_prompt_contains_full_prd.py`.
- `tests/test_worker_prompt_contains_seed_meta.py`.
- `tests/test_worker_prompt_contains_own_ledger.py`.
- `tests/test_evaluator_prompt_contains_validator_output.py`.
- `tests/test_evaluator_prompt_contains_seed_test_file.py`.

**Validation:**
- Unit tests pass.
- A demo run shows the worker's user message size growing as expected; spot-check that the new sections are present and well-formed.
- **F9** (no cross-task awareness → pre-empting future tasks) should noticeably decrease — the worker now sees the full PRD and understands *why* not to touch future-task surfaces. **NB: this does not fix F5.** The Phase 3 demo showed the expensive friction is the *ruff* asymmetry ([#25](https://github.com/AlteredCraft/tilth/issues/25)) — the worker is forced to touch/work-around future-task seed files because workspace-wide ruff flags them, regardless of how well it *understands* the PRD. Visibility addresses comprehension (F9); #25 addresses the validator scoping (F5). Don't expect Phase 4 alone to cut the T-001-style token cost — that needs #25.

**Demo expectation:**
- Scope-creep accusations on side-effect files drop further (combined with Phase 3's `work_arounds`).
- The "judge can't review seed test files" failure shape ([#16](https://github.com/AlteredCraft/tilth/issues/16)) should resolve — evaluator now has the seed test inline.
- Worker iteration count trends down *on F9-driven rejections* (reading-ahead, pre-emption). The F5 ruff dance is unaffected until #25 — so the 20–40% reduction hypothesis is only cleanly measurable once #25 lands or on seeds that don't trip workspace-wide lint.

**Risks:**
- Prompt size explodes. Mitigation: `seed-meta.json` and other-task ACs are short by construction; the ledger is capped from Phase 2; validator output may need truncation if a test dumps a lot — add a per-field cap with a "(truncated)" marker if needed.
- Worker reads ahead into future tasks and pre-builds. Mitigation: the system prompt is explicit that the full PRD is *context*, not *work to do*; the evaluator catches scope creep into future-task territory via the existing `rejection_category == "scope_creep"`.

---

## Phase 5 — Self-improver reads the ledger

**Status: ✅ Landed 2026-05-30** ([`b17991e`](https://github.com/AlteredCraft/tilth/commit/b17991e)). Closes [#9](https://github.com/AlteredCraft/tilth/issues/9). What shipped:

- **`loop._self_improve_session_context(session)`** (new) — assembles the cross-task signal: the session's rejection-category histogram and every task's evaluator ledger arc (reusing `verdict.format_ledger_section`). Returns `""` until there's something to show.
- **`loop._self_improve`** — injects that context into the user message and **emits a `prompt_assembled` event with `role="self_improve"`** (the observability-parity gap this phase was meant to close — worker + evaluator already did, self_improve didn't). One `span_id` across the call's events.
- **`session.ledger_task_ids()`** (new) — read-only `ledger/*.jsonl` enumerator that, unlike `ledger_dir()`, does *not* create the directory.
- **`tilth/prompts/propose_learning.md`** — a short "Session signal" section: ground a proposal in a *recurring* pattern, not a one-off.
- **Divergence from the scope below:** the histogram is rebuilt fresh via `summary.build_from_events(session.events_path)` rather than read from `summary.json`. `_self_improve` runs *before* the task-boundary `_refresh_summary`, so reading the file would lag by the just-finished task; rebuilding reuses the canonical rollup logic and stays current.
- **Tests:** 335 green (was 329). New `tests/test_self_improve_sees_ledger.py` — the context helper (histogram + per-task arcs + empty case), `ledger_task_ids` (sorted; doesn't create the dir), and `_self_improve` end-to-end asserting the `self_improve` `prompt_assembled` fires with the signal in it.
- **Demo validation:** the *wiring* is proven against a real session — rendering `_self_improve_session_context` over `20260530-073150-761226` produced `half_finished: 1, scope_creep: 2` plus the full per-task verdict arcs (the workspace-wide-ruff `scope_creep` recurrence, the committed-`todos.md` `half_finished`). The *behavior* (does the model turn that into a grounded learning rather than a platitude?) needs a live self-improve call on a session with ≥2 same-category rejections — **not separately quantified this session.** Claim the wiring + the signal quality, not the learning quality, until measured.
- **Known follow-up (uncapped, deliberately):** self_improve runs per-task and injects *all* ledgers each call, so the context grows with the session. Fine at demo scale; add a char cap / last-N-per-task if a long session bloats it.

**Goal:** The existing self-improve step (`tilth/prompts/propose_learning.md` → `proposed-learnings.md`) reads the per-task ledger and the session-wide `rejection_category` counts to propose better-grounded learnings.

**Why this phase last:** Self-improve is a leaf in the loop's flow. It benefits from everything Phases 1–4 produce. Doing it last means we know the signal it's working from is real. Closes [#9](https://github.com/AlteredCraft/tilth/issues/9) (self-improver gets no signal from judge rejections).

**Already in place (don't rebuild):** the `rejection_category` rollup already exists in `summary.json` as `evaluator.rejection_categories` (overall) and `tasks.<id>.evaluator.rejection_categories` (per-task) — shipped in Phase 1. The per-task ledger read path (`session.read_ledger`) shipped in Phase 2. Phase 5 is mostly *wiring existing data into the self-improve prompt*.

**Scope:**
- `tilth/loop.py` (`_self_improve`) — when assembling the self-improve user message, include:
    - All ledger files from the session. Phase 2 added `read_ledger(task_id)` but not an enumerate-all helper — add a small `session.ledger_task_ids()` (glob `ledger/*.jsonl`) or read the set from the PRD task ids, then `read_ledger` each.
    - The `evaluator.rejection_categories` rollup from `summary.json` (already computed — just read it).
    - **Emit a `prompt_assembled` event for self_improve** (today only worker + evaluator do — observability-parity invariant from the conventions block; `_self_improve` already emits `model_call` from Phase 1, so this closes the gap).
- `tilth/prompts/propose_learning.md` — small update: "you now have access to per-task rejection ledgers and the session's rejection-category counts. If a pattern shows up across tasks (e.g., repeated `scope_creep` rejections in the same kind of code), propose a learning grounded in that pattern."

**Tests for Tilth:**
- `tests/test_self_improve_sees_ledger.py` — the self-improve prompt's user message contains the ledger summary and the rejection-category rollup; a `prompt_assembled` event with `role="self_improve"` is emitted.

**Validation:**
- Unit tests pass.
- A demo run that produces ≥2 rejections of the same `rejection_category` produces a `proposed-learnings.md` entry that names the pattern, not just one symptom.

**Demo expectation:**
- `proposed-learnings.md` after a session reads as *grounded suggestions* rather than *generic platitudes*. Useful smell-test: would a human reading the entry know which iteration in the session triggered it?

**Risks:**
- Low — this phase is additive context to an existing step.

---

## Phase 6 — Visualizer ledger panel → split out to [#26](https://github.com/AlteredCraft/tilth/issues/26)

Always flagged as out-of-scope-for-v1-implementation, and now tracked as its own issue rather than inline here. The ledger is the richest artifact v1 produces and the visualizer is the natural place to render it, but it's a read-side nicety, not loop mechanics — off the v1 critical path.

**Scope now lives in [#26](https://github.com/AlteredCraft/tilth/issues/26):** (a) a per-task ledger panel showing the iteration arc (verdicts over time, diffs collapsible), and (b) dedicated renderers for the v1 event types that still fall through to `_render_unknown` — `prompt_assembled`, `ledger_appended`, `evaluator_parse_error`, `case_parse_error`, `empty_model_response`. (Phase 1's `_render_evaluator_verdict` is the model to follow.)

---

## Cross-cutting concerns

### Capturing prompts in `events.jsonl` for validation ✅ shipped (Phase 1)

Several validation criteria require inspecting the *user message* an actor received in a given iteration. **Done in Phase 1:** the `prompt_assembled` event (worker at task-start `iter=0`; evaluator per judge call) carries the assembled user message capped at `PROMPT_ASSEMBLED_CHAR_CAP` (16k) with `chars`/`truncated` metadata. Phase 5 should extend it to self_improve (noted in that phase). Inspect with: `jq -r 'select(.type=="prompt_assembled" and .payload.role=="evaluator") | .payload.content'`.

### Demo-run protocol per phase

After completing each phase, on a fresh seed:
```bash
uv run tilth reset --yes && \
uv run tilth prep-feature ~/projects/tilth-demo && \
uv run tilth run          ~/projects/tilth-demo
git restore pyproject.toml      # F10: uv init inside the worktree re-pollutes [tool.uv.workspace] members
```
Then inspect (the post-run review is itself the hyper-observability proof point — point an agent at the session and ask "any anomalies?"):
```bash
SID=$(ls -t sessions | head -1)
jq -c 'select(.type=="evaluator_verdict")|.payload|{iter,verdict,rejection_category,next_step}' sessions/$SID/events.jsonl
jq -c 'select(.type=="tool_call" and .payload.tool=="submit_case")|.payload.args' sessions/$SID/events.jsonl   # Phase 3+
jq -c '{iter,diff_summary,case,v:.verdict.verdict}' sessions/$SID/ledger/*.jsonl     # Phase 2+
jq '.evaluator' sessions/$SID/summary.json
```
The phase isn't done until the *behavior shift* in *Demo expectation* is visible.

> **`prep-feature` is interactive** (TTYFrontend `ask_user`) — it can't be driven from a headless/background shell, and re-running it every phase is the slow link in this loop. [#24](https://github.com/AlteredCraft/tilth/issues/24) (`tilth reset --to-seed`) tracks a destructive rewind-to-post-seed flag so the same committed seed can be re-`run` without re-interviewing. Until it lands, a human drives `prep-feature` and pastes the run summary back for review.

**Caveat — the clean seed may not exercise the feature.** Phases 2–5 only light up on a task that **rejects** (ledger memory, case disagreement, rejection-category learnings). A zero-reject run proves wiring, not behaviour. If a clean run produces no rejections, deliberately exercise the path — a harder/contradictory seed, or inspect a prior run that did reject (e.g. `20260529-113158`). Don't mark a phase "demo-validated" off a run that never triggered its mechanism.

### Open questions, assigned to phases

The sketch's open questions land here:

| Open question | Phase | Note |
|---|---|---|
| OQ #1 — Ledger size cap | Phase 2 | ✅ **Resolved:** `LEDGER_INJECT_LIMIT=5`. No prompt-size issue seen in the Phase 2 demo; token-budgeted truncation deferred until one appears. |
| OQ #2 — `work_arounds` discipline | Phase 3 | ✅ **Shipped + observed:** cap at 5 + evaluator-prompt skepticism. In `20260529-134013` the worker did *not* abuse it as a permission slip — it named a real cross-task edit and the evaluator rejected it anyway (named ≠ excused). Holding; revisit if a later run shows relabel-creep. |
| OQ #3 — AC coverage gaps | Phase 3 | **Not auto-enforced (deliberate).** `judge.md` instructs "a missing AC = `acceptance_gap`" but it's a judgement call, not a schema check — consistent with soften-toward-judgement. The mechanical auto-reject-on-incomplete-coverage option is deferred (would need the PRD's AC list threaded into the case validator); revisit if the evaluator misses a gap in practice. |
| OQ #4 — Self-improver and ledger | Phase 5 | ✅ **Resolved:** self_improve reads every task's ledger + the rejection histogram (rebuilt fresh from events, not the lagging `summary.json`). Closes [#9](https://github.com/AlteredCraft/tilth/issues/9). |
| OQ #5 — Cross-task evaluator memory | Out of scope | v1.5 question; flagged in sketch |
| OQ #6 — Token budget | Continuous | **Data points:** clean 3-task run ≈ 238k (`20260528-074315`); 2 rejections ≈ 562k (`20260529-113158`); Phase 3 run ≈ 407k (`20260529-134013`). Cost is dominated by the **F5 ruff dance** ([#25](https://github.com/AlteredCraft/tilth/issues/25)) — T-001 alone was 226k/27 iters fighting workspace-wide lint, not the dialogue. The "better feedback nets fewer tokens" hypothesis is confounded by F5 until #25 lands; measure again after. |
| OQ #7 — `submit_case` failure modes | Phase 3 | ✅ **Resolved:** reuses `verdict.parse_verdict`. In `20260529-134013` there were **zero** `case_parse_error`s across 6 submissions; premature submits were caught by the validator floor (fed back as the submit_case tool_result, no judge call burned), not by parse failures. |
| OQ #8 — Seeder updates | Phase 3 evaluation | **Evidence in:** the demo seed *did* break the dialogue (#23 contradiction, #22 collateral). Phase 3's `work_arounds` lets the worker *name* the friction (confirmed in `20260529-134013`); the structural seeder fix (contract negotiation) stays v2. |
| OQ #9 — Visualizer | Phase 6 | ✅ **Split out:** filed as [#26](https://github.com/AlteredCraft/tilth/issues/26) (ledger panel + dedicated renderers). Off the v1 critical path. |

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
- **Visualizer panel for the ledger.** Tracked in [#26](https://github.com/AlteredCraft/tilth/issues/26).

## Rough sequencing note

Each phase is small enough to land as 1–3 PRs. Phases 1 and 2 can probably be one PR each. Phase 3 is the biggest (worker `system.md` rewrite + new tool + loop changes) and may want to be 2–3 PRs. Phase 4 is multiple small PRs (one per visibility surface). Phase 5 is one PR. No phase needs to land as a single mega-PR.
