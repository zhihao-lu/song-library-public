"""Where sets are kept: one SQLite file in the data folder (the yfm-data volume in Docker).

Also each named viewer's capos (a name is only a name: no password), and who led the set on each
date (shown on past sets).

A set is a date (its name), a tag (always '' for now: room for "Saturday" / "Chinese"
later, when a date may have several sets), its songs as the builder sends them
([{"slug", "to"}]) and the builder's layout options. Every change bumps its version,
which is how pages notice that someone else changed it.
"""

import hashlib
import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import time, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sets (
    id          INTEGER PRIMARY KEY,
    date        TEXT NOT NULL,
    tag         TEXT NOT NULL DEFAULT '',
    songs       TEXT NOT NULL,
    layout      TEXT NOT NULL,
    columns     INTEGER NOT NULL,
    paper       TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT NOT NULL,
    deleted_at  TEXT,   -- no longer used: sets are deleted outright
    created_by  TEXT    -- the name in the bar of whoever made it, if any; kept, never shown
);
-- One set per date (and tag). ("Not deleted" is left over from when deleting hid a set.)
CREATE UNIQUE INDEX IF NOT EXISTS sets_date_tag ON sets (date, tag) WHERE deleted_at IS NULL;
-- Share links: a hidden snapshot of the song picker, found by its code. `digest` identifies
-- the content (and name), so the same songs and keys give the same link. `name` is the one-time
-- setlist's own name, if it was given one (added later: see SetStore.__init__).
CREATE TABLE IF NOT EXISTS shares (
    code         TEXT PRIMARY KEY,
    digest       TEXT NOT NULL UNIQUE,
    songs        TEXT NOT NULL,
    layout       TEXT NOT NULL,
    columns      INTEGER NOT NULL,
    paper        TEXT NOT NULL,
    last_opened  TEXT NOT NULL,
    name         TEXT
);
-- Who led on each date (and tag), as one standard name: from the team's history and the schedule
-- (history_import.py). Past sets show it.
CREATE TABLE IF NOT EXISTS leaders (
    date  TEXT NOT NULL,
    tag   TEXT NOT NULL DEFAULT '',
    name  TEXT NOT NULL,
    PRIMARY KEY (date, tag)
);
-- A viewer's capo for a song in a set, saved under their name (see SetStore.capos).
CREATE TABLE IF NOT EXISTS capos (
    person  TEXT NOT NULL,
    set_id  INTEGER NOT NULL,
    slug    TEXT NOT NULL,
    shape   TEXT NOT NULL,
    fret    INTEGER NOT NULL,
    PRIMARY KEY (person, set_id, slug)
);
-- An unsaved setlist kept under a name, so the builder can offer it back on any device.
CREATE TABLE IF NOT EXISTS drafts (
    person    TEXT PRIMARY KEY,
    draft     TEXT NOT NULL,   -- JSON, as the builder sent it
    saved_at  TEXT NOT NULL
);
"""

FIELDS = ("date", "songs", "layout", "columns", "paper")


class DateTaken(Exception):
    """Another set already has this date. `existing` is that set."""

    def __init__(self, existing):
        super().__init__(f"{existing['date']} already has a set.")
        self.existing = existing


SERVICE_OVER = time(17)  # Saturday's service is over by 5 pm: its set is then a past set


def first_coming_day(now):
    """The first date whose set is still to come: today, or tomorrow once it's a Saturday after
    5 pm (the service is over). Sets dated before it are past sets."""
    today = now.date()
    if today.weekday() == 5 and now.time() >= SERVICE_OVER:
        return today + timedelta(days=1)
    return today


class PastSet(Exception):
    """A past set is what the team sang: it can be changed, but not deleted, replaced, or moved to
    a coming date (where it could then be deleted). "Past" is before `today` (an ISO date)."""


class Stale(Exception):
    """The set changed since the caller read it. `current` is the set as it is now."""

    def __init__(self, current):
        super().__init__("Someone changed this set since you opened it.")
        self.current = current


class SetStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()  # one writer at a time, so the date check and insert agree
        with self._db() as db:
            db.executescript(SCHEMA)
            # Sets made before who made them was kept.
            if "created_by" not in {c["name"] for c in db.execute("PRAGMA table_info(sets)")}:
                db.execute("ALTER TABLE sets ADD COLUMN created_by TEXT")
            # Share links made before they had names.
            if "name" not in {c["name"] for c in db.execute("PRAGMA table_info(shares)")}:
                db.execute("ALTER TABLE shares ADD COLUMN name TEXT")
            # Sets deleted when deleting only hid them (in "Recently deleted") go for good.
            db.execute("DELETE FROM capos WHERE set_id IN (SELECT id FROM sets WHERE deleted_at IS NOT NULL)")
            db.execute("DELETE FROM sets WHERE deleted_at IS NOT NULL")

    @contextmanager
    def _db(self):
        """A connection that commits on success and is always closed."""
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _row(row):
        if row is None:
            return None
        s = dict(row)
        s["songs"] = json.loads(s["songs"])
        return s

    def get(self, set_id):
        with self._db() as db:
            return self._row(db.execute("SELECT * FROM sets WHERE id = ?", (set_id,)).fetchone())

    def on_date(self, date, tag=""):
        with self._db() as db:
            return self._row(db.execute("SELECT * FROM sets WHERE date = ? AND tag = ?",
                                        (date, tag)).fetchone())

    def all_sets(self):
        with self._db() as db:
            return [self._row(r) for r in db.execute("SELECT * FROM sets")]

    def create(self, data, now, created_by=None):
        with self._lock, self._db() as db:
            if existing := self.on_date(data["date"]):
                raise DateTaken(existing)
            cur = db.execute(
                "INSERT INTO sets (date, songs, layout, columns, paper, updated_at, created_by)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (data["date"], json.dumps(data["songs"]), data["layout"], data["columns"], data["paper"],
                 now.isoformat(timespec="seconds"), created_by))
            set_id = cur.lastrowid
        return self.get(set_id)

    def update(self, set_id, data, version, now, replace=None):
        """Replace a set's date, songs and options, if it's still at `version`.
        None if there's no such set. If its new date has another set, that's refused (DateTaken)
        unless `replace` is that set's id: then it's deleted, with its capos, in the same step.
        A past set (see first_coming_day) can't move to a coming date or be replaced (PastSet)."""
        today = first_coming_day(now).isoformat()
        with self._lock, self._db() as db:
            current = self.get(set_id)
            if not current:
                return None
            if current["version"] != version:
                raise Stale(current)
            if current["date"] < today <= data["date"]:
                raise PastSet("A past set can't be moved to a coming date.")
            existing = self.on_date(data["date"])
            if existing and existing["id"] != set_id:
                if existing["id"] != replace:
                    raise DateTaken(existing)
                if existing["date"] < today:
                    raise PastSet("A past set can't be replaced.")
                db.execute("DELETE FROM sets WHERE id = ?", (existing["id"],))
                db.execute("DELETE FROM capos WHERE set_id = ?", (existing["id"],))
            db.execute(
                "UPDATE sets SET date = ?, songs = ?, layout = ?, columns = ?, paper = ?, version = version + 1,"
                " updated_at = ? WHERE id = ?",
                (data["date"], json.dumps(data["songs"]), data["layout"], data["columns"], data["paper"],
                 now.isoformat(timespec="seconds"), set_id))
        return self.get(set_id)

    def delete(self, set_id, now):
        """Delete a set for good, with the capos saved for it. False if there's no such set; a past
        set (see first_coming_day) isn't deleted (PastSet)."""
        with self._lock, self._db() as db:
            current = self.get(set_id)
            if current and current["date"] < first_coming_day(now).isoformat():
                raise PastSet("A past set can't be deleted.")
            gone = db.execute("DELETE FROM sets WHERE id = ?", (set_id,)).rowcount > 0
            db.execute("DELETE FROM capos WHERE set_id = ?", (set_id,))
            return gone

    # ------------------------------------------------------------ who led

    def set_leaders(self, leaders, tag=""):
        """{date: name}: who led on each date, replacing whoever was there."""
        with self._lock, self._db() as db:
            db.executemany("INSERT OR REPLACE INTO leaders (date, tag, name) VALUES (?, ?, ?)",
                           [(d, tag, name) for d, name in leaders.items()])

    def leaders(self):
        """{(date, tag): name} for every date someone is down for."""
        with self._db() as db:
            return {(r["date"], r["tag"]): r["name"] for r in db.execute("SELECT date, tag, name FROM leaders")}

    def leader_on(self, date, tag=""):
        with self._db() as db:
            row = db.execute("SELECT name FROM leaders WHERE date = ? AND tag = ?", (date, tag)).fetchone()
        return row["name"] if row else None

    # ------------------------------------------------------------ share links

    CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I to misread

    def share(self, setlist, now, keep):
        """The code of a share link for `setlist` (songs, layout, columns, paper, and a name if it
        has one): the existing one if these songs were shared already, else a new one. A setlist
        with no name leaves it out, so its link is the one it had before links had names."""
        setlist = {k: v for k, v in setlist.items() if k != "name" or v}
        digest = hashlib.sha256(json.dumps(setlist, sort_keys=True).encode()).hexdigest()
        stamp = now.isoformat(timespec="seconds")
        with self._lock, self._db() as db:
            db.execute("DELETE FROM shares WHERE last_opened < ?", ((now - keep).isoformat(timespec="seconds"),))
            row = db.execute("SELECT code FROM shares WHERE digest = ?", (digest,)).fetchone()
            if row:
                db.execute("UPDATE shares SET last_opened = ? WHERE code = ?", (stamp, row["code"]))
                return row["code"]
            while True:
                code = "".join(secrets.choice(self.CODE_LETTERS) for _ in range(6))
                if not db.execute("SELECT 1 FROM shares WHERE code = ?", (code,)).fetchone():
                    break
            db.execute("INSERT INTO shares (code, digest, songs, layout, columns, paper, last_opened, name)"
                       " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (code, digest, json.dumps(setlist["songs"]), setlist["layout"], setlist["columns"],
                        setlist["paper"], stamp, setlist.get("name")))
            return code

    def open_share(self, code, now, keep):
        """A share link's snapshot, counting as opened now; None if unknown or expired."""
        stamp = now.isoformat(timespec="seconds")
        with self._lock, self._db() as db:
            db.execute("DELETE FROM shares WHERE last_opened < ?", ((now - keep).isoformat(timespec="seconds"),))
            row = db.execute("SELECT * FROM shares WHERE code = ?", (code.upper(),)).fetchone()
            if not row:
                return None
            db.execute("UPDATE shares SET last_opened = ? WHERE code = ?", (stamp, row["code"]))
        return self._row(row)

    # ------------------------------------------------------------ capos, by person
    # `person` is a name as typed; names match whatever their case and spacing.
    # Capos come and go as {set_id (str): {slug: {"shape", "fret"}}}.

    @staticmethod
    def person_key(name):
        return " ".join(name.split()).casefold()

    def capos(self, name):
        out = {}
        with self._db() as db:
            for r in db.execute("SELECT set_id, slug, shape, fret FROM capos WHERE person = ?", (self.person_key(name),)):
                out.setdefault(str(r["set_id"]), {})[r["slug"]] = {"shape": r["shape"], "fret": r["fret"]}
        return out

    def set_capo(self, name, set_id, slug, capo):
        """Save one capo, or clear it with capo=None."""
        with self._lock, self._db() as db:
            if capo:
                db.execute("INSERT OR REPLACE INTO capos (person, set_id, slug, shape, fret) VALUES (?, ?, ?, ?, ?)",
                           (self.person_key(name), set_id, slug, capo["shape"], capo["fret"]))
            else:
                db.execute("DELETE FROM capos WHERE person = ? AND set_id = ? AND slug = ?",
                           (self.person_key(name), set_id, slug))

    # ------------------------------------------------------------ a draft, by person

    def draft(self, name, since):
        """(draft, saved_at) under this name, if saved at or after `since`; else (None, None)."""
        with self._db() as db:
            row = db.execute("SELECT draft, saved_at FROM drafts WHERE person = ?", (self.person_key(name),)).fetchone()
        if not row or row["saved_at"] < since.isoformat(timespec="seconds"):
            return None, None
        return json.loads(row["draft"]), row["saved_at"]

    def save_draft(self, name, draft, now):
        """Keep `draft` as this name's one draft (None clears it). Old ones go meanwhile."""
        with self._lock, self._db() as db:
            if draft is None:
                db.execute("DELETE FROM drafts WHERE person = ?", (self.person_key(name),))
            else:
                db.execute("INSERT OR REPLACE INTO drafts (person, draft, saved_at) VALUES (?, ?, ?)",
                           (self.person_key(name), json.dumps(draft, ensure_ascii=False), now.isoformat(timespec="seconds")))

    def add_capos(self, name, capos):
        """Add a device's capos to a name's; where both have one for a song, the name's wins."""
        with self._lock, self._db() as db:
            db.executemany("INSERT OR IGNORE INTO capos (person, set_id, slug, shape, fret) VALUES (?, ?, ?, ?, ?)",
                           [(self.person_key(name), int(set_id), slug, c["shape"], c["fret"])
                            for set_id, songs in capos.items() for slug, c in songs.items()])
        return self.capos(name)
