# Visualizing a session

Render `events.jsonl` as a chat-style HTML page. Easier to skim than `jq`-ing the raw log.

## How to visualize

```bash
uv run tilth --visualize                # most recent session
uv run tilth --visualize <session_id>   # or name one explicitly
```

Writes `sessions/<id>/chat.html` — a single self-contained file (inline CSS, no JS) that renders the log as a conversation:

- model calls (with collapsible reasoning blocks where the model emitted any),
- tool calls and results,
- validator runs,
- judge verdicts,
- AGENTS.md updates,
- commits,
- and stops,

…all grouped by task.

The visualizer is read-only and runs over the saved `events.jsonl`, so it's safe to invoke against a finished or in-progress session.

## What the output looks like

![Sample chat.html render: session header, task divider, model-call meta-strip with an expanded reasoning fold-out, tool call and result bubbles](../assets/session-render.png)

> **Diagram suggestion** — *annotated screenshot pointing to: (1) the session header strip, (2) per-task dividers, (3) a model-call meta strip with a collapsible reasoning block, (4) a tool-call/result pair, (5) a validator card, (6) a judge verdict card. Useful for orienting first-time readers of a long chat.html.*

## When to use it

- After a clean run, for a quick scan of how the agent solved each task.
- After a failed run, to see exactly where the loop diverged before a cap or an error.
- Mid-run, to peek without disturbing the live process — `chat.html` is regenerated on demand from the still-growing `events.jsonl`.
