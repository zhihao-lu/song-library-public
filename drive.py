"""Google Drive access: the read-only API client, and the crawler for the one-time import.

PDFs are imported, and so are Word files and Google Docs, as PDFs (doc2pdf.py).

Uses the Drive v3 REST API with an API key, which can read files shared as
"anyone with the link". Older link-shared items carry a resource key that must
be sent in the X-Goog-Drive-Resource-Keys header; that's handled here.

Config (environment variables):
  GOOGLE_API_KEY       required; a Google Cloud API key with the Drive API enabled
  DRIVE_FOLDER_URL     the shared root folder link (default: the YFM resource folder)
  DRIVE_INCLUDE        regex for which top-level folders to index
  DATA_DIR             where the crawl index and downloaded PDFs are cached (default ./data)
"""

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

import doc2pdf
from doc2pdf import is_document

API = "https://www.googleapis.com/drive/v3/files"
# Set DRIVE_FOLDER_URL in .env to your own shared folder (see .env.example).
DEFAULT_FOLDER_URL = "https://drive.google.com/drive/folders/YOUR-SONG-RESOURCE-FOLDER-ID"
DEFAULT_INCLUDE = r"song|composition|hymn|music book|vocal"
FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"
PDF = "application/pdf"


class DriveError(RuntimeError):
    pass


class RateLimited(DriveError):
    """Google keeps refusing downloads; stop and resume later."""


def parse_folder_url(url):
    """'https://drive.google.com/drive/folders/<id>?resourcekey=<rk>' -> (id, rk)."""
    m = re.search(r"/folders/([\w-]+)", url)
    if not m:
        raise DriveError(f"Not a Drive folder link: {url}")
    rk = parse_qs(urlparse(url).query).get("resourcekey", [None])[0]
    return m[1], rk


# A key at the end of a file name, after a real separator: "-C", "_Ab", "(Bb)", "Key of D".
# A bare space isn't enough ("Plan A" is a title, not a key).
KEY_IN_NAME = re.compile(r"(?:[_(\[-]\s*|(?i:\bkey\s*(?:of\s*)?)|\s+in\s+)([A-G][#b]?m?)\s*[)\]]?$")  # also "Song in G"


def key_from_name(name):
    """'Washed-chords-C.pdf' -> 'C'; 'Song (Bb).pdf' -> 'Bb'; else None."""
    stem = re.sub(r"(?:\.docx?)?\.pdf$|\.docx?$", "", name, flags=re.I)
    m = KEY_IN_NAME.search(stem)
    return m[1] if m else None


class DriveClient:
    """Read-only Drive v3 calls with an API key: throttling, backoff and resource keys."""

    MIN_INTERVAL = 1.0                     # seconds between requests, across all threads
    LIST_INTERVAL = None                   # between folder listings, if they may go faster than downloads
    BACKOFF = (30, 60, 120, 240)           # waits when Google says "slow down"

    def __init__(self, api_key=None):
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY")
        self._lock = threading.Lock()
        self._next_request = 0.0
        self._session = requests.Session()

    def _throttle(self, interval=None):
        with self._lock:
            wait = self._next_request - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._next_request = time.monotonic() + (self.MIN_INTERVAL if interval is None else interval)

    @staticmethod
    def _rate_limited(r):
        """Google's automated-traffic block ("Sorry..." page) or a quota/rate error."""
        if r.status_code == 429:
            return True
        if r.status_code == 403:
            if "<title>Sorry" in r.text[:500]:
                return True
            try:
                reasons = {e.get("reason") for e in r.json()["error"].get("errors", [])}
            except Exception:
                return False
            return bool(reasons & {"rateLimitExceeded", "userRateLimitExceeded", "downloadQuotaExceeded"})
        return False

    def _get(self, url, params=None, resource_keys=(), stream=False, interval=None):
        params = dict(params or {}, key=self.api_key, supportsAllDrives="true")
        headers = {}
        rks = [f"{i}/{rk}" for i, rk in resource_keys if rk]
        if rks:
            headers["X-Goog-Drive-Resource-Keys"] = ",".join(rks)
        transient = 0
        backoff = iter(self.BACKOFF)
        while True:
            self._throttle(interval)
            r = self._session.get(url, params=params, headers=headers, timeout=60, stream=stream)
            if self._rate_limited(r):
                wait = next(backoff, None)
                if wait is None:
                    raise RateLimited("Google is rate-limiting downloads; try again later (re-running resumes).")
                print(f"  Google asked us to slow down; waiting {wait}s…", flush=True)
                with self._lock:  # pause every thread, not just this one
                    self._next_request = max(self._next_request, time.monotonic() + wait)
                continue
            if r.status_code in (500, 502, 503) and transient < 3:
                transient += 1
                time.sleep(2 ** transient)
                continue
            if r.status_code >= 400:
                try:
                    msg = r.json()["error"]["message"]
                except Exception:
                    msg = r.text[:200]
                raise DriveError(f"Drive API {r.status_code}: {msg}")
            return r

    def children(self, folder_id, rk=None):
        """The files and folders directly in a folder, as Drive lists them."""
        files, token = [], None
        while True:
            params = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": "nextPageToken, files(id, name, mimeType, resourceKey, modifiedTime, size, md5Checksum, "
                          "shortcutDetails(targetId, targetMimeType, targetResourceKey))",
                "pageSize": 1000, "includeItemsFromAllDrives": "true",
            }
            if token:
                params["pageToken"] = token
            data = self._get(API, params, [(folder_id, rk)], interval=self.LIST_INTERVAL).json()
            files += data.get("files", [])
            token = data.get("nextPageToken")
            if not token:
                return files

    def download(self, file_id, rk=None):
        """A binary file's contents."""
        return self._get(f"{API}/{file_id}", {"alt": "media"}, [(file_id, rk)]).content

    def export(self, file_id, mime, rk=None):
        """A Google Docs/Sheets file converted to `mime` (e.g. text/csv)."""
        return self._get(f"{API}/{file_id}/export", {"mimeType": mime}, [(file_id, rk)]).content


