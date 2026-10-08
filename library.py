"""The song library: a folder of ChordPro files (plus optional original PDFs).

Each song is <slug>.cho. If <slug>.pdf exists, setlists can use it for
"original layout" output; otherwise the song is typeset from the .cho.
The folder is re-read automatically when files change, so edits show up
without restarting the server.

The library lives in Google Drive, and the folder is a local copy of it
(see drive_mirror.py). Set LIBRARY_DIR to use a plain local folder instead.
"""

import os
import re
import threading
import unicodedata
from pathlib import Path

from pypinyin import lazy_pinyin

import chordtools as ct
from drive import RateLimited
from drive_mirror import DriveMirror
from pdf2chordpro import CJK_RE, to_simplified

DIRECTIVE = re.compile(r"^\{\s*([\w-]+)\s*(?::\s*(.*?))?\s*\}$")


def search_form(s):
    # Simplified form, so a Traditional title is found by a Simplified search and vice versa.
    s = to_simplified(unicodedata.normalize("NFKC", s)).lower()
    return re.sub(r"[\s_\-–—.,:;!?()\[\]'’\"&/]+", " ", s).strip()


LABEL_LINE = re.compile(r"^\W*(?:intro|verse|pre-?chorus|chorus|bridge|tag|outro|interlude|ending|instrumental|refrain"
                        r"|主歌|副歌|桥段|前奏|间奏|尾奏|结尾)\b[\s\d.:：()（）x×]*\W*$", re.I)
NAMES_LINE = re.compile(r"^[A-Z][a-z]+(?: [A-Z][a-z]+)+(?:\s*(?:,|&|and)\s*[A-Z][a-z]+(?: [A-Z][a-z]+)+)+$")  # "Keith Getty and Stuart Townend"
CHORD_TOKEN = re.compile(r"^(?:\||[A-G][#b]?(?:m|maj|min|sus|dim|aug|add)?\d*(?:/[A-G][#b]?)?)$")
CREDIT_LINE = re.compile(r"(?i)\b(?:words|music|lyrics|written|arranged|arr\.|ccli|copyright|©|admin|publishing|songselect"
                         r"|capo|tempo|key of|作词|作曲|词|曲)\b|[©℗]")


def opening_line(cho, length=50):
    """A chart's first line of lyrics, to tell apart songs that share a title ("我相信 · 雷声轰隆…"):
    chords, chord-only lines, section names and credit lines left out; cut at `length`."""
    for line in cho.splitlines():
        if line.lstrip().startswith("{"):
            continue
        words = re.sub(r"\[[^\]]*\]", "", line).split()  # a chord inside a word leaves no gap
        bar = "|" in words  # a bar of chords before the words: "C | D | Em If you've…"
        while bar and words and CHORD_TOKEN.match(words[0]):
            words.pop(0)
        text = " ".join(words)
        if (not text or LABEL_LINE.match(text) or CREDIT_LINE.search(text) or NAMES_LINE.match(text)
                or ":" in text[:12] and "|" in text
                or len(re.findall(r"[A-Za-z]{2,}|[\u4e00-\u9fff]", text)) < 3):
            continue
        return text if len(text) <= length else text[:length].rstrip(" ,，、") + "…"
    return ""


def search_fields(title, other=""):
    """The fields matches() searches, for a song with this title and these other words (artist,
    composer, folder, file name…). Every song the site searches is indexed by this one function."""
    py_words, py_joined, py_initials = pinyin_forms(title)
    return {"_title": search_form(title), "_other": search_form(other),
            "_py_words": py_words, "_py_compact": py_joined + py_initials, "_initials": english_initials(title)}


def english_initials(title):
    """The first letters of a title's English words, as typed to find it: "Just That Good" -> "jtg".
    Also without a part in brackets ("Oceans (Where Feet May Fail)" -> "owfmf" and "o"). Only for
    two words or more: one letter is just the start of a word, which searching finds anyway."""
    forms = []
    for text in (title, re.sub(r"\s*[(\[].*?[)\]]", " ", title)):
        words = re.findall(r"[a-z0-9]+", search_form(text))
        if len(words) >= 2 and (initials := "".join(w[0] for w in words)) not in forms:
            forms.append(initials)
    return forms


