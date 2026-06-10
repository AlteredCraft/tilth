# The worker↔evaluator dialogue (case & verdict)

The inner loop ends in a structured exchange: the worker presents a **case**, the
evaluator returns a **verdict**, and a per-task **ledger** gives the evaluator
memory across iterations of the same task. This page is the canonical description
of that exchange; [The two loops](two-loops.md) shows where it sits in the loop,
and [Agent visibility](agent-visibility.md) covers what each side does and doesn't
see.

**The evaluator is the only gate.** In the prompt-driven core there are no
codified validators — no harness-run test suite, no linter — so nothing has
checked the work before the evaluator reads it. Its prompt says exactly that:
read the diff as ground truth, judge whether the code would actually produce the
behaviour the acceptance criteria describe, assume nothing passed. The worker,
for its part, is told to *verify* its work via `bash` before presenting it —
"the code looks right" is not verification — but that's an instruction, not an
enforcement step.

## A note on the name

The reviewing role is the **evaluator** — in prose, in events (`evaluator_verdict`),
in the summary rollup, and on the visualizer card.

## The worker's case — `submit_case`

The worker doesn't signal "done" by going quiet. It calls **`submit_case`**, a
*control-flow* tool intercepted in `_run_task` (it isn't a worktree tool — it ends
the turn rather than doing work). The schema lives in `tilth/case.py`:

| Field | Required | What it is |
|---|---|---|
| `summary` | ✓ | One- or two-line claim of what the task achieved |
| `ac_coverage` | ✓ | A list mapping **each acceptance criterion** → the `file:symbol` that satisfies it (plus optional `evidence`) |
| `work_arounds` | — | Things it touched that the criteria don't mention, declared so the evaluator can weigh them rather than read them as scope creep |
| `uncertainties` | — | Ambiguities it resolved by choosing, surfaced instead of buried in confident prose |

`system.md` frames the worker as an **advocate**: argue honestly, not persuasively.
The case is for the reasoning the diff can't show on its own — not a way to argue
past work that isn't there. If `submit_case` can't be parsed or validated, the
harness logs a `case_parse_error`, feeds the error back as the `submit_case`
tool_result, and lets the worker retry — it doesn't count as an evaluator call or
end the task.

## The evaluator's verdict — `submit_verdict`

When a valid case arrives, `_evaluator_task` calls the evaluator, which must respond
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

- **`scope_creep`** — work that belongs to a different task, or unrelated files (also the category for the cross-task-interference hard reject, including edits to the harness's `.tilth/` task files).
- **`acceptance_gap`** — an explicit acceptance criterion isn't satisfied by the diff (also the category for the empty-diff hard reject).
- **`half_finished`** — debug prints, TODOs, dead code, partial implementations.
- **`spec_violation`** — breaks an *explicit, named* constraint from the task, the AC, or `AGENTS.md` (soft style preferences don't count).
- **`tests_pass_but_wrong`** — *only when the worker added its own tests as evidence:* satisfies the test letter but not the intent (hardcoded value, mocked the wrong thing, trivial assertion).
- **`weak_test`** — *only when the worker added its own tests as evidence:* the test exists but doesn't exercise the behaviour the AC describes.

(The last two apply only when the worker chose to write tests — the harness doesn't
supply or run any itself; most tasks won't have tests, and the evaluator is told
that's fine: judge the diff directly.)

On a reject, `next_step` becomes the worker-visible feedback (via `format_reject_feedback`).
If the model never produces a valid `submit_verdict` after two attempts, each failure
is logged as an `evaluator_parse_error` (with the raw payload preserved), and the loop
synthesises a fallback reject verdict (`evaluator_verdict` with `parse_failed: true`) so
the task fails closed rather than silently passing.

## What the evaluator sees

`_evaluator_task` assembles, into a context fresh-across-tasks: the task description +
acceptance criteria, the feature overview (the why + scope boundaries, from
`.tilth/tasks/overview.md`), the project-context files (`AGENTS.md`/`CLAUDE.md`)
when present, the task **ledger** (below), the worker's structured case, and the
diff of the working tree against the session branch's HEAD. It sees none of the
worker's chain-of-thought or tool history — that isolation is the point; an
evaluator that could read the worker's reasoning would tend to agree with it.

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

The worker also sees its **own** task's ledger — the evaluator's prior verdicts — under
`## Prior iterations on this task (from the evaluator)`, so it can address feedback
directly instead of re-deriving from scratch. This is the one place the review feedback
crosses back to the worker by design; it still sees no generic cross-task reviewer.

The ledger is a flat file under the session root, not in the worktree or the live
conversation, so it **survives resume**: `Session.wake()` re-roots and `read_ledger`
reads straight off disk, and the first retry after a resume shows both sides the verdict
history from the run before — even though the conversation is gone. `tilth reset` drops
`sessions/<id>/`, so it discards the ledgers too. The ledger is the fifth durable
[memory channel](../architecture/memory-channels.md).

## The exchange, end to end

1. Worker calls `submit_case` (its done-signal).
   - **Case doesn't parse** → the error is returned as the `submit_case` tool_result; next iteration (no evaluator call).
2. Valid case → `_evaluator_task` reads the ledger, builds the evaluator prompt, calls the evaluator, appends the verdict to the ledger.
   - **Accept** → `_run_task` returns `"done"`; the task is committed.
   - **Reject** → `format_reject_feedback(verdict)` is returned as the `submit_case` tool_result; next iteration.

The reject feedback comes back as the **`submit_case` tool_result**, not a fresh user
message — every tool call must be answered with a tool_result before the next model
call. The reject costs the worker a forward iteration on the same fixed budget, which
is why [a stricter evaluator effectively shrinks the working
budget](two-loops.md#a-subtlety-evaluator-rejections-eat-iterations).

## Where each piece lives

| Concern | Code |
|---|---|
| Worker case: schema, parse, prompt rendering | `tilth/case.py` |
| Evaluator verdict: schema, parse, feedback + ledger formatting | `tilth/verdict.py` |
| Evaluator prompt (static) | `tilth/prompts/evaluator.md` |
| Worker advocate framing | `tilth/prompts/system.md` |
| The exchange + ledger read/append | `tilth/loop.py:_evaluator_task`, `_run_task` |
| Ledger I/O | `tilth/session.py:append_ledger_entry` / `read_ledger` |
