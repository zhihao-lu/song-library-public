// Helpers the pages share. Loaded before each page's own script (not deferred).

// "2026-10-03" -> "Sat, 3 Oct 2026": a set's date as the pages show it.
function longDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

// "2026-10-03" -> "03-10-2026 Setlist": a set's PDF file name.
function nameFor(iso) {
  return iso.split("-").reverse().join("-") + " Setlist";
}

// "2026-10-03" -> "Saturday 3 October": a set's date as a heading (with the year when it isn't
// this year's). `weekday: false` leaves the day's name out ("26 September").
function fullDate(iso, { weekday = true } = {}) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-GB", { ...(weekday && { weekday: "long" }), day: "numeric",
    month: "long", ...(y !== new Date().getFullYear() && { year: "numeric" }) }).replace(",", "");
}

// ---------------------------------------------------------------- your name
// Who's using this device, "" until they say (nav.js shows it, as "Guest" until then). With a
// name, the capos picked on sets are saved under it too, so they follow the person to their other
// devices. This device keeps its own copy: {setId or "s:<share code>": {slug: {shape, fret}}}.
const CAPOS = "capos";
function savedCapos() {
  try { return JSON.parse(localStorage.getItem(CAPOS) || "{}"); } catch (_) { return {}; }
}
// Capos for share links stay on the device only, so they're kept when the name's replace the rest.
const sharedOnly = capos => Object.fromEntries(Object.entries(capos).filter(([k]) => k.startsWith("s:")));
const setsOnly = capos => Object.fromEntries(Object.entries(capos).filter(([k]) => !k.startsWith("s:")));
function keepCapos(capos) {
  try { localStorage.setItem(CAPOS, JSON.stringify({ ...sharedOnly(savedCapos()), ...capos })); } catch (_) {}
}
// Your name is kept twice, in this browser's storage and in a cookie: the browsers built into
// chat apps (Telegram's) may clear one between visits while keeping the other.
function cookieName() {
  const m = document.cookie.match(/(?:^|; )yfm_name=([^;]*)/);
  try { return m ? decodeURIComponent(m[1]) : ""; } catch (_) { return ""; }
}
function keepNameCookie(name) {
  document.cookie = `yfm_name=${encodeURIComponent(name)}; path=/; max-age=${name ? 31536000 : 0}; samesite=lax`;
}
const yourName = {
  get() {
    let name = "";
    try { name = localStorage.getItem("name") || ""; } catch (_) {}
    if (!name) {  // this browser forgot it: the cookie may still have it
      name = cookieName();
      if (name) try { localStorage.setItem("name", name); } catch (_) {}
    } else if (!cookieName()) keepNameCookie(name);  // a name saved before the cookie
    return name;
  },
  capoUrl(name = this.get()) { return `/api/people/${encodeURIComponent(name)}/capos`; },
  // The capos chosen on this device join the name's; where both have one, the name's wins.
  // A "namechange" event follows, once they're in.
  async save(name) {
    try { localStorage.setItem("name", name); } catch (_) {}
    keepNameCookie(name);
    try {
      const r = await fetch(this.capoUrl(name), { method: "POST", headers: { "Content-Type": "application/json" },
                                                  body: JSON.stringify({ capos: setsOnly(savedCapos()) }) });
      if (r.ok) keepCapos((await r.json()).capos);
    } catch (_) {}
    dispatchEvent(new CustomEvent("namechange"));
  },
  forget() {
    try { localStorage.removeItem("name"); } catch (_) {}
    keepNameCookie("");
    dispatchEvent(new CustomEvent("namechange"));
  },
  // The capos saved under the name replace this device's copy (offline: the copy will do).
  async fetchCapos() {
    if (!this.get()) return;
    try {
      const r = await fetch(this.capoUrl());
      if (r.ok) keepCapos((await r.json()).capos);
    } catch (_) {}
  },
};

// ---------------------------------------------------------------- saving a PDF
// A computer downloads it. A phone or tablet gets its share sheet instead (Save to Files,
// Telegram, Print…): the browsers built into chat apps don't download, they only show the PDF,
// and people don't see how to keep it. The share sheet only opens straight from a tap, so on a
// phone the pages build the PDF ahead, as soon as they show something new, and Download PDF
// shares it at once. While a PDF is being built, the button says so (pdfBusy).
const shareFiles = matchMedia("(pointer: coarse)").matches &&
  !!navigator.canShare?.({ files: [new File(["%PDF-"], "set.pdf", { type: "application/pdf" })] });

