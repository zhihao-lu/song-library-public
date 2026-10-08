"""/analytics: which songs the team sings most, worked out from the sets.

A past set is what the team sang (see sets_store.first_coming_day); a set still to come is
marked as not sung yet. /api/analytics/sung gives one row per song per set, with who led and the
key it was played in, and the page (static/analytics.html) counts and pivots them. Like the sets
themselves, it needs no password; it's simply not linked from anywhere.
"""
from flask import Blueprint, jsonify, request

import sets_api
from library import matches, search_fields
from pages import page
from sets_store import first_coming_day

bp = Blueprint("song_stats", __name__)


@bp.get("/analytics")
def analytics_page():
    return page("analytics.html")


@bp.get("/api/analytics/sung")
def sung():
    """{"today", "rows": [{date, past, leader, song, title, artist, key}]}. `song` identifies the
    song itself, so its arrangements and older slugs count as one song."""
    coming = first_coming_day(sets_api._now()).isoformat()
    return jsonify(today=coming, rows=[{k: v for k, v in r.items() if not k.startswith("_")} for r in _rows(coming)])


@bp.get("/api/analytics/find")
def find():
    """{"songs": [song, …]}: the songs in the sets matching ?q=, best first, found exactly as the
    main page's search finds songs (library.matches: titles, pinyin, initials, artists…)."""
    index = {}
    for r in _rows(first_coming_day(sets_api._now()).isoformat()):
        index.setdefault(r["song"], r["_search"])
    return jsonify(songs=[s["song"] for s in matches(index.values(), request.args.get("q", ""))])


def _rows(coming):
    """One row per song per set, oldest first; each row's `_search` is what matches() reads."""
    library, store = sets_api._library(), sets_api._store()
    leaders = store.leaders()
    rows = []
    for s in sorted(store.all_sets(), key=lambda s: s["date"]):
        base = {"date": s["date"], "past": s["date"] < coming, "leader": leaders.get((s["date"], s["tag"]))}
        for e in s["songs"]:
            rows.append({**base, **_song(library, e)})
    return rows


def _song(library, entry):
    """Which song a set's entry is, as {song, title, artist, key}."""
    if "slug" not in entry:  # a song from the team's history that was never in the library
        return _outside("title:" + entry["title"].casefold(), entry["title"], entry.get("to"))
    found = library.get(entry["slug"])
    if not found:
        title = library.last_title(entry["slug"]) or entry["slug"]
        return _outside("gone:" + library.resolve(entry["slug"]), title, entry.get("to"))
    artist = (found.get("artist") or "").strip()
    if artist.casefold() == "by":  # some charts' credit line is only the word "by"
        artist = ""
    song = found.get("song_id") or found["slug"]
    return {"song": song, "title": found["title"], "artist": artist, "key": entry.get("to") or found.get("key"),
            "_search": {**found, "song": song}}


def _outside(song, title, key):
    """A song the library doesn't have (any more), searchable by its title like any other."""
    return {"song": song, "title": title, "artist": "", "key": key,
            "_search": {"song": song, "title": title, **search_fields(title)}}
