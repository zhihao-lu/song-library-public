// Usage tracking (analytics.py): what happens on each page goes to the server in small batches.
// Every page loads this first. It records the page being opened and left, and every button, tab
// and link pressed (by its label); pages add the steps that matter with track("event", {details}).
// The device's id is a random one kept in this browser; a visit is a run of activity with no
// 30-minute gap. The name in the bar travels as its cookie, which the server reads.
(() => {
  const IDLE = 30 * 60 * 1000;
  const random = () => (crypto.randomUUID?.() || String(Math.random()).slice(2) + Date.now()).replace(/-/g, "");
  let device = "", visit = { id: random(), last: 0 };
  try {
    device = localStorage.getItem("did") || "";
    if (!device) localStorage.setItem("did", device = random());
    const saved = JSON.parse(localStorage.getItem("visit") || "null");
    if (saved && Date.now() - saved.last < IDLE) visit = saved;
  } catch (_) {}
  device = device || random();
  function touchVisit() {
    if (Date.now() - visit.last >= IDLE && visit.last) visit = { id: random(), last: 0 };
    visit.last = Date.now();
    try { localStorage.setItem("visit", JSON.stringify(visit)); } catch (_) {}
  }

  let queue = [], timer = null;
  function flush(beacon) {
    clearTimeout(timer); timer = null;
    if (!queue.length) return;
    const body = JSON.stringify({ device, visit: visit.id, events: queue });
    queue = [];
    if (beacon && navigator.sendBeacon?.("/api/events", new Blob([body], { type: "text/plain" }))) return;
    fetch("/api/events", { method: "POST", body, keepalive: true, headers: { "Content-Type": "text/plain" } }).catch(() => {});
  }
  window.track = (event, data = {}) => {
    try {
      touchVisit();
      queue.push({ page: location.pathname, event, data });
      if (queue.length >= 20) flush();
      else if (!timer) timer = setTimeout(flush, 4000);
    } catch (_) {}
  };

  const touch = matchMedia("(pointer: coarse)").matches;
  track("page_view", { ref: document.referrer && new URL(document.referrer).host !== location.host ? document.referrer : "",
                       w: innerWidth, h: innerHeight, touch, standalone: matchMedia("(display-mode: standalone)").matches });
  // Time actually spent looking at the page, sent when it's hidden (switching apps, closing it).
  let shownAt = Date.now(), seen = 0;
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      seen += Date.now() - shownAt;
      track("page_leave", { ms: seen });
      flush(true);
    } else {
      shownAt = Date.now();
    }
  });
  addEventListener("pagehide", () => flush(true));

  // Whatever is pressed: buttons, tabs, links, menu items. Its label is what's on it (or its
  // aria-label), trimmed; never what was typed into a field.
  // A row in a list (a search result, a song) goes by its title alone, without its source tags; a
  // top-bar link by its full name ("Setlists", not "SetlistsSets").
  const titleOf = t => [...t.childNodes].filter(n => !n.classList?.contains("src")).map(n => n.textContent).join("");
  const labelOf = el => {
    const title = el.querySelector?.(".title"), full = el.querySelector?.(".full");
    return (el.getAttribute("aria-label") || (title && titleOf(title)) || full?.textContent || el.textContent ||
            el.title || el.value || "").replace(/\s+/g, " ").trim().slice(0, 60);
  };
  document.addEventListener("click", e => {
    const el = e.target.closest?.("button, a, [role=tab], summary, [data-i], .chip");
    if (!el) return;
    // Where, for rows: a search result (tapping one adds it) or a song already in the setlist.
    const where = el.closest("#results") ? "search results" : el.closest("#songs") ? "setlist" : undefined;
    track("click", { label: labelOf(el), id: el.id || undefined, where,
                     href: el.tagName === "A" ? el.getAttribute("href") : undefined });
  }, true);
  // Choices: menus and tick boxes (not text fields).
  document.addEventListener("change", e => {
    const el = e.target;
    if (el.tagName === "SELECT") track("change", { id: el.id || el.getAttribute("aria-label") || "select", value: el.value });
    else if (el.type === "checkbox") track("change", { id: el.id || labelOf(el.closest("label") || el), value: el.checked });
  }, true);
})();
