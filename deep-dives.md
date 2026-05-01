# Deep dives

Honest, code-level walk-throughs of mechanics that aren't load-bearing for *using* the harness, but matter when you want to extend, debug, or reason about the safety story.

`USAGE.md` covers "how do I run this on my project." This file covers "how does it actually work inside."

---

## 1. The two loops (Ralph vs. tool-use)

There are two loops in the harness, and the names matter because they govern different things.

### Outer loop — `run()` in `loop.py`

The Ralph loop proper. Picks the next pending task from `prd.json`, runs it to completion, resets context, picks the next one.

Bounded by:

- `TILTH_MAX_WALL_CLOCK_MINUTES`
- `TILTH_MAX_TOKENS`
- "no more pending tasks"

This loop has no iteration cap. If you have 20 tasks and the wall-clock and token caps allow it, it'll run all 20.

### Inner loop — `_run_task()` in `loop.py`

The tool-use / ReAct loop *inside* a single task. Bounded by `TILTH_MAX_ITERATIONS_PER_TASK`. **This is what the env var caps.**

```python
for iter_n in range(client.config.max_iterations_per_task):
    resp = client.chat(messages, tools=tool_schemas)
    ...
```

So: "Ralph loop = outer, tool-use loop = inner, the iterations env var caps the inner."

### What one inner iteration actually is

Each iteration is **exactly one worker `client.chat()` call**, plus whatever the harness does in response. Three branches per iteration:

1. **Model emits tool calls.** Harness executes them (with `pre_tool` hook gating, `post_edit` follow-up), appends results as tool messages, `continue` to next iteration.
2. **Model emits no tool calls (claims done).** Harness runs validators (`ruff`, `pytest`). pytest is **filtered to the current task's tests** by filename convention (`tests/test_<task-id-lower>_*.py`); other tests are not run during this task's validation, so a future task's failing tests can't masquerade as the current task's failure and pull the worker into building out-of-scope code.
   - Validators pass → judge call. Judge accepts → `return "done"`. Judge rejects → append rejection as a user message, fall through to next iteration.
   - Validators fail → append failure report as a user message, fall through to next iteration.
3. **Loop falls off the end** — N iterations consumed, model still hasn't both declared done *and* satisfied validators+judge → `return "iter_cap"`. Task gets marked `failed` in `prd.json`, the run halts.

### What does and doesn't count as an iteration

| Action | Counts as an iteration? |
|---|---|
| Worker model call (any of the three branches above) | **Yes** — one per iteration |
| Tool execution (bash, file ops, etc.) | No — runs as part of an iteration |
| Validator runs (ruff, pytest) | No |
| Judge model call | **No** — separate call, not an iteration |
| `_self_improve` AGENTS.md update call | **No** — happens once after the inner loop returns "done" |
| Validator failure feedback round | Yes — the next worker call to fix it is iteration N+1 |
| Judge rejection feedback round | Yes — same reason |

### A subtlety: judge rejections eat iterations

This is worth flagging because it's not obvious. With `MAX_ITERATIONS_PER_TASK=8`:

- Worker spends 5 iterations writing code, declares done.
- Validators pass, judge rejects.
- Worker has 3 iterations left to address the rejection, declare done again, get re-judged.
- If the judge rejects again, worker has fewer iterations to recover.

**A stricter judge effectively shrinks the working iteration budget.** The judge prompt's instruction to "name a specific concern, vague rejections waste worker iterations" exists for this exact reason — every judge rejection costs the worker forward progress on the same fixed budget.

### Worst-case tokens per task

```
worker_tokens × MAX_ITERATIONS_PER_TASK   (8 by default)
+ judge_tokens × number_of_judge_calls     (1 per "I'm done" attempt)
+ self_improve_tokens                      (1 if task succeeds, 0 otherwise)
```

The judge can be called multiple times per task — every "I'm done" attempt that passes validators triggers a judge call. There is no separate cap on judge calls.

### Mental model

- **`MAX_WALL_CLOCK_MINUTES`** and **`MAX_TOKENS`** stop the Ralph loop.
- **`MAX_ITERATIONS_PER_TASK`** stops a task that's spinning. Bounds worker effort *within* a task. Caps tokens per task *indirectly* (no direct per-task token cap exists).

