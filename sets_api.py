"""Sets: setlists saved on the server, named by their date and shared with everyone.

Anyone who can open the site can make, change or delete a set; nobody owns one. A past set (from
before today, or today's once Saturday's service is over at 5 pm) is what the team sang, so it can be changed but not deleted, replaced, or moved to
a coming date (sets_store.PastSet).

  /sets                 the Sets tab
  /sets/<id>            View: a set's pages, for reading at practice
  /api/sets             list (upcoming, then past) / create
  /api/sets/<id>        get / update / delete (for good)
  /s/<code>             a share link's View page: a hidden snapshot of the song picker
  /api/shares           make a share link (the same songs and name give the same link)
  /api/shares/<code>    open one (links are forgotten 30 days after they were last opened)
  /api/people/<name>/capos                get / add this device's (the name's own win)
  /api/people/<name>/capos/<set>/<slug>   save / clear one capo
  /api/people/<name>/draft                get / keep / clear the name's unsaved setlist (14 days)
"""

import os
from datetime import date as Date
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote

from flask import Blueprint, abort, current_app, jsonify, make_response, request, send_from_directory

import chordtools as ct
from pages import page
from library import public
from sets_store import DateTaken, PastSet, SetStore, Stale, first_coming_day

bp = Blueprint("sets", __name__)

DEFAULT_DB = Path(__file__).parent / "data" / "sets.db"
_stores = {}


def _library():
    return current_app.extensions["library"]


def _now():
    return current_app.config.get("NOW", datetime.now)()


def _past(s):
    """A set from before today (or today's, once Saturday's service is over at 5 pm): what the team
    sang, so it's kept (it can be changed, not deleted)."""
    return s["date"] < first_coming_day(_now()).isoformat()


def _store():
    path = str(current_app.config.get("SETS_DB") or os.environ.get("SETS_DB") or DEFAULT_DB)
    if path not in _stores:
        _stores[path] = SetStore(path)
    return _stores[path]


def _songs(entries):
    """A setlist's songs as the pages see them: each with its library details (as search returns
    them), so the builder can edit it. A song since removed from the library is marked missing,
    and so is one that was never in it (kept by its title, with no slug)."""
    library = _library()
    songs = []
    for e in entries:
        if "slug" not in e:
            songs.append({"slug": None, "title": e["title"], "key": None, "to": e.get("to"),
                          "missing": True, "capos": []})
            continue
        song = library.get(e["slug"])
        gone = library.last_title(e["slug"])
        info = public(song) if song else {"slug": e["slug"], "key": None,
                                          "title": f"{gone} (removed from library)" if gone else e["slug"]}
        # Capos for the key the team plays it in; a fixed chart can't be redrawn in other shapes.
        capos = ct.capo_options(e.get("to") or song["key"]) if song and song["transposable"] else []
        songs.append({**info, "to": e.get("to"), "missing": song is None, "capos": capos})
    return songs


def _maker():
    """The name in the bar of whoever is saving (the pages keep it in a cookie too), or None for a guest."""
    name = " ".join(unquote(request.cookies.get("yfm_name", "")).split())
    return name[:60] or None


def _public(s):
    """A set as the pages see it."""
    return {"id": s["id"], "date": s["date"], "tag": s["tag"], "songs": _songs(s["songs"]), "layout": s["layout"],
            "columns": s["columns"], "paper": s["paper"], "version": s["version"], "updated_at": s["updated_at"]}


def _parse(body):
    """The builder's setlist as a set's fields, or ValueError."""
    try:
        day = Date.fromisoformat(str(body.get("date", "")))
    except ValueError:
        raise ValueError("Pick the set's date.") from None
    return {"date": day.isoformat(), **_parse_setlist(body)}


def _parse_setlist(body):
    """The builder's songs and layout options, or ValueError."""
    songs = body.get("songs")
    entries = [_song_entry(e) if isinstance(e, dict) else None for e in songs] if isinstance(songs, list) else [None]
    if None in entries:
        raise ValueError("A set's songs must be a list of songs.")
    layout = body.get("layout", "original")
    columns = int(body.get("columns", 1))
    paper = body.get("paper", "A4")
    if layout not in ("original", "uniform") or columns not in (1, 2) or paper not in ("A4", "Letter"):
        raise ValueError("Unknown layout options.")
    return {"songs": entries, "layout": layout, "columns": columns, "paper": paper}


def _song_entry(e):
    """A setlist song as it's kept: a library song by its slug, or a song not in the library by its
    title (sets imported from the team's history have those). None if it's neither."""
    to = e.get("to") or None
    if isinstance(e.get("slug"), str):
        return {"slug": e["slug"], "to": to}
    if isinstance(e.get("title"), str) and e["title"].strip():
        return {"title": e["title"].strip(), "to": to}
    return None


@bp.get("/sets")
def sets_page():
    return page("sets.html")


@bp.get("/sets/<int:set_id>")
def view_page(set_id):
    """A set's pages, full width, for reading at practice."""
    return page("view.html")


@bp.get("/api/sets")
def list_sets():
    """Upcoming sets (today's included, until a Saturday's 5 pm) soonest first, then past sets newest first, each with who
    led it (None if no one was recorded)."""
    sets = sorted(_store().all_sets(), key=lambda s: s["date"])
    leaders = _store().leaders()
    return jsonify(upcoming=[_public(s) for s in sets if not _past(s)],
                   past=[{**_public(s), "leader": leaders.get((s["date"], s["tag"]))} for s in reversed(sets) if _past(s)])


@bp.post("/api/sets")
def create_set():
    try:
        data = _parse(request.json or {})
        saved = _store().create(data, _now(), created_by=_maker())
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except DateTaken as e:
        return jsonify(error=str(e), existing=_public(e.existing)), 409
    return jsonify(_public(saved)), 201


