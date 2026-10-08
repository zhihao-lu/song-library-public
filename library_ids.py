"""Arrangement IDs, kept in the sets database: the one thing about the library Drive can't rebuild.

An arrangement's ID (called slug in the API) is tied to the Drive files of its charts, so
renaming a file or correcting a title never changes it. Also kept, per ID: its last title
(an ID with no charts left is shown as "<title> (removed from library)"), the song and
source it belongs to and its number (v1, v2… never change once given), and aliases: IDs of
arrangements that were folded into another one, which every endpoint resolves.
"""

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS library_ids (
    slug     TEXT PRIMARY KEY,
    title    TEXT NOT NULL,
    song     TEXT NOT NULL,             -- the song's loose title key (import_drive.title_key)
    source   TEXT NOT NULL,             -- the source it was first seen in
    number   INTEGER NOT NULL,          -- its number within that source and song: v1, v2…
    removed  INTEGER NOT NULL DEFAULT 0 -- no charts left in Drive
);
-- Which ID each Drive file's chart belongs to, and what the file's contents were.
CREATE TABLE IF NOT EXISTS library_files (
    file_id  TEXT PRIMARY KEY,
    slug     TEXT NOT NULL,
    md5      TEXT
);
CREATE TABLE IF NOT EXISTS library_aliases (
    alias    TEXT PRIMARY KEY,
    slug     TEXT NOT NULL
);
"""


class IdStore:
    """`path` is the sets database, or ":memory:" (with `copy_of`, a copy of one) for a dry run."""

    def __init__(self, path, copy_of=None):
        self._memory = str(path) == ":memory:"
        self._lock = threading.Lock()
        if self._memory:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            if copy_of and Path(copy_of).exists():
                src = sqlite3.connect(copy_of)
                src.backup(self._conn)
                src.close()
        else:
            self.path = Path(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self):
        db = self._conn if self._memory else sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            if not self._memory:
                db.close()

    def load(self):
        """Everything, as {"ids": {slug: row}, "files": {file_id: (slug, md5)}, "aliases": {alias: slug}}."""
        with self._lock, self._db() as db:
            return {
                "ids": {r["slug"]: dict(r) for r in db.execute("SELECT * FROM library_ids")},
                "files": {r["file_id"]: (r["slug"], r["md5"]) for r in db.execute("SELECT * FROM library_files")},
                "aliases": {r["alias"]: r["slug"] for r in db.execute("SELECT * FROM library_aliases")},
            }

    def save(self, state):
        """Replace everything with `state` (as load() returns it), in one go."""
        with self._lock, self._db() as db:
            db.execute("DELETE FROM library_ids")
            db.execute("DELETE FROM library_files")
            db.execute("DELETE FROM library_aliases")
            db.executemany("INSERT INTO library_ids (slug, title, song, source, number, removed)"
                           " VALUES (:slug, :title, :song, :source, :number, :removed)", state["ids"].values())
            db.executemany("INSERT INTO library_files (file_id, slug, md5) VALUES (?, ?, ?)",
                           [(f, s, m) for f, (s, m) in state["files"].items()])
            db.executemany("INSERT INTO library_aliases (alias, slug) VALUES (?, ?)", state["aliases"].items())
