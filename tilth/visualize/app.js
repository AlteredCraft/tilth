/* Polling client for the live session view.
 *
 * Rendering of the chat tail happens server-side (one renderer to maintain);
 * this script appends HTML chunks, keeps the header chips fresh, and builds
 * the dashboard (limit utilization, stat band, session timeline, context
 * pressure) from the compact `facts` the server ships alongside each chunk.
 * Limit meters read the caps recorded in the session_start fact. Facts cover
 * exactly
 * the same events as the HTML, so a replayed dashboard is identical to a
 * live-tailed one. State that must round-trip with the server: `offset`
 * (byte cursor into events.jsonl) and `lastTask` (task-divider state).
 *
 * Poll cadence: 1s while the session is running, 10s once it reaches a
 * terminal status (a finished session can still be resumed later, so polling
 * never fully stops), exponential backoff up to 15s while the server is
 * unreachable.
 */

(function () {
  "use strict";

  const sessionId = document.body.dataset.session;
  const main = document.getElementById("events");
  const chipStatus = document.getElementById("chip-status");
  const chipTokens = document.getElementById("chip-tokens");
  const chipCount = document.getElementById("chip-count");
  const followToggle = document.getElementById("follow-toggle");

  let offset = 0;
  let lastTask = "";
  let count = 0;
  let failures = 0;
  // Follow is an explicit user-set state (the floating "follow" toggle): on
  // means the view stays pinned to the stream's end as new events land. A
  // real scroll-away gesture switches it off — auto-scroll must never fight
  // the user — but nothing switches it on except the toggle.
  let following = false;

  // ------------------------------------------------------------ page chrome

  // The sticky page header's height feeds the filter bar's `top` so the two
  // stack instead of overlap; the chips wrap on narrow screens, so measure
  // rather than hardcode. Set once synchronously — ResizeObserver delivery
  // rides the rendering pipeline, which occluded tabs don't run.
  const pageHead = document.querySelector("header.page-head");
  function syncHeadHeight() {
    document.documentElement.style.setProperty(
      "--page-head-h", pageHead.offsetHeight + "px"
    );
  }
  syncHeadHeight();
  new ResizeObserver(syncHeadHeight).observe(pageHead);

  // ---------------------------------------------------------------- follow

  function atBottom() {
    return window.innerHeight + window.scrollY >= document.body.scrollHeight - 80;
  }

  // Follow-mode appends scroll smoothly. The animation's own intermediate
  // scroll events must not read as "the user scrolled away" (which cancels
  // following), so they're flagged with `autoScrolling`; real user gestures
  // (wheel/touch) clear the flag — the browser aborts a smooth scroll on
  // user input, and follow must break with it.
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let autoScrolling = false;

  function scrollToBottom(smooth) {
    if (smooth && !reduceMotion.matches) {
      autoScrolling = true;
      window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
    } else {
      window.scrollTo(0, document.body.scrollHeight);
    }
  }

  function setFollowing(on) {
    following = on;
    followToggle.classList.toggle("on", on);
    followToggle.setAttribute("aria-pressed", String(on));
  }

  window.addEventListener("scroll", function () {
    if (atBottom()) {
      autoScrolling = false;
    } else if (autoScrolling) {
      // our own smooth scroll in flight — not user intent
    } else if (following) {
      setFollowing(false);
    }
  });

  function userScrollGesture() {
    autoScrolling = false;
    if (following && !atBottom()) setFollowing(false);
  }
  window.addEventListener("wheel", userScrollGesture, { passive: true });
  window.addEventListener("touchmove", userScrollGesture, { passive: true });

  followToggle.addEventListener("click", function () {
    if (following) {
      setFollowing(false);
    } else {
      setFollowing(true);
      scrollToBottom(false);
    }
  });

  document.getElementById("jump-top").addEventListener("click", function () {
    setFollowing(false);
    window.scrollTo(0, 0);
  });

  document.getElementById("jump-bottom").addEventListener("click", function () {
    scrollToBottom(false);
  });

  function setStatus(status, label) {
    chipStatus.textContent = label || status;
    chipStatus.className = "chip chip-" + status;
  }

  // ----------------------------------------------------------- facts state

  const PALETTE = [
    "var(--task-1)", "var(--task-2)", "var(--task-3)",
    "var(--task-4)", "var(--task-5)", "var(--task-6)",
  ];

  const S = {
    start: null,
    end: null,
    pt: 0,
    et: 0,
    cached: 0,        // cache-hit tokens, a subset of pt (annotation, not addend)
    reasoning: 0,     // thinking tokens, a subset of et
    cost: 0,          // total USD across all calls
    workerCost: 0,
    evalCost: 0,
    workerCalls: 0,
    evalCalls: 0,
    tools: {},
    toolTotal: 0,
    blocks: 0,
    accepts: 0,
    rejects: 0,
    categories: {},
    tasks: new Map(), // id -> {first, last, status, ticks, marks, iters}
    bars: [],         // {task, pt, phase, flag} in arrival order
    maxPt: 0,
    limits: null,     // configured caps from session_start (or null on old logs)
    taskTotal: null,  // feature's full task count (may exceed tasks seen so far)
  };

  function task(id) {
    if (!S.tasks.has(id)) {
      S.tasks.set(id, {
        first: null, last: null, status: null, ticks: [], marks: [], iters: 0,
      });
    }
    return S.tasks.get(id);
  }

  // Timeline zoom: a drag-selected [viewStart, viewEnd] window over the gantt,
  // in absolute event-time. null means "full span" — and on a live session the
  // window then keeps tracking S.end as new events land; locking it takes an
  // explicit drag. `zoomDragging` suppresses the per-poll rebuild so a 1s
  // refresh can't wipe the rubber-band mid-gesture.
  let viewStart = null;
  let viewEnd = null;
  let zoomDragging = false;

  function ingest(f) {
    if (S.start === null || f.t < S.start) S.start = f.t;
    if (S.end === null || f.t > S.end) S.end = f.t;
    if (f.task) {
      const tk = task(f.task);
      if (tk.first === null) tk.first = f.t;
      tk.last = f.t;
    }
    if (f.e === "start") {
      if (f.limits) S.limits = f.limits;
      if (typeof f.task_count === "number") S.taskTotal = f.task_count;
    } else if (f.e === "model") {
      const cost = f.cost || 0;
      if (f.phase === "evaluator") {
        S.evalCalls += 1;
        S.evalCost += cost;
      } else {
        S.workerCalls += 1;
        S.workerCost += cost;
        if (f.task) {
          const tk = task(f.task);
          tk.ticks.push(f.t);
          // `iter` is 1-indexed (loop sends iter_n + 1), so the max seen is the
          // iteration count used — the number the per-task iteration cap bounds.
          if (f.iter) tk.iters = Math.max(tk.iters, f.iter);
        }
      }
      S.pt += f.pt;
      S.et += f.et;
      S.cached += f.ct || 0;
      S.reasoning += f.rt || 0;
      S.cost += cost;
      if (f.pt > S.maxPt) S.maxPt = f.pt;
      S.bars.push({ task: f.task, pt: f.pt, phase: f.phase, flag: false });
    } else if (f.e === "tool") {
      S.tools[f.tool] = (S.tools[f.tool] || 0) + 1;
      S.toolTotal += 1;
    } else if (f.e === "block") {
      S.blocks += 1;
    } else if (f.e === "verdict") {
      if (f.verdict === "accept") S.accepts += 1;
      else {
        S.rejects += 1;
        if (f.category) S.categories[f.category] = (S.categories[f.category] || 0) + 1;
        for (let i = S.bars.length - 1; i >= 0; i--) {
          if (S.bars[i].task === f.task && S.bars[i].phase === "evaluator") {
            S.bars[i].flag = true;
            break;
          }
        }
      }
      if (f.task) task(f.task).marks.push({ t: f.t, ok: f.verdict === "accept" });
    } else if (f.e === "task_end") {
      if (f.task) task(f.task).status = f.status;
    }
  }

  function taskColor(id) {
    let i = 0;
    for (const key of S.tasks.keys()) {
      if (key === id) return PALETTE[i % PALETTE.length];
      i += 1;
    }
    return PALETTE[0];
  }

  // -------------------------------------------------------------- renderers

  function fmtK(n) {
    return n >= 10000 ? (n / 1000).toFixed(1) + "k" : n.toLocaleString();
  }

  function mmss(seconds) {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return m + ":" + String(s).padStart(2, "0");
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function show(id) {
    document.getElementById(id).hidden = false;
  }

  function fmtInt(n) {
    return String(Math.round(n));
  }

  // Mirror of tilth.usage.format_cost: enough precision that a cheap run's
  // spend doesn't round away to $0.00.
  function fmtCost(n) {
    if (n >= 0.005) return "$" + n.toFixed(2);
    if (n >= 0.00005) return "$" + n.toFixed(4);
    return "$" + n.toFixed(6);
  }

  // Utilization band → severity. Low use is healthy headroom (green), the run
  // only deserves attention as it nears a cap.
  function meterTone(pct) {
    if (pct >= 90) return "bad";
    if (pct >= 75) return "warn";
    return "ok";
  }

  // o: {name, used, max, fmt, dot?, status?}. A 0/absent max means "no finite
  // cap" and the caller is expected to skip it — every meter shown here has one.
  // `status` ("done" | "failed") adds a completion glyph after the name.
  function meterEl(o) {
    const max = o.max || 0;
    const pct = max > 0 ? Math.min(100, (o.used / max) * 100) : 0;
    const m = el("div", "meter meter-" + meterTone(pct));
    const head = el("div", "meter-head");
    const name = el("span", "meter-name");
    if (o.dot) {
      const dot = el("span", "meter-dot");
      dot.style.background = o.dot;
      name.appendChild(dot);
    }
    name.appendChild(document.createTextNode(o.name));
    if (o.status === "done" || o.status === "failed") {
      const done = o.status === "done";
      name.appendChild(el(
        "span", "meter-status " + (done ? "done" : "failed"), done ? "✓" : "✕"));
      name.title = done ? "task complete" : "task failed";
    }
    head.append(
      name,
      el("span", "meter-val",
        o.fmt(o.used) + " / " + o.fmt(max) + " · " + Math.round(pct) + "%"),
    );
    const track = el("div", "meter-track");
    const fill = el("i", "meter-fill");
    fill.style.width = pct + "%";
    track.appendChild(fill);
    m.append(head, track);
    return m;
  }

  function renderLimits() {
    const L = S.limits;
    if (!L) return; // pre-limits session log — degrade to no panel
    show("limits-panel");

    const session = document.getElementById("session-meters");
    session.replaceChildren();
    if (L.max_token_dollar_spend > 0) {
      session.appendChild(meterEl({
        name: "Cost budget", used: S.cost, max: L.max_token_dollar_spend,
        fmt: fmtCost,
      }));
    }
    if (L.max_wall_clock_minutes > 0) {
      const wall = Math.max(0, (S.end || 0) - (S.start || 0)); // seconds
      session.appendChild(meterEl({
        name: "Wall clock", used: wall, max: L.max_wall_clock_minutes * 60,
        fmt: mmss,
      }));
    }

    // Per-task: iterations always (the iter_cap is the usual failure mode);
    // evaluator calls only when that cap is set (0 means unlimited).
    const taskMeters = document.getElementById("task-meters");
    taskMeters.replaceChildren();
    let rows = 0;
    for (const [id, tk] of S.tasks) {
      if (L.max_iterations_per_task > 0) {
        taskMeters.appendChild(meterEl({
          name: id + " · iterations", used: tk.iters,
          max: L.max_iterations_per_task, fmt: fmtInt, dot: taskColor(id),
          status: tk.status,
        }));
        rows += 1;
      }
      if (L.max_evaluator_calls_per_task > 0) {
        taskMeters.appendChild(meterEl({
          name: id + " · evaluator calls", used: tk.marks.length,
          max: L.max_evaluator_calls_per_task, fmt: fmtInt, dot: taskColor(id),
          status: tk.status,
        }));
        rows += 1;
      }
    }
    document.getElementById("task-limit-group").hidden = rows === 0;

    // "Per task · N tasks" — prefer the recorded feature total (known up front)
    // and fall back to tasks seen so far on pre-task_count logs.
    const total = S.taskTotal != null ? S.taskTotal : S.tasks.size;
    document.getElementById("task-limit-label").textContent =
      "Per task · " + total + (total === 1 ? " task" : " tasks");
  }

  function renderStats() {
    show("stat-band");
    const total = S.pt + S.et;
    document.getElementById("stat-tokens").textContent = fmtK(total);
    const bar = document.getElementById("stat-tokens-bar");
    bar.replaceChildren();
    if (total > 0) {
      const p = el("i");
      p.style.flex = String(S.pt);
      p.style.background = "var(--agent-fg)";
      const e = el("i");
      e.style.flex = String(S.et);
      e.style.background = "var(--eval-bar)";
      bar.append(p, e);
    }
    // cached ⊆ prompt and reasoning ⊆ eval — annotate their parent bucket only
    // when present, never as separate addends.
    let tokenSub = "prompt " + fmtK(S.pt);
    if (S.cached) tokenSub += " (" + fmtK(S.cached) + " cached)";
    tokenSub += " · eval " + fmtK(S.et);
    if (S.reasoning) tokenSub += " (" + fmtK(S.reasoning) + " reasoning)";
    document.getElementById("stat-tokens-sub").textContent = tokenSub;

    // Cost tile: total spend, split by actor — the worker↔evaluator allocation
    // expressed in the currency that matters. 0 across the board (non-OpenRouter
    // providers don't report cost) reads as a plain dash.
    document.getElementById("stat-cost").textContent =
      S.cost > 0 ? fmtCost(S.cost) : "—";
    document.getElementById("stat-cost-sub").textContent =
      S.cost > 0
        ? "worker " + fmtCost(S.workerCost) + " · evaluator " + fmtCost(S.evalCost)
        : "";

    document.getElementById("stat-calls").textContent =
      String(S.workerCalls + S.evalCalls);
    document.getElementById("stat-calls-sub").textContent =
      "worker " + S.workerCalls + " · evaluator " + S.evalCalls;

    document.getElementById("stat-tools").textContent = String(S.toolTotal);
    const top = Object.entries(S.tools).sort((a, b) => b[1] - a[1]).slice(0, 3);
    document.getElementById("stat-tools-sub").textContent =
      top.map(([name, n]) => name + " " + n).join(" · ");

    const verdicts = document.getElementById("stat-verdicts");
    verdicts.replaceChildren(
      el("span", "good", S.accepts + "✓"),
      document.createTextNode(" / "),
      el("span", S.rejects ? "bad" : "", S.rejects + "✕"),
    );
    const cats = Object.entries(S.categories)
      .map(([name, n]) => name + " ×" + n).join(" · ");
    const verdictSub = document.getElementById("stat-verdicts-sub");
    verdictSub.replaceChildren(
      cats ? el("span", "bad", cats) : document.createTextNode("—"),
    );

    document.getElementById("stat-blocks").textContent = String(S.blocks);
    document.getElementById("stat-blocks-sub").textContent =
      "of " + S.toolTotal + " tool calls";

    const wall = Math.max(0, (S.end || 0) - (S.start || 0));
    document.getElementById("stat-clock").textContent = mmss(wall);
    document.getElementById("stat-clock-sub").textContent = S.workerCalls
      ? "~" + (wall / S.workerCalls).toFixed(1) + "s / iteration"
      : "";
  }

  function renderTimeline() {
    if (S.tasks.size === 0 || S.end === S.start) return;
    // A live poll must not rebuild the gantt out from under an active drag.
    if (zoomDragging) return;
    show("timeline-panel");
    const vs = viewStart === null ? S.start : viewStart;
    const ve = viewEnd === null ? S.end : viewEnd;
    const span = Math.max(1, ve - vs);
    const pct = (t) => ((t - vs) / span) * 100;
    const inView = (t) => t >= vs && t <= ve;

    const gantt = document.getElementById("gantt");
    gantt.replaceChildren();
    for (const [id, tk] of S.tasks) {
      const row = el("div", "gantt-row");
      row.appendChild(el("span", "tid", id));
      const track = el("div", "track");
      // The task span may start before or end after the window; clamp it to
      // the visible edges and only draw it when it actually overlaps.
      if (tk.first !== null && tk.last >= vs && tk.first <= ve) {
        const left = Math.max(0, Math.min(100, pct(tk.first)));
        const right = Math.max(0, Math.min(100, pct(tk.last)));
        const span = el("div", "span");
        span.style.left = left + "%";
        span.style.width = Math.max(0.5, right - left) + "%";
        span.style.background = taskColor(id);
        track.appendChild(span);
      }
      for (const t of tk.ticks) {
        if (!inView(t)) continue;
        const tick = el("i", "tick");
        tick.style.left = pct(t) + "%";
        track.appendChild(tick);
      }
      for (const mark of tk.marks) {
        if (!inView(mark.t)) continue;
        const m = el("span", "marker " + (mark.ok ? "ok" : "rej"), mark.ok ? "✓" : "✕");
        m.style.left = pct(mark.t) + "%";
        m.title = mark.ok ? "evaluator accepts" : "evaluator rejects";
        track.appendChild(m);
      }
      row.appendChild(track);
      gantt.appendChild(row);
    }

    // Axis reads as elapsed-from-session-start, so a zoomed window shows its
    // true offset range (e.g. 12:30 → 18:45) rather than restarting at 0:00.
    const axis = document.getElementById("gantt-axis");
    axis.replaceChildren();
    const base = vs - S.start;
    for (let i = 0; i <= 5; i++) {
      axis.appendChild(el("span", "", mmss(base + (span * i) / 5)));
    }

    const zoomed = viewStart !== null || viewEnd !== null;
    document.getElementById("timeline-reset").hidden = !zoomed;
    document.getElementById("timeline-hint").hidden = zoomed;
  }

  // Drag a box across the gantt to set [viewStart, viewEnd]; the reset button
  // (revealed once zoomed) clears it back to the full span. Pixel<->time math
  // rides a live `.track` rect captured at mousedown, so it composes when
  // already zoomed and needs no hardcoded label-gutter width.
  function setupTimelineZoom() {
    const gantt = document.getElementById("gantt");

    gantt.addEventListener("mousedown", function (down) {
      if (down.button !== 0) return;
      const track = gantt.querySelector(".track");
      if (!track) return;
      const plot = track.getBoundingClientRect();
      const gRect = gantt.getBoundingClientRect();
      if (down.clientX < plot.left || down.clientX > plot.right) return;

      const vs = viewStart === null ? S.start : viewStart;
      const ve = viewEnd === null ? S.end : viewEnd;
      const span = Math.max(1, ve - vs);
      const x0 = down.clientX;
      const clampX = (x) => Math.max(plot.left, Math.min(plot.right, x));

      zoomDragging = true;
      const sel = el("div", "zoom-select");
      sel.style.top = "0";
      sel.style.bottom = "0";
      gantt.appendChild(sel);

      function draw(x1) {
        const a = clampX(Math.min(x0, x1));
        const b = clampX(Math.max(x0, x1));
        sel.style.left = a - gRect.left + "px";
        sel.style.width = b - a + "px";
      }
      draw(x0);

      function move(m) {
        draw(m.clientX);
      }
      function up(u) {
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
        sel.remove();
        zoomDragging = false;
        const a = clampX(Math.min(x0, u.clientX));
        const b = clampX(Math.max(x0, u.clientX));
        if (b - a >= 4) {
          viewStart = vs + ((a - plot.left) / plot.width) * span;
          viewEnd = vs + ((b - plot.left) / plot.width) * span;
        }
        renderTimeline();
      }
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
      down.preventDefault();
    });

    document.getElementById("timeline-reset").addEventListener("click", function () {
      viewStart = null;
      viewEnd = null;
      renderTimeline();
    });
  }
  setupTimelineZoom();

  function renderBars() {
    if (S.bars.length === 0) return;
    show("pressure-panel");
    const bars = document.getElementById("bars");
    bars.replaceChildren();
    const hint = el("span", "y-hint", fmtK(S.maxPt) + " ─");
    hint.id = "bars-max";
    bars.appendChild(hint);

    let prevTask = null;
    for (const b of S.bars) {
      if (prevTask !== null && b.task !== prevTask) {
        bars.appendChild(el("div", "bar-gap"));
      }
      prevTask = b.task;
      const bar = el("div", "bar" + (b.phase === "evaluator" ? " eval" : ""));
      if (b.phase !== "evaluator") bar.style.background = taskColor(b.task);
      bar.style.height = (S.maxPt ? (b.pt / S.maxPt) * 100 : 0) + "%";
      bar.title = (b.phase === "evaluator" ? "evaluator · " : b.task + " · ")
        + b.pt.toLocaleString() + " prompt tokens";
      if (b.flag) {
        const flag = el("span", "flag");
        flag.title = "verdict: reject";
        bar.appendChild(flag);
      }
      bars.appendChild(bar);
    }

    const legend = document.getElementById("bars-legend");
    legend.replaceChildren();
    for (const id of S.tasks.keys()) {
      const key = el("span", "key");
      const swatch = el("span", "swatch");
      swatch.style.background = taskColor(id);
      key.append(swatch, document.createTextNode(id));
      legend.appendChild(key);
    }
    const evalKey = el("span", "key");
    const evalSwatch = el("span", "swatch");
    evalSwatch.style.background = "var(--eval-bar)";
    evalKey.append(evalSwatch, document.createTextNode("evaluator"));
    legend.appendChild(evalKey);
    if (S.rejects > 0) {
      const rejKey = el("span", "key");
      const rejSwatch = el("span", "swatch round");
      rejSwatch.style.background = "var(--bad)";
      rejKey.append(rejSwatch, document.createTextNode("reject"));
      legend.appendChild(rejKey);
    }
  }

  function renderDashboard() {
    renderLimits();
    renderStats();
    renderTimeline();
    renderBars();
  }

  // ---------------------------------------------------------------- filters

  let mode = "everything";
  const offKinds = new Set();
  const presets = Array.from(document.querySelectorAll(".preset"));
  const chips = Array.from(document.querySelectorAll(".fchip"));

  function applyFilters() {
    const msgs = main.querySelectorAll(".msg");
    let shown = 0;
    for (const m of msgs) {
      let visible;
      if (mode === "dialogue") visible = m.dataset.dialog === "1";
      else if (mode === "problems") visible = m.dataset.problem === "1";
      else visible = !offKinds.has(m.dataset.kind);
      m.classList.toggle("hidden", !visible);
      if (visible) shown += 1;
    }
    document.getElementById("filter-count").textContent =
      shown === msgs.length
        ? msgs.length + " events"
        : "showing " + shown + " of " + msgs.length + " events";
  }

  for (const p of presets) {
    p.addEventListener("click", function () {
      mode = p.dataset.mode;
      for (const x of presets) x.classList.toggle("active", x === p);
      if (mode === "everything") {
        offKinds.clear();
        for (const c of chips) {
          c.classList.add("on");
          c.classList.remove("off");
        }
      }
      applyFilters();
    });
  }
  for (const c of chips) {
    c.addEventListener("click", function () {
      mode = "everything";
      for (const x of presets) {
        x.classList.toggle("active", x.dataset.mode === "everything");
      }
      c.classList.toggle("off");
      c.classList.toggle("on", !c.classList.contains("off"));
      if (c.classList.contains("off")) offKinds.add(c.dataset.kind);
      else offKinds.delete(c.dataset.kind);
      applyFilters();
    });
  }

  // ------------------------------------------------------------------ poll

  async function poll() {
    let delay = 1000;
    try {
      const params = new URLSearchParams({ offset: String(offset) });
      if (lastTask) params.set("last_task", lastTask);
      const resp = await fetch(
        "/api/session/" + encodeURIComponent(sessionId) + "/events?" + params
      );
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const data = await resp.json();
      failures = 0;

      if (data.n_new > 0) {
        main.insertAdjacentHTML("beforeend", data.html);
        count += data.n_new;
        for (const f of data.facts || []) ingest(f);
        renderDashboard();
        applyFilters();
        if (following) scrollToBottom(true);
      }
      offset = data.offset;
      lastTask = data.last_task || "";

      setStatus(data.status, data.status_label);
      chipTokens.textContent = data.tokens_used.toLocaleString() + " tokens"
        + (data.cost > 0 ? " · " + fmtCost(data.cost) : "");
      chipCount.textContent = count + " events";
      // A paused-but-resumable session gets a label like "running (interrupted)"
      // — only a plain live "running" warrants the 1s cadence.
      if ((data.status_label || data.status) !== "running") delay = 10000;
    } catch (err) {
      failures += 1;
      setStatus("disconnected");
      delay = Math.min(15000, 1000 * Math.pow(2, failures));
    }
    setTimeout(poll, delay);
  }

  poll();
})();
