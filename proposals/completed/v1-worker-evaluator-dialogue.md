# Sketch: worker–evaluator dialogue (v1)

**Status:** Sketch / pre-proposal — concrete shape, not implementation-ready.
**Author:** Sam (with Claude conversational pass)
**Date:** 2026-05-26
**Related:** [`frictions-2026-05-26.md`](frictions-2026-05-26.md) (the inventory this answers); [Anthropic — Harness Design for Long-Running App Development](https://www.anthropic.com/engineering/harness-design-long-running-apps); [OpenAI — Harness Engineering](https://openai.com/index/harness-engineering/).
**Preserves:** Brain / Hands / Session split; worktree isolation; read-only-AGENTS.md; mechanical-validator-first gating.

## The shape in one paragraph

The current Tilth pattern is *worker stops → evaluator issues a fresh-context verdict on the diff*. v1 replaces this with a **dialogue**: the worker submits a structured **case** when it believes the task is done; the evaluator reviews the case + diff + test file + its own **per-task ledger** of this task's prior iterations and issues a **verdict** of `accept` or `reject`. The worker's visibility expands to include the full PRD, seed-meta, and its own task's ledger — but **not** harness mechanics (token caps, cross-task evaluator memory, other workers). The evaluator's role grows from "diff reviewer" to "task-scoped reviewer with memory and richer remediation feedback." The mechanical floor (per workspace; today ruff + pytest for the Python demo) stays the deterministic anchor under all of this — the evaluator's prose-judgment sits on top of it, never replaces it.

(A third verdict — `halt` — was sketched and **deferred**. Halt requires policy [eligibility bar, terminal-state semantics] that should be grounded in real ledger data from v1 sessions, not predicted upfront. Once v1 ships and ledgers accumulate, we can look at actual failure shapes and decide what halt should be. See *What v1 is deliberately not*.)

This is **not** a rewrite of the loop. It's a richer role for two of the existing actors (worker, evaluator) plus one new durable channel (the ledger). The seeder is unchanged in v1; whether *it* grows similarly is a v2 question (see *Open questions*).

## The dialogue

### Worker's `case` (structured submission, replaces "stop calling tools")

The worker no longer "stops calling tools and responds with a summary." Instead, when it believes the task is done, it submits a structured case via a new `submit_case` tool call. Shape:

```json
{
  "summary": "one to three sentences",
  "ac_coverage": [
    {
      "criterion": "exact text from the AC",
      "addressed_by": "todo_cli/__main__.py:main() — argparse subparser handles 'add'",
      "evidence": "tests/test_t002_cli_add.py::test_add_returns_zero"
    },
    ...
  ],
  "work_arounds": [
    "Deleted README.md, main.py, .python-version — created by `uv init` per the description; outside AC scope but required by the authorized command."
  ],
  "uncertainties": [
    "AC says 'empty string after .strip()' — interpreted as len(args.item.strip()) == 0; described behavior matches but the phrasing was ambiguous."
  ]
}
```

The four fields each address a specific friction:

- **`summary`** — concise framing (today's "respond with a summary" survives, narrowed).
- **`ac_coverage`** — explicit 1:1 mapping between AC and the change that addresses it. Closes the soft contract in [`prd-json.md:66`](../../docs/deep-dives/prd-json.md) — the evaluator now reads a worker-authored claim of which AC each piece of work satisfies and can check it. (F3.)
- **`work_arounds`** — slot for the worker to *name* "I had to touch this file because of the authorized command's side effects." Resolves the F4 disambiguation problem at the source: cleanup-vs-creep stops being a diff-reading riddle and becomes a worker-authored claim the evaluator can accept or reject.
- **`uncertainties`** — where the worker flags ambiguity rather than guessing. Currently no such slot exists; the worker either guesses or pads the summary with hedging. Giving it a structured place forces the question into the open.

The worker's `system.md` will need a non-trivial rewrite — it's now framed as an *advocate* (presenting a case), not a *tool-user that stops*. Done isn't "no more tool calls"; done is "I submitted a case."

### Evaluator's verdict (structured response)

```json
{
  "verdict": "accept" | "reject",
  "rejection_category": "scope_creep" | "acceptance_gap" | "weak_test" | "tests_pass_but_wrong" | "half_finished" | "spec_violation" | null,
  "concern": "one to three sentences",
  "evidence": ["file:line", "tests/test_t001_scaffold.py::test_pkg_exists", ...],
  "next_step": "concrete remediation the worker can act on"
}
```

- `verdict` is the gate. `null` on `next_step` is valid for `accept`.
- `rejection_category` makes patterns visible across the ledger and the session — three rejects in a row with the same category means the issue is structural, not surface. This same category data is what informs the halt heuristic when halt is reconsidered post-v1.
- `evidence` and `next_step` mirror OpenAI's *"inject remediation instructions into agent context"* pattern — failure as teaching. `next_step` is the most load-bearing addition. Today's reject is diagnosis; v1's reject is diagnosis + remediation pointer. Measurable too: did the worker's next iteration address the named step?

## The evaluator's per-task ledger

A new durable channel: `sessions/<id>/ledger/<task_id>.jsonl`. One entry per iteration the evaluator was called. Each entry:

```json
{
  "iter": 12,
  "ts": "2026-05-26T17:38:22Z",
  "diff_summary": "M tests/test_t002_cli_add.py; +24 -2",
  "case": { ... },
  "verdict": { ... }
}
```

**Read at the start of every evaluator call.** Injected into the evaluator's user message under a `## Prior iterations on this task` section. Capped at a sensible size (probably last 5 entries, or a token budget — see *Open questions*).

What the ledger unlocks:

- **Pattern detection.** Three rejects with `rejection_category == "scope_creep"` and `evidence` naming the same file — the evaluator can call this out in the next verdict instead of issuing the same reject in fresh context. Escalating feedback shape: first reject diagnoses; third reject teaches the underlying principle.
- **Data for the deferred halt decision.** When v1 sessions accumulate ledgers, we'll be able to look at actual rejection arcs (how many rounds before the worker recovers? what does a truly unwinnable task look like in the ledger?) and design halt against real shapes instead of guessed ones. The ledger is the measurement instrument that lets halt land later, well-grounded.
- **Stops re-litigating.** If the worker is iterating on a fix the evaluator approved-in-principle two iterations ago, the evaluator doesn't re-debate; it focuses on what's new.

Ledger is **internal memory for the evaluator**. It is *not* the audit trail (`events.jsonl` continues to play that role; the ledger feeds it via `evaluator_verdict` events, but isn't itself the audit log).

## Visibility (what each role sees)

The sharpened version of F8: **expose project state liberally, keep harness mechanics hidden.** This preserves the gaming/self-management defenses of today's wall while resolving the reasoning-impaired-worker cost.

| Surface | Worker sees | Evaluator sees | Note |
|---|---|---|---|
| Current task description + AC | ✓ (as today) | ✓ (as today) | — |
| **Full PRD (other tasks)** | ✓ **new** | ✓ **new** | F5/F9. Makes "don't pre-empt future tasks" comprehensible. |
| **`seed-meta.json` (open_questions, blockers, scope_notes)** | ✓ **new** | ✓ **new** | Authored *for* a human; the worker is allowed to read context the human authored about its work. |
| **Own task's evaluator ledger** | ✓ **new** | ✓ **new** | Worker knows what's been rejected and why. |
| AGENTS.md | ✓ (as today) | ✓ (as today) | — |
| `progress.txt` tail | ✓ (as today) | ✗ | — |
| Validator pass/fail | ✓ (via feedback) | ✓ (input) | — |
| Validator output content | ✗ | ✓ **new** | F6. Today evaluator gets pass/fail bool; v1 gives it the report. |
| **Matching seed test file content** | ✓ (as on disk, already) | ✓ **new** | This was PR #17's payload. Now part of the dialogue, not bolted on. |
| Cross-task evaluator memory | ✗ | ✓ **new (read-only)** | Evaluator carries patterns across tasks; worker doesn't. |
| Token budget / iter cap | ✗ | ✗ | Hidden-cap rationale still holds. |
| Other workers / parallel sessions | ✗ | ✗ | n/a today; design anchor for later. |
| `events.jsonl`, `checkpoint.json` | ✗ | ✗ | Pure harness state. |

The line being drawn: anything *the seeder/the human authored about the work* is visible; anything *the harness produced to manage the work* is not. AGENTS.md, PRD, seed-meta, ledger — all "about the work." Token counts, checkpoint, events.jsonl — all "managing the work."

## The evaluator's new authorities

| Verdict | Behavior |
|---|---|
| `accept` | As today: commit, advance, self-improve. |
| `reject` | As today: inject feedback, worker iterates. Difference: feedback is now structured (`concern` + `evidence` + `next_step`), and the worker sees it in the context of the ledger. |

The escape-from-broken-task case (F7) is **not** resolved by v1 — the only termination path remains the existing iter/evaluator cap, which ends the entire run. This is unchanged from today and is a known limitation. The bet is that the ledger from v1 sessions will give us the empirical data needed to design a real halt mechanism (eligibility, terminal-state semantics, downstream-task implications) without guessing.

## On disk: state layout

```
sessions/<id>/
├── checkpoint.json         # existing
├── events.jsonl            # existing — adds `evaluator_verdict` w/ structured payload
├── summary.json            # existing
├── prd.json                # existing
├── seed-meta.json          # existing — now also read by worker
├── progress.txt            # existing
├── proposed-learnings.md   # existing — see Open questions
├── ledger/                 # NEW
│   ├── T-001.jsonl
│   ├── T-002.jsonl
│   └── ...
└── workspace/              # existing worktree
```

The ledger is per-task because evaluator memory is task-scoped. Cross-task memory for the evaluator is a separate channel (probably `ledger/_session.jsonl` with rolled-up patterns — see *Open questions*).

`events.jsonl` continues to be the audit log; ledger entries are also event-logged so the visualizer can render them. The ledger files are the **read path** for the evaluator (cheap to read on each call); events.jsonl is the **append-only audit trail** (the existing pattern).

## The mechanical floor stays the anchor

This is the load-bearing principle from the OpenAI piece, and the answer to my own nervous note in the prior exchange: *the evaluator's prose-judgment must sit on top of deterministic checks, never replace them*.

v1 keeps the gate semantics: validators pass before the evaluator is called — that's the existing pattern, preserved. Today's `tilth/validators.py` hard-codes the floor as ruff + pytest, which couples the floor to Python. The principle ("language-appropriate static + tests") is generic; the implementation isn't. Making the floor **per-workspace pluggable** is tracked separately in [#20](https://github.com/AlteredCraft/tilth/issues/20) and is a v1 prerequisite for non-Python workspaces. The dialogue itself is mechanism-agnostic and doesn't depend on which validators run — only that they ran and passed.

What v1 does **not** do: weaken the floor. The worker still can't submit a case until validators pass. The evaluator still can't accept if validators failed. The case + ledger + structured feedback are a richer *judgment layer*; they don't replace the floor.

What v1 implicitly invites for v2: **richer mechanical checks** (Tilth's analog of OpenAI's custom lints — structural tests, AC↔assertion mapping check, scope-static-check). Once [#20](https://github.com/AlteredCraft/tilth/issues/20) lands, adding these is just *more validator entries*. Some of what the evaluator is asked to evaluator in v1 could be mechanically enforced in v2, freeing the evaluator to focus on harder calls. Not in scope for v1; the path to it is open.

## The risk to manage

**Prose-good, evidence-thin.** If the case's `ac_coverage` becomes persuasive prose the evaluator reads sympathetically, the role drifts toward "rewarding good explanations" rather than "rewarding good work." This is the failure mode every human code-review process has.

Three mitigations v1 should ship with:

1. **`evidence` in the case is a pointer, not prose.** `addressed_by: "todo_cli/__main__.py:main() — argparse subparser handles 'add'"` is fine; `addressed_by: "the implementation thoughtfully considers all the edge cases the criterion implies"` is not. The case schema should reject the latter shape — `addressed_by` is a file:symbol pointer with a brief annotation, not free prose.
2. **`evidence` in the verdict cites the same shape.** Evaluator can't accept on the worker's say-so; it has to name the file:symbol or assertion that proves the claim.
3. **The mechanical floor is the only floor.** No case prose can substitute for a failing test. The validator pass/fail remains the precondition; the case is read *after* validators pass, not as a way to argue around them.

The case is a **legibility aid for the evaluator**, not a vehicle for the worker to argue. The system reminds itself of that distinction in the schema and the prompts.

## What v1 is deliberately not

- **Not contract negotiation at seed time.** The Anthropic-style "evaluator reviews proposed contract before any code is written" pattern is a v2 move. v1 keeps the seeder as one-shot terminal; the evaluator only enters at run time. F1 (seed unvalidated) is therefore *not* addressed in v1 — the evaluator will see contradictory seeds through the ledger and surface them in `concern`, but has no authority to call the task broken. Worth a v2.
- **Not halt-the-task authority.** Sketched and deferred. The evaluator can only `accept` or `reject` in v1; F7 (no escape from a broken task short of full-run termination) remains. Halt requires policy decisions (eligibility bar, terminal-state semantics, downstream-task handling) that should be grounded in the ledger data v1 sessions will produce, not guessed upfront. Once v1 sessions have run and we can see what unwinnable tasks actually look like in the ledger, halt becomes a v1.5 question with real evidence to work from.
- **Not a clarification dialogue.** The worker submits one case; the evaluator issues one verdict; no follow-up Q&A in v1. Doubles turn count for marginal gain. The structured case probably gets 80% of the value.
- **Not richer mechanical checks (custom lints, AC↔assertion enforcement).** OpenAI's pattern suggests these are high-value, but they're a separate workstream. v1 keeps the existing floor (with the pluggability work in [#20](https://github.com/AlteredCraft/tilth/issues/20) as a prerequisite).
- **Not a generalized hook lifecycle.** Today's narrow `pre_tool` / `post_edit` model holds for v1. Each new v1 seam (`submit_case`, ledger read/write, structured verdict) is wired directly. The natural moment to formalize a lifecycle is alongside v2's custom mechanical lints, once we can see whether the seams cluster — tracked in [#19](https://github.com/AlteredCraft/tilth/issues/19).
- **Not a change to the workspace mechanism.** v1 ships on the existing git-worktree model. Clone-outside-Tilth and container-based isolation are orthogonal; the process-isolation workstream is [#13](https://github.com/AlteredCraft/tilth/issues/13). The dialogue is mechanism-agnostic — and notably, once [#13](https://github.com/AlteredCraft/tilth/issues/13) lands, the visibility table below shifts from *path-discovery* (what the worker can reach via `../`) to *mount/expose policy* (what the harness chooses to make available). That's a strict improvement and makes F8 enforceable rather than aspirational.
- **Not backwards-compatible with v0 sessions.** Pre-1.0; mid-session schema breaks are fine. v0 sessions either reset cleanly or refuse to resume with a clear error.
- **Not cross-session evaluator memory.** Evaluator forgets between sessions. Each session's ledger lives and dies with the session.
- **Not parallel-worker / multi-agent.** Single worker, single evaluator. v1 doesn't paint that future into a corner (the ledger could scale to per-worker), but doesn't build it.
- **Not a rewrite of the seeder.** The seeder produces what it produces today; v1 reads its output more carefully.
- **Not changes to the demo workspace.** v1 lands in Tilth itself; the demo benefits without code changes.

## Open questions

1. **Ledger size cap.** Last 5 entries? Last N tokens? Compacted older entries (drop the diff_summary, keep the verdict)? The evaluator's prompt grows with iteration count; needs a strategy. Lean toward token-budgeted with truncation of older `diff_summary` fields.
2. **`work_arounds` discipline.** What stops the worker from listing every file change as a "necessary work-around"? The schema validates structure but not honesty. Maybe the evaluator's prompt needs an explicit "treat `work_arounds` claims skeptically; the worker has incentive to expand scope this way." Or: limit to N entries to force the worker to triage.
3. **AC coverage gaps.** What does the evaluator do when `ac_coverage` lists only 3 of 4 ACs from the PRD entry? Auto-reject? Or flag and let the evaluator decide based on whether the missing AC is satisfied by the test file? Probably the former — a missing AC in the case is the worker admitting it didn't address it.
4. **`proposed-learnings.md` in this model.** Today's self-improve step still applies — but with structured rejection categories accumulating in the ledger, the proposed-learnings step has much better evidence to work from. Worth tweaking: the self-improve prompt could read the ledger for patterns and propose more grounded learnings.
5. **Cross-task evaluator memory shape.** Per-task ledger handles within-task patterns. What about T-001 surfaced something that should inform T-002's review? My instinct: a session-level `ledger/_patterns.jsonl` written at task-end with rolled-up signals (e.g., "this seeder consistently authors broad descriptions") that the evaluator can read. But that's a v1.5 question — needs more thought.
6. **Token budget.** Case + ledger + structured feedback adds tokens per iteration. Does the better-grounded rejection feedback (and worker visibility of the ledger) net out positive against today's fresh-context-every-time pattern? Worth measuring in the first session. Hypothesis: yes, because worker iteration count drops when remediation is concrete (`next_step` pointing at the actual gap) and when the worker can see what's already been tried.
7. **Worker `submit_case` failure modes.** What if the worker submits a malformed case? Current parse-error pattern (interview's `_parse_args`) is the model: log, inject parse error as tool result, let the model retry. Same pattern here.
8. **Does the seeder need to know about the dialogue?** The seeder writes `description` and `acceptance_criteria` that will now be read by a worker-as-advocate and judged via a structured case. Does the *shape* of what the seeder writes need to change to be case-friendly? E.g., ACs should be phrased as checkable claims, not aspirations. The seeder prompt may need a small update, even if the architecture stays one-shot.
9. **The visualizer.** The ledger is rich — the visualizer should render it. Probably as a panel per task showing the iteration arc (verdicts over time, with diffs collapsible). Out of scope for v1 implementation but worth flagging for the same workstream.

## Additional notes

- **The "case" shape is also a measurement instrument.** Over a few sessions you'll see whether `ac_coverage` is honest (does the worker actually address what it claims?), whether `work_arounds` are mostly legitimate or mostly hand-wavy, and whether `next_step` from the evaluator gets acted on in the next iteration. Each is a measurable signal about whether v1 is working, in a way today's pass/fail bool isn't.
- **This sketch keeps the Brain/Hands/Session split intact.** Brain = worker + evaluator (richer roles, but same separation). Hands = tools + worktree (unchanged). Session = state on disk (one new channel: the ledger). The frictions doc's closing note — *"the load-bearing pieces are not the problem"* — holds. v1 is structural cleanup at the seams, not a foundation rewrite.
- **What this *doesn't* address from the friction list.** F1 (seed unvalidated — surfaced by the ledger but not prevented). F7 (no escape from a broken task — halt deferred; iter/evaluator cap remains the only terminator). F10 (containment / sessions inside the harness tree) — orthogonal; tracked in [#13](https://github.com/AlteredCraft/tilth/issues/13). F11 (compound questions / open_questions discipline) — seeder-side; not touched by v1. The OpenAI piece's "lints inject remediation" pattern at full strength — out of scope (mechanical floor stays narrow in v1; v2 territory).
- **No single decision-before-implementing.** With halt deferred (the previous blocker), the remaining open questions can be refined as the implementation lands. Ledger size cap and `work_arounds` discipline are the two worth thinking about early — both affect prompt shape — but neither is irreversible.