Default `MAX_ITERATIONS_PER_TASK=8` means: each task gets at most 8 worker turns to explore → edit → run tests → fix lint → respond to judge → finally declare done with everything green. For tightly-scoped tasks with upfront tests, that's usually 3–5 in practice. Bumping to 12 or 16 gives the agent more room on harder tasks; lowering to 4 forces tighter PRDs.

---

## 2. Token recording and enforcement

Tokens flow through four files. End-to-end with line numbers:

### The cap is set at startup

`tilth/client.py:54` reads `TILTH_MAX_TOKENS` from the env (default 2,000,000) into `TilthConfig.max_tokens`. One integer, set once per run, never mutated:

```python
max_tokens=int(os.environ.get("TILTH_MAX_TOKENS", "2000000")),
```

### The session owns the running counter

`tilth/session.py` holds the running total on the `Session` dataclass:

```python
@dataclass
class Session:
    ...
    tokens_used: int = 0
```

Two relevant methods:

- **`add_tokens(n)`** — increments the counter and **immediately persists `checkpoint.json`**. That second part matters: if the process dies, the next `--resume` reads the persisted total and continues from there. Token accounting survives crashes.
- **`save_checkpoint()`** — serialises `tokens_used` into `checkpoint.json` along with the rest of the resume state.

```python
def add_tokens(self, n: int) -> None:
    self.tokens_used += n
    self.save_checkpoint()
```

The counter has two homes: an in-memory `int` for the live process, and a JSON file on disk that's at-most-one-call out of date.

### Three call sites record tokens

There's one model call per "spot" in the loop, and each records tokens the same way. All three live in `tilth/loop.py` (line numbers shift as the file evolves; grep `session.add_tokens` to find them):

| Site | Function | What it's calling |
|---|---|---|
| `_judge_task` | `_judge_task` | judge model on a finished task |
| `_self_improve` | `_self_improve` | worker model asking "should AGENTS.md be updated?" |
| `_run_task` | `_run_task` | worker model — the main per-iteration call |

The pattern is the same in all three:

```python
resp = client.chat(...)            # OpenAI-shape response (normalised by client._normalise)
usage = resp.get("usage") or {}
prompt_tokens     = int(usage.get("prompt_tokens") or 0)
eval_tokens       = int(usage.get("completion_tokens") or 0)
session.add_tokens(prompt_tokens + eval_tokens)
```

A few things worth noting about this pattern:

- **Source of truth = the provider.** No local tokenisation (no `tiktoken`). We trust the `usage` block in the response. Every OpenAI-compatible endpoint returns it — that's the one piece of the API surface we depend on.
- **`or 0` everywhere.** If a provider ships a malformed response, the token count silently falls to zero rather than crashing the run. Defensive choice; the alternative is a 2-hour run dying on one weird `null`.
- **`prompt + completion`, not `total`.** Some providers report `total_tokens` separately; we sum the two we trust. Equivalent to `total_tokens` for every well-formed response.

The third site (in `_run_task`) also logs the per-call breakdown to `events.jsonl` as a `model_call` event:

```python
session.log("model_call", {
    "task_id": task["id"],
    "iter": iter_n + 1,
    "prompt_tokens": prompt_tokens,
    "eval_tokens": eval_tokens,
    "tokens_used_total": session.tokens_used,
})
```

That's the audit trail. After a run, grep `events.jsonl` for `model_call` and reconstruct exactly when tokens were spent. The judge and self-improve sites *don't* currently log a per-call event — they update the running total but skip the per-call detail. Symmetry would make the audit cleaner; small TODO.

### Enforcement is at the top of each task

`_stop_reason()` in `tilth/loop.py` checks both wall-clock and token caps:

```python
def _stop_reason(client: LLMClient, session: Session) -> str | None:
    if session.elapsed_minutes() >= client.config.max_wall_clock_minutes:
        return "wall_clock"
    if session.tokens_used >= client.config.max_tokens:
        return "token_cap"
    return None
```

