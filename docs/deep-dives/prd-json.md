# The PRD format

`prd.json` is the task list. It's small — usually 3–8 entries — but every stage of a run reads from it or writes to it, and the contracts between fields are not all enforced by the harness. This page is the reference for the schema, the lifecycle, and which contracts are load-bearing-but-soft (so changing them needs care).

For *how* the PRD gets created, see [Seeding a session](seeding.md). For *where it lives on disk*, see [Session layout](session-layout.md). This page is the data structure itself.

## Shape

```json
[
  {
    "id": "T-001",
    "title": "Scaffold the project",
    "description": "Bootstrap with `uv init`, create the `todo_cli/` package …",
    "acceptance_criteria": [
      "`todo_cli/` package exists with `__init__.py` and `__main__.py`",
      "`pyproject.toml` has `pytest` and `ruff` as dev deps",
      "Sentinel test imports the package and passes"
    ],
    "status": "pending"
  },
  …
]
```

A list of entries. Each entry has five fields. The sink ([`tilth/seed/sink.py:_validate`](https://github.com/AlteredCraft/tilth/blob/main/tilth/seed/sink.py)) enforces:

| Field | Type | Validation |
|---|---|---|
| `id` | string | `^T-\d{3,}$` (zero-padded, ≥ 3 digits). Must be unique across entries. |
| `title` | string | Non-empty (JSON Schema; not re-checked at runtime). |
| `description` | string | Non-empty (JSON Schema). |
| `acceptance_criteria` | list[string] | Must be a non-empty list. Item count is *not* bounded — the seeder prompt asks for 2–4, but nothing rejects 1 or 10. |
| `status` | string | Always `pending` at seed time. Normalised by the sink — anything the model writes here is discarded. |

The sink also enforces one cross-field rule: for every PRD entry, there must be a matching `test_t<NNN>_<slug>.py` file in the seed's `test_files` map (`TEST_FILE_RE` and the prefix cross-check in `_validate`). One task → one test file → no orphans, no duplicates.

## Lifecycle on disk

`prd.json` lives at `sessions/<id>/prd.json` (moved out of the worktree in Phase 1 of the prep-feature proposal; see [Memory channels](../architecture/memory-channels.md) for why). Three operations touch it:

1. **Created** — `FileSeedSink.write_seed` at the end of `tilth prep-feature`. Written atomically (staged as `.tmp`, `os.replace`'d into place). All entries land with `status: "pending"`. Immediately after, prep makes one `seed: N task(s) + M test(s)` commit so `prd.json` and the seed tests are anchored in HEAD before the first worker iteration (see [Seeding a session](seeding.md)) — without it the worker sees the seeded tests as uncommitted scope creep.
2. **Read** — `_load_prd(session.root)` at the start of every Ralph-loop iteration. Returns the full list; `_next_pending(prd)` picks the first entry whose status is `pending`. ([loop.py:259](https://github.com/AlteredCraft/tilth/blob/main/tilth/loop.py))
3. **Mutated** — `_save_prd(session.root, prd)` rewrites the file when a task's status flips. Two transitions: `pending → done` (after the evaluator accepts and the commit lands) and `pending → failed` (after a terminal task outcome: `iter_cap`, `evaluator_cap`, `empty_responses`, or `no_case`). Wall-clock and token caps and interrupts stop *before* the task is marked failed — they leave the task `pending` and the session resumable, setting only the session status. The `status` field is the only thing that ever changes after creation.

Nothing else reads or writes `prd.json`. The worker never sees the file path; it sees the whole plan as prose context but not the JSON (more below).

## Per-field reader table

Who reads each field, and what they do with it. This is the load-bearing piece — the same fields are interpreted by different readers in very different ways.

| Field | Seeder writes | Worker sees | Evaluator sees | Validator reads | Harness uses |
|---|---|---|---|---|---|
| `id` | ✓ | task header in user prompt | task header in evaluator prompt | filename pattern (`test_t<NNN>_*.py`) | task selection + commit message |
| `title` | ✓ | task header | task header | — | commit message (`<id>: <title>`) |
| `description` | ✓ | full text in user prompt | full text in evaluator prompt | — | — |
| `acceptance_criteria` | ✓ | bulleted in user prompt; the worker maps each to a `file:symbol` in its `submit_case` `ac_coverage` | bulleted in evaluator prompt; several of the six rejection categories (`acceptance_gap`, `weak_test`, `tests_pass_but_wrong`) reference them | — | — |
| `status` | always `pending` | every task's status appears in the full-plan context (read-only) | — | `_next_pending` reads to pick next task; `_save_prd` mutates after each task |

A few notes worth pulling out of the table:

- **The worker never receives `prd.json` directly.** [`memory.build_user_prompt`](https://github.com/AlteredCraft/tilth/blob/main/tilth/memory.py) formats the current task as a user message. Since the Phase 4 visibility expansion it also injects the *whole* plan — every task collapsed (id/title/status/description/AC), framed as "context, not work to do" and capped at 6 KB — so the worker understands the surrounding tasks but never sees the JSON file, the mutable `status` machinery, or the queue it manages. The framing is "context, not a worklist" (see [Agent visibility](agent-visibility.md)).
- **The evaluator sees one task at a time, fresh across tasks but with per-task memory.** It carries no memory across tasks, but within a task it reads the last 5 entries of that task's ledger (`sessions/<id>/ledger/<task_id>.jsonl`) — its own prior verdicts — so it can confirm a concern was resolved instead of re-litigating it. It gates on the diff plus the worker's structured case, the real validator output, and the inlined seed test, anchored on `description` + `acceptance_criteria`.
- **Validators never touch `prd.json`.** [`validators.run_pytest`](https://github.com/AlteredCraft/tilth/blob/main/tilth/validators.py) maps a task id to a filename glob (`T-001` → `test_t001_*.py`) and runs pytest on whatever files match. The list of acceptance criteria is never read by the validator, only the test file's assertions.

> Beyond `prd.json`, the Phase 4 expansion widened both views: the worker prompt also carries a curated `seed-meta.json` slice and its own task's evaluator ledger; the evaluator prompt also carries the inlined seed test and the full validator output. Those aren't `prd.json` fields, but they shape how each actor reads the ones above. See [The worker↔evaluator dialogue](worker-evaluator-dialogue.md).

> **MVP scope: the 1:1 mapping between `acceptance_criteria` and test assertions is convention, not enforcement.**
>
> The seeder prompt instructs *"acceptance criteria map 1:1 to test assertions. A criterion the tests don't pin down is decorative; an assertion with no matching criterion is scope creep."* But nothing in the harness verifies that mapping at runtime — the sink only checks `acceptance_criteria` is a non-empty list, and the validator runs whatever assertions happen to be in the test file. The evaluator now sees the seed test inlined and is told to flag `weak_test` when the test is thinner than the AC describes (`evaluator.md`, "Reading the seed acceptance test") — but nothing mechanically counts assertions against criteria, so a thin-but-passing test can still slip through if the evaluator misses it.
>
> Practical implications:
>
> | Seeder produces | What happens at run time |
> |---|---|
> | 4 AC, 4 matching assertions | Ideal. Worker has clear contract; evaluator has full coverage to evaluate. |
> | 4 AC, 2 weak smoke tests | Validators pass if those 2 pass. The worker can still submit a case. The evaluator now sees the inlined seed test and can call `weak_test` when the test is thinner than the AC — better purchase than before, but still no mechanical guard. |
> | 4 AC, 6 assertions (extras) | All 6 run. The evaluator might flag extras as `scope_creep` if they imply work outside the task description. |
> | AC: `"user is happy"` | Unactionable for the worker, untestable for the validator, no purchase for the evaluator. Run quietly succeeds with no real contract anywhere. |
>
> We're leaving this as soft for the MVP — we want to see real usage data on whether the seeder gets this wrong often before adding enforcement that might over-fit to the wrong failure mode. If `assert_count == len(criteria)` becomes the right check, it goes in [`tilth/seed/sink.py:_validate`](https://github.com/AlteredCraft/tilth/blob/main/tilth/seed/sink.py); the evaluator-side half is partly here already — [`tilth/prompts/evaluator.md`](https://github.com/AlteredCraft/tilth/blob/main/tilth/prompts/evaluator.md) now inlines the seed test and asks for a `weak_test` reject, a soft version of "for each criterion, name the matching assertion or reject." A hard `assert_count == len(criteria)` check still isn't shipped.

## What `prd.json` is *not*

A few common misreadings worth heading off:

- **Not the seed.** The seed is `prd.json` + the test files in `<workspace>/tests/` + `seed-meta.json`. `prd.json` is one piece. The terminal `write_seed` call writes all three atomically.
- **Not a worker memory channel.** [Memory channels](../architecture/memory-channels.md) covers the channels the worker actually reads from — `AGENTS.md`, git history, `progress.txt`, the per-task evaluator ledger, and (as injected prose context) the plan from `prd.json`. But the worker never sees the file itself; only the prompt-shaped slice.
- **Not versioned.** There's no `prd_version` field today, and the schema isn't formally pinned. Breaking changes would require a migration. Most plausible future additions (task dependencies, parallel execution hints, dynamic re-seeding) would all need a version bump *and* probably a migration helper; nothing of the sort exists yet.
- **Not the contract with the human reviewer.** That's `seed-meta.json` (the `tldr`, `open_questions`, `blockers`, `scope_notes`) plus the eventual git diff. `prd.json` is the harness's working state — read by the loop, mutated by the loop, archived alongside `events.jsonl` when the session ends.

## Where to look in the code

| Want to understand… | Read… |
|---|---|
| The shape and validation rules | [`tilth/seed/sink.py`](https://github.com/AlteredCraft/tilth/blob/main/tilth/seed/sink.py) — `_validate`, `_normalise_entry`, the regex constants at the top. |
| How the worker sees one task | [`tilth/memory.py:build_user_prompt`](https://github.com/AlteredCraft/tilth/blob/main/tilth/memory.py) — the path from `prd.json` to the worker's user message (current task + full plan as context + seed context + own ledger). |
| How the evaluator sees one task | [`tilth/loop.py:_evaluator_task`](https://github.com/AlteredCraft/tilth/blob/main/tilth/loop.py) — assembles description + AC + AGENTS.md + the task ledger + the worker's case + the inlined seed test + full validator output + diff into the fresh-context evaluator call (`_evaluator_prompt` loads the static system prompt). |
| Task selection | [`tilth/loop.py:_next_pending`](https://github.com/AlteredCraft/tilth/blob/main/tilth/loop.py) — picks the first `pending` entry; that's the whole algorithm. |
| Status mutations | [`tilth/loop.py:_save_prd`](https://github.com/AlteredCraft/tilth/blob/main/tilth/loop.py) call sites — two of them, one per terminal status. |