// The PDF for what a page shows. `make(key)` builds it; `onBusy(true/false)` while it does.
// prepare(key): on a phone, build it now, ahead of the tap. ready(key): the PDF, if built.
// get(key): the PDF, built now if need be.
function pdfAhead(make, onBusy) {
  let key = null, blob = null, job = null;
  function run(k) {
    key = k; blob = null; onBusy(true);
    const mine = job = make(k).then(
      b => { if (job === mine) { blob = b; onBusy(false); } return b; },
      err => { if (job === mine) { key = null; job = null; onBusy(false); } throw err; });
    return mine;
  }
  return {
    prepare(k) { if (shareFiles && k !== key) run(k).catch(() => {}); },
    ready(k) { return k === key ? blob : null; },
    get(k) { return k === key && job ? job : run(k); },
  };
}

// Download PDF: share the PDF if it's ready (on a phone: at once, from the tap); otherwise build
// it, then share or download it. A share sheet opened after the wait may be refused (the tap is
// too long ago): the PDF is ready by then, so the next tap shares it.
async function savePdf(ahead, key, name) {
  const ready = ahead.ready(key);
  if (ready) return shareFiles ? sharePdf(ready, name) : downloadFile(ready, name);
  const blob = await ahead.get(key);
  if (shareFiles) sharePdf(blob, name); else downloadFile(blob, name);
}
function sharePdf(blob, name) {
  const file = new File([blob], name, { type: "application/pdf" });
  return navigator.share({ files: [file], title: name.replace(/\.pdf$/i, "") }).catch(err => {
    // AbortError: the sheet was closed. NotAllowedError: too long after the tap; tap again.
    if (err.name !== "AbortError" && err.name !== "NotAllowedError") downloadFile(file, name);
  });
}
function downloadFile(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 60000);
}
// The Download PDF button while its PDF is built: a spinner and "Generating PDF…".
function pdfBusy(button, on) {
  if (on) {
    button.dataset.label ??= button.textContent;
    button.innerHTML = `<span class="btn-spinner" aria-hidden="true"></span>Generating PDF…`;
    button.setAttribute("aria-busy", "true");
  } else if (button.dataset.label) {
    button.textContent = button.dataset.label;
    delete button.dataset.label;
    button.removeAttribute("aria-busy");
  }
}

// Someone may change a set while it's open elsewhere: run `check` when the tab comes back and
// every 30 s. (Each page decides what to offer; nothing reloads by itself.)
function watchForChanges(check) {
  setInterval(check, 30000);
  document.addEventListener("visibilitychange", check);
}

// A quick tooltip for anything with data-tip: the browser's own (title) waits a second or more.
// Shown on hover after a moment, and on focus (so a tap on a focusable one shows it on a phone).
(() => {
  let tip, timer, on;
  function show(el) {
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "tip"; tip.setAttribute("role", "tooltip");
      tip.style.cssText = "position:fixed;z-index:200;max-width:280px;padding:6px 9px;border-radius:6px;" +
        "background:#1E2B47;color:#E8ECF3;font:12.5px/1.4 'Hanken Grotesk',system-ui,sans-serif;" +
        "border:1px solid #3A4763;box-shadow:0 6px 18px rgba(4,8,16,.5);pointer-events:none;text-transform:none;letter-spacing:0;font-weight:400";
      document.body.append(tip);
    }
    tip.textContent = el.dataset.tip; tip.hidden = false; on = el;
    const r = el.getBoundingClientRect(), t = tip.getBoundingClientRect();
    const left = Math.max(8, Math.min(r.left + r.width / 2 - t.width / 2, innerWidth - t.width - 8));
    const top = r.top - t.height - 6 < 8 ? r.bottom + 6 : r.top - t.height - 6;
    tip.style.left = left + "px"; tip.style.top = top + "px";
  }
  function hide() { clearTimeout(timer); on = null; if (tip) tip.hidden = true; }
  document.addEventListener("mouseover", e => {
    const el = e.target.closest?.("[data-tip]");
    if (el === on) return;
    hide();
    if (el) timer = setTimeout(() => show(el), 120);
  });
  document.addEventListener("focusin", e => { const el = e.target.closest?.("[data-tip]"); hide(); if (el) show(el); });
  document.addEventListener("focusout", hide);
  document.addEventListener("scroll", hide, true);
})();