The outer `run()` loop calls it before picking the next task:

```python
def run(worktree, session, client):
    while True:
        stop = _stop_reason(client, session)
        if stop:
            # surfaces the relevant cap value, e.g. [TILTH_MAX_TOKENS=2000000]
            console.print(f"[yellow]stopping: {stop}[/yellow]...")
            session.log("stop", {"reason": stop})
            return
        ...
        task = _next_pending(prd)
        ...
```

So the granularity of enforcement is **between tasks, not between calls**. A task that's already running will finish (or hit its iteration cap) even if it tips us over the token budget mid-task. The cap stops the *next* task from starting.

Trade-off:

- **Pro:** never abandon a task half-finished. Worktree branch always has a clean per-task commit history.
- **Con:** a single runaway task could overshoot the cap by a meaningful amount before the loop notices. Worst case is bounded by `MAX_ITERATIONS_PER_TASK × tokens_per_call`.

If you wanted hard mid-task enforcement, you'd add the same check inside `_run_task`'s for-loop after each `client.chat(...)` and break early. Five lines, but you lose the "always finish the current task cleanly" property.

### What gets logged on a token-cap stop

`events.jsonl` ends with:

```json
{"ts": "...", "type": "stop", "payload": {"reason": "token_cap"}}
```

`checkpoint.json` has the final `tokens_used` value. So a session that hit the cap is identifiable from either file alone, and `--resume` will see the same `tokens_used` total — meaning **resume of a cap-stopped session will immediately re-trip the cap and stop again**. To resume past a cap, bump `TILTH_MAX_TOKENS` in `.env` before running `--resume`. The harness reads env on each invocation, so the new cap takes effect.

The wall-clock baseline (`started_at`) is treated differently: `Session.wake()` resets it to "now" on every resume so the cap applies *per resume* rather than cumulatively. Without that reset, a resume the next day would trip wall-clock immediately. Tokens are cumulative; wall-clock is per-resume. Asymmetric on purpose.

### What this does *not* do

A few honest gaps worth knowing:

1. **No dollar-cost tracking.** Tokens, not dollars. The cap is provider-agnostic — useful as a coarse safety net, useless for "stop when I've spent $50 on this run." Adding dollar tracking means a per-model price table and a cost lookup at each `add_tokens` site. Not in MVP.
2. **No per-model breakdown.** If worker and judge are different models on different providers, the running total mashes them together. Splitting `tokens_used` into `worker_tokens_used` and `judge_tokens_used` is ~10 lines if it ever matters.
3. **No headroom warning.** The cap is binary — under it, run; at it, stop. No "you're at 80% of your token budget" alert. Easy to add in `_stop_reason` if you want it.
4. **The cap is over the whole session, not per-task.** A 10-task run with a 2M cap means tasks 1–9 might gobble tokens and starve task 10. There's no per-task budget. The iteration cap (default 8 model calls per task) is the proxy; combined with average tokens/call, that approximates a per-task ceiling.

---

## 3. What the agent sees (and what it doesn't)

The agent is deliberately walled off from most of the harness's machinery. It sees only the things it needs to do its job; everything else — task queueing, audit logging, token accounting, resume state, the judge, the self-improvement step — is invisible.

This is a load-bearing design choice, not a convenience. Worth understanding before extending the harness.

### Visibility table

| Artifact | Agent's view |
|---|---|
| **Current task** | Sees the task description and acceptance criteria, injected as the user message. Doesn't know the task came from `prd.json`. |
| **`AGENTS.md`** | Sees the *content* (injected at task start). Could also `read_file` it. Updates happen via the `_self_improve` prompt — a separate model call in a different context — that asks "what should I append?" The worker answering that question doesn't know it's writing to AGENTS.md, just answers a structured JSON question. |
| **`progress.txt`** | Sees the last ~30 lines, injected. Doesn't write to it — the harness appends after task done/fail. |
| **`prd.json`** | **Never sees the file or its structure.** Doesn't know it exists as JSON, doesn't know about status fields, doesn't know other tasks are queued. |
| **`events.jsonl`** | **Never sees it.** Pure harness audit log. |
| **`checkpoint.json`** | **Never sees it.** Resume mechanics. |
| **Token counts and caps** | **Never sees them.** Caps enforce silently between tasks. |
| **Worktree branch** | Sees a workspace directory — doesn't know it's a worktree, doesn't know its branch name, doesn't know commits are made on its behalf. |
| **Judge / self-improvement** | **Never sees them.** The judge is a separate fresh-context call; self-improvement is a separate prompt. The worker has no idea its work is being evaluated or reflected on. |
| **Validators** | Sees the *failure report* when validators fail (injected as the next user message). Sees nothing when they pass. |
| **Tool registry** | Sees the JSON schemas of all registered tools on every call (passed via the `tools=` parameter). The system prompt does *not* enumerate them — schemas are the canonical source. |

