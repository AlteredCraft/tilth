# Session layout — where a run lives on disk

A single Tilth run has artifacts on disk in two distinct places: the **harness side** (under Tilth's `sessions/<id>/`) and the **target repo side** (a branch and a worktree admin entry inside the repo's `.git/`). This split is deliberate.

![Filesystem trees for one Tilth run: HARNESS SIDE under ~/projects/tilth/sessions/<id>/ holds workspace/, events.jsonl, summary.json, checkpoint.json, chat.html; TARGET REPO SIDE under ~/projects/tilth-demo/.git/ holds refs/heads/session/<id> and worktrees/<id>/. A sage-green arrow labeled 'git worktree binds these' connects the workspace/ on the left to the worktrees/<id>/ admin entry on the right.](../assets/session-layout.png)

*One session, two locations. The sage-green link is the `git worktree` registration that binds them.*
{: .caption }

The agent's *working directory* sits inside Tilth's `sessions/`, but every `git` operation that worktree performs reads and writes the target repo's `.git/`. That's how `git worktree add` works — the worktree directory can live anywhere on disk; its git database is the repo it was created from. `workspace.py:create_worktree` runs `git worktree add <target> -b session/<id>` with `cwd=source`, which registers the worktree under the target repo's `.git/worktrees/` and creates the branch in its refs.

## Why the working tree lives on Tilth's side, not in the target repo

A session has more artifacts than just the worktree — the rest of `sessions/<id>/` (events log, summary, checkpoint, rendered chat) all belong to one run. Co-locating them under one directory means one logical container per run, and `--reset` only has to walk one tree on the harness side.

The flip side: the target repo stays pristine. Tilth never asks you to add anything to your `.gitignore`, and never drops a `.worktrees/` directory at the root of your project. The only thing it writes into the target repo is the branch and the worktree admin entry — both reversible with one `git worktree remove --force` + one `git branch -D`. If you delete `~/projects/tilth` entirely, no harness directories are left behind in your project. `--reset` handles both halves cleanly in one command; see [Reset mechanics](reset-mechanics.md).

## Implications worth knowing

- **`ls` in the target repo won't show the worktree.** If you're looking for "where is the agent editing right now," look under Tilth's `sessions/<id>/workspace/`, not in the target repo.
- **Branches accumulate in the target repo, not in Tilth.** Every run leaves a `session/<id>` branch in the target repo's `.git/refs/heads/`. If you delete `~/projects/tilth` without resetting first, those branches stay behind in your project. Clean them up the same way you would any feature branch (`git branch -D session/<id>` or `--reset` before you blow Tilth away).
- **Multiple concurrent sessions against the same target repo are fine.** Each gets its own `sessions/<id>/workspace/` directory on the left and its own branch + admin entry on the right. Git is happy to host many worktrees off one repo.
- **The admin entry is the link.** If the working tree directory under Tilth gets removed manually (e.g., `rm -rf`), the `.git/worktrees/<id>/` admin entry becomes stale; `git worktree prune` cleans it up. `--reset` does this correctly.

## See also

- [Resetting a session](../getting-started/resetting.md) — the operator-facing teardown command.
- [Reset mechanics](reset-mechanics.md) — implementation walk-through, idempotency contract.
- [Safety guards → Worktree isolation](../reference/safety-guards.md#worktree-isolation) — the safety story this layout supports.
