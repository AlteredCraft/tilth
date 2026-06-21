# Token recording and enforcement

Tilth records the provider's own `usage` block in **full** — prompt, completion, cached, and reasoning tokens plus the USD cost — and enforces a cumulative session cap **between tasks**. The guiding principle: carry the full detail through every layer; aggregate lossily only at the edges (the cap check and human-facing display). The whole path is short; this page is the map, with pointers into the code.

```mermaid
flowchart LR
  env[TILTH_MAX_TOKENS] --> cfg[TilthConfig.max_tokens<br/>set once, client.py]
  call[every model call<br/>loop._chat_healthy] --> ext[usage.extract_usage<br/>canonical record]
  ext --> rec[session.record_usage<br/>per-actor breakdown]
  rec --> mem[Session.tokens_used<br/>cap counter: prompt + eval]
  rec --> brk[Session.usage<br/>worker / evaluator detail]
  rec --> ckpt[checkpoint.json<br/>persisted each call]
  cfg --> gate{_stop_reason<br/>top of each task}
  mem --> gate
  gate -- "tokens_used ≥ max_tokens" --> stop([stop: token_cap])
```

## One canonical usage record

`tilth/usage.py` is the single source of truth for what a model call cost and how such records combine. `extract_usage` reads a provider `usage` block into one dict — `{prompt, eval, total, cached, reasoning, cost}` — and `add_usage` is the one field-wise combine primitive, reused by the live session and every summary aggregation so the breakdown can never drift between them. The load-bearing invariant: **`cached ⊆ prompt` and `reasoning ⊆ eval`** — they are subsets (cache hits among the prompt tokens; thinking tokens among the completion tokens), never additive, and must never inflate the total or the cap.

## The cap is set once

`tilth/client.py` reads `TILTH_MAX_TOKENS` (default 2,000,000) into `TilthConfig.max_tokens` — one integer per run, never mutated.

## The session owns the running counter

`Session.tokens_used` (`tilth/session.py`) is the live total the cap reads. `record_usage(u, phase)` advances it by `prompt + eval` (cached/reasoning excluded — they are subsets), routes the full record into the actor's `Session.usage` bucket (`worker` / `evaluator`), and **immediately persists `checkpoint.json`**, so if the process dies the next `tilth resume` continues from the saved total *and* breakdown — accounting survives crashes. Both the counter and the breakdown have two homes: in-memory and a JSON file at most one call out of date. (Old checkpoints predate the breakdown; `wake()` defaults it to zero while still restoring the cap counter — the full per-call history remains in `events.jsonl`.)

## One call site records usage

Every model call — worker and evaluator alike — routes through the single provider-health gate `loop._chat_healthy`. It calls `client.chat`, reads the `usage` block into the canonical record, and records it **per attempt** (before logging the event, so `tokens_used_total` is post-increment):

```python
u = usage.extract_usage(resp.get("usage"))
session.record_usage(u, base.get("phase"))   # phase None → worker bucket
```

Deliberate choices:

- **Source of truth = the provider.** No local tokenisation (no `tiktoken`); we trust the `usage` block every OpenAI-compatible endpoint returns. On OpenRouter we send the `usage: {include: true}` opt-in (gated like the reasoning opt-in) so the detail and `cost` are always populated.
- **Tolerant extraction.** `extract_usage` coerces missing/`null`/absent fields to zero rather than crashing a two-hour run on one weird leaf; non-OpenRouter providers simply degrade to prompt/eval/total with the rest zeroed.
- **Cap = `prompt + eval`, not `total`.** We sum the two fields we trust (equivalent to `total_tokens` for a well-formed response); cached/reasoning never count toward the cap, and cost never caps at all — it is display-only.

`_chat_healthy` logs a `model_call` event on every attempt (healthy or not), carrying the full flat detail (`prompt_tokens`, `eval_tokens`, `cached_tokens`, `reasoning_tokens`, `cost`, `tokens_used_total`), the health verdict, and provider evidence — so grepping `events.jsonl` for `model_call` reconstructs exactly when tokens were spent, on what, and why each turn ended. `summary.py` re-aggregates those events into per-session, per-actor, and per-task breakdowns (`summary.json`); the CLI run summary and the visualizer read those. Because recording happens on every attempt, a provider-retry's tokens are counted even though the unhealthy response never became a conversation turn. See [Session layout → Event types](session-layout.md#event-types) and [Hyper-observability](hyper-observability.md).

## Enforcement is at the top of each task

`_stop_reason()` (`tilth/loop.py`) checks both session-level caps before the outer loop picks the next task:

```python
def _stop_reason(client, session):
    if session.elapsed_minutes() >= client.config.max_wall_clock_minutes:
        return "wall_clock"
    if session.tokens_used >= client.config.max_tokens:
        return "token_cap"
    return None
```

So enforcement granularity is **between tasks, not between calls**. A task already running finishes (or hits its iteration cap) even if it tips over the budget mid-task; the cap stops the *next* task from starting.

- **Pro:** never abandon a task half-finished — the branch always has clean per-task commits.
- **Con:** a runaway task can overshoot by up to `MAX_ITERATIONS_PER_TASK × tokens_per_call`.

Hard mid-task enforcement would be the same check inside `_run_task`'s loop with an early break — five lines, but you'd lose the "always finish the current task cleanly" property. That trade-off is [invariant 6](../architecture/overview.md#architecture-invariants-worth-preserving).

## Cumulative tokens, per-resume wall-clock

`checkpoint.json` carries the final `tokens_used`, and `tilth resume` reads it back — so **resuming a token-cap-stopped session re-trips the cap immediately** unless you bump `TILTH_MAX_TOKENS` in `.env` first (env is read on each invocation). Wall-clock is the opposite: `Session.wake()` resets `started_at` to "now" each resume, so that cap is per-resume. **Tokens are cumulative; wall-clock is per-resume** — asymmetric on purpose.

## What it tracks, and what it still won't cap

The accounting now carries the full detail end to end:

- **Dollar cost is recorded** (OpenRouter's own `cost` per call), aggregated per session / per actor / per task, and shown in the CLI summary and visualizer — but it is **display-only**. There is no cost cap; the cap is still tokens (`stop at $50` is not a stop reason). Non-OpenRouter providers report no cost and show `$0`.
- **Worker vs evaluator is split**, not mashed together — `Session.usage` and the summary's `by_phase` keep the allocation, and the cached/reasoning subsets are carried alongside.

What it deliberately still does *not* do:

1. **No cost cap and no headroom *stop*.** The cap is binary on tokens — no "you're at 80%" halt (the visualizer's utilization meters are the at-a-glance version). Easy to add in `_stop_reason`.
2. **The token cap is whole-session, not per-task.** Tasks 1–9 could starve task 10. The iteration cap (32 calls/task) is the per-task proxy.