### Why this separation is deliberate

Three failure modes prevented by hiding mechanics from the agent:

1. **Gaming the judge.** If the agent knew a judge was reading the diff, it would pad commits with explanatory comments aimed at the judge instead of writing working code. Independent evaluation only works if the worker doesn't optimise for it.
2. **Token shortcuts.** If the agent knew the token cap, it would cut corners ("I'll skip writing the test to save tokens"). The only objective the worker is given is "do the task." Cost accounting is the harness's problem, not the agent's.
3. **Trying to manage state itself.** If the agent saw `prd.json`, it would try to mark its own task done, or skip ahead, or rewrite the queue. Both real failure modes seen in earlier hand-built loops. State management belongs in code; the agent works on one task at a time and stops.

### Corollary: AGENTS.md should stay project-focused

A natural follow-on. AGENTS.md is for *project* conventions, not harness mechanics:

- Belongs in AGENTS.md: language version, test framework, file layout, style rules, project-specific gotchas, accumulated learnings.
- Does **not** belong in AGENTS.md: "record token counts in `events.jsonl`" (agent doesn't write that file), "update `prd.json` status when done" (agent doesn't manage prd), "stop after 8 iterations" (handled by `max_iterations_per_task`), "don't run dangerous commands" (handled by `pre_tool` hook), "the judge will evaluate your work" (see "gaming the judge" above).

The cleanest test: if you removed a rule from AGENTS.md and the harness still enforces the underlying behaviour, the rule shouldn't be there. Harness machinery handles the mechanics; AGENTS.md handles the project.

### What the agent *is* explicitly told (in `prompts/system.md`)

- Its role: "focused worker agent operating inside a long-running harness."
- Each task starts a fresh conversation; no memory of prior tasks beyond the loaded context.
- How to use tools (prefer narrow over broad — `read_file` over `bash cat`, `grep` over `bash grep -r`).
- "Done" criteria: acceptance criteria met, tests pass, workspace committable.
- Constraints: no destructive commands, no force pushes, no out-of-workspace edits.
- A self-review reminder: ask "what evidence do I have?" before declaring done.

That's the entire visible contract. Everything else is the harness's job, in code.

### When you'd break this rule

Two cases where it can be right to expose harness internals:

1. **Debugging.** During development, expose the iteration counter to the agent (e.g. inject "you have 3 iterations left" on each turn) to see if behaviour changes. Useful as a probe; remove before production.
2. **Explicit budget signalling.** If you want the agent to plan its work against a known budget — "you have 50K tokens for this task, plan accordingly" — that's a different pattern than the hidden cap, and it requires changing the system prompt's framing (the agent is now a planner, not just a worker). The MVP intentionally doesn't do this; the iteration cap is a hard stop, not a soft hint.

---

## How the caps fit together

At any moment during a run, four things can stop it:

1. **All `prd.json` tasks done** — happy path; outer loop exits cleanly.
2. **`MAX_WALL_CLOCK_MINUTES` exceeded** — outer loop checks at the top of each task; the *current* task finishes first.
3. **`MAX_TOKENS` exceeded** — same enforcement granularity as wall-clock; inter-task only.
4. **`MAX_ITERATIONS_PER_TASK` exceeded inside a single task** — that task is marked `failed`, the run halts (does not continue to the next task — failures are halting events, not skip events). The next `--resume` flips the failed task back to `pending` and the agent retries it with a fresh iteration budget; partial work survives via a soft-reset of the FAILED placeholder commit.

