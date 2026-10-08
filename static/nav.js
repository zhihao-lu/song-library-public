// The top of every page (see theme.css). Each page writes its sky, bar and heading itself, so they
// show at once; this fills in the logo, draws the stars and the hill, and adds your name.
(() => {
  const nav = document.querySelector(".topnav");
  if (!nav) return;
  // The logo is inlined (not an <img>) so its text takes the page's colour.
  fetch("/static/logo.svg").then(r => r.text()).then(svg => {
    nav.querySelector(".logo").innerHTML = svg.replace(/<style>[\s\S]*?<\/style>/, "");
  }).catch(() => {});

  // The look trial is over: forget the cookie that used to pick a look.
  document.cookie = "look=; path=/; max-age=0; samesite=lax";

  // The hill's crest is at 73.4% across and 31% of its height down (theme.css puts the star there).
  const STAR = "168.1,-20.7 159.0,30.8 184.2,10.6 161.5,33.6 213.7,29.9 161.9,37.2 188.9,55.0 160.0,40.5 " +
    "179.6,89.0 156.6,42.0 148.1,73.2 153.0,41.2 112.9,74.8 150.5,38.4 118.2,40.0 150.1,34.8 105.8,7.0 " +
    "152.0,31.5 140.5,1.3 155.4,30.0";
  const sky = nav.closest(".sky");
  if (sky) sky.insertAdjacentHTML("afterbegin", `<div class="sky-art" aria-hidden="true">
    <div class="glow"></div>
    <svg class="hill" viewBox="0 0 1000 70" preserveAspectRatio="none">
      <path d="M0 70 L0 60 C 235 58, 500 24, 734 22 C 845 21, 938 34, 1000 42 L1000 70 Z"/></svg>
    <svg class="crest" viewBox="104 -22 112 112"><polygon points="${STAR}" fill="#FFD84D" stroke="#F2B705"
      stroke-width="2" stroke-linejoin="round"/></svg>
  </div>`);
  // The three stars sit in the bar, between the pages and your name (theme.css).
  if (sky) nav.insertAdjacentHTML("beforeend", `<span class="stars" aria-hidden="true">
    <svg viewBox="0 0 46 30"><g fill="currentColor">
      <circle cx="5" cy="19" r="4.5" opacity=".08"/><circle cx="5" cy="19" r="1.3" opacity=".7"/>
      <circle cx="22" cy="6" r="5" opacity=".09"/><circle cx="22" cy="6" r="1.6" opacity=".85"/>
      <circle cx="40" cy="23" r="4" opacity=".07"/><circle cx="40" cy="23" r="1.1" opacity=".6"/></g></svg>
  </span>`);

  // Your name: who's using this device, "Guest" until it's set. The capos you pick on a set
  // follow the name to your other devices (see yourName in common.js).
  if (!sky || typeof yourName === "undefined") return;
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const PERSON = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
    stroke-linecap="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg>`;
  nav.insertAdjacentHTML("beforeend", `<details class="who" id="who">
    <summary><span class="avatar" aria-hidden="true"></span><span class="who-name"></span></summary>
    <form class="who-box" id="whoForm">
      <label for="whoName">Your name</label>
      <input type="text" id="whoName" autocomplete="name" maxlength="60" placeholder="e.g. Wei">
      <small>Just your name, no password. The site remembers your choices under it (like the capos you
        pick on a set, and a setlist you haven't saved yet), so they follow you to your other devices. Anyone who types the same name sees them too.</small>
      <div class="btns"><button type="submit">Save</button>
        <button type="button" class="secondary" id="whoForget">Forget on this device</button></div>
    </form>
  </details>`);
  const who = nav.querySelector("#who");
  function show() {
    const name = yourName.get();
    who.classList.toggle("guest", !name);
    who.querySelector(".avatar").innerHTML = name ? esc([...name][0].toUpperCase()) : PERSON;
    who.querySelector(".who-name").textContent = name || "Guest";
    who.querySelector("summary").setAttribute("aria-label", name ? `Your name: ${name}. Change it` : "Your name: Guest. Set your name");
    who.querySelector("#whoName").value = name;
    who.querySelector("#whoForget").hidden = !name;
  }
  show();
  addEventListener("namechange", show);
  who.addEventListener("toggle", () => { if (who.open) who.querySelector("#whoName").focus(); });
  who.querySelector("#whoForm").onsubmit = async e => {
    e.preventDefault();
    const name = who.querySelector("#whoName").value.trim().replace(/\s+/g, " ");
    if (!name) return;
    who.open = false;
    if (typeof track === "function") track("name_set", { first: !yourName.get() });
    const saving = yourName.save(name);  // stores the name at once; the capos follow
    show();
    await saving;
  };
  who.querySelector("#whoForget").onclick = () => { who.open = false; yourName.forget(); };
  // Closes on a tap elsewhere or Escape, like any menu.
  document.addEventListener("click", e => { if (who.open && !who.contains(e.target)) who.open = false; });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && who.open) { who.open = false; who.querySelector("summary").focus(); }
  });
})();
