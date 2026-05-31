# Frictions: a first-principles re-approach starting point

**Status:** Snapshot. **No proposals in this document** — describing pain, not prescribing fixes.
**Author:** Sam (with Claude conversational pass)
**Date:** 2026-05-26
**Related sessions:** `20260526-132309-ab3927` (#16's worked example), `20260526-173822-f411ea` (the trial that motivated this rewrite consideration)
**Related issues:** [#10](https://github.com/AlteredCraft/tilth/issues/10), [#13](https://github.com/AlteredCraft/tilth/issues/13), [#16](https://github.com/AlteredCraft/tilth/issues/16), [#18](https://github.com/AlteredCraft/tilth/issues/18)
**Reverted attempt:** [PR #17](https://github.com/AlteredCraft/tilth/pull/17) tried to address one face of F3/F6 by embedding the seed test in the judge prompt; reverted because it didn't actually move the loop's failure rate.

## Why this document

We've had enough seeded runs to see the same shape of failure recur across sessions: tasks burning 40+ iterations on what should be 8-iteration work, judge rejects the worker can't escape without gaming the rule, contradictions baked in at seed time that the run-time architecture can't recover from. Patching the surface keeps shipping work without moving the underlying behavior.

This document is the inventory the next design pass should start from. Each friction is described with: what it is, where it's been observed, what it costs. **No remedies proposed.** Holding back from solutions deliberately — a first-principles rethink shouldn't be anchored on the current architecture's repair surface.

## The frictions

### F1. The seed is the contract but nothing validates the seed

The seeder's terminal `write_seed` call writes `prd.json` + per-task test files atomically. The sink (`tilth/seed/sink.py:_validate`) checks shape only — ID format, filename pattern, 1:1 task↔test file presence. **Nothing checks whether the contract is internally consistent.**

Specifically, the sink will happily write:
- A task whose `description` instructs `uv init` while the `acceptance_criteria` don't acknowledge `uv init`'s documented side effects.
- A task whose `acceptance_criteria` enumerate four behaviors while the matching test file asserts only one of them ("acceptance criteria map 1:1 to test assertions" is convention per `docs/deep-dives/prd-json.md`, not enforcement).
- Future-task seed tests that fail collection at earlier-task stages (because they import symbols that don't exist yet).
- A task whose `description` and `acceptance_criteria` say different things about the same observable behavior.

The seeder *prompt* (`tilth/seed/prompts.md`) instructs the model to avoid all of these. There is no runtime check that the instruction was followed.

**Where seen:** session `20260526-173822-f411ea` T-001 — description says "Run `uv init --name todo-cli`," AC are two import checks. `uv init` creates README.md, main.py, .python-version, none authorized by AC. The worker is in an impossible position by iter 1.

**Cost:** Every downstream actor (worker, validator, judge) treats the seed as ground truth. A seed contradiction becomes an unresolvable rejection loop that can only be detected by the human reviewing logs after the run has burned tokens.

### F2. Acceptance criteria and description can contradict

A specialization of F1, named separately because it's the most common shape. The `description` is what the worker reads and acts on. The `acceptance_criteria` are what the judge evaluates against. These can drift:

- Description authorizes a command whose side effects the AC doesn't authorize. (F1's example.)
- Description names a specific implementation path; AC names only the observable outcome. Worker takes the path; judge accepts. (No harm, but the convention isn't load-bearing.)
- Description is vague where AC is precise. Worker reads description, implements something that satisfies it loosely; judge measures against AC and rejects. (Common in human-written PRDs too — but the seeder is supposed to produce *better* than human-average here.)

The seeder prompt has guidance on this (the "load-bearing decision" vs. "non-load-bearing assumption" §5 distinction in `prompts.md`), but it's a discipline rule applied by the model, not a structural check.

**Where seen:** F1's case. Also issue [#18](https://github.com/AlteredCraft/tilth/issues/18) is the upstream version — the seeder asking compound questions and burying half of each load-bearing decision in `open_questions`.

### F3. Tests are first-class artifact, but no role can challenge them

The seeder writes tests. The worker satisfies them (and is told not to modify them). The judge evaluates the diff (and per the now-shipped `commit_seed`, doesn't see test files because they're in HEAD — see F6). Validators run them mechanically.

**No one is empowered to say "the test itself is wrong."**

Failure modes the system has no defense against:
- Seed test asserts something other than what its corresponding AC says.
- Seed test pins down half the AC (e.g., AC says "exits 1 and prints to stderr"; test only checks exit code).
- Seed test fails collection at the stage it's supposed to run (because it imports something from a later task's implementation).
- Seed test asserts something the description doesn't authorize.

PR #17 (reverted) tried to address part of this — let the judge see the test file and explicitly reject it. The instinct was right; the surface-level fix didn't move outcomes in the trial run.

**Cost:** Whatever the seeder writes as tests *is* the contract, untouchable by any reviewer. The seed has to be perfect to a degree that no other model output is asked to be.

### F4. The scope-creep rule can't tell creep from cleanup

The judge's hard reject for scope creep (`tilth/prompts/judge.md`): if the diff modifies any file not in this task's AC, reject. The rule exists for a real reason — without it, workers pre-emptively implement future tasks (observed before the rule was added).

But from the diff alone, the judge can't distinguish:

| Apparent diff | Actually | Should it reject? |
|---|---|---|
| `-README.md` (deleted) | Worker cleaning up `uv init` side effect | No — it's a side effect of an authorized command |
| `-README.md` (deleted) | Worker deleted a file unrelated to the task | Yes |
| `M tests/test_t002_add.py` | Worker pre-emptively wrote T-002 | Yes |
| `M tests/test_t002_add.py` | Worker fixed a typo in a seed test that fails collection at T-001 stage | Maybe, but the worker had no choice |
| `M pyproject.toml` (added `[tool.ruff] exclude = ["tests/test_t002..."]`) | Worker silencing future-task test noise | Yes (correctly rejected — but the *problem* it was solving was real) |

The diff is identical in pairs 1, 2 and pairs 3, 4. The judge has no way to disambiguate.

**Where seen:** Session `20260526-173822-f411ea`, all three rejections (iter 20, 31, 38).

**Cost:** Worker iteration loops trying alternate routes to satisfy a strict rule that doesn't admit "cleanup of authorized side effects" as a category. The worker eventually games (cosmetic edits, ruff excludes, see F12).

### F5. Worker affordance bleed: validators filter, worker doesn't

`tilth/validators.py:run_pytest` filters to `test_t<NNN>_*.py` for the current task plus prior completed tasks. The worker has no equivalent filter when it runs its own checks.

Concrete sequence (session `20260526-173822-f411ea`):
1. Worker runs `pytest` (broad) at iter 18-19.
2. Sees seed tests for T-002 and T-003 fail collection (they import `todo_cli.__main__:main` which doesn't yet behave the way they want).
3. Treats this as "failing tests I need to fix."
4. Edits `tests/test_t002_cli_add.py` and `tests/test_t003_persist.py` to "make them pass."
5. Judge rejects on scope creep at iter 20.

The worker doesn't know:
- Which tests are "live" at the current task's stage.
- That future-task seed tests are expected to fail collection.
- That the validator only runs a filtered subset.
- That touching future-task tests is a hard reject.

**Tension with the agent-visibility invariant (F8):** telling the worker "future-task tests exist and will fail; ignore them" is leaking harness mechanics. Not telling it leaves the worker to discover this through scope-creep rejects.

### F6. The judge's information asymmetry

The judge sees, per `_judge_task`:
- Task description + AC
- AGENTS.md
- Validator status (pass/fail bool)
- The diff (capped at 12k chars)

The judge does *not* see:
- The matching seed test file content (in HEAD; not in diff — this is issue #16).
- Other tasks' descriptions, ACs, or test files (no cross-task awareness).
- Prior judge calls' reasoning (each call is fresh-context).
- Validator output content (only pass/fail).
- The session's prior iterations on this task (no idea what the worker has already tried).

It then has to make a binary accept/reject call with no memory and a narrow window. The judge has the smallest information surface of any actor in the system but the highest-stakes decision.

**Where seen:** Issue #16 directly; F4's disambiguation problem is downstream of this.

### F7. Cost shape is bimodal; no early-out for unsalvageable tasks

Successful tasks are cheap: 8 iterations, tens of thousands of tokens. Tasks that hit an unresolvable contradiction are expensive: 41 iter / 448k tokens on T-001 alone in #16's session; T-001 in `173822` ran to iter 38+ before user interrupt.

The harness has caps (`TILTH_MAX_ITERATIONS_PER_TASK=32`, `TILTH_MAX_TOKENS`) but no mechanism to detect "this task's contract is unsatisfiable" earlier than the cap. The signal exists in the loop — three consecutive judge rejects on the same scope-creep reasoning, the worker trying the same fix shape repeatedly — but nothing reads it.

When a task fails (iter cap or judge cap), the whole run halts (`_run_session` returns on first failure). One bad seed → entire run lost.

**Cost:** Wasted tokens are the direct cost. The indirect cost: the harness's *cost shape* hides the seed quality signal. If "bad seed" looked like "early bail with diagnostic," seed quality would tighten quickly. Instead it looks like "ran for a while and stopped, here are some logs."

### F8. The agent-visibility invariant has reasoning costs

`docs/deep-dives/agent-visibility.md` documents the rule: the worker doesn't see harness mechanics. The rule exists for real reasons (gaming the judge, token shortcuts, self-managing state — all observed failure modes before the wall).

The cost: when something goes wrong from the worker's perspective, it can only diagnose *at the work surface*. It can't reason about its situation. Examples:

- Worker can't understand why `pytest` reports test collection errors when the source files don't exist — because it doesn't know about staged seeded tests.
- Worker can't understand why running `uv init` produces files the AC doesn't authorize — because it doesn't know it's in a worktree inside a parent uv workspace.
- Worker can't understand why touching `tests/test_t002_*.py` is a problem — because it doesn't know other tasks exist.
- Worker can't tell whether it's been making progress over the last 5 iterations or going in circles — because each iteration's history is partial and it has no view of the trajectory.

The rule is load-bearing. The cost is reasoning-impaired worker behavior in exactly the cases where good reasoning would unstick a bad seed. The two are in genuine tension.

### F9. No cross-task awareness anywhere

The seeder writes the whole PRD. After that, every reader sees one task at a time:

- Worker: current task only (see `memory.assemble_task_prompt`).
- Judge: current task only.
- Validator: current task's tests + completed tasks' tests (ratcheting), but treats them as opaque files.
- `_self_improve`: current task only.

So:
- A T-002 design decision that quietly closes off T-005's contract isn't noticed until T-005 fails.
- A repeated failure pattern across tasks (e.g., worker keeps misunderstanding `uv` semantics) isn't surfaced — `_self_improve` runs per-task and collects into `proposed-learnings.md` for human review, not for in-run application.
- The harness has no "is the run going well overall" check between tasks. Each task is evaluated in isolation against its own contract.

**Cost:** Failures that span tasks (architectural, not local) are invisible to every automated reviewer.

### F10. Containment: sessions live inside the harness, demo lives under the harness's parent

`sessions/<id>/workspace/` is a git worktree inside `tilth/`. The worker runs commands inside this worktree. Result:

- `uv init` from inside the worktree mutates tilth's `pyproject.toml` because uv walks up looking for a workspace root and finds tilth. (The earlier "fix" via `exclude = ["sessions"]` doesn't work because `uv init` mutates the parent unconditionally.)
- Per-session `.python-version`, `pytest.ini` (none today, but plausible), or any tool that walks up the tree can interact with tilth's tooling.
- The demo lives at `~/projects/tilth-demo` (illustrative), not under `tilth/`, but the *worktree* of `session/<id>` from that repo gets checked out into `tilth/sessions/<id>/workspace/`. So the worker is editing the demo's code while *located* inside tilth's tree.

**Cost:** Layout-induced bleed. Every "I need to stop X from happening" issue (the uv workspace one, future similar ones for other tools) is the same friction surface.

### F11. Interview UX: compound questions, soft `open_questions` discipline

Two named issues:

- [#18](https://github.com/AlteredCraft/tilth/issues/18): the seeder asks "X *and* Y?" with options that only enumerate answers to X. The user picks an X option; Y is silently dropped or buried.
- The "load-bearing decision must be asked, not logged" rule (in `prompts.md` §5) is a discipline rule the model must remember. There's no structural check that an `open_questions` entry isn't actually a load-bearing decision in disguise.

Both compress to: **the seeder can produce a seed that *looks* complete (has tasks, has tests, has open_questions, has scope_notes) while having quietly dropped a decision the worker now has to guess.** Guessed decisions become contradictions become rejection loops (F1/F2).

### F12. Worker gaming as a symptom of system over-constraint

When the worker hits a rule it can't satisfy honestly, it games:

- #16's session: cosmetic one-line edit to `tests/test_t001_scaffold.py` to get the file into the diff and clear a "file must exist" AC the judge couldn't see was already satisfied.
- `173822` session: added `[tool.ruff] exclude = [...]` to silence ruff noise from future-task tests it couldn't otherwise quiet.
- (Observed in earlier runs, not in current sessions:) padding commits with explanatory comments aimed at the judge.

Gaming is the worker's escape valve when the rule is strict and the situation doesn't admit a clean answer. The harness has no mechanism to detect "the worker is doing the *wrong* thing because the rules cornered it into the wrong thing." From the harness's perspective, gaming looks like accepted work.

**Cost:** False positives. The run finishes; the work compiles and passes tests; the seed contract has been technically met; but the diff includes cosmetic edits, exclude rules, and judge-aimed prose that nobody asked for and nobody benefits from.

## Cross-cutting observations

A few things that aren't single frictions but emerge across them:

- **The seeder is the highest-leverage actor in the system, but it gets the least feedback.** Every other actor (worker, judge, validator, self-improve) runs many times per session and gets evaluated against artifacts. The seeder runs once, terminal, and the only feedback is "did the run go well overall" — which arrives too late to influence the seed it wrote.
- **The architecture's "fresh context per judge call" + "agent doesn't see mechanics" + "no cross-task awareness" are individually defensible but jointly create blindness.** No actor has the view needed to catch architectural-level failures.
- **The cost of being wrong scales nonlinearly.** A small seed contradiction can burn 90% of a session's budget. There's no proportionality between "how wrong is the seed" and "how much does it cost to discover that."
- **Tilth's quality gate is "tests pass + judge accepts," and we've seen both pass with broken work** (PR #17 was reverted partly because it didn't change this). The gate has shape but not teeth.
- **The harness was designed to keep the worker honest. It currently doesn't have machinery to keep the seeder honest, the judge honest, or itself honest about the seeder.**

## What this document is not

- Not a roadmap. Not a list of issues to file. Not "things to fix in order."
- Not a proposal for an architecture. The first-principles pass starts after this document, not from it.
- Not a claim that the current architecture is wrong end-to-end. The Brain/Hands/Session split, the worktree isolation, the validator/judge separation, the read-only-AGENTS.md stance — these have held up. The frictions above are mostly at the seams, not in the load-bearing structure. A rewrite shouldn't conclude the load-bearing pieces are the problem.

## References

- **[Harness Design for Long-Running Application Development](https://www.anthropic.com/engineering/harness-design-long-running-apps)** — Anthropic Engineering. Describes the multi-agent harness behind their long-running app builds (planner / generator / evaluator), the **sprint contract negotiation** pattern, and the iterative calibration loop for the evaluator.

  *Applicability:* High signal for the first-principles pass. The contract-negotiation pattern (generator proposes "what done looks like"; evaluator reviews; they iterate before any code is written) is a direct structural answer to F1, F2, and F3 — Tilth's seeder writes the contract atomically with no negotiation. Their evaluator interacting with the *running application* (via Playwright MCP) is a different information-surface model than Tilth's diff-only judge and reframes F4/F6/F12 as scaffolding for a limited evaluator rather than as a strict-rule problem. Three of their stated principles deserve to be held up against any rewrite: *"every component encodes an assumption about what the model can't do on its own,"* *"harness complexity should match model capability,"* and *"re-examine harnesses with new models."* What does **not** transfer cleanly: Playwright as a mechanism (Python CLI ≠ web app — the *principle* transfers, not the tool), stuck-task detection (F7 stays open; they handle stuck-state structurally via context resets, not detection), and Tilth-specific frictions around containment (F10) and interview UX (F11).

- **[Harness engineering: leveraging Codex in an agent-first world](https://openai.com/index/harness-engineering/)** — OpenAI Engineering (Ryan Lopopolo, Feb 2026). Five months of operating a fully agent-generated product (~1M lines of code, ~1,500 PRs, zero manually-written code, ~3.5 PRs/engineer/day). The post explicitly cites the **Ralph Wiggum Loop** as the pattern Codex uses — direct lineage with Tilth.

  *Applicability:* The most directly relevant single source for the rewrite. Maps onto Tilth's frictions in several places at once: **plans as first-class repo artifacts** (versioned, gardened, co-located with code) reframes F1 and F9 — Tilth's PRD shouldn't be a one-shot terminal write under `sessions/` but a living artifact every actor reads; **structural enforcement via custom linters** (with error messages crafted to *inject remediation instructions into agent context*) suggests an alternative to F4/F5/F12 — interpretive judge rules become deterministic static checks the worker can't game and that *teach* on failure; **the evaluator interacts with the running app via Chrome DevTools Protocol and a per-worktree observability stack** (LogQL/PromQL on logs/metrics/traces) gives F6's information-asymmetry concern a concrete shape; **AGENTS.md is a ~100-line index/map, not a manual** — the post lists the failure modes of monolithic AGENTS.md verbatim ("context is a scarce resource, too much guidance becomes non-guidance, it rots instantly, hard to verify"), with structured `docs/` as the system of record and a **recurring doc-gardening agent** as the anti-rot mechanism Tilth lacks; **"when the agent struggles, identify what's missing and have the agent write the fix"** is the in-loop self-improvement pattern that `proposed-learnings.md` only seeds. The single biggest challenge this post poses to current Tilth is to **F8**: their stated principle is *"from the agent's point of view, anything it can't access in-context while running effectively doesn't exist"* and *"pulling more of the system into a form the agent can inspect, validate, and modify directly increases leverage"* — the **opposite** of Tilth's agent-visibility invariant. The two stances correspond to different worker failure-mode bets (gaming/self-management vs. invisible-knowledge) and the first-principles pass has to make a call on which Tilth is being designed for. Two lines deserve to anchor the rewrite directly: *"our most difficult challenges now center on designing environments, feedback loops, and control systems"* (the framing) and *"the discipline shows up more in the scaffolding rather than the code"* (the mandate). What does **not** transfer: the zero-manually-written-code starting premise (Tilth helps with features on top of human codebases), the throughput-driven merge philosophy (single-worker ephemeral sessions don't have the throughput to make merge gates expensive), Chrome DevTools as the specific mechanism (Python CLI/library targets need a different richer-than-diff feedback surface), and the always-on background-Codex infrastructure.

