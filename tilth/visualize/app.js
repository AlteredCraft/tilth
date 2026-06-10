/* Polling client for the live session view.
 *
 * Deliberately thin: all rendering happens server-side (one renderer to
 * maintain); this script only appends HTML chunks and keeps the header chips
 * fresh. State that must round-trip with the server: `offset` (byte cursor
 * into events.jsonl) and `lastTask` (task-divider state).
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
  let following = true;

  function atBottom() {
    return window.innerHeight + window.scrollY >= document.body.scrollHeight - 80;
  }

  function scrollToBottom() {
    window.scrollTo(0, document.body.scrollHeight);
  }

  window.addEventListener("scroll", function () {
    if (atBottom()) {
      following = true;
      followBtn.hidden = true;
    } else if (following) {
      following = false;
      followBtn.hidden = false;
    }
  });

  followBtn.addEventListener("click", function () {
    following = true;
    followBtn.hidden = true;
    scrollToBottom();
  });

  function setStatus(status) {
    chipStatus.textContent = status;
    chipStatus.className = "chip chip-" + status;
  }

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
        if (following) scrollToBottom();
      }
      offset = data.offset;
      lastTask = data.last_task || "";

      setStatus(data.status);
      chipTokens.textContent = data.tokens_used.toLocaleString() + " tokens";
      chipCount.textContent = count + " events";
      if (data.status !== "running") delay = 10000;
    } catch (err) {
      failures += 1;
      setStatus("disconnected");
      delay = Math.min(15000, 1000 * Math.pow(2, failures));
    }
    setTimeout(poll, delay);
  }

  poll();
})();
