"""A local copy of the song library, kept in step with two Google Drive folders.

The library lives in Drive:
  cho folder   <slug>.cho files, plus _original_keys.csv and _exclude.txt
  PDF folder   the original PDFs: <slug>.pdf, and <slug>@<Key>.pdf for as-is songs.
               A Word file or Google Doc counts as the PDF of the same name
               (<slug>.docx is <slug>.pdf), converted by doc2pdf.py; a real PDF
               of that name wins.

A new song can be just its PDF (or document): a sync converts any PDF that has
no <slug>.cho to ChordPro, the same way the Drive import does, and keeps the
result in auto/<slug>.cho here. The API key can't write to Drive, so it stays
local; uploading a real <slug>.cho to the cho folder replaces it.

A sync lists both folders, downloads the cho folder's new and changed files
(search needs all of them), and deletes local files that are gone from Drive.
PDFs are then downloaded in the background, so building a setlist doesn't wait
on Drive. SongLibrary reads the local copy as an ordinary folder.

Files are compared by Drive's MD5 checksum, so a copy that's already here (a
restored cache, or the old library/ folder copied in) is kept, not downloaded.

Downloads go at a gentle pace. Google answers a burst of downloads from one IP
with a "Sorry... automated queries" page for a while; when that happens the
sync stops and the next one carries on. A PDF a setlist needs but that isn't
here yet is fetched on the spot; if Google refuses, the song is typeset from
its .cho instead.

Drive may turn an uploaded _original_keys.csv into a Google Sheet; that's
exported back to CSV, so the team can edit the keys as a Sheet.

The site's "Sync now" button runs a sync straight away, for when someone has
just dropped a file in.

Config (environment variables):
  GOOGLE_API_KEY         required; the folders must be shared "anyone with the link"
  DRIVE_CHO_FOLDER_URL   link to the cho folder (default: YFM's "zz. Automation/.cho Files")
  DRIVE_PDF_FOLDER_URL   link to the PDF folder (default: YFM's PDF folder)
  DRIVE_SYNC_MINUTES     how often to look for changes (default 5)
"""

import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import doc2pdf
from drive import FOLDER, DriveClient, DriveError, RateLimited, parse_folder_url

GSHEET = "application/vnd.google-apps.spreadsheet"
GOOGLE_APPS = "application/vnd.google-apps."
MANIFEST = "_drive.json"  # name -> version (MD5, or modifiedTime for a Sheet) of each local file
# Bump when pdf2chordpro improves, so songs converted here are converted again.
CONVERTER_VERSION = 2
# Set DRIVE_CHO_FOLDER_URL and DRIVE_PDF_FOLDER_URL in .env to your own folders (see .env.example).
DEFAULT_CHO_URL = "https://drive.google.com/drive/folders/YOUR-CHO-FOLDER-ID"
DEFAULT_PDF_URL = "https://drive.google.com/drive/folders/YOUR-PDF-FOLDER-ID"


def version(f):
    return f.get("md5Checksum") or f.get("modifiedTime")


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


PACE = 0.3  # seconds between downloads: fast enough, and well clear of Google's burst block


def paced_client(api_key=None):
    """A Drive client for keeping a local copy: downloads at a gentle pace, and when Google
    refuses them it gives up at once (RateLimited), so the next sync carries on."""
    client = DriveClient(api_key)
    client.MIN_INTERVAL = PACE
    client.LIST_INTERVAL = 0.05  # listing folders isn't what Google blocks; downloads are
    client.BACKOFF = ()
    if not client.api_key:
        raise DriveError("GOOGLE_API_KEY is not set, so the Drive library can't be read.")
    return client


