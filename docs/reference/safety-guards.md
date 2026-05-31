# Safety guards

Tilth runs autonomously for an hour or two at a time, against a real workspace, with shell access. The safety story is small but deliberate.

## Hard stops

- **Iteration cap per task** (default `8`, env: `TILTH_MAX_ITERATIONS_PER_TASK`).
- **Wall-clock cap per run** (default `120` minutes, env: `TILTH_MAX_WALL_CLOCK_MINUTES`).
- **Token cap per session** (default `2,000,000`, env: `TILTH_MAX_TOKENS`).
- **Optional evaluator-call cap per task** (default `0` = off, env: `MAX_EVALUATOR_CALLS_PER_TASK`).
- **Empty-response abort.** Three consecutive empty model responses (provider hiccup — no content, tool calls, or reasoning) abort the task with reason `empty_responses` and halt the run. Retried with backoff first. Worth a guard because empty turns cost no tokens, so the token cap never catches the spin. Fixed at 3; no env knob.
- **No-case circuit breaker.** A worker that keeps going quiet without calling `submit_case` is nudged up to 3 times, then the task aborts with reason `no_case` and the run halts. Fixed at 3; no env knob.

The full mental model — which cap operates at which layer, how they interact, and what happens on a hit — lives in [How the caps fit together](../deep-dives/caps.md). The empty-response and no-case backstops above are additional run-halting outcomes beyond the four caps.

## Pre-tool veto

The `pre_tool` hook runs before every tool call and can block the call outright. Currently blocked patterns include:

- `git push --force` / `git push -f`
- `git reset --hard`
- `git clean -f` (and force variants — `-fd`, `-fdx`, etc.)
- `sudo` (including chained, e.g. `... && sudo ...`)
- `curl ... | sh` / `wget ... | sh` and friends
- fork bombs

A blocked call surfaces as a tool-error feedback message to the agent on the next iteration. The agent learns "that doesn't work here" and chooses a different approach.

> **Diagram suggestion** — *flow showing tool call → pre_tool hook → either "executes" or "blocked, feedback fed to next iteration." Annotate the silent-on-pass / verbose-on-fail contract.*

## Worktree isolation

Every run creates a per-session git worktree on a `session/<id>` branch in *your* repo's git database. The working tree itself lives under Tilth's `sessions/<id>/workspace/` — see [Session layout](../deep-dives/session-layout.md) for the split. The file tools (`read_file` / `write_file` / `edit_file`) refuse paths that escape the worktree (`tools/files.py:_resolve`). The `bash` tool is **not** path-sandboxed, so treat the worktree boundary as a guard on the file tools, not on shell access — a determined `cd ..` reaches harness state (see the network/shell note below, and [Agent visibility](../deep-dives/agent-visibility.md) on the honest scope of the boundary).

The branch is **never auto-merged**. Open a PR and review like any other branch.

## What's *not* a guard

A few honest gaps to be upfront about:

- **No dollar-cost cap.** The token cap is provider-agnostic, but does not translate to dollars. See [Token recording → What this does *not* do](../deep-dives/token-recording.md#what-this-does-not-do).
- **No headroom warning.** Caps are binary — under, run; at, stop. No "you're at 80%" alert.
- **No mid-task token cut-off.** Token enforcement is between tasks. A single runaway task can overshoot by up to `MAX_ITERATIONS_PER_TASK × tokens_per_call`. The trade-off is intentional; see [Token recording → Enforcement](../deep-dives/token-recording.md#enforcement-is-at-the-top-of-each-task).
- **No allow-listed network egress.** Tools are intentionally narrow (no web fetch, no MCP, no curl-based downloads), so the network surface area is whatever the hard-coded shell guard misses. Treat the harness as having shell access and don't run it on machines you wouldn't run a foreign script on.
