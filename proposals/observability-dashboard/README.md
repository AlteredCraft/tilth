# Observability dashboard — direction mockups

Two self-contained HTML mockups for the next stage of `tilth visualize`: a metrics
dashboard + dev-tools-style timeline above a filterable communication tail. Open
either directly in a browser (no server needed; webfonts load from Google Fonts,
with monospace fallbacks offline).

Both are populated with **real data from session `20260610-131639-c7e325`** — the
4-task demo run whose T-003 hit an evaluator reject (`acceptance_gap`) followed by
a fix-and-resubmit cycle. That arc is what makes the filters worth demoing: the
"Worker ↔ Evaluator" view collapses 132 events into the 4-message case/verdict
dialogue.

## Mockup A — "Ledger" (`mockup-a-ledger.html`)

An **evolution** of the current viewer: same warm-paper palette and bubble
language, page still scrolls as one document.

- Stat band: tokens (with prompt/eval split bar), model calls, tool calls,
  verdicts, hooks, wall clock.
- **Context pressure** chart — prompt tokens per model call, colored by task.
  The sawtooth makes context growth and per-task resets legible at a glance;
  the red dot marks the reject.
- **Session timeline** — task-band gantt with iteration ticks and ✓/✕ verdict
  markers.
- Sticky filter bar over the existing tail: presets (Everything / Worker ↔
  Evaluator / Problems) + per-kind chips (worker, tools, evaluator, harness).

Cheapest path from today's code: keep the document flow, server-render the
charts as divs/SVG from `summary.json` + `events.jsonl`, reuse the existing
bubble renderers untouched.

## Mockup B — "Flight Recorder" (`mockup-b-flight-recorder.html`)

A **full-viewport instrument panel**: fixed top bar, instrument deck, scrollable
stream, status bar. Dark, dense, devtools-flavored.

- **Waterfall timeline is the centerpiece** — lanes for worker / tools / eval /
  harness, task bands behind, hover tooltips per span, reject span glows red.
  A brush region (mocked over T-003) sketches the headline interaction:
  *brush a time range → the stream scopes to it; click a span → jump to its event.*
- Left readout column: token meter, verdict counts, hook counts, tool histogram.
- Stream toolbar: devtools-style segmented filter (ALL / WORKER / TOOLS / EVAL /
  HARNESS / WORKER↔EVAL), a ⚠ PROBLEMS toggle, and a **live text filter** (try
  typing "prefix").

More build: needs a real client-side event model (the live tail already streams
rendered HTML; brushing/jumping wants structured events instead), but it is the
version that *sells* hyper-observability.

## Open questions for picking a direction

1. One column that scrolls away (A) vs. fixed chrome with an always-visible
   timeline (B)?
2. Should filters be presets-first (A) or devtools-style facets + search (B)?
3. Is timeline↔tail linking (brush, click-to-jump) in scope for v1, or is a
   static timeline enough?
4. Light/warm continuity with the docs site vs. a deliberately different
   "instrument" identity for the viewer?

Elements are mix-and-matchable — e.g. A's layout with B's waterfall and search.