class DriveMirror:

    def __init__(self, folder, cho_url, pdf_url=None, api_key=None, sync_minutes=None, client=None, background=True):
        """`client` is what reads Drive (default: a paced DriveClient; tests pass a fake one).
        Without `background`, PDFs are downloaded during the sync instead of after it."""
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.client = client or paced_client(api_key)
        self.background = background
        self.cho_folder = parse_folder_url(cho_url)
        self.pdf_folder = parse_folder_url(pdf_url) if pdf_url else None
        self.interval = 60 * float(sync_minutes or os.environ.get("DRIVE_SYNC_MINUTES") or 5)
        self.pdfs = {}          # name -> Drive file, from the last listing
        self.pdf_version = 0    # bumped whenever the set of PDFs changes
        self.synced_at = 0.0
        self.last_result = None  # what the last sync did, for the site to show
        self.first_sync = threading.Event()
        self._manifest_path = self.folder / MANIFEST
        self.auto_dir = self.folder / "auto"   # .cho files converted here from PDFs with none in Drive
        try:
            self._manifest = json.loads(self._manifest_path.read_text())
        except Exception:
            self._manifest = {}
        self._lock = threading.Lock()       # manifest and file writes
        self._syncing = threading.Lock()
        self._pdf_download = threading.Lock()
        self._pdf_locks = {}

    @classmethod
    def from_env(cls, folder):
        return cls(folder, os.environ.get("DRIVE_CHO_FOLDER_URL") or DEFAULT_CHO_URL,
                   os.environ.get("DRIVE_PDF_FOLDER_URL") or DEFAULT_PDF_URL)

    # ------------------------------------------------------------------ sync
    def refresh(self):
        """Start a background sync if the last one is older than the interval."""
        if time.time() - self.synced_at >= self.interval and not self._syncing.locked():
            threading.Thread(target=self.sync, daemon=True).start()

    def wait_ready(self, timeout=120):
        """Block until the first sync has finished (or failed), so a cold start isn't empty."""
        self.refresh()
        self.first_sync.wait(timeout)

    def sync(self, force=False):
        """Bring the .cho files up to date and list the PDFs; returns what changed.

        A forced sync (the site's button) waits out one already running, then runs anyway,
        so a file dropped in during that sync is still picked up.
        """
        if not self._syncing.acquire(blocking=force):
            return self.last_result
        started = time.time()
        result = {"updated": [], "removed": [], "failed": [], "new_songs": [], "error": None}
        try:
            self._sync_cho(result)
            if self.pdf_folder:
                self._list_pdfs(result)
                self._convert_new_pdfs(result)
        except Exception as e:  # keep serving the last good copy
            result["error"] = str(e)
        finally:
            self.synced_at = time.time()
            self.last_result = {**result, "at": self.synced_at}
            self.first_sync.set()
            self._syncing.release()
        print(f"Drive library: {len(result['updated'])} updated, {len(result['removed'])} removed, "
              f"{len(result['new_songs'])} converted, {len(result['failed'])} failed in {time.time() - started:.1f}s"
              + (f"; sync failed: {result['error']}" if result["error"] else ""), flush=True)
        if self.pdf_folder and not result["error"]:
            if self.background:
                threading.Thread(target=self._download_pdfs, daemon=True).start()
            else:
                self._download_pdfs()
        return self.last_result

    def _listing(self, folder, documents=False):
        """{name: file} for the files directly in a folder (the newest wins if names repeat).

        With `documents`, Word files and Google Docs are listed under the PDF name they stand for.
        """
        files = {}
        for f in self.client.children(*folder):
            if f["mimeType"] == FOLDER or "/" in f["name"]:
                continue
            name = f["name"]
            if f["mimeType"] == GSHEET and name.removesuffix(".csv") == "_original_keys":
                name = "_original_keys.csv"
            elif documents and doc2pdf.is_document(name, f["mimeType"]):
                name, f = doc2pdf.pdf_name(name), {**f, "document": True}
            elif f["mimeType"].startswith(GOOGLE_APPS):
                continue  # other Docs/Sheets can't be library files
            old = files.get(name)
            if (not old or old.get("document", False) > f.get("document", False)
                    or old.get("document") == f.get("document") and f.get("modifiedTime", "") > old.get("modifiedTime", "")):
                files[name] = f  # a real PDF beats a document; otherwise the newest wins
        return files

    def _is_current(self, name, f):
        """Is the local copy of `name` the same as Drive's? Adopts a matching copy it hasn't seen."""
        path = self.folder / name
        if not path.exists():
            return False
        if self._manifest.get(name) == version(f):
            return True
        if f.get("md5Checksum") and not f.get("document") and md5(path) == f["md5Checksum"]:
            with self._lock:
                self._manifest[name] = f["md5Checksum"]
            return True
        return False

    def _sync_cho(self, result):
        remote = {n: f for n, f in self._listing(self.cho_folder).items()
                  if n.endswith(".cho") or n in ("_original_keys.csv", "_exclude.txt")}
        stale = [(n, f) for n, f in remote.items() if not self._is_current(n, f)]

        def fetch(name, f):
            try:
                self._fetch(name, f)
                result["updated"].append(name)
            except RateLimited:
                raise
            except DriveError as e:  # one bad file shouldn't stop the rest
                result["failed"].append(f"{name}: {e}")

        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda nf: fetch(*nf), stale))
        finally:
            with self._lock:
                self._save_manifest()
        for p in self.folder.iterdir():  # gone from Drive: drop it here too
            if p.suffix in (".cho", ".csv", ".txt") and p.name not in remote:
                self._remove(p.name)
                result["removed"].append(p.name)

    def _list_pdfs(self, result):
        remote = {n: f for n, f in self._listing(self.pdf_folder, documents=True).items() if n.lower().endswith(".pdf")}
        if self.pdfs:  # (the first listing after a start isn't news)
            result["updated"] += sorted(n for n, f in remote.items()
                                        if n not in self.pdfs or version(f) != version(self.pdfs[n]))
            result["removed"] += sorted(self.pdfs.keys() - remote.keys())
        if remote.keys() != self.pdfs.keys():
            self.pdf_version += 1
        self.pdfs = remote
        for p in self.folder.glob("*.pdf"):
            if p.name not in remote:
                self._remove(p.name)

    def _convert_new_pdfs(self, result):
        """Songs that are only a PDF in Drive: convert each to auto/<slug>.cho.

        <slug>@<Key>.pdf files are other keys of an as-is song, not songs of their own.
        A file is converted once per version of it (and of the converter), including one
        that can't be (sheet music).
        """
        from import_drive import convert_one  # heavy, and it imports modules that import this one
        wanted = {n[:-4]: f for n, f in self.pdfs.items()
                  if "@" not in n and not (self.folder / f"{n[:-4]}.cho").exists()}
        self.auto_dir.mkdir(exist_ok=True)
        for p in self.auto_dir.glob("*.cho"):  # its PDF is gone, or Drive has a real .cho now
            if p.stem not in wanted:
                p.unlink()
        with self._lock:
            for k in [k for k in self._manifest if k.startswith("auto/") and k[5:-4] not in wanted]:
                del self._manifest[k]
        for slug, f in sorted(wanted.items()):
            done = f"{version(f)} converter {CONVERTER_VERSION}"
            if self._manifest.get(f"auto/{slug}.cho") == done:
                continue
            pdf = self.pdf(f"{slug}.pdf")  # RateLimited ends the sync; the next one carries on
            if not pdf:
                result["failed"].append(f"{f['name']}: couldn't download it")
                continue
            item = {"id": f["id"], "name": f["name"], "path": "", "mime": f["mimeType"]}
            try:
                cho, skipped, _ = convert_one(pdf, item)
            except Exception as e:
                cho, skipped = None, f"conversion failed ({e})"
            (self.auto_dir / f"{slug}.cho").unlink(missing_ok=True)
            if cho:
                tmp = self.auto_dir / f"{slug}.cho.part"
                tmp.write_text(cho, encoding="utf-8")
                tmp.replace(self.auto_dir / f"{slug}.cho")
                title = re.search(r"^\{title:\s*(.*?)\s*\}$", cho, re.M)
                result["new_songs"].append(title[1] if title else slug)
            else:
                result["failed"].append(f"{f['name']}: {skipped or 'not a chord chart'}")
            with self._lock:
                self._manifest[f"auto/{slug}.cho"] = done
                self._save_manifest()

    def _download_pdfs(self):
        """Download every PDF that's missing or changed, one at a time, in the background."""
        if not self._pdf_download.acquire(blocking=False):
            return
        try:
            todo = [(n, f) for n, f in self.pdfs.items() if not self._is_current(n, f)]
            with self._lock:
                self._save_manifest()
            for i, (name, f) in enumerate(todo):
                try:
                    self.pdf(name)
                except RateLimited:
                    print(f"Drive library: Google paused downloads; {len(todo) - i} PDFs left for the next sync",
                          flush=True)
                    return
            if todo:
                print(f"Drive library: downloaded {len(todo)} PDFs", flush=True)
        finally:
            self._pdf_download.release()

    def _fetch(self, name, f):
        rk = f.get("resourceKey")
        if f["mimeType"] == GSHEET:
            data = self.client.export(f["id"], "text/csv", rk)
        else:
            data = doc2pdf.fetch_pdf(self.client, f["id"], f["name"], f["mimeType"], rk)
        path = self.folder / name
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(data)
        with self._lock:
            tmp.replace(path)
            self._manifest[name] = version(f)
        return path

    def _remove(self, name):
        with self._lock:
            (self.folder / name).unlink(missing_ok=True)
            self._manifest.pop(name, None)
            self._save_manifest()

    def _save_manifest(self):
        tmp = self._manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._manifest, ensure_ascii=False, indent=0))
        tmp.replace(self._manifest_path)

    # ------------------------------------------------------------------ PDFs
    def has_pdf(self, name):
        return name in self.pdfs

    def pdf(self, name):
        """Local path of a library PDF, downloading it if needed; None if Drive has none.

        Raises RateLimited if Google is refusing downloads right now.
        """
        f = self.pdfs.get(name)
        if not f:
            return None
        with self._lock:
            lock = self._pdf_locks.setdefault(name, threading.Lock())
        with lock:  # the background download and a setlist asking at once fetch it once
            if self._is_current(name, f):
                return self.folder / name
            try:
                path = self._fetch(name, f)
            except RateLimited:
                raise
            except (DriveError, doc2pdf.ConversionError) as e:
                print(f"Couldn't download {name}: {e}", flush=True)
                return None
            with self._lock:
                self._save_manifest()
            return path
