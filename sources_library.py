"""The song library from three Drive sources (YFM, MM, ASE: their display order), with arrangements.

Drive is the only way songs get in (docs/adr/0001). Each check lists every source, folders
and subfolders, and keeps a local copy of each chart keyed by Drive file ID and checksum:
only new and changed files are downloaded (at the gentle pace drive_mirror uses) and each is
analysed once (charts.py). Files gone from Drive are dropped. The library is then worked out
afresh from the charts (catalog.py), with arrangement IDs kept in the sets database.

Also read, from the '.cho Files' folder: hand-fixed <id>.cho files, which win their
arrangement, and the corrections Sheet. The flat PDF folder is only listed, so today's songs
keep their slugs.

While Google refuses downloads, what's here keeps being served and the next check carries on.
Checks run every SOURCES_SYNC_MINUTES (default 60); Sync now forces one.

Config (environment variables):
  LIBRARY_MODE=sources   serve this library (library.library_from_env)
  SOURCES_CACHE_DIR      its local copy (default data/sources-library)
  GOOGLE_API_KEY         as for the mirror
"""

import csv
import io
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import chordtools as ct
import doc2pdf
from catalog import Corrections, build
from charts import ANALYSIS_VERSION, MUSIC_VERSION, analyse, better_title, file_path, music_fingerprint
from drive import DEFAULT_FOLDER_URL, DEFAULT_INCLUDE, FOLDER, PDF, SHORTCUT, DriveError, RateLimited, parse_folder_url
from drive_mirror import DEFAULT_CHO_URL, DEFAULT_PDF_URL, GSHEET, paced_client
from library import in_chart_mode, matches, opening_line, parse_song, public, search_fields
from library_ids import IdStore

CORRECTIONS_SHEET = "_corrections"


@dataclass(frozen=True)
class Source:
    """A Drive folder the site takes charts from, subfolders included.

    include    which top-level folders to take (a regex); None: everything
    leave_out  files and folders to leave out (a regex on their names)
    only_new   folders whose charts only count for a song nobody else has, and then only as its
               one arrangement (a regex on the file's folders): a music book of others' charts
    """
    name: str
    url: str
    include: str = None
    leave_out: str = None
    only_new: str = None


SOURCES = (
    Source("YFM", DEFAULT_FOLDER_URL, include=DEFAULT_INCLUDE, only_new=r"^7\. Hillsong Digital Music Book(?:/|$)"),
    Source("MM", "https://drive.google.com/drive/folders/YOUR-MM-FOLDER-ID",
           include=r"^(?:01 Chinese Song Scores|02 English Song Scores|03 Hymns Scores|04 赞美之泉 SongBooks)$"),
    Source("ASE", "https://drive.google.com/drive/folders/YOUR-ASE-FOLDER-ID",
           leave_out=r"^Song_Book\+March\+2014\.pdf$"),
)


def _is_chart_file(f):
    return f["mimeType"] == PDF or f["name"].lower().endswith(".pdf") or doc2pdf.is_document(f["name"], f["mimeType"])


def _resolve(f):
    """A shortcut as the file it points to."""
    if f["mimeType"] == SHORTCUT and f.get("shortcutDetails"):
        d = f["shortcutDetails"]
        return {**f, "id": d["targetId"], "mimeType": d.get("targetMimeType", ""), "resourceKey": d.get("targetResourceKey")}
    return f


def version(f):
    return f.get("md5Checksum") or f.get("modifiedTime")


