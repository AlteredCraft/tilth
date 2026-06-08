# Session layout — where a run lives on disk

A single Tilth run has artifacts on disk in two distinct places: the **harness side** (under Tilth's `sessions/<id>/`) and the **target repo side** (a branch and a worktree admin entry inside the repo's `.git/`). This split is deliberate.

![Filesystem trees for one Tilth run: HARNESS SIDE under ~/projects/tilth/sessions/<id>/ holds workspace/, events.jsonl, summary.json, checkpoint.json, chat.html; TARGET REPO SIDE under ~/projects/tilth-demo/.git/ holds refs/heads/session/<id> and worktrees/<id>/. A sage-green arrow labeled 'git worktree binds these' connects the workspace/ on the left to the worktrees/<id>/ admin entry on the right.](../assets/session-layout.png)

*One session, two locations. The sage-green link is the `git worktree` registration that binds them.*
{: .caption }

The agent's *working directory* sits inside Tilth's `sessions/`, but every `git` operation that worktree performs reads and writes the target repo's `.git/`. That's how `git worktree add` works — the worktree directory can live anywhere on disk; its git database is the repo it was created from. `workspace.py:create_worktree` runs `git worktree add <target> -b session/<id>` with `cwd=source` (via `ensure_worktree`, which reuses an existing worktree when `prep-feature` already created one), which registers the worktree under the target repo's `.git/worktrees/` and creates the branch in its refs.

## Why the working tree lives on Tilth's side, not in the target repo

