# Safety guards

Tilth runs autonomously for an hour or two at a time, against a real workspace, with shell access. The safety story is small but deliberate.

## Hard stops

Tilth enforces a budget so an unattended run can't spin forever. The full mental model — which stop operates at which layer, what a hit looks like, how to resume past one — is in [What can stop a run](../deep-dives/two-loops.md#what-can-stop-a-run). The four tunable caps:

| Cap | Default | Env |
|---|---|---|
| Iteration, per task | `32` | `TILTH_MAX_ITERATIONS_PER_TASK` |
| Wall-clock, per run | `120` min | `TILTH_MAX_WALL_CLOCK_MINUTES` |
| Token, per session | `2,000,000` | `TILTH_MAX_TOKENS` |
| Evaluator-call, per task *(optional)* | `0` = off | `MAX_EVALUATOR_CALLS_PER_TASK` |

Two fixed circuit-breakers have no env knob — they guard against failures the caps can't see:

- **Provider-health gate.** Every model call is checked against the provider's own signals (`error` object, `finish_reason: "error"`, or a completely empty body). Unhealthy responses never enter the conversation — retried with the history untouched (8 attempts ≈ 3 minutes of backoff). Exhaustion aborts the task with `provider_failure` but leaves the session resumable. Worth a guard because unhealthy turns can cost no tokens, so the token cap never catches a bad endpoint.
- **No-case circuit breaker.** A worker that keeps going quiet without calling `submit_case` is nudged up to 3 times, then the task aborts with `no_case` and the run halts.

## Pre-tool veto

The `pre_tool` hook runs before every tool call and can block the call outright. Currently blocked patterns include:

- `git push --force` / `git push -f`
- `git reset --hard`
- `git clean -f` (and force variants — `-fd`, `-fdx`, etc.)
- `sudo` (including chained, e.g. `... && sudo ...`)
- `curl ... | sh` / `wget ... | sh` and friends
- fork bombs

A blocked call surfaces as a tool-error feedback message to the agent on the next iteration. The agent learns "that doesn't work here" and chooses a different approach.

## Worktree isolation

Every run creates a per-session git worktree on a `session/<id>` branch in *your* repo's git database. The working tree itself lives under `~/.tilth/sessions/<id>/workspace/` — see [Session layout](../deep-dives/session-layout.md) for the split. The file tools (`read_file` / `write_file` / `edit_file`) refuse paths that escape the worktree (`tools/files.py:_resolve`). The `bash` tool is **not** path-sandboxed, so treat the worktree boundary as a guard on the file tools, not on shell access — a determined `cd ..` reaches harness state (see the network/shell note below, and [Agent visibility](../architecture/agent-visibility.md) on the honest scope of the boundary).

The branch is **never auto-merged**. Open a PR and review like any other branch.

## What's *not* a guard

A few honest gaps to be upfront about:

- **No dollar-cost cap.** Dollar cost is now recorded and shown (on OpenRouter) but never gates a run — the cap is tokens, not dollars. See [Token recording → What it tracks, and what it still won't cap](../deep-dives/token-recording.md#what-it-tracks-and-what-it-still-wont-cap).
- **No headroom warning.** Caps are binary — under, run; at, stop. No "you're at 80%" alert.
- **No mid-task token cut-off.** Token enforcement is between tasks. A single runaway task can overshoot by up to `MAX_ITERATIONS_PER_TASK × tokens_per_call`. The trade-off is intentional; see [Token recording → Enforcement](../deep-dives/token-recording.md#enforcement-is-at-the-top-of-each-task).
- **No allow-listed network egress.** Tools are intentionally narrow (no web fetch, no MCP, no curl-based downloads), so the network surface area is whatever the hard-coded shell guard misses. Treat the harness as having shell access and don't run it on machines you wouldn't run a foreign script on.