def pinyin_forms(text):
    """Pinyin search keys for Chinese text: (syllables, joined, initials).

    叫我抬起头的神 -> ['jiao', 'wo', 'tai', ...], 'jiaowotaiqitoudeshen', 'jwtqtds'.
    ü is accepted as either 'v' or 'u' (lv / lu).
    """
    if not CJK_RE.search(text or ""):
        return [], [], []
    sylls = [s.lower() for s in lazy_pinyin(text, errors="ignore") if s.strip()]  # 'lv' for ü
    variants = {tuple(sylls), tuple(s.replace("v", "u") for s in sylls)}
    words = sorted({s for v in variants for s in v})
    joined = sorted({"".join(v) for v in variants})
    initials = "".join(s[0] for s in sylls)
    return words, joined, [initials]


def in_chart_mode(key, chart):
    """An original key in the chart's mode ('Db' for a major chart, 'Bbm' for a minor one)."""
    parsed = ct.parse_key(key) if key else None
    if not parsed:
        return None
    minor = chart[1] if chart else parsed[1]
    return ct.key_name(parsed[0], minor)


def parse_song(path):
    text = path.read_text(encoding="utf-8")
    meta = {}
    for line in text.splitlines():
        m = DIRECTIVE.match(line.strip())
        if m:
            name, val = m[1].lower(), (m[2] or "").strip()
            if name == "meta" and " " in val:
                k, v = val.split(" ", 1)
                meta.setdefault(k.lower(), v.strip())
            elif name in ("title", "t", "subtitle", "st", "artist", "composer", "lyricist", "key"):
                meta.setdefault({"t": "title", "st": "subtitle"}.get(name, name), val)
    key = meta.get("key")
    parsed = ct.parse_key(key) if key else None
    return {
        "slug": path.stem,
        "title": meta.get("title") or path.stem,
        "first_line": opening_line(text),
        "artist": meta.get("artist") or meta.get("composer") or meta.get("subtitle") or "",
        "key": ct.key_name(*parsed) if parsed else None,
        "folder": meta.get("source_folder", ""),
        "source_file": meta.get("source_file", ""),
        "pdf_pages": meta.get("pdf_pages"),               # e.g. "2-3": skip a license page
        "lyrics_ok": meta.get("lyrics") != "unreadable",  # else uniform layout uses the PDF
        # A scanned PDF (read by OCR into the .cho): shown as-is in its own key, typeset in others.
        "pdf_scan": meta.get("pdf") == "scan",
        # As-is songs (image-only PDFs) go into setlists unchanged, in their original key.
        "transposable": meta.get("transpose") != "no" and bool(parsed),
        # As-is songs may come in several keys, each its own PDF (<slug>@<Key>.pdf).
        "keys": [k.strip() for k in (meta.get("keys") or "").split(",") if k.strip()],
        # The song's original (recording) key, if a chart said so; the team CSV overrides it.
        "original_key": in_chart_mode(meta.get("original_key"), parsed),
        **search_fields(meta.get("title") or path.stem,
                        " ".join([meta.get("artist", ""), meta.get("subtitle", ""), meta.get("composer", ""),
                                  meta.get("lyricist", ""), meta.get("source_folder", ""),
                                  re.sub(r"\s*\(\d\)(?=\.pdf$)", "", meta.get("source_file", ""), flags=re.I)])),
    }