class SourcesLibrary:
    def __init__(self, folder, client, ids, sources=SOURCES, cho_url=DEFAULT_CHO_URL, flat_url=DEFAULT_PDF_URL,
                 clock=time.time, sync_minutes=None, background=True, adopt_from=()):
        """`client` reads Drive (tests pass a fake one); `ids` is the IdStore; `clock` is the
        time now, in seconds (tests move it); without `background` a due check runs in the
        request that noticed it. `adopt_from` are other local copies (another sources library's,
        or the mirror's) whose files are copied instead of downloaded again."""
        self.folder = Path(folder)
        (self.folder / "files").mkdir(parents=True, exist_ok=True)
        (self.folder / "fixes").mkdir(exist_ok=True)
        self.client, self.ids, self.sources = client, ids, tuple(sources)
        self.cho_folder = parse_folder_url(cho_url) if cho_url else None
        self.flat_folder = parse_folder_url(flat_url) if flat_url else None
        self.clock, self.background = clock, background
        self.adopt_from = [Path(p) for p in adopt_from]
        self._adoptable = None
        self.interval = 60 * float(sync_minutes or os.environ.get("SOURCES_SYNC_MINUTES") or 60)
        self.synced_at = 0.0
        self.last_result = None
        self.first_sync = threading.Event()
        self._manifest_path = self.folder / "intake.json"
        if not self._manifest_path.exists():
            self._take_over_a_copy()
        try:
            self._manifest = json.loads(self._manifest_path.read_text())
        except Exception:
            self._manifest = {"files": {}, "fixes": {}, "corrections": None, "flat": {}}
        self._syncing = threading.Lock()
        self._lock = threading.Lock()
        self._songs, self._aliases, self._removed = {}, {}, {}
        self.skipped = []  # [(file, reason)] from the last check
        if self._manifest["files"]:
            # Serve what's here straight away; the first check (minutes of listing) runs meanwhile.
            self._rebuild()
            self.first_sync.set()

    def _take_over_a_copy(self):
        """A first start: begin from another sources library's copy (the test run's), if there's one."""
        import shutil
        for other in self.adopt_from:
            if (other / "intake.json").exists():
                for part in ("files", "fixes"):
                    if (other / part).is_dir():
                        shutil.copytree(other / part, self.folder / part, dirs_exist_ok=True)
                if (other / "corrections.csv").exists():
                    shutil.copyfile(other / "corrections.csv", self.folder / "corrections.csv")
                shutil.copyfile(other / "intake.json", self._manifest_path)
                print(f"Sources library: starting from the copy in {other}", flush=True)
                return

    @classmethod
    def from_env(cls, folder=None, ids=None):
        from sets_api import DEFAULT_DB
        data = Path(__file__).parent / "data"
        folder = Path(folder or os.environ.get("SOURCES_CACHE_DIR") or data / "sources-library")
        others = [data / "test-run", data / "sources-library", Path(os.environ.get("DRIVE_CACHE_DIR") or data / "drive-library")]
        return cls(folder, paced_client(), ids or IdStore(os.environ.get("SETS_DB") or DEFAULT_DB),
                   cho_url=os.environ.get("DRIVE_CHO_FOLDER_URL") or DEFAULT_CHO_URL,
                   flat_url=os.environ.get("DRIVE_PDF_FOLDER_URL") or DEFAULT_PDF_URL,
                   adopt_from=[p for p in others if p.resolve() != folder.resolve()])

    # ------------------------------------------------------------------ checks
    def refresh(self):
        """Check the sources if the last check is older than the interval."""
        if self.clock() - self.synced_at >= self.interval and not self._syncing.locked():
            if self.background:
                threading.Thread(target=self.sync, daemon=True).start()
            else:
                self.sync()

    def wait_ready(self, timeout=120):
        self.refresh()
        self.first_sync.wait(timeout)

    def sync(self, force=False):
        """List the sources, bring the local copy up to date and work the library out again."""
        if not self._syncing.acquire(blocking=force):
            return self.last_result
        started = time.time()
        result = {"updated": [], "removed": [], "failed": [], "new_songs": [], "error": None}
        before = {s["slug"] for s in self._songs.values()}
        try:
            listing = self._list_sources()
            extras = self._list_extras()
            self._take_in(listing, extras, result)
        except (DriveError, RateLimited) as e:  # keep serving the last good copy
            result["error"] = str(e)
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
            raise
        finally:
            try:
                self._rebuild()
            finally:
                self.synced_at = self.clock()
                result["new_songs"] = sorted(s["title"] for s in self._songs.values() if s["slug"] not in before) \
                    if before else []
                self.last_result = {**result, "at": self.synced_at}
                self.first_sync.set()
                self._syncing.release()
        print(f"Sources library: {len(result['updated'])} updated, {len(result['removed'])} removed, "
              f"{len(result['failed'])} skipped in {time.time() - started:.1f}s"
              + (f"; check failed: {result['error']}" if result["error"] else ""), flush=True)
        return self.last_result

    def _walk(self, source):
        """Every chart file in a source: [{Drive file..., "source", "path"}], path = its folders inside it.

        Folders are listed several at a time, a level at a time: one by one, the ~450 of them took
        minutes, and listing isn't what Google blocks."""
        root_id, root_rk = parse_folder_url(source.url)
        include = re.compile(source.include, re.I) if source.include else None
        leave_out = re.compile(source.leave_out, re.I) if source.leave_out else None
        found, seen = [], set()
        queue = [(root_id, root_rk, [])]
        with ThreadPoolExecutor(max_workers=8) as pool:
            while queue:
                level, queue = queue, []
                listings = pool.map(lambda q: [_resolve(f) for f in self.client.children(q[0], q[1])], level)
                for (_, _, path), children in zip(level, listings):
                    files = []
                    for f in children:
                        if f["id"] in seen or leave_out and leave_out.search(f["name"]):
                            continue
                        seen.add(f["id"])
                        if f["mimeType"] == FOLDER:
                            if path or not include or include.search(f["name"]):
                                queue.append((f["id"], f.get("resourceKey"), path + [f["name"]]))
                        elif (path or not include) and _is_chart_file(f):
                            files.append(f)
                    # A real PDF beats a Word file or Google Doc of the same name next to it.
                    pdfs = {f["name"] for f in files if not doc2pdf.is_document(f["name"], f["mimeType"])}
                    for f in files:
                        if doc2pdf.is_document(f["name"], f["mimeType"]) and doc2pdf.pdf_name(f["name"]) in pdfs:
                            continue
                        found.append({**f, "source": source.name, "path": "/".join(path)})
        return found

    def _list_sources(self):
        return [f for source in self.sources for f in self._walk(source)]

    def _list_extras(self):
        """The '.cho Files' folder (hand-fixed charts, corrections) and the flat PDF folder's names."""
        cho = self.client.children(*self.cho_folder) if self.cho_folder else []
        flat = self.client.children(*self.flat_folder) if self.flat_folder else []
        return cho, flat

    def _take_in(self, listing, extras, result):
        files = self._manifest["files"]
        live = {f["id"]: f for f in listing}
        for file_id in [i for i in files if i not in live]:  # gone from Drive
            result["removed"].append(files[file_id]["name"])
            self._drop(file_id)
        failed = self._manifest.setdefault("failed", {})
        for file_id in [i for i in failed if i not in live]:
            del failed[file_id]
        for f in live.values():  # renamed or moved: no download needed
            if f["id"] in files and files[f["id"]]["version"] == version(f):
                files[f["id"]].update(name=f["name"], path=f["path"], source=f["source"], modified=f.get("modifiedTime", ""))
        todo = [f for f in live.values() if f["id"] not in files or files[f["id"]]["version"] != version(f)
                or files[f["id"]]["record"].get("version") != ANALYSIS_VERSION]
        self._blocked = False
        try:
            self._take_in_extras(*extras, result)
            for i, f in enumerate(todo, 1):
                self._take_in_file(f, result)
                if i % 25 == 0:
                    self._save()  # a restart during a long first intake keeps what's done
                if i % 100 == 0:
                    print(f"Sources library: took in {i} of {len(todo)} files", flush=True)
        finally:
            self._save()
        if self._blocked:
            raise RateLimited("Google is rate-limiting downloads; the files that need one are left for the next check.")

    def _take_in_file(self, f, result):
        """Take in one chart (from a local copy if there's one, else downloaded) and analyse it.
        Once Google refuses a download, files that need one are left for the next check."""
        path = self.folder / "files" / f"{f['id']}.pdf"
        entry = self._manifest["files"].get(f["id"])
        record, current = None, bool(entry and entry["version"] == version(f) and path.exists())
        if not current:
            current, record = self._adopt(f, path)
        if not current:
            try:
                word = doc2pdf.is_word(f["name"]) and self._local_copy(f)
                if word:  # a Word file downloaded by hand: converted here
                    data = doc2pdf.word_to_pdf(word.read_bytes(), f["name"])
                elif self._blocked:
                    return
                else:
                    data = doc2pdf.fetch_pdf(self.client, f["id"], f["name"], f["mimeType"], f.get("resourceKey"))
            except RateLimited:
                self._blocked = True
                return
            except (DriveError, doc2pdf.ConversionError) as e:
                return self._failed(f, f"couldn't download it ({e})", result)
            if not data.startswith(b"%PDF"):
                return self._failed(f, "not a PDF", result)
            tmp = path.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(path)
        item = {"id": f["id"], "name": f["name"], "path": f["path"], "mime": f["mimeType"]}
        self._manifest.setdefault("failed", {}).pop(f["id"], None)
        if not record or record.get("version") != ANALYSIS_VERSION:
            record = analyse(path, item)
        if record.get("skipped"):
            result["failed"].append(f"{f['name']}: {record['skipped']}")
        else:
            result["updated"].append(f["name"])
        self._manifest["files"][f["id"]] = {
            "version": version(f), "name": f["name"], "path": f["path"], "source": f["source"],
            "mime": f["mimeType"], "modified": f.get("modifiedTime", ""), "md5": f.get("md5Checksum"),
            "rk": f.get("resourceKey"), "document": doc2pdf.is_document(f["name"], f["mimeType"]), "record": record}

    def _failed(self, f, reason, result):
        """A file that couldn't be taken in at all: said on Sync now, and kept for the test run's report."""
        result["failed"].append(f"{f['name']}: {reason}")
        self._manifest.setdefault("failed", {})[f["id"]] = {
            "name": f["name"], "path": f["path"], "source": f["source"], "rk": f.get("resourceKey"), "reason": reason}

    def _adopt(self, f, path, index_only=False):
        """Copy `f` from another local copy instead of downloading it. Returns (copied, its
        analysis if that copy had one for this version)."""
        import shutil
        if self._adoptable is None:
            self._adoptable = {"ids": {}, "md5s": {}}
            for other in self.adopt_from:
                try:
                    manifest = json.loads((other / "intake.json").read_text())
                    for file_id, e in manifest["files"].items():
                        self._adoptable["ids"].setdefault(file_id, (other / "files" / f"{file_id}.pdf", e))
                except Exception:
                    pass
                try:  # the mirror's copy: file name -> MD5
                    for name, md5 in json.loads((other / "_drive.json").read_text()).items():
                        self._adoptable["md5s"].setdefault(md5, other / name)
                except Exception:
                    pass
                if other.is_dir() and not (other / "intake.json").exists() and not (other / "_drive.json").exists():
                    # Files downloaded by hand from Drive's web page (unzipped, in any folders).
                    from drive_mirror import md5 as md5_of
                    for found in other.rglob("*"):
                        if found.is_file() and not found.name.startswith("."):
                            self._adoptable["md5s"].setdefault(md5_of(found), found)
        if index_only:
            return False, None
        found, entry = self._adoptable["ids"].get(f["id"], (None, None))
        if found and entry["version"] == version(f) and found.exists():
            shutil.copyfile(found, path)
            return True, entry["record"]
        return (not doc2pdf.is_document(f["name"], f["mimeType"]) and self._adopt_by_md5(f, path)), None

    def _adopt_by_md5(self, f, path):
        """Copy a file with the same contents from another local copy. True if it did."""
        import shutil
        found = self._local_copy(f)
        if found:
            shutil.copyfile(found, path)
        return bool(found)

    def _local_copy(self, f):
        """A local file with the same contents as Drive's `f` (the mirror's copy, or downloaded by hand)."""
        from drive_mirror import md5
        if self._adoptable is None:
            self._adopt(f, None, index_only=True)
        found = self._adoptable["md5s"].get(f.get("md5Checksum"))
        return found if found and found.exists() and md5(found) == f["md5Checksum"] else None

    def _take_in_extras(self, cho_listing, flat_listing, result):
        """Hand-fixed .cho files and the corrections Sheet (downloaded), and the flat folder (listed)."""
        fixes = self._manifest["fixes"]
        wanted = {f["name"]: f for f in cho_listing if f["name"].endswith(".cho") and f["mimeType"] != FOLDER}
        for name in [n for n in fixes if n not in wanted]:
            (self.folder / "fixes" / name).unlink(missing_ok=True)
            del fixes[name]
        for name, f in wanted.items():
            if fixes.get(name) != version(f):
                if not self._adopt_by_md5(f, self.folder / "fixes" / name):
                    (self.folder / "fixes" / name).write_bytes(self.client.download(f["id"], f.get("resourceKey")))
                fixes[name] = version(f)
                result["updated"].append(name)
        sheet = next((f for f in cho_listing if f["mimeType"] == GSHEET and f["name"] == CORRECTIONS_SHEET), None)
        path = self.folder / "corrections.csv"
        if not sheet:
            path.unlink(missing_ok=True)
            self._manifest["corrections"] = None
        elif self._manifest["corrections"] != version(sheet):
            path.write_bytes(self.client.export(sheet["id"], "text/csv", sheet.get("resourceKey")))
            self._manifest["corrections"] = version(sheet)
        self._manifest["flat"] = {f["name"]: {"id": f["id"], "md5": f.get("md5Checksum"), "rk": f.get("resourceKey")}
                                  for f in flat_listing if f["mimeType"] != FOLDER}

    def _drop(self, file_id):
        (self.folder / "files" / f"{file_id}.pdf").unlink(missing_ok=True)
        self._manifest["files"].pop(file_id, None)

    def _save(self):
        tmp = self._manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._manifest, ensure_ascii=False))
        tmp.replace(self._manifest_path)

    # ------------------------------------------------------------------ the library
    def _inputs(self):
        """What catalog.build needs, from the local copy."""
        for e in self._manifest["files"].values():  # older analyses: same music is now worked out this way
            r = e["record"]
            if r.get("cho") and not r.get("as_is") and (r.get("music") or {}).get("version") != MUSIC_VERSION:
                r["music"] = music_fingerprint(r["cho"])
        only_new = {s.name: re.compile(s.only_new) for s in self.sources if s.only_new}
        charts = [{"id": i, **{k: e[k] for k in ("source", "path", "name", "document", "modified", "md5")},
                   **e["record"], "title": better_title(e["record"]["title"], e["path"], e["name"]),
                   "only_new": bool(e["source"] in only_new and only_new[e["source"]].search(e["path"]))}
                  for i, e in self._manifest["files"].items() if not e["record"].get("skipped")]
        fixes = {}
        for name in self._manifest["fixes"]:
            path = self.folder / "fixes" / name
            if path.exists():
                fixes[name[:-4]] = self._fix(path)
        path = self.folder / "corrections.csv"
        rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig")))) if path.exists() else []
        legacy = {"paths": {}, "md5s": {}, "file_ids": {}}
        for slug, fix in fixes.items():
            if fix.get("source_file"):
                legacy["paths"][file_path({"path": fix.get("source_folder"), "name": fix["source_file"]})] = slug
        for name, f in self._manifest["flat"].items():
            slug = re.sub(r"(?:@[^@]*)?\.(?:pdf|docx?)$", "", doc2pdf.pdf_name(name) if doc2pdf.is_word(name) else name,
                          flags=re.I)
            if slug and slug != name:
                legacy["file_ids"][f["id"]] = slug
                if f.get("md5"):
                    legacy["md5s"][f["md5"]] = slug
        return charts, fixes, Corrections(_sheet_rows(rows)), legacy

    @staticmethod
    def _fix(path):
        song = parse_song(path)
        return {"slug": path.stem, "cho": path.read_text(encoding="utf-8"), "title": song["title"],
                "artist": song["artist"], "key": song["key"], "as_is": not song["transposable"],
                "lyrics_ok": song["lyrics_ok"], "pdf_scan": song["pdf_scan"], "pdf_pages": song["pdf_pages"],
                "original_key": song["original_key"], "source_folder": song["folder"],
                "source_file": song["source_file"]}

    def _rebuild(self):
        charts, fixes, corrections, legacy = self._inputs()
        arrangements, state = build(charts, [s.name for s in self.sources], self.ids.load(), fixes, corrections, legacy)
        self.ids.save(state)
        self.skipped = [(e, e["record"]["skipped"]) for e in self._manifest["files"].values() if e["record"].get("skipped")]
        songs = {a["slug"]: self._song(a) for a in arrangements}
        with self._lock:
            self._songs, self._aliases = songs, state["aliases"]
            self._removed = {s: r["title"] for s, r in state["ids"].items() if r["removed"]}
            self.arrangements = arrangements

    def _song(self, a):
        """An arrangement as the site's pages see a song (library.parse_song's fields, and more)."""
        best = a["best"]
        key = best.get("key")
        transposable = not best["as_is"] and bool(ct.parse_key(key or ""))
        chart_keys = sorted(a["per_key"], key=lambda k: ct.parse_key(k)[0] if ct.parse_key(k) else 99)
        chart = ct.parse_key(key) if key else None
        names = " ".join(f"{c.get('path', '')} {c.get('name', '')}" for c in a["charts"])
        return {
            "slug": a["slug"], "song_id": a["song_id"], "title": a["title"], "artist": a["artist"], "key": key,
            "folder": best.get("path", ""), "source_file": best.get("name", ""),
            "pdf_pages": best.get("pdf_pages"), "lyrics_ok": best.get("lyrics_ok", True),
            "pdf_scan": best.get("pdf_scan", False), "transposable": transposable,
            "keys": chart_keys if not transposable else [], "chart_keys": chart_keys,
            "original_key": in_chart_mode(a["original_key"], chart), "has_pdf": bool(self._pdf_id(best)),
            "auto": not best.get("hand_fixed"), "sources": a["sources"], "number": a["number"],
            "label": a["label"], "tag": a["tag"], "first_line": opening_line(best.get("cho") or ""),
            "_arrangement": a,
            **search_fields(a["title"], " ".join([a["artist"], re.sub(r"\.(?:pdf|docx?)\b", " ", names, flags=re.I)])),
        }

    @staticmethod
    def _pdf_id(chart):
        if chart.get("hand_fixed"):
            return chart.get("pdf_id")
        return None if chart.get("fix_only") else chart.get("id")

    def songs(self):
        self.wait_ready()
        return self._songs

    def resolve(self, slug):
        return self._aliases.get(slug, slug)

    def get(self, slug):
        return self.songs().get(self.resolve(slug))

    def last_title(self, slug):
        return self._removed.get(self.resolve(slug))

    def count(self):
        return len(self.songs())

    def chart(self, slug, key=None):
        song = self.get(slug)
        if not song:
            return None
        a = song["_arrangement"]
        c = a["per_key"].get(key) if key is not None else a["best"]
        if c is None:
            return None
        pdf_id = self._pdf_id(c)
        path = self.folder / "files" / f"{pdf_id}.pdf" if pdf_id else None
        return {"key": c.get("key"), "cho_key": c.get("key"),
                "transposable": not c["as_is"] and bool(ct.parse_key(c.get("key") or "")),
                "lyrics_ok": c.get("lyrics_ok", True), "pdf_scan": c.get("pdf_scan", False),
                "pdf_pages": c.get("pdf_pages"), "has_pdf": bool(pdf_id),
                "pdf": lambda: path if path and path.exists() else None,
                "cho": lambda: c["cho"]}

    def search(self, query, limit=40, sources=None):
        """Songs matching `query`, one result per arrangement; a song's arrangements together, in
        source order. With `sources`, only arrangements tagged with one of them."""
        order = {s.name: i for i, s in enumerate(self.sources)}
        found = matches(self.songs().values(), query)
        if sources:
            found = [s for s in found if set(s["sources"]) & set(sources)]
        first = {}
        for i, s in enumerate(found):
            first.setdefault(s["song_id"], i)
        found.sort(key=lambda s: (first[s["song_id"]], order[s["sources"][0]], s["number"]))
        return [public(s) for s in found[:limit]]

    def status(self):
        counts = {s.name: len({x["song_id"] for x in self._songs.values() if s.name in x["sources"]})
                  for s in self.sources}
        songs = self.songs()
        return {"songs": len({x["song_id"] for x in songs.values()}), "arrangements": len(songs), "drive": True,
                "synced_at": self.synced_at or None, "mode": "sources",  # None: not checked since starting
                "sync_minutes": self.interval / 60,
                "sources": [{"name": s.name, "songs": counts[s.name]} for s in self.sources]}


def _sheet_rows(rows):
    """Corrections Sheet rows, with the column names people are likely to type."""
    names = {"slug": "id", "song": "id", "file": "id", "path": "id", "original key": "original_key",
             "same as": "same_as", "different song": "different_song", "leave out": "leave_out",
             "exclude": "leave_out", "arrangement label": "label"}
    out = []
    for r in rows:
        out.append({names.get(k.strip().lower(), k.strip().lower()): v for k, v in r.items() if k})
    return out
