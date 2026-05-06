# Token recording and enforcement

Tokens flow through four files. End-to-end with line numbers (line numbers shift as the file evolves; grep `session.add_tokens` to find them).

> **Diagram suggestion** — *a single horizontal "lifecycle" diagram: env var (TILTH_MAX_TOKENS) → TilthConfig (set once) → Session.tokens_used (in-memory) → checkpoint.json (on disk) → _stop_reason() check. Each arrow labelled with the file/function that performs the transition.*

## The cap is set at startup

`tilth/client.py:54` reads `TILTH_MAX_TOKENS` from the env (default 2,000,000) into `TilthConfig.max_tokens`. One integer, set once per run, never mutated:

```python
max_tokens=int(os.environ.get("TILTH_MAX_TOKENS", "2000000")),
```

## The session owns the running counter

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

## Three call sites record tokens

There's one model call per "spot" in the loop, and each records tokens the same way. All three live in `tilth/loop.py`:

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

## Enforcement is at the top of each task

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

> **Diagram suggestion** — *timeline showing N model calls running through one task, then a `_stop_reason()` check labelled "between-task gate". Annotate the worst-case overshoot region (mid-task tokens above the cap line, but enforcement waits for the gate). Visually communicates the trade-off below.*

Trade-off:

- **Pro:** never abandon a task half-finished. Worktree branch always has a clean per-task commit history.
- **Con:** a single runaway task could overshoot the cap by a meaningful amount before the loop notices. Worst case is bounded by `MAX_ITERATIONS_PER_TASK × tokens_per_call`.

If you wanted hard mid-task enforcement, you'd add the same check inside `_run_task`'s for-loop after each `client.chat(...)` and break early. Five lines, but you lose the "always finish the current task cleanly" property.

## What gets logged on a token-cap stop

`events.jsonl` ends with:

```json
{"ts": "...", "type": "stop", "payload": {"reason": "token_cap"}}
```

`checkpoint.json` has the final `tokens_used` value. So a session that hit the cap is identifiable from either file alone, and `--resume` will see the same `tokens_used` total — meaning **resume of a cap-stopped session will immediately re-trip the cap and stop again**. To resume past a cap, bump `TILTH_MAX_TOKENS` in `.env` before running `--resume`. The harness reads env on each invocation, so the new cap takes effect.

The wall-clock baseline (`started_at`) is treated differently: `Session.wake()` resets it to "now" on every resume so the cap applies *per resume* rather than cumulatively. Without that reset, a resume the next day would trip wall-clock immediately. **Tokens are cumulative; wall-clock is per-resume.** Asymmetric on purpose.

## What this does *not* do

A few honest gaps worth knowing:

1. **No dollar-cost tracking.** Tokens, not dollars. The cap is provider-agnostic — useful as a coarse safety net, useless for "stop when I've spent $50 on this run." Adding dollar tracking means a per-model price table and a cost lookup at each `add_tokens` site. Not in MVP.
2. **No per-model breakdown.** If worker and judge are different models on different providers, the running total mashes them together. Splitting `tokens_used` into `worker_tokens_used` and `judge_tokens_used` is ~10 lines if it ever matters.
3. **No headroom warning.** The cap is binary — under it, run; at it, stop. No "you're at 80% of your token budget" alert. Easy to add in `_stop_reason` if you want it.
4. **The cap is over the whole session, not per-task.** A 10-task run with a 2M cap means tasks 1–9 might gobble tokens and starve task 10. There's no per-task budget. The iteration cap (default 8 model calls per task) is the proxy; combined with average tokens/call, that approximates a per-task ceiling.
