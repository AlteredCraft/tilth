# Architecture overview

Tilth is built around three independently-replaceable components — **Brain**, **Hands**, **Session** — and four memory channels that live outside the agent. The split is intentional: each component has one job and the boundaries are load-bearing for the safety story.

## The three components

### Brain

`tilth/client.py` + `tilth/loop.py`. Ralph loop calling any OpenAI-compatible endpoint via the `openai` Python SDK. Worker and judge can sit on different providers.

The Brain knows how to talk to a model. It does not know how to run code, manage state, or commit work. It hands tool calls off to Hands and writes the audit trail to Session.

### Hands

`tilth/workspace.py` (per-session git worktree) + `tilth/tools/` (allow-listed bash, file ops, search) + `tilth/hooks/` (pre-tool veto, post-edit lint).

Hands knows how to *do things to a workspace*. Every tool call lands here. The pre-tool hook can veto dangerous commands; the post-edit hook can run a linter after a write. The workspace is a per-session git worktree, so Hands' blast radius is bounded to that branch.

### Session

`tilth/session.py`. Append-only `events.jsonl` + `checkpoint.json`, enough to `wake(session_id)` on a fresh process. A `summary.json` is rebuilt at every task boundary (`tilth/summary.py`) as a denormalised view for the visualizer and any external consumers.

Session is the durable record. Everything that happens — every model call, tool call, validator run, judge verdict — is logged here. The agent never sees this layer; it exists for the human reading the run afterwards (and for `--resume` to find its footing).

> **Diagram suggestion** — *three labelled boxes (Brain, Hands, Session) with arrows showing the flow per iteration: Brain emits tool calls → Hands executes → Session logs. A separate dashed arrow shows Session feeding `--resume` on a fresh process.*

## Generator/evaluator separation

A separate **judge** call (`tilth/prompts/judge.md`) reviews each finished task in a fresh context — diff + acceptance criteria, nothing else. The judge is stateless across tasks and (by design) sees none of the worker's chain-of-thought, tool history, or accumulated context. That independence is the whole point of having a judge.

You can route the judge to a *different* provider than the worker via `TILTH_JUDGE_BASE_URL` / `TILTH_JUDGE_API_KEY`. Cross-family judging (e.g. open worker model + frontier closed judge) catches failure modes that same-family judging shares as blind spots.

> **Diagram suggestion** — *split-frame: top half shows the worker's tool-use loop with full history; bottom half shows the judge invocation with only "diff + acceptance criteria" coming in. Emphasises the context isolation between the two roles.*

## Repo layout

```
tilth/
├── README.md, USAGE.md, deep-dives.md, CLAUDE.md
├── pyproject.toml, .env.example, .gitignore
├── tilth/
│   ├── loop.py            # Ralph loop CLI + the inner tool-use loop
│   ├── client.py          # OpenAI-compat wrapper, dual-client routing
│   ├── session.py         # events.jsonl + checkpoint.json + wake()
│   ├── summary.py         # roll events.jsonl into summary.json (denormalised view)
│   ├── memory.py          # AGENTS.md / progress.txt loading + injection
│   ├── workspace.py       # git worktree create / commit / diff
│   ├── validators.py      # ruff + pytest runners
│   ├── tools/             # bash, files, search — registered in __init__.py
│   ├── hooks/             # pre_tool, post_edit
│   ├── prompts/           # system.md, judge.md, agents_update.md
│   └── visualize/         # --visualize: events.jsonl → chat.html
└── sessions/              # per-run state (gitignored)
```

The demo workspace is a separate repo (`AlteredCraft/tilth-demo-todo-cli`) cloned alongside Tilth — by convention at `{{your projects folder}}/tilth-demo`. It is not part of the Tilth repo.

## Architecture invariants worth preserving

These are load-bearing. Read [Deep dives](../deep-dives/index.md) before breaking any of them.

1. **Brain / Hands / Session split.** Don't blur the three. New code goes in the module whose job it is — model calls in `client.py`, sandbox/tool ops in `workspace.py` and `tools/`, durable state in `session.py`.
2. **The agent doesn't see harness mechanics.** No `prd.json` structure, no `events.jsonl`, no `summary.json`, no token counts, no judge, no checkpoints. Hiding these prevents gaming, shortcutting, and self-managed state. New features should preserve this boundary unless explicitly intended otherwise. See [Agent visibility](../deep-dives/agent-visibility.md).
3. **Tool registry is the canonical source for "what tools exist".** `tilth/tools/__init__.py` defines the registry; the system prompt should *not* enumerate tools (it gets stale).
4. **Hook contract: "success silent, failures verbose" — to the *agent*.** Pass states inject nothing into the loop's message history; failures inject a feedback message that the next worker iteration sees. **Telemetry is separate.** Every hook invocation should emit a `hook_run` event regardless of outcome — observability is for the developer reading `events.jsonl`, not the agent.
5. **The worktree branch is never auto-merged.** `commit_task` commits to the session branch; humans review and merge.
6. **Token cap enforcement is between tasks, not mid-task.** The "always finish the current task cleanly" property matters; preserve it. See [Token recording](../deep-dives/token-recording.md).
