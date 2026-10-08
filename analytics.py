"""Usage tracking: what people do on the site, kept on this server, and a private page to read it.

The pages send what happens (track.js): each page shown, each button pressed, and the steps that
matter (a search, a song added, a PDF downloaded, a set saved). Each event carries the browser's
own random id (`device`), the visit it belongs to (`visit`: a run of activity with no 30-minute
gap), and, when someone has typed their name in the bar, that name (from its cookie).

  /api/events   the pages send events here (in batches, also as a beacon when a page closes)
  /stats        the stats page, and /api/stats its numbers: both need STATS_PASSWORD (basic
                auth, any user name). With no STATS_PASSWORD set, they don't exist.

Events live in their own database (EVENTS_DB, default data/events.db), apart from the sets.
"""
import hmac
import json
import os
import sqlite3
import threading
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote

from flask import Blueprint, Response, abort, current_app, jsonify, request, send_from_directory

bp = Blueprint("analytics", __name__)
DEFAULT_DB = Path(__file__).parent / "data" / "events.db"
MAX_BATCH = 100      # events in one request
MAX_DATA = 2000      # characters of one event's details
OUTCOMES = ("pdf_downloaded", "share_link", "set_saved")  # what a visit to the builder is for

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY,
    at      TEXT NOT NULL,      -- server time (the site runs on Singapore time)
    device  TEXT NOT NULL,
    visit   TEXT NOT NULL,
    name    TEXT,               -- the name in the bar, if any
    look    TEXT,               -- night or classic
    page    TEXT NOT NULL,      -- the path, e.g. "/" or "/sets/12"
    event   TEXT NOT NULL,      -- e.g. page_view, click, song_added
    data    TEXT NOT NULL       -- JSON details
);
CREATE INDEX IF NOT EXISTS events_at ON events (at);
"""


class EventStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, rows):
        with self._lock, self._db() as db:
            db.executemany("INSERT INTO events (at, device, visit, name, look, page, event, data)"
                           " VALUES (:at, :device, :visit, :name, :look, :page, :event, :data)", rows)

    def since(self, start):
        with self._db() as db:
            rows = db.execute("SELECT * FROM events WHERE at >= ? ORDER BY at, id",
                              (start.isoformat(timespec="seconds"),)).fetchall()
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]


_stores = {}


def _store():
    path = str(current_app.config.get("EVENTS_DB") or os.environ.get("EVENTS_DB") or DEFAULT_DB)
    if path not in _stores:
        _stores[path] = EventStore(path)
    return _stores[path]


def _now():
    return current_app.config.get("NOW", datetime.now)()


def _clip(value, n):
    return str(value or "")[:n]


# ---------------------------------------------------------------- receiving events

@bp.post("/api/events")
def receive():
    """{device, visit, events: [{page, event, data}]}. A beacon sends it as text, so the body is
    read whatever its content type."""
    try:
        body = json.loads(request.get_data(as_text=True) or "{}")
        events = body["events"]
        device, visit = _clip(body["device"], 64), _clip(body["visit"], 64)
        if not (device and visit and isinstance(events, list)):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        return jsonify(error="Not a batch of events."), 400
    name = " ".join(unquote(request.cookies.get("yfm_name", "")).split())[:60] or None
    look = "night"  # the only look since the classic one was retired (older events may say "classic")
    at = _now().isoformat(timespec="seconds")
    rows = []
    for e in events[:MAX_BATCH]:
        if not isinstance(e, dict) or not e.get("event"):
            continue
        data = json.dumps(e.get("data") if isinstance(e.get("data"), dict) else {}, ensure_ascii=False)
        rows.append({"at": at, "device": device, "visit": visit, "name": name, "look": look,
                     "page": _clip(e.get("page"), 200) or "/", "event": _clip(e["event"], 40),
                     "data": data if len(data) <= MAX_DATA else "{}"})
    if rows:
        _store().add(rows)
    return "", 204


# ---------------------------------------------------------------- reading them

def _stats_password():
    return current_app.config.get("STATS_PASSWORD") or os.environ.get("STATS_PASSWORD")


def stats_allowed():
    """Whether this request may see the stats (also lets it past the site's own password)."""
    password, auth = _stats_password(), request.authorization
    return bool(password and auth and hmac.compare_digest(auth.password or "", password))


def _guard():
    if not _stats_password():
        abort(404)
    if not stats_allowed():
        abort(Response("Password required.", 401, {"WWW-Authenticate": 'Basic realm="YFM stats"'}))


@bp.get("/stats")
def stats_page():
    _guard()
    return send_from_directory(current_app.static_folder, "stats.html")


@bp.get("/api/stats")
def stats():
    """The numbers for the last ?days= days (default 30)."""
    _guard()
    days = max(1, min(int(request.args.get("days", 30) or 30), 365))
    now = _now()
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return jsonify(summarise(_store().since(start), start, now))


def _page_kind(path):
    if path == "/":
        return "Find songs (builder)"
    if path.startswith("/sets/"):
        return "A set"
    if path.startswith("/s/"):
        return "A share link"
    return {"/sets": "Setlists", "/convert": "Convert PDF"}.get(path, path)


def summarise(events, start, now):
    """Everything the stats page shows, from the events since `start`."""
    visits = defaultdict(list)
    for e in events:
        visits[e["visit"]].append(e)

    def has(evs, *names):
        return any(e["event"] in names for e in evs)

    views = [e for e in events if e["event"] == "page_view"]
    builder = {v: evs for v, evs in visits.items() if any(e["page"] == "/" for e in evs)}

    # Each day's visits and devices (a visit counts on the day it started).
    by_day = {(start + timedelta(days=i)).date().isoformat(): {"visits": 0, "devices": set()}
              for i in range((now.date() - start.date()).days + 1)}
    heat = [[0] * 24 for _ in range(7)]  # [weekday Mon..Sun][hour]
    for evs in visits.values():
        first = datetime.fromisoformat(evs[0]["at"])
        day = by_day.get(first.date().isoformat())
        if day:
            day["visits"] += 1
            day["devices"].add(evs[0]["device"])
        heat[first.weekday()][first.hour] += 1

    def first_data(evs, event, key):
        return next((e["data"].get(key) for e in evs if e["event"] == event and e["data"].get(key)), None)

    # Per visit to the builder: which tab it ended in, and what it came to.
    funnel = {"Opened the builder": len(builder),
              "Added a song": sum(has(evs, "song_added") for evs in builder.values()),
              "Downloaded a PDF": sum(has(evs, "pdf_downloaded") for evs in builder.values()),
              "Made a share link": sum(has(evs, "share_link") for evs in builder.values()),
              "Saved a set": sum(has(evs, "set_saved") for evs in builder.values())}
    tabs = Counter(e["data"].get("mode") for e in events if e["event"] == "tab")
    outcome_by_mode = defaultdict(Counter)
    for e in events:
        if e["event"] in OUTCOMES and e["data"].get("mode"):
            outcome_by_mode[e["data"]["mode"]][e["event"]] += 1

    searches = [e for e in events if e["event"] == "search" and e["data"].get("q")]
    people = defaultdict(lambda: {"visits": set(), "last": "", "pdfs": 0, "sets": 0, "shares": 0})
    for e in events:
        if not e["name"]:
            continue
        p = people[e["name"]]
        p["visits"].add(e["visit"])
        p["last"] = max(p["last"], e["at"])
        p["pdfs"] += e["event"] == "pdf_downloaded"
        p["sets"] += e["event"] == "set_saved"
        p["shares"] += e["event"] == "share_link"

    def journey(v, evs):
        return {"visit": v, "device": evs[0]["device"][:8], "name": next((e["name"] for e in evs if e["name"]), None),
                "look": evs[-1]["look"], "start": evs[0]["at"], "end": evs[-1]["at"],
                "phone": bool(first_data(evs, "page_view", "touch")),
                "outcomes": sorted({e["event"] for e in evs if e["event"] in OUTCOMES}),
                "steps": [{"at": e["at"], "page": e["page"], "event": e["event"], "data": e["data"]} for e in evs]}

    recent = sorted(visits.items(), key=lambda kv: kv[1][-1]["at"], reverse=True)[:50]
    durations = sorted((datetime.fromisoformat(evs[-1]["at"]) - datetime.fromisoformat(evs[0]["at"])).total_seconds()
                       for evs in visits.values())
    return {
        "range": {"start": start.date().isoformat(), "end": now.date().isoformat()},
        "totals": {"visits": len(visits), "devices": len({e["device"] for e in events}),
                   "people": len({e["name"] for e in events if e["name"]}), "page_views": len(views),
                   "pdfs": sum(e["event"] == "pdf_downloaded" for e in events),
                   "shares": sum(e["event"] == "share_link" for e in events),
                   "sets_saved": sum(e["event"] == "set_saved" for e in events),
                   "median_visit_seconds": durations[len(durations) // 2] if durations else 0},
        "by_day": [{"day": d, "visits": v["visits"], "devices": len(v["devices"])} for d, v in by_day.items()],
        "heat": heat,
        "pages": Counter(_page_kind(e["page"]) for e in views).most_common(),
        "looks": Counter(e["look"] for e in views).most_common(),
        "phones": Counter("Phone or tablet" if e["data"].get("touch") else "Computer" for e in views).most_common(),
        "funnel": list(funnel.items()),
        "tabs": [[m or "?", n] for m, n in tabs.most_common()],
        "outcomes_by_tab": {m: dict(c) for m, c in outcome_by_mode.items()},
        "clicks": Counter(e["data"].get("label") or e["data"].get("id") or "?"
                          for e in events if e["event"] == "click").most_common(30),
        "searches": Counter(e["data"]["q"].strip().lower() for e in searches).most_common(25),
        "searches_no_results": Counter(e["data"]["q"].strip().lower() for e in searches
                                       if e["data"].get("results") == 0).most_common(15),
        "songs_added": Counter(e["data"].get("title") or e["data"].get("slug")
                               for e in events if e["event"] == "song_added").most_common(25),
        "people": sorted(({"name": n, "visits": len(p["visits"]), "last": p["last"], "pdfs": p["pdfs"],
                           "sets": p["sets"], "shares": p["shares"]} for n, p in people.items()),
                         key=lambda p: p["last"], reverse=True),
        "journeys": [journey(v, evs) for v, evs in recent],
    }
