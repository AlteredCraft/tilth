# Visualizing a session

Render `events.jsonl` as a chat-style HTML page. Easier to skim than `jq`-ing the raw log.

## How to visualize

```bash
uv run tilth visualize                # most recent session
uv run tilth visualize <session_id>   # or name one explicitly
```

The pre-Phase-3 flag form `--visualize` still works for one minor version.

Writes `sessions/<id>/chat.html` — a single self-contained file (inline CSS, no JS) that renders the log as a conversation:

- the **seed context panel** above the timeline when `sessions/<id>/seed-meta.json` exists — TL;DR, open questions, blockers, and scope notes from the prep-feature interview (with a head strip showing the interviewer model, total tokens, and start→end times), so the reviewer sees them before scrolling through per-task events,
- a **seed-prepared marker** pinning the interview moment in the timeline (`session_prepared`),
- model calls (with collapsible reasoning blocks where the model emitted any),
- tool calls and results,
- validator runs,
- evaluator verdicts (accept / reject, with the rejection category, concern, evidence, and next-step on rejects),
- proposed learnings,
- commits,
- and stops,

…all grouped by task.

The visualizer is read-only and runs over the saved `events.jsonl`, so it's safe to invoke against a finished or in-progress session.

## What the output looks like

![Sample chat.html render: session header, task divider, model-call meta-strip with an expanded reasoning fold-out, tool call and result bubbles](../assets/session-render.png)

> **Diagram suggestion** — *annotated screenshot pointing to: (1) the session header strip, (2) per-task dividers, (3) a model-call meta strip with a collapsible reasoning block, (4) a tool-call/result pair, (5) a validator card, (6) an evaluator verdict card. Useful for orienting first-time readers of a long chat.html.*

## When to use it

- After a clean run, for a quick scan of how the agent solved each task.
- After a failed run, to see exactly where the loop diverged before a cap or an error.
- Mid-run, to peek without disturbing the live process — `chat.html` is regenerated on demand from the still-growing `events.jsonl`.
