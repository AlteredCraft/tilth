# The worker↔evaluator dialogue (case & verdict)

The inner loop ends in a structured exchange: the worker presents a **case**, the
evaluator returns a **verdict**, and a per-task **ledger** gives the evaluator
memory across iterations of the same task. This page is the canonical description
of that exchange; [The two loops](two-loops.md) shows where it sits in the loop,
and [Agent visibility](agent-visibility.md) covers what each side does and doesn't
see.

The evaluator earns its place by judging the thing the validators can't. Ruff and
pytest prove the code runs and the test is green — table stakes. The evaluator is
the reviewer asking the next question: is this a *proper* solution, or does it just
happen to pass? That framing is what the rejection categories below encode.

## A note on the name

The reviewing role is the **evaluator** — in prose, in events (`evaluator_verdict`),
in the summary rollup, and on the visualizer card.

## The worker's case — `submit_case`

The worker no longer signals "done" by going quiet. It calls **`submit_case`**, a
*control-flow* tool intercepted in `_run_task` (it isn't a worktree tool — it ends
the turn rather than doing work). The schema lives in `tilth/case.py`:

| Field | Required | What it is |
|---|---|---|
| `summary` | ✓ | One- or two-line claim of what the task achieved |
| `ac_coverage` | ✓ | A list mapping **each acceptance criterion** → the `file:symbol` that satisfies it (plus optional `evidence`) |
| `work_arounds` | — | Things it touched that the criteria don't mention, declared so the evaluator can weigh them rather than read them as scope creep |
| `uncertainties` | — | Ambiguities it resolved by choosing, surfaced instead of buried in confident prose |

`system.md` frames the worker as an **advocate**: argue honestly, not persuasively.
The mechanical checks (ruff + pytest) run regardless — the case is for the reasoning
a test can't capture, not a way to argue past a failing one. If `submit_case` can't
be parsed or validated, the harness logs a `case_parse_error`, feeds the error back
as the `submit_case` tool_result, and lets the worker retry — it doesn't count as an
evaluator call or end the task.

## The evaluator's verdict — `submit_verdict`

When a case passes validators, `_evaluator_task` calls the evaluator, which must respond
with exactly one **`submit_verdict`** tool call (`tilth/verdict.py`):

| Field | On accept | On reject |
|---|---|---|
| `verdict` | `"accept"` | `"reject"` |
| `rejection_category` | `null` | one of the six below |
| `concern` | 1–3 sentences | 1–3 sentences |
| `evidence` | pointers (may be empty) | pointers, e.g. `pkg/foo.py:42` |
| `next_step` | `null` | the concrete remediation the worker can act on |

The six rejection categories are a closed enum — naming the *shape* of the failure,
not just "rejected":

- **`scope_creep`** — work that belongs to a different task, or unrelated files.
- **`acceptance_gap`** — an explicit acceptance criterion isn't satisfied by the diff (also the category for the empty-diff hard reject).
- **`weak_test`** — the seed test passes but doesn't exercise the behaviour the AC describes.
- **`tests_pass_but_wrong`** — satisfies the test letter but not the intent (hardcoded value, mocked the wrong thing, deleted the assertion).
- **`half_finished`** — debug prints, TODOs, dead code, partial implementations.
- **`spec_violation`** — breaks an *explicit, named* constraint from the task, the AC, or `AGENTS.md` (soft style preferences don't count).

On a reject, `next_step` becomes the worker-visible feedback (via `format_reject_feedback`).
If the model never produces a valid `submit_verdict` after two attempts, each failure
is logged as an `evaluator_parse_error` (with the raw payload preserved), and the loop
synthesises a fallback reject verdict (`evaluator_verdict` with `parse_failed: true`) so
the task fails closed rather than silently passing.

## What the evaluator sees

The verdict is no longer gated on the diff alone. `_evaluator_task` assembles, into a
context fresh-across-tasks: the task description + AC, the cumulative diff, the
worker's structured case, this task's **seed acceptance test inlined** (the exact
file the validator ran — grounding the `weak_test` evaluation), the **full** per-validator
output (ruff + pytest), `AGENTS.md` when present, and the task **ledger** (below). It
still sees none of the worker's chain-of-thought or tool history — that isolation is
the point; an evaluator that could read the worker's reasoning would tend to agree
with it.

## The per-task ledger — memory across iterations

A task can be rejected and re-submitted several times. The **ledger** at
`sessions/<id>/ledger/<task_id>.jsonl` is what stops each evaluator call from being
amnesiac. One append-only entry per evaluator call (`session.append_ledger_entry`):

```json
{"ts": "...", "iter": 3, "diff_summary": "...", "case": { ... }, "verdict": { ... }}
```

The last `LEDGER_INJECT_LIMIT` (5) entries are injected into the evaluator prompt under
`## Prior iterations on this task`. The prompt tells it to **confirm a resolved concern
rather than re-litigate it**, and to **escalate** (teach the principle, get more concrete)
when the same `rejection_category` recurs on the same surface instead of reissuing the
same sentence.

Since the [Phase 4 visibility expansion](agent-visibility.md), the worker also sees its
**own** task's ledger — the evaluator's prior verdicts — under `## Prior iterations on
this task (from the evaluator)`, so it can address feedback directly instead of
re-deriving from scratch. This is the one place the review feedback crosses back to the
worker by design; it still sees no generic cross-task reviewer.

The ledger is a flat file under the session root, not in the worktree or the live
conversation, so it **survives resume**: `Session.wake()` re-roots and `read_ledger`
reads straight off disk, and the first retry after a resume shows both sides the verdict
history from the run before — even though the conversation is gone. `tilth reset` drops
`sessions/<id>/`, so it discards the ledgers too. The ledger is the fifth durable
[memory channel](../architecture/memory-channels.md).

## The exchange, end to end

1. Worker calls `submit_case` (its done-signal).
2. Harness runs validators (ruff + pytest, filtered to this task's tests plus every `done` task's tests).
   - **Validators fail** → the failure report is returned as the `submit_case` tool_result; next iteration.
3. Validators pass → `_evaluator_task` reads the ledger, builds the evaluator prompt, calls the evaluator, appends the verdict to the ledger.
   - **Accept** → `_run_task` returns `"done"`; the task is committed.
   - **Reject** → `format_reject_feedback(verdict)` is returned as the `submit_case` tool_result; next iteration.

Both the reject feedback and validator failures come back as the **`submit_case`
tool_result**, not a fresh user message — every tool call must be answered with a
tool_result before the next model call. The reject costs the worker a forward
iteration on the same fixed budget, which is why [a stricter evaluator effectively
shrinks the working budget](two-loops.md#a-subtlety-evaluator-rejections-eat-iterations).

## Where each piece lives

| Concern | Code |
|---|---|
| Worker case: schema, parse, prompt rendering | `tilth/case.py` |
| Evaluator verdict: schema, parse, feedback + ledger formatting | `tilth/verdict.py` |
| Evaluator prompt (static) | `tilth/prompts/evaluator.md` |
| Worker advocate framing | `tilth/prompts/system.md` |
| The exchange + ledger read/append | `tilth/loop.py:_evaluator_task`, `_run_task` |
| Ledger I/O | `tilth/session.py:append_ledger_entry` / `read_ledger` |
