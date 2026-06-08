# Hyper-observability

The ultimate goal for Tilth is a minimal, productive agent harness with
**hyper-observability**: *every prompt the harness sends is accessible and
adaptable, and every run is fully inspectable after the fact.* When no one is
watching a run mid-flight (Tilth runs [autonomously](../index.md#how-tilth-differs-from-other-harnesses)),
the recording *is* the supervision — the artifact you read afterwards is the
only window you get.

This is an early example of that goal, not a finished product. The pieces below
are what exists today; the [roadmap](#whats-not-here-yet) names what doesn't.
The point of writing it down now is to hold the design to the standard — every
new feature should leave a run *more* inspectable, not less.

Two pillars:

1. **Every prompt is accessible (and adaptable).** Nothing the harness sends to
   a model is hidden from you — the assembled message, the memory channels that
   fed it, the token cost it incurred. And the prompts themselves are plain
   files you can edit.
2. **Every run is inspectable after the fact.** A finished (or in-progress) run
   replays end-to-end from a single append-only log, with no live process to
   attach to.

## What's here now — the surface

Everything below is sourced from `sessions/<id>/events.jsonl`, the append-only
audit trail. The full event catalogue lives in
[Session layout → Event types](session-layout.md#event-types); this is the
observability-minded reading of it.

### Every prompt the harness sends is recorded

- **`prompt_assembled`** — emitted for every user message *before it is sent*,
  tagged with its `role` (`worker` / `evaluator` / `self_improve`), the
  iteration, and the (capped) content. You can read the exact words each model
  saw on each turn.
- **`memory_load`** — emitted alongside, recording which
  [memory channels](../architecture/memory-channels.md) fed that prompt:
  per-channel `present` / `chars` / `truncated` / `sha256_8`. So you can tell
  not just *what* was sent but *where each part came from* and whether it was
  clipped.
- **`TILTH_PROMPT_DUMP` (opt-in, default off)** — `prompt_assembled` records the
  (capped) user message; when you need the *exact bytes* — the system prompt,
  the full conversation history, and the literal tool schemas — set
  `TILTH_PROMPT_DUMP=1`. Tilth then writes the complete request to
  `sessions/<id>/prompts/<NNNN-label>.md` before *every* model call (worker,
  evaluator, self-improve, prep interview), and stamps each `model_call` event
  with a `prompt_dump` path pointing at its file — so you can navigate from the
  event straight to the request that produced it. It's a debugger's flag: a few
  KB–~20KB per iteration, and the dumps can include workspace file contents, so
  it stays off unless you're chasing a specific question.

### Every model call is recorded

- **`model_call`** — emitted when any model call returns (worker, evaluator, or
  self-improve), carrying `prompt_tokens`, `eval_tokens`, `finish_reason`, and
  the model's reasoning when it emitted any. Grep `events.jsonl` for
  `model_call` and you can reconstruct exactly when tokens were spent and why a
  turn ended. See [Token recording](token-recording.md).

### Every run replays end-to-end

- **`tilth visualize`** rolls `events.jsonl` into a single self-contained
  `chat.html` — inline CSS, no JS — that renders the run as a conversation
  grouped by task: model calls with collapsible reasoning, tool calls and
  results, validator runs, evaluator verdicts, commits, and stops. Read-only and
  safe to run mid-flight. See [Visualizing a session](../getting-started/visualizing.md).
- **`summary.json`** is the denormalised rollup of the same log — a
  machine-readable view for when you want the shape of a run without replaying
  every event.

### The prompts are plain, editable files

The worker, evaluator, and self-improve prompts live in `tilth/prompts/*.md`
(and the seeder's in `tilth/seed/`). They ship verbatim every turn, so the
"accessible and adaptable" half is literal: open the file, read exactly what the
agent is told, change it, and the next run uses your version. There is no hidden
prompt template assembled out of reach.

## What's not here yet

Naming the gaps is part of the point — hyper-observability is a direction, and
some of these are deliberate non-goals.

**By design — not coming:**

- **No live TUI or mid-run dashboard.** Observability here is *offline-first*: a
  finished run you inspect, not a process you babysit. The whole premise is that
  no human is watching mid-task, so the effort goes into the replayable artifact
  rather than a live view. `chat.html` regenerates on demand from the still-
  growing log if you want to peek, but that's a snapshot, not a stream.

**On the roadmap:**

- **Runtime prompt *adaptation*.** Today "adaptable" means *edit the file
  between runs*. The longer goal is making prompts adjustable as a first-class
  surface — overridable per-run without forking the source.
- **Richer cost accounting.** Token counts are recorded, but there's no
  dollar-cost translation, no per-model split, and no headroom warning as a cap
  approaches. See the [gaps in Token recording](token-recording.md).
- **A live tail.** A way to follow `events.jsonl` as it grows without
  re-rendering, for the times you *do* want to watch.

**An honest gap, not a feature:** the worker isn't fully walled off from harness
state. The worktree is mounted under `sessions/<id>/workspace/`, so a determined
model with `bash` can reach `events.jsonl`, `summary.json`, and the rest via
relative `../` paths. Full inspectability for *you* and full opacity to the
*agent* are different problems; see [Agent visibility](agent-visibility.md) for
where that wall currently sits.

## Observability that pays off in development

Hyper-observability isn't only for reading your own runs — it has turned out to
be one of the more useful moves while building Tilth *itself*.

The practice: after a run, hand its `events.jsonl` (or the rendered
`chat.html`) to the co-development agent and ask a single open question —
*"look through this run and flag anything anomalous."* Because the log records
every prompt, every memory load, every token count, and every verdict in order,
the agent can spot things a human skimming `jq` output tends to miss: a memory
channel that silently truncated, an evaluator rejecting on a category that
doesn't match its stated concern, a worker burning iterations re-reading the
same file, an empty-response streak, tokens spent on a turn that produced
nothing. Several harness bugs and prompt weaknesses surfaced this way before
they showed up as a failed demo.

It's a virtuous loop: the more faithfully a run is recorded, the more a second
agent can reason about it — so investments in observability compound into
faster development of the harness that produces it. The richer the log, the
sharper the anomaly hunt.

## See also

- [Visualizing a session](../getting-started/visualizing.md) — the practical
  how-to for `tilth visualize`.
- [Session layout → Event types](session-layout.md#event-types) — the full
  event catalogue behind everything above.
- [Token recording](token-recording.md) — how tokens are recorded, and the
  cost-accounting gaps.
- [Agent visibility](agent-visibility.md) — what the worker sees vs. what the
  harness hides, and where the `../` wall sits.
