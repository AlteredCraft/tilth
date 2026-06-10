# Architecture overview

Tilth is built around three independently-replaceable components — **Brain**, **Hands**, **Session** — and the [memory channels](memory-channels.md) that live outside the agent. The split is intentional: each component has one job and the boundaries are load-bearing for the safety story.

## The three components

### Brain

`tilth/client.py` — the LLM-reasoning role: an OpenAI-compatible model call via the `openai` Python SDK. Tilth instantiates this role twice, in different shapes:

- **Worker Brain** — runs in a tool-use loop with full message history accumulated across iterations on the current task, and Hands access. The Brain that *does the work*.
- **Evaluator Brain** — invoked once per submitted case (a task may be rejected and re-submitted several times), in a context fresh except for a per-task ledger of its prior verdicts, with no tool access. Receives the diff against the task's acceptance criteria and returns a structured verdict (`submit_verdict`): accept, or reject with a typed `rejection_category` (one of six) plus a concrete `next_step`. The Brain that *reviews the work*. (The role is the *evaluator*)

The Ralph loop (`tilth/loop.py`) orchestrates both: it drives the Worker until it presents its case (`submit_case`), then calls the Evaluator once. There is no codified validator step between them — **the evaluator is the only gate**. An Evaluator invocation is itself one-shot — it is not in a loop, though the worker↔evaluator exchange can repeat several times within a task. See [The worker↔evaluator dialogue](../deep-dives/worker-evaluator-dialogue.md).

The Brain knows how to talk to a model. It does not know how to run code, manage state, or commit work. It hands tool calls off to Hands and writes the audit trail to Session.

### Hands

`tilth/workspace.py` (per-session git worktree) + `tilth/tools/` (allow-listed bash, file ops, search) + `tilth/hooks/` (pre-tool veto).

Hands knows how to *do things to a workspace*. Every tool call lands here. The pre-tool hook can veto dangerous commands. The workspace is a per-session git worktree, so Hands' blast radius is bounded to that branch.

Today only the Worker Brain has Hands; the Evaluator runs without tools by design — its independence from the worker's tool history is the whole point. Nothing in the architecture prevents a future Evaluator from being given a constrained set of (likely read-only) Hands — e.g. running tests or fetching additional context — but the current implementation keeps it pure-evaluation.

### Session

`tilth/session.py`. Append-only `events.jsonl` + `checkpoint.json`, enough to `wake(session_id)` on a fresh process. A `summary.json` is rebuilt at every task boundary (`tilth/summary.py`) as a denormalised view for the visualizer and any external consumers. Per-task evaluator ledgers live alongside it under `sessions/<id>/ledger/` and are re-read on resume.

Session is the durable record. Everything that happens — every model call, tool call, evaluator verdict — is logged here. The agent never sees this layer; it exists for the human reading the run afterwards (and for `tilth resume` to find its footing). What that durable record buys you — every prompt recorded, every run replayable — is the subject of [Hyper-observability](../deep-dives/hyper-observability.md).

This page is the *who*; for the *what flows through* — the artifacts the loop reads, the artifacts it writes, and the three that do both — see [Anatomy of a run](anatomy-of-a-run.md).

> **Diagram suggestion** — *three labelled boxes (Brain, Hands, Session) with arrows showing the flow per iteration: Brain emits tool calls → Hands executes → Session logs. A separate dashed arrow shows Session feeding `tilth resume` on a fresh process.*

## Generator/evaluator separation

The Worker/Evaluator split above is a deliberate generator/evaluator separation. The Evaluator Brain (`tilth/prompts/evaluator.md`) sees none of the worker's chain-of-thought or tool history — only what's *about the work*: the task and its acceptance criteria, the feature overview, the project-context files, the worker's structured case, the diff, and its own prior verdicts on this task (the ledger). That isolation from the worker's reasoning is what makes the verdict useful; if the evaluator could see the worker's chain-of-thought, it would tend to agree with it.

You can route the evaluator to a *different* provider than the worker via `TILTH_EVALUATOR_BASE_URL` / `TILTH_EVALUATOR_API_KEY`. Cross-family evaluation (e.g. open worker model + frontier closed evaluator) catches failure modes that same-family evaluation shares as blind spots.

