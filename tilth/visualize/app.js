/* Polling client for the live session view.
 *
 * Rendering of the chat tail happens server-side (one renderer to maintain);
 * this script appends HTML chunks, keeps the header chips fresh, and builds
 * the dashboard (stat band, session timeline, context pressure) from the
 * compact `facts` the server ships alongside each chunk. Facts cover exactly
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
  const followBtn = document.getElementById("follow");

  let offset = 0;
  let lastTask = "";
  let count = 0;
  let failures = 0;
  // The page lands at the top (dashboard first); the "↓ bottom" button jumps
  // to the tail's end and switches on follow-the-stream behaviour.
  let following = false;

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

  window.addEventListener("scroll", function () {
    if (atBottom()) {
      autoScrolling = false;
      following = true;
      followBtn.hidden = true;
    } else if (autoScrolling) {
      // our own smooth scroll in flight — not user intent
    } else if (following) {
      following = false;
      followBtn.hidden = false;
    }
  });

  function userScrollGesture() {
    autoScrolling = false;
    if (following && !atBottom()) {
      following = false;
      followBtn.hidden = false;
    }
  }
  window.addEventListener("wheel", userScrollGesture, { passive: true });
  window.addEventListener("touchmove", userScrollGesture, { passive: true });

  followBtn.addEventListener("click", function () {
    following = true;
    followBtn.hidden = true;
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
    workerCalls: 0,
    evalCalls: 0,
    tools: {},
    toolTotal: 0,
    blocks: 0,
    accepts: 0,
    rejects: 0,
    categories: {},
    tasks: new Map(), // id -> {first, last, status, ticks: [], marks: []}
    bars: [],         // {task, pt, phase, flag} in arrival order
    maxPt: 0,
  };

  function task(id) {
    if (!S.tasks.has(id)) {
      S.tasks.set(id, { first: null, last: null, status: null, ticks: [], marks: [] });
    }
    return S.tasks.get(id);
  }

  function ingest(f) {
    if (S.start === null || f.t < S.start) S.start = f.t;
    if (S.end === null || f.t > S.end) S.end = f.t;
    if (f.task) {
      const tk = task(f.task);
      if (tk.first === null) tk.first = f.t;
      tk.last = f.t;
    }
    if (f.e === "model") {
      if (f.phase === "evaluator") S.evalCalls += 1;
      else {
        S.workerCalls += 1;
        if (f.task) task(f.task).ticks.push(f.t);
      }
      S.pt += f.pt;
      S.et += f.et;
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
    document.getElementById("stat-tokens-sub").textContent =
      "prompt " + fmtK(S.pt) + " · eval " + fmtK(S.et);

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
    show("timeline-panel");
    const totalSpan = Math.max(1, S.end - S.start);
    const pct = (t) => ((t - S.start) / totalSpan) * 100;

    const gantt = document.getElementById("gantt");
    gantt.replaceChildren();
    for (const [id, tk] of S.tasks) {
      const row = el("div", "gantt-row");
      row.appendChild(el("span", "tid", id));
      const track = el("div", "track");
      const span = el("div", "span");
      span.style.left = pct(tk.first) + "%";
      span.style.width = Math.max(0.5, pct(tk.last) - pct(tk.first)) + "%";
      span.style.background = taskColor(id);
      track.appendChild(span);
      for (const t of tk.ticks) {
        const tick = el("i", "tick");
        tick.style.left = pct(t) + "%";
        track.appendChild(tick);
      }
      for (const mark of tk.marks) {
        const m = el("span", "marker " + (mark.ok ? "ok" : "rej"), mark.ok ? "✓" : "✕");
        m.style.left = pct(mark.t) + "%";
        m.title = mark.ok ? "evaluator accepts" : "evaluator rejects";
        track.appendChild(m);
      }
      row.appendChild(track);
      gantt.appendChild(row);
    }

    const axis = document.getElementById("gantt-axis");
    axis.replaceChildren();
    for (let i = 0; i <= 5; i++) {
      axis.appendChild(el("span", "", mmss((totalSpan * i) / 5)));
    }
  }

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
        followBtn.hidden = following || atBottom();
      }
      offset = data.offset;
      lastTask = data.last_task || "";

      setStatus(data.status, data.status_label);
      chipTokens.textContent = data.tokens_used.toLocaleString() + " tokens";
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
