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

1. **Created** — `FileSeedSink.write_seed` at the end of `tilth prep-feature`. Written atomically (staged as `.tmp`, `os.replace`'d into place). All entries land with `status: "pending"`.
2. **Read** — `_load_prd(session.root)` at the start of every Ralph-loop iteration. Returns the full list; `_next_pending(prd)` picks the first entry whose status is `pending`. ([loop.py:259](https://github.com/AlteredCraft/tilth/blob/main/tilth/loop.py))
3. **Mutated** — `_save_prd(session.root, prd)` rewrites the file when a task's status flips. Two transitions: `pending → done` (after the judge accepts and the commit lands) and `pending → failed` (after iter-cap / wall-clock-cap / token-cap / interrupt). The `status` field is the only thing that ever changes after creation.

Nothing else reads or writes `prd.json`. The worker never sees the file path; it only sees its own task slice (more below).

## Per-field reader table

Who reads each field, and what they do with it. This is the load-bearing piece — the same fields are interpreted by different readers in very different ways.

| Field | Seeder writes | Worker sees | Judge sees | Validator reads | Harness uses |
|---|---|---|---|---|---|
| `id` | ✓ | task header in user prompt | task header in judge prompt | filename pattern (`test_t<NNN>_*.py`) | task selection + commit message |
| `title` | ✓ | task header | task header | — | commit message (`<id>: <title>`) |
| `description` | ✓ | full text in user prompt | full text in judge prompt | — | — |
| `acceptance_criteria` | ✓ | bulleted in user prompt; system prompt makes them the "done" contract | bulleted in judge prompt; three of four rejection categories reference them | — | — |
| `status` | always `pending` | — | — | — | `_next_pending` reads to pick next task; `_save_prd` mutates after each task |

A few notes worth pulling out of the table:

- **The worker never receives `prd.json` directly.** [`memory.assemble_task_prompt`](https://github.com/AlteredCraft/tilth/blob/main/tilth/memory.py) extracts only the current task and formats it as a user message. The worker doesn't know how many other tasks exist, doesn't know any other task's status, doesn't know prior tasks' criteria. This is intentional (see [Agent visibility](agent-visibility.md)).
- **The judge sees one task at a time, in a fresh context.** No history of prior judge calls, no memory of why earlier tasks were accepted. Gating is per-diff, anchored on `description` + `acceptance_criteria`.
- **Validators never touch `prd.json`.** [`validators.run_pytest`](https://github.com/AlteredCraft/tilth/blob/main/tilth/validators.py) maps a task id to a filename glob (`T-001` → `test_t001_*.py`) and runs pytest on whatever files match. The list of acceptance criteria is never read by the validator, only the test file's assertions.

> **MVP scope: the 1:1 mapping between `acceptance_criteria` and test assertions is convention, not enforcement.**
>
> The seeder prompt instructs *"acceptance criteria map 1:1 to test assertions. A criterion the tests don't pin down is decorative; an assertion with no matching criterion is scope creep."* But nothing in the harness verifies that mapping at runtime — the sink only checks `acceptance_criteria` is a non-empty list, the validator runs whatever assertions happen to be in the test file, and the judge *might* spot a missing assertion by reading both AC and the diff but isn't given an explicit "check AC↔assertion correspondence" instruction.
>
> Practical implications:
>
> | Seeder produces | What happens at run time |
> |---|---|
> | 4 AC, 4 matching assertions | Ideal. Worker has clear contract; judge has full coverage to evaluate. |
> | 4 AC, 2 weak smoke tests | Validators pass if those 2 pass. Worker may declare done. Judge *might* catch "acceptance gap" if a missing criterion is obvious in the diff. No automatic guard. |
> | 4 AC, 6 assertions (extras) | All 6 run. Judge might flag extras as scope creep if they imply work outside the task description. |
> | AC: `"user is happy"` | Unactionable for the worker, untestable for the validator, no purchase for the judge. Run quietly succeeds with no real contract anywhere. |
>
> We're leaving this as soft for the MVP — we want to see real usage data on whether the seeder gets this wrong often before adding enforcement that might over-fit to the wrong failure mode. If `assert_count == len(criteria)` becomes the right check, it goes in [`tilth/seed/sink.py:_validate`](https://github.com/AlteredCraft/tilth/blob/main/tilth/seed/sink.py); if instead the right check is judge-side ("for each criterion, name the matching assertion or reject"), it goes in [`tilth/prompts/judge.md`](https://github.com/AlteredCraft/tilth/blob/main/tilth/prompts/judge.md). Both are plausible; neither is shipped.

## What `prd.json` is *not*

A few common misreadings worth heading off:

- **Not the seed.** The seed is `prd.json` + the test files in `<workspace>/tests/` + `seed-meta.json`. `prd.json` is one piece. The terminal `write_seed` call writes all three atomically.
- **Not a worker memory channel.** [Memory channels](../architecture/memory-channels.md) covers the four channels the worker actually reads from — `AGENTS.md`, git history, `progress.txt`, and (indirectly, via task injection) `prd.json`. But the worker never sees the file; only the single task's prompt-shaped slice.
- **Not versioned.** There's no `prd_version` field today, and the schema isn't formally pinned. Breaking changes would require a migration. Most plausible future additions (task dependencies, parallel execution hints, dynamic re-seeding) would all need a version bump *and* probably a migration helper; nothing of the sort exists yet.
- **Not the contract with the human reviewer.** That's `seed-meta.json` (the `tldr`, `open_questions`, `blockers`, `scope_notes`) plus the eventual git diff. `prd.json` is the harness's working state — read by the loop, mutated by the loop, archived alongside `events.jsonl` when the session ends.

## Where to look in the code

| Want to understand… | Read… |
|---|---|
| The shape and validation rules | [`tilth/seed/sink.py`](https://github.com/AlteredCraft/tilth/blob/main/tilth/seed/sink.py) — `_validate`, `_normalise_entry`, the regex constants at the top. |
| How the worker sees one task | [`tilth/memory.py:assemble_task_prompt`](https://github.com/AlteredCraft/tilth/blob/main/tilth/memory.py) — the only path from `prd.json` to the worker's user message. |
| How the judge sees one task | [`tilth/loop.py:_judge_prompt`](https://github.com/AlteredCraft/tilth/blob/main/tilth/loop.py) — assembles description + AC + AGENTS.md + diff into the fresh-context judge call. |
| Task selection | [`tilth/loop.py:_next_pending`](https://github.com/AlteredCraft/tilth/blob/main/tilth/loop.py) — picks the first `pending` entry; that's the whole algorithm. |
| Status mutations | [`tilth/loop.py:_save_prd`](https://github.com/AlteredCraft/tilth/blob/main/tilth/loop.py) call sites — two of them, one per terminal status. |