class SongLibrary:
    def __init__(self, folder=None, mirror=None):
        folder = folder or os.environ.get("LIBRARY_DIR")
        if folder is None and mirror is None:
            # A folder of its own: syncing deletes files that aren't in Drive.
            mirror = DriveMirror.from_env(os.environ.get("DRIVE_CACHE_DIR") or Path(__file__).parent / "data" / "drive-library")
        self.mirror = mirror
        self.folder = Path(mirror.folder if mirror else folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self._songs, self._stamp = {}, None
        self._lock = threading.Lock()

    def _cho_files(self):
        """slug -> .cho path: the library's own, plus ones converted from PDFs that have none."""
        files = {}
        if self.mirror:
            files.update((p.stem, p) for p in self.mirror.auto_dir.glob("*.cho"))
        files.update((p.stem, p) for p in self.folder.glob("*.cho"))
        return files

    def _snapshot(self):
        csv_path = self.folder / "_original_keys.csv"
        csv_stamp = (("_original_keys.csv", csv_path.stat().st_mtime_ns),) if csv_path.exists() else ()
        pdf_stamp = (("_pdfs", self.mirror.pdf_version),) if self.mirror else ()
        chos = tuple(sorted((slug, str(p), p.stat().st_mtime_ns) for slug, p in self._cho_files().items()))
        return chos + csv_stamp + pdf_stamp

    def _has_pdf(self, slug):
        name = f"{slug}.pdf"
        return self.mirror.has_pdf(name) if self.mirror else (self.folder / name).exists()

    def _team_original_keys(self):
        """library/_original_keys.csv (edited by the team): slug -> original key."""
        import csv
        path = self.folder / "_original_keys.csv"
        if not path.exists():
            return {}
        with open(path, encoding="utf-8-sig", newline="") as f:
            return {r["slug"]: r["original_key"].strip() for r in csv.DictReader(f)
                    if r.get("slug") and (r.get("original_key") or "").strip()}

    def songs(self):
        """All songs, reloading any .cho files that changed since last time."""
        if self.mirror:
            self.mirror.wait_ready()  # also starts a background sync when it's due
        stamp = self._snapshot()
        with self._lock:
            if stamp != self._stamp:
                old, fresh = self._songs, {}
                team = self._team_original_keys()
                for entry in stamp:
                    if len(entry) != 3:
                        continue  # the CSV / PDF stamps
                    slug, path, mtime = entry
                    prev = old.get(slug)
                    if prev and prev["_mtime"] == (path, mtime):
                        song = prev
                    else:
                        try:
                            song = parse_song(Path(path))
                            song["_mtime"] = (path, mtime)
                            song["_chart_original"] = song["original_key"]
                            song["auto"] = self.mirror is not None and Path(path).parent == self.mirror.auto_dir
                        except Exception as e:
                            print(f"Skipping {path}: {e}")
                            continue
                    chart = ct.parse_key(song["key"]) if song["key"] else None
                    song["original_key"] = in_chart_mode(team.get(slug), chart) or song["_chart_original"]
                    song["has_pdf"] = self._has_pdf(slug)
                    # Keys with a chart of their own: an as-is or scanned song's PDFs, one per key.
                    song["chart_keys"] = song["keys"] if not song["transposable"] or song["pdf_scan"] else []
                    fresh[slug] = song
                self._songs, self._stamp = fresh, stamp
            return self._songs

    def get(self, slug):
        return self.songs().get(slug)

    def cho_path(self, slug):
        return self._cho_files().get(slug, self.folder / f"{slug}.cho")

    def pdf_path(self, slug, key=None):
        """The song's original PDF, or with `key` an as-is song's PDF in that key.

        None if there's none, or if it isn't downloaded yet and Google is refusing downloads.
        """
        name = f"{slug}@{key}.pdf" if key else f"{slug}.pdf"
        if self.mirror:
            try:
                return self.mirror.pdf(name)
            except RateLimited:
                return None
        p = self.folder / name
        return p if p.exists() else None

    def search(self, query, limit=40, sources=None):
        """Songs matching `query` (see matches()). This library has no sources to filter by."""
        return [public(s) for s in matches(self.songs().values(), query)[:limit]]

    def resolve(self, slug):
        """The ID a slug stands for now (the three-source library has aliases; this one doesn't)."""
        return slug

    def last_title(self, slug):
        """The title a song had before it was removed from the library, if known."""
        return None

    def status(self):
        m = self.mirror
        return {"songs": self.count(), "drive": bool(m), "synced_at": m.synced_at or None if m else None,
                "mode": "mirror" if m else "folder", "sources": [], "sync_minutes": m.interval / 60 if m else None}

    def chart(self, slug, key=None):
        """How to draw a song: the chart it shows, or with `key` its own chart in that key
        (an as-is or scanned song's <slug>@<Key>.pdf); None if there's no such chart.

        {"key", "transposable", "lyrics_ok", "pdf_scan", "pdf_pages", "has_pdf",
         "pdf": () -> Path or None, "cho": () -> text, "cho_key": the key the ChordPro is in}
        """
        song = self.get(slug)
        if not song or key is not None and key not in song["chart_keys"]:
            return None
        return {"key": key or song["key"], "cho_key": song["key"],
                **{k: song[k] for k in ("transposable", "lyrics_ok", "pdf_scan", "pdf_pages", "has_pdf")},
                "pdf": lambda: self.pdf_path(slug, key),
                "cho": lambda: self.cho_path(slug).read_text(encoding="utf-8")}

    def count(self):
        return len(self.songs())

    def sync(self):
        """Fetch changes from Drive now; returns what changed (None without Drive)."""
        if not self.mirror:
            return None
        result = self.mirror.sync(force=True)
        self.songs()
        return result


def library_from_env():
    """The library the site serves, as the environment says.

    LIBRARY_DIR          a plain local folder
    LIBRARY_MODE         "mirror" (default): today's two Drive folders (drive_mirror.py);
                         "sources": the three sources, with arrangements (sources_library.py).
                         Each keeps its own local copy, so switching back and forth is a restart.
    """
    if os.environ.get("LIBRARY_DIR"):
        return SongLibrary(os.environ["LIBRARY_DIR"])
    mode = (os.environ.get("LIBRARY_MODE") or "mirror").strip().lower()
    if mode == "sources":
        from sources_library import SourcesLibrary
        return SourcesLibrary.from_env()
    if mode != "mirror":
        raise ValueError(f"LIBRARY_MODE must be 'mirror' or 'sources', not {mode!r}")
    return SongLibrary()


def matches(songs, query):
    """The songs matching `query`, best first.

    Every query word must match the title (or its pinyin), or the artist, composer, folder or
    file name. Lyrics are deliberately not searched (too many loose matches). Pinyin can also
    be typed run together or as initials: "jiaowotai", "jwtqtds"."""
    words = search_form(query).split()
    if not words:
        return []
    compact = "".join(words)
    results = []
    for song in songs:
        # Pinyin typed as one run ("jiaowotai") or initials ("jwtqtds").
        if compact.isascii() and any(c.startswith(compact) for c in song["_py_compact"]):
            results.append((12, song))
            continue
        title_words = song["_title"].split() + song["_py_words"]
        other_words = song["_other"].split()
        score = 0
        for w in words:
            if any(t.startswith(w) for t in title_words):
                score += 4
            elif w.isdigit():
                break  # numbers only match titles, not Bible references or years in credits
            elif any(t.startswith(w) for t in other_words) or w in song["_title"]:
                score += 2  # substring of the title covers Chinese titles, which have no spaces
            else:
                break
        else:
            phrase = " ".join(words)
            if song["_title"].startswith(phrase):
                score += 3
            results.append((score, song))
            continue
        # An English title by its initials ("jtg"), when the words didn't match: below any song
        # whose words did, so typing a real word still lists those first.
        if len(compact) >= 2 and compact.isascii() and any(i.startswith(compact) for i in song.get("_initials", ())):
            results.append((1, song))
    results.sort(key=lambda r: (-r[0], r[1]["title"].lower()))
    return [s for _, s in results]


def public(song):
    return {k: v for k, v in song.items() if not k.startswith("_")}