A session has more artifacts than just the worktree — the rest of `sessions/<id>/` (events log, summary, checkpoint, rendered chat, plus the run's durable state — `prd.json`, `progress.txt`, `seed-meta.json`, the per-task `ledger/<task_id>.jsonl` files, any collected `proposed-learnings.md`, and — only when [`TILTH_PROMPT_DUMP`](hyper-observability.md) is on — a `prompts/` directory of per-call request dumps) all belong to one run. Co-locating them under one directory means one logical container per run, and `tilth reset` only has to walk one tree on the harness side.

The flip side: the target repo stays pristine. Tilth never asks you to add anything to your `.gitignore`, and never drops a `.worktrees/` directory at the root of your project. The only thing it writes into the target repo is the branch and the worktree admin entry — both reversible with one `git worktree remove --force` + one `git branch -D`. If you delete your Tilth clone entirely, no harness directories are left behind in your project. `tilth reset` handles both halves cleanly in one command; see [Reset mechanics](reset-mechanics.md).

## Implications worth knowing

- **`ls` in the target repo won't show the worktree.** If you're looking for "where is the agent editing right now," look under Tilth's `sessions/<id>/workspace/`, not in the target repo.
- **Branches accumulate in the target repo, not in Tilth.** Every run leaves a `session/<id>` branch in the target repo's `.git/refs/heads/`. If you delete your Tilth clone without resetting first, those branches stay behind in your project. Clean them up the same way you would any feature branch (`git branch -D session/<id>` or `tilth reset` before you blow Tilth away).
- **Multiple concurrent sessions against the same target repo are fine.** Each gets its own `sessions/<id>/workspace/` directory on the left and its own branch + admin entry on the right. Git is happy to host many worktrees off one repo.
- **The worktree can exist before a run starts.** `tilth prep-feature` creates the `session/<id>` branch and `sessions/<id>/workspace/` worktree up front (`workspace.py:ensure_worktree`) so seed tests land on the branch, not in your source tree. A `prepared` session already has both halves on disk before any `tilth run`.
- **The admin entry is the link.** If the working tree directory under Tilth gets removed manually (e.g., `rm -rf`), the `.git/worktrees/<id>/` admin entry becomes stale; `git worktree prune` cleans it up. `tilth reset` does this correctly.

## Event types

`events.jsonl` is the append-only audit trail — one JSON object per line, `{ts, type, payload, ...}`. The canonical list lives in `tilth/session.py`'s module docstring; this table is the reader's-eye summary. The **Visualizer** column notes whether [`tilth visualize`](../getting-started/visualizing.md) renders a dedicated card for the type or falls through to a generic block.

| Event | Emitted when | Key payload | Visualizer |
|---|---|---|---|
| `session_start` | A session begins (worktree created) | `phase` (`prep-feature` \| `run`), `source` | card |
| `session_prepared` | `prep-feature` wrote a seed bundle | `prd_entries`, `test_files`, `interviewer_model`, `tokens_used` | card |
| `seed_committed` | Prep anchored the seed as one commit | `sha`, `branch` | — |
| `session_resume` | `tilth resume` woke a session | `last_stop`, `retried`, `pending`, `unwound_commit` | card |
| `context_reset` | A new task starts; messages rebuilt from disk | `task_id` | card |
| `prompt_assembled` | A user message is assembled, pre-send | `role` (`worker` \| `evaluator` \| `self_improve`), `iter`, `content` (capped) | — |
| `memory_load` | Memory channels loaded into a prompt | per-channel `present`/`chars`/`truncated`/`sha256_8` | — |
| `model_call` | Any model call returns | `prompt_tokens`, `eval_tokens`, `phase` (non-worker), `attempt` (evaluator), `finish_reason`, reasoning, `prompt_dump` (path; only when `TILTH_PROMPT_DUMP` is on) | card |
| `empty_model_response` | The provider returned an empty turn | `iter`, `streak`, `finish_reason`, token counts | — |
| `tool_call` | The model invoked a tool (incl. `submit_case`) | `tool`, `args` | card |
| `tool_result` | The harness answered a tool call | `tool`, result | card |
| `pre_tool_block` | `pre_tool` vetoed a tool call | `tool`, `reason` | card (special) |
| `hook_run` | A lifecycle hook ran | `hook`, `outcome`, `tool`, `reason?` | — |
| `validator_run` | ruff / pytest / mypy ran | `validator`, pass/fail, output | card |
| `case_parse_error` | A `submit_case` couldn't be parsed | `iter`, `error`, `raw_tool_calls` | — |
| `evaluator_verdict` | The evaluator returned a verdict (successor to `evaluator_verdict`) | `verdict`, `rejection_category`, `concern`, `evidence`, `next_step`, `parse_failed?` | card |
| `evaluator_parse_error` | A `submit_verdict` couldn't be parsed (per attempt) | `attempt`, `error`, `raw_tool_calls` | — |
| `ledger_appended` | An entry was appended to a task's ledger | `task_id`, `iter`, `verdict_summary` | — |
| `commit` | A task's work was committed to the branch | `task_id`, `sha` | card |
| `task_done` | A task was accepted (validators + evaluator) | `task_id` | card |
| `task_failed` | A task could not be completed | `reason` ∈ {`iter_cap`, `evaluator_cap`, `empty_responses`, `no_case`} | card |
| `proposed_learnings` | The self-improvement step ran | `emitted`, `entry?`, `reason?` | card |
| `stop` | The run terminated | `reason` ∈ {`all_done`, `wall_clock`, `token_cap`, `iter_cap`, `interrupted`, `error`} | card |

The full per-entry payload (including the OTel-shape `trace_id` / `span_id` fields every task event carries) is documented in `tilth/session.py`. Per-task **ledger** entries live in `ledger/<task_id>.jsonl`, *not* in `events.jsonl` — `ledger_appended` is only a pointer; see [The worker↔evaluator dialogue](worker-evaluator-dialogue.md).

## See also

- [Resetting a session](../getting-started/resetting.md) — the operator-facing teardown command.
- [Reset mechanics](reset-mechanics.md) — implementation walk-through, idempotency contract.
- [Safety guards → Worktree isolation](../reference/safety-guards.md#worktree-isolation) — the safety story this layout supports.
