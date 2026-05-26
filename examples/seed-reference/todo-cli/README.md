# Seed reference — `todo-cli`

A frozen snapshot of what a finished Tilth task seed looks like, captured from
the demo workspace ([`AlteredCraft/tilth-demo-todo-cli`](https://github.com/AlteredCraft/tilth-demo-todo-cli))
before the Phase 1 artifact moves removed `prd.json` and the test files from
the demo's `main` branch.

## What's here

- `prd.json` — the 5-task seed for the todo CLI (T-001 through T-005).
- `tests/test_t00*.py` — the matching acceptance tests, one per task,
  using subprocess + `tmp_path` against `python3 -m todo_cli`.

## Why it lives here

The seed is no longer a checked-in artifact in the demo repo — `tilth
prep-feature` (Phase 2) produces seeds at run time, and the demo demonstrates
that flow rather than skipping it. This directory keeps the canonical
hand-crafted example reachable as a teaching artifact: when you read
`docs/getting-started/your-own-project.md` and want to see *what a good seed
looks like after the interview is done*, this is the answer.

## Reading it

- The `description` field is what the worker agent sees as the user prompt for
  that task; notice the level of concreteness (exact file paths, exact stdout,
  exact exit codes).
- Each `acceptance_criteria` bullet maps 1:1 to a test function in the
  corresponding `test_t00N_*.py`. That mapping is the quality gate — a criterion
  the tests don't pin down is decorative; an assertion that doesn't correspond
  to a criterion is scope creep.
- Test files follow the project's existing style (subprocess + `tmp_path`).
  Match the project; don't introduce new patterns.

## Not load-bearing

Nothing in the harness reads from `examples/`. Editing or deleting this
directory has no runtime effect — it's documentation by example.
