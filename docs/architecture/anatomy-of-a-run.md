# Anatomy of a run

The [overview](overview.md) tells you *who* does what — Brain reasons, Hands act, Session records. This page tells you *what flows through*: the artifacts the per-task loop reads to do its work, and the ones it leaves behind.

A Tilth run is, in the end, a function over files. It reads a fixed set of inputs, turns the loop once per task, and writes a fixed set of outputs — and three of those outputs feed straight back in as the loop's working memory. Getting that shape in your head first makes the deep dives easier to place.

![Three-zone data-flow diagram titled "ANATOMY OF A RUN". LEFT, under the label "INPUTS — WHAT THE LOOP READS", a vertical stack of monospace chips: system.md, AGENTS.md, seed-meta.json, tests/, repo @ worktree. CENTRE, a large circular loop-arrow glyph labelled "PER-TASK LOOP" with the italic caption "one task at a time". RIGHT, under the label "OUTPUTS — WHAT THE LOOP WRITES", a vertical stack of monospace chips: commits, events.jsonl, summary.json, checkpoint.json, proposed-learnings.md. Sage-green arrows flow left-to-right from the input chips into the loop and out to the output chips. A heavier sage-green arc sweeps beneath the loop from the output side back round to the input side, labelled "written out, read back in", passing through a bottom row labelled "WORKING MEMORY" that holds three chips: prd.json, progress.txt, ledger/.](../assets/anatomy-of-a-run.jpg)

*A run as a function over files: the per-task loop reads its inputs (left), turns once per task, and writes its outputs (right). Three artifacts — `prd.json`, `progress.txt`, and the evaluator `ledger/` — are written out and read back in as the loop's working memory (the lower arc).*
{: .caption }

The split below mirrors that diagram: pure inputs, the loop, pure outputs, and the three artifacts that are both.

## Inputs — what the loop reads

Read-only, from the loop's point of view. Most are seeded before the run — by you (`AGENTS.md`) or by [`tilth prep-feature`](../deep-dives/seeding.md) (`seed-meta.json`, the acceptance tests). The loop consumes them; it doesn't edit them.

| Artifact | Lives in | Read by | Carries |
|---|---|---|---|
| `system.md` | `tilth/prompts/` (harness) | worker (its system prompt) | the worker's role, tool guidance, "done" criteria |
| `AGENTS.md` | the workspace (user-owned) | worker, evaluator, self-improve step | your project's conventions |
| `seed-meta.json` (a slice) | `sessions/<id>/` | worker (curated slice only) | the interview's TL;DR, scope notes, blockers, open questions |
| `test_t<NNN>_*.py` | the worktree (`workspace/tests/`) | pytest (the floor); evaluator (inlined); worker (on disk) | the per-task acceptance bar |
| the source repo | the worktree | worker, via Hands | the code the run changes |

The instructions (`system.md`, `AGENTS.md`) and the plan-derived context (the `seed-meta.json` slice, the acceptance criteria) are assembled into each fresh task prompt. *What the worker actually sees, versus what stays harness-only, is its own subject* — see [Agent visibility](../deep-dives/agent-visibility.md). For the input channels in depth, see [Memory channels](memory-channels.md).

## The loop — what turns

Between read and write sits the loop you came here to run. Its mechanics live elsewhere — [The two loops](../deep-dives/two-loops.md) for the Ralph (outer) / tool-use (inner) split — but the per-task shape is:

![Six rounded boxes left to right — PROMPT, TOOL LOOP, VALIDATORS, EVALUATOR, SELF-IMPROVE, COMMIT — depicting one task's lifecycle. A "WORKER SEES" bar spans PROMPT and TOOL LOOP; a "HARNESS ONLY" bar spans the rest. Sage-green arrows connect each box; two thinner feedback curves return to TOOL LOOP from VALIDATORS (validator_failed) and EVALUATOR (evaluator_rejected).](../assets/per-task-lifecycle.jpg)

*One task's lifecycle. The worker sees the prompt and the tool loop; the validators, evaluator, self-improve step, and commit are harness-side.*
{: .caption }

This page stays out of those internals. What matters here is that each turn reads the inputs above and produces the outputs below.

## Outputs — what the loop writes

Written by the harness as the run proceeds. The worker writes none of them directly — it writes code, which the harness commits. They exist for the human reading the run afterwards, and for `tilth resume` / `tilth visualize`.

| Artifact | Lives in | Written | Read afterwards by |
|---|---|---|---|
| commits on `session/<id>` | the source repo's `.git` | one per accepted task | humans (review + merge); evaluator (via the diff) |
| `events.jsonl` | `sessions/<id>/` | append-only, every step | humans; `tilth visualize`; `tilth resume` |
| `summary.json` | `sessions/<id>/` | rebuilt at each task boundary | humans; the visualizer; external consumers |
| `checkpoint.json` | `sessions/<id>/` | as the run proceeds (it carries the running token total, so it rewrites after each model call) | `tilth resume` |
| `proposed-learnings.md` | `sessions/<id>/` | by the self-improve step, when it has something | you, at end of run |

Where each lives on disk — and the full `events.jsonl` event taxonomy — is in [Session layout](../deep-dives/session-layout.md). The branch is [never auto-merged](overview.md#architecture-invariants-worth-preserving); you review and merge it like any feature branch.

One artifact the loop *doesn't* write is `chat.html`: [`tilth visualize`](../getting-started/visualizing.md) renders it on demand from `events.jsonl` after a run. It's an out-of-band step, not part of the loop — which is why it sits outside both this table and the diagram.

## Working memory — the artifacts that are both

Three artifacts are written by the loop *and* read back by the next turn. They are how a run keeps continuity when each task starts from a fresh context, and across `tilth resume` in a brand-new process: the state lives on disk, not in the model's head.

| Artifact | Lives in | Written | Read back by |
|---|---|---|---|
| `prd.json` | `sessions/<id>/` | `tilth prep-feature` seeds it; the harness flips status per task | the harness (next-task selection); the worker sees the *plan* as prose context |
| `progress.txt` | `sessions/<id>/` | one line per task outcome | the worker (last ~30 lines, next task) |
| `ledger/<task_id>.jsonl` | `sessions/<id>/` | one entry per evaluator call | the evaluator (its prior verdicts, next iteration); the worker (on a retry) |

This is the loop's durable working memory — the sage-green arc in the diagram. It is also why a run survives interruption: stop it at any point and the next process reads these three back and picks up where it left off. See [Resume mechanics](../deep-dives/resume-mechanics.md).

> The worker never reads `events.jsonl`, `summary.json`, `checkpoint.json`, or its own token counts. Those are outputs *about* the run, for the human — not inputs to it. Keeping them out of the loop is [invariant 2](overview.md#architecture-invariants-worth-preserving); the honest scope of that boundary (a determined worker with `bash` can still reach them via relative paths) is in [Agent visibility](../deep-dives/agent-visibility.md).

## See also

- [Architecture overview](overview.md) — who does what (Brain / Hands / Session).
- [Memory channels](memory-channels.md) — the input channels in detail.
- [Session layout](../deep-dives/session-layout.md) — where the outputs live on disk, plus the event taxonomy.
- [The two loops](../deep-dives/two-loops.md) — what the loop in the middle actually does.