Caps 2 and 3 are session-level. Cap 4 is task-level. There is no per-call cap and no per-task token cap. That's the full safety story.

## 4. Resume mechanics

`--resume` wakes a session and re-enters the outer loop. Three things happen on wake:

1. **`Session.wake()` reads `checkpoint.json`** and reconstructs `tokens_used`, `workspace`, `branch`. `started_at` is reset to `time.time()` (wall-clock budget is per-resume).
2. **`_prepare_resume()` reads the trailing `stop` event** from `events.jsonl` to learn how the previous run ended, then:
   - If `last_stop == "all_done"`, no-op (besides logging).
   - Otherwise, any task in `prd.json` with `status == "failed"` is flipped back to `"pending"` and `ws.unwind_failed_commit()` soft-resets the `FAILED (...)` placeholder commit so the partial work returns to the index. Without that soft-reset, the judge's `task_diff` (HEAD vs working tree) would only see *new* edits on the retry, not the cumulative work — incorrect evaluation.
3. **A `session_resume` event** is logged with the structured plan: `last_stop`, `retried`, `pending`, `unwound_commit`, and a one-line summary. This is the parallel of `session_start` for resumes; both transitions are auditable from `events.jsonl` alone.

Bare `--resume` (no session ID) selects the most recent session in `sessions/` by directory name (the timestamp prefix sorts chronologically). Explicit `--resume <session_id>` is unchanged.

Resume does not loop endlessly. If a retried task hits iter-cap *again*, the outer loop halts with `stop {reason: iter_cap}` just like the original run; the next `--resume` would retry once more. The retries are recursive in invocation, not in mechanism — each one is just a fresh ride through the same loop.

### Resumable-session detection

When you run `uv run tilth <workspace>` (no `--resume` / `--reset`), `_find_resumable_session()` scans `sessions/` newest-first and looks for a directory whose `session_start.source` matches `<workspace>` and whose last `stop.reason` is anything other than `all_done` (or has no `stop` event at all — covers crashes that died before logging). If one exists, the harness prints a heads-up listing the `--resume` / `--reset` recovery commands and pauses 5 seconds before calling `Session.new()`. Ctrl-C during the pause returns 130 cleanly.

The detection is read-only — no files modified, no state mutated. It exists purely to surface that a fresh run will silently abandon resumable progress, which is the failure mode the iteration loop ("halt → tweak → continue") inadvertently optimises for.

---

## 5. Reset mechanics

`--reset [<session_id>]` tears down a session's artifacts. It runs entirely outside the normal loop — no model calls, no validators, no judge. The flow:

1. **Resolve the session.** Bare `--reset` picks the latest by directory name (parallel to `--resume`); explicit `--reset <id>` targets that one.
2. **Recover paths.** `_read_checkpoint()` gives `workspace` (worktree) and `branch`. `_source_for_session()` scans `events.jsonl` for the `session_start` event to recover the source repo (the path is already in the log, so no checkpoint schema change was needed for this).
3. **Confirm.** `input("Continue? [y/N] ")` unless `--yes` is passed. The prompt is the default; `--yes` is the override.
4. **Tear down via `ws.reset_session_state()`:**
   - `git worktree remove --force <worktree>` against the source. Force is always passed: `--reset`'s whole purpose is to discard a session's work, and the user already confirmed via the `[y/N]` prompt (or `--yes`). Refusing on dirty would defeat the user's stated intent. A failure here now indicates a true filesystem-level problem (locks, perms) rather than uncommitted changes.
   - `git branch -D session/<id>` against the source (force-delete is correct for the `session/*` namespace, which is never auto-merged).
   - `shutil.rmtree(sessions/<id>/)` for whatever's left on the harness side.
5. Each step is **idempotent** — already-missing pieces are reported as skipped, not errored. You can run `--reset` against a half-cleaned-up state and it'll finish the job.

There's no `--reset --all` and no "keep events" mode. The `[y/N]` prompt is the only safety gate; once you confirm, the session is gone.