@bp.get("/api/sets/<int:set_id>")
def get_set(set_id):
    s = _store().get(set_id)
    if not s:
        abort(404)
    return jsonify(_public(s))


@bp.put("/api/sets/<int:set_id>")
def update_set(set_id):
    try:
        body = request.json or {}
        data = _parse(body)
        # "replace": the id of the set on the new date, which this one takes the place of.
        saved = _store().update(set_id, data, body.get("version"), _now(), replace=body.get("replace"))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except DateTaken as e:
        return jsonify(error=str(e), existing=_public(e.existing)), 409
    except Stale as e:
        return jsonify(error=str(e), set=_public(e.current)), 409
    except PastSet as e:
        return jsonify(error=str(e)), 409
    if not saved:
        abort(404)
    return jsonify(_public(saved))


@bp.delete("/api/sets/<int:set_id>")
def delete_set(set_id):
    try:
        if not _store().delete(set_id, _now()):
            abort(404)
    except PastSet as e:
        return jsonify(error=str(e)), 409
    return jsonify(ok=True)


# ---------------------------------------------------------------- capos under a name

SHAPES = {shape for shapes in ct.CAPO_SHAPES.values() for shape in shapes}
BAD_CAPO = f"A capo is a shape and a fret from 1 to {ct.MAX_CAPO_FRET}."


def _person(name):
    if not name.strip():
        abort(make_response(jsonify(error="Type a name."), 400))
    return name


def _capo(c):
    """A capo as sent: {"shape", "fret"}, or ValueError."""
    if not (isinstance(c, dict) and c.get("shape") in SHAPES and isinstance(c.get("fret"), int)
            and 1 <= c["fret"] <= ct.MAX_CAPO_FRET):
        raise ValueError(BAD_CAPO)
    return {"shape": c["shape"], "fret": c["fret"]}


def _resolved(capos):
    """Capos under the IDs their songs have now (a song folded into another one has a new ID)."""
    resolve = _library().resolve
    return {set_id: {resolve(slug): c for slug, c in songs.items()} for set_id, songs in capos.items()}


KEEP_DRAFTS = timedelta(days=14)
MAX_DRAFT = 100_000  # characters: a long setlist is a few thousand


@bp.get("/api/people/<name>/draft")
def get_draft(name):
    draft, saved_at = _store().draft(_person(name), _now() - KEEP_DRAFTS)
    return jsonify(draft=draft, saved_at=saved_at)


@bp.put("/api/people/<name>/draft")
def keep_draft(name):
    """{"draft": {songs: [...], ...}}: the builder's unsaved setlist, kept as the builder sent it."""
    if request.content_length and request.content_length > MAX_DRAFT:
        return jsonify(error="That setlist is too long to keep."), 413
    draft = (request.get_json(silent=True) or {}).get("draft")
    if not (isinstance(draft, dict) and isinstance(draft.get("songs"), list)):
        return jsonify(error="A draft is a setlist: {songs: [...]}."), 400
    _store().save_draft(_person(name), draft, _now())
    return jsonify(ok=True)


@bp.delete("/api/people/<name>/draft")
def clear_draft(name):
    _store().save_draft(_person(name), None, _now())
    return "", 204


@bp.get("/api/people/<name>/capos")
def get_capos(name):
    return jsonify(capos=_resolved(_store().capos(_person(name))))


@bp.post("/api/people/<name>/capos")
def add_capos(name):
    try:
        capos = {str(int(set_id)): {_library().resolve(slug): _capo(c) for slug, c in songs.items()}
                 for set_id, songs in ((request.json or {}).get("capos") or {}).items()}
    except (ValueError, AttributeError):
        return jsonify(error=BAD_CAPO), 400
    return jsonify(capos=_resolved(_store().add_capos(_person(name), capos)))


@bp.put("/api/people/<name>/capos/<int:set_id>/<slug>")
def save_capo(name, set_id, slug):
    try:
        capo = _capo(request.json)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    _store().set_capo(_person(name), set_id, _library().resolve(slug), capo)
    return jsonify(ok=True)


@bp.delete("/api/people/<name>/capos/<int:set_id>/<slug>")
def clear_capo(name, set_id, slug):
    for old in {slug, _library().resolve(slug)}:
        _store().set_capo(_person(name), set_id, old, None)
    return jsonify(ok=True)


# ---------------------------------------------------------------- share links
# The song picker's songs as a hidden snapshot, with no date, opened only through its link.

KEEP_SHARES = timedelta(days=30)


@bp.post("/api/shares")
def create_share():
    try:
        setlist = {**_parse_setlist(request.json or {}), "name": _share_name(request.json or {})}
    except ValueError as e:
        return jsonify(error=str(e)), 400
    if not setlist["songs"]:
        return jsonify(error="Add at least one song."), 400
    return jsonify(code=_store().share(setlist, _now(), KEEP_SHARES)), 201


@bp.get("/api/shares/<code>")
def get_share(code):
    s = _store().open_share(code, _now(), KEEP_SHARES)
    if not s:
        abort(404)
    return jsonify(code=s["code"], songs=_songs(s["songs"]), layout=s["layout"], columns=s["columns"],
                   paper=s["paper"], name=s["name"])


SHARE_NAME_MAX = 60  # as long as "your name" may be


def _share_name(body):
    """A one-time setlist's name, with its spacing tidied; None if it has none, ValueError if too long."""
    name = " ".join(str(body.get("name") or "").split())
    if len(name) > SHARE_NAME_MAX:
        raise ValueError(f"Keep the name to {SHARE_NAME_MAX} characters.")
    return name or None


@bp.get("/s/<code>")
def share_page(code):
    """A share link's View page (the same page as a set's, without Edit)."""
    return page("view.html")