> **Diagram suggestion** — *split-frame: top half shows the worker's tool-use loop with full history; bottom half shows the evaluator invocation with only "diff + case + acceptance criteria + overview" coming in. Emphasises the context isolation between the two roles.*

## Repo layout

```
tilth/
├── README.md, CLAUDE.md, mkdocs.yml
├── docs/                  # MkDocs source — annotated nav in mkdocs.yml is the topic index
├── pyproject.toml, .env.example, .gitignore
├── tilth/
│   ├── cli.py             # verb-routed entry: run / resume / reset / visualize
│   ├── loop.py            # Ralph loop + inner tool-use loop + subcommand handlers
│   ├── client.py          # OpenAI-compat wrapper, dual-client routing (worker / evaluator)
│   ├── session.py         # events.jsonl + checkpoint.json + ledger + wake()
│   ├── summary.py         # roll events.jsonl into summary.json (denormalised view)
│   ├── memory.py          # context files / progress.txt / overview / full-plan injection
│   ├── tasks.py           # load + validate <workspace>/.tilth/tasks/ (overview + T-NNN files)
│   ├── workspace.py       # git worktree create / commit / diff
│   ├── case.py            # worker submit_case schema / parse / render
│   ├── verdict.py         # evaluator submit_verdict schema / parse / ledger format
│   ├── tools/             # bash, files, search — registered in __init__.py
│   ├── hooks/             # pre_tool
│   ├── prompts/           # system.md, evaluator.md
│   └── visualize/         # tilth visualize: live web viewer over sessions/
└── sessions/              # per-run state (gitignored)
```

The demo workspace is a separate repo (`AlteredCraft/tilth-demo-todo-cli`) — not part of the Tilth repo. Clone it wherever you keep code; the docs use `~/projects/tilth-demo` as an illustrative path, but the location is arbitrary.

## Architecture invariants worth preserving

These are load-bearing. Read [Deep dives](../deep-dives/index.md) before breaking any of them.

1. **Brain / Hands / Session split.** Don't blur the three. New code goes in the module whose job it is — model calls in `client.py`, sandbox/tool ops in `workspace.py` and `tools/`, durable state in `session.py`.
2. **The agent doesn't see harness mechanics.** No `task-status.json`, no `events.jsonl`, no `summary.json`, no token counts, no checkpoints. Hiding these prevents gaming, shortcutting, and self-managed state. The visibility expansion softened this deliberately: the worker now sees the feature overview and the whole task list *as prose context* (not the mutable status store), and the evaluator's prior verdicts on its current task — so it can act on review feedback. It still never sees the harness files, token counts, checkpoints, or the queue-management machinery. **Honest scope:** even the hidden part is a *design goal*, not an enforcement guarantee in default mode — the worker has `bash` and could reach harness state via relative paths from the worktree (`sessions/<id>/workspace/`). Real enforcement is opt-in process isolation, planned in [#13](https://github.com/AlteredCraft/tilth/issues/13). See [Agent visibility](../deep-dives/agent-visibility.md).
3. **Tool registry is the canonical source for "what tools exist".** `tilth/tools/__init__.py` defines the registry; the system prompt should *not* enumerate tools (it gets stale).
4. **Hook contract: "success silent, failures verbose" — to the *agent*.** Pass states inject nothing into the loop's message history; failures inject a feedback message that the next worker iteration sees. **Telemetry is separate.** Every hook invocation should emit a `hook_run` event regardless of outcome — observability is for the developer reading `events.jsonl`, not the agent.
5. **The worktree branch is never auto-merged.** `commit_task` commits to the session branch; humans review and merge.
6. **Token cap enforcement is between tasks, not mid-task.** The "always finish the current task cleanly" property matters; preserve it. See [Token recording](../deep-dives/token-recording.md).
7. **Session state belongs to the harness, not the source repo.** The working tree for a run lives at `sessions/<id>/workspace/` *inside Tilth* (gitignored); only the branch `session/<id>` and its worktree admin entry land in the source repo's `.git`. The source repo stays pristine — the only thing you add to it is the `.tilth/tasks/` directory you author. See [Session layout](../deep-dives/session-layout.md).