class DriveLibrary(DriveClient):
    def __init__(self, api_key=None, folder_url=None, include=None, data_dir=None):
        super().__init__(api_key)
        self.root_id, self.root_rk = parse_folder_url(folder_url or os.environ.get("DRIVE_FOLDER_URL") or DEFAULT_FOLDER_URL)
        self.include = re.compile(include or os.environ.get("DRIVE_INCLUDE") or DEFAULT_INCLUDE, re.I)
        self.data_dir = Path(data_dir or os.environ.get("DATA_DIR") or Path(__file__).parent / "data")
        (self.data_dir / "files").mkdir(parents=True, exist_ok=True)
        self.index_path = self.data_dir / "index.json"
        self.items, self.built_at = [], 0
        if self.index_path.exists():
            try:
                saved = json.loads(self.index_path.read_text())
                self.items, self.built_at = saved["items"], saved["built_at"]
            except Exception:
                pass

    # ------------------------------------------------------------------ crawl
    def crawl(self):
        """Walk the included folders and rebuild the index of PDFs, Word files and Google Docs."""
        if not self.api_key:
            raise DriveError("GOOGLE_API_KEY is not set (put it in .env)")
        items, seen = [], set()
        lock = threading.Lock()

        def resolve(f):
            if f["mimeType"] == SHORTCUT and f.get("shortcutDetails"):
                d = f["shortcutDetails"]
                return {**f, "id": d["targetId"], "mimeType": d.get("targetMimeType", ""),
                        "resourceKey": d.get("targetResourceKey")}
            return f

        def walk(folder_id, rk, path):
            try:
                children = [resolve(f) for f in self.children(folder_id, rk)]
            except DriveError as e:
                print(f"  skipping {'/'.join(path)}: {e}")
                return []
            sub = []
            for f in children:
                with lock:
                    if f["id"] in seen:
                        continue
                    seen.add(f["id"])
                if f["mimeType"] == FOLDER:
                    sub.append((f["id"], f.get("resourceKey"), path + [f["name"]]))
                elif f["mimeType"] == PDF or f["name"].lower().endswith(".pdf") or is_document(f["name"], f["mimeType"]):
                    with lock:
                        items.append({
                            "id": f["id"], "rk": f.get("resourceKey"), "name": f["name"],
                            "mime": f["mimeType"], "modified": f.get("modifiedTime", ""),
                            "path": "/".join(path), "key_hint": key_from_name(f["name"]),
                        })
            return sub

        top = [resolve(f) for f in self.children(self.root_id, self.root_rk)]
        queue = [(f["id"], f.get("resourceKey"), [f["name"]]) for f in top
                 if f["mimeType"] == FOLDER and self.include.search(f["name"])]
        with ThreadPoolExecutor(max_workers=8) as pool:
            while queue:
                queue = [s for subs in pool.map(lambda a: walk(*a), queue) for s in subs]
        items.sort(key=lambda i: (i["path"].lower(), i["name"].lower()))
        with self._lock:
            self.items, self.built_at = items, time.time()
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"built_at": self.built_at, "items": items}))
        tmp.replace(self.index_path)
        return len(items)

    # ------------------------------------------------------------------ download
    def cache_path(self, item):
        stamp = re.sub(r"\W", "", item.get("modified", ""))[:14]
        return self.data_dir / "files" / f"{item['id']}-{stamp}.pdf"

    def is_cached(self, item):
        return self.cache_path(item).exists()

    def fetch(self, item):
        """Download a PDF (a Word file or Google Doc: converted to one) into the cache; return its path."""
        path = self.cache_path(item)
        if path.exists():
            return path
        content = doc2pdf.fetch_pdf(self, item["id"], item["name"], item["mime"], item.get("rk"))
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(content)
        if not content.startswith(b"%PDF"):
            tmp.unlink(missing_ok=True)
            raise DriveError(f"{item['name']} did not download as a PDF")
        for old in path.parent.glob(f"{item['id']}-*.pdf"):  # drop stale versions
            old.unlink(missing_ok=True)
        tmp.replace(path)
        return path
