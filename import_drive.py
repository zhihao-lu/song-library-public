#!/usr/bin/env python3
"""One-time import: song folders (Google Drive, or local copies) -> library/ of ChordPro files.

For every chord chart PDF in the included Drive folders this writes (Word files and
Google Docs count as PDFs: they're converted to one first, see doc2pdf.py):
  library/<slug>.cho   the ChordPro conversion (edit freely - it's yours now)
  library/<slug>.pdf   the original PDF, used for "original layout" setlists

Sheet music (staff notation) and PDFs without chords are skipped and listed in
library/_import_report.txt. Existing files are never overwritten unless you pass
--overwrite, so re-running only adds songs that are new in Drive.

Usage:
  .venv/bin/python import_drive.py --from-folder "~/Downloads/2. Chinese Songs"   # local copies
  .venv/bin/python import_drive.py              # straight from Drive (GOOGLE_API_KEY in .env)
  .venv/bin/python import_drive.py --limit 20   # try a few first

Local folders are only read - nothing in them is moved, renamed or deleted.
"""

import argparse
import pdfplumber
import hashlib
import os
import re
import shutil
import sys
import threading
import unicodedata
import tempfile
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import config  # noqa: F401  (loads .env)
import chordtools as ct
import doc2pdf
import pdf2chordpro as p2c
from drive import KEY_IN_NAME, DriveError, DriveLibrary, RateLimited, key_from_name
from setlist import has_notation

ROOT = Path(__file__).parent


def slugify(text):
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"\.(?:pdf|docx?)$", "", text)
    text = re.sub(r"[^\w]+", "-", text, flags=re.UNICODE).strip("-")
    return text or "song"


def with_meta(cho, meta):
    """Insert {meta: ...} lines (and a {key:} if missing) right after {title:}."""
    lines = cho.splitlines()
    at = next((i + 1 for i, l in enumerate(lines) if l.startswith("{title")), 0)
    extra = []
    if meta.get("key") and not ct.declared_key(cho):
        extra.append(f"{{key: {meta['key']}}}")
    extra += [f"{{meta: {k} {v}}}" for k, v in meta.items() if k != "key" and v]
    return "\n".join(lines[:at] + extra + lines[at:]) + "\n"


def title_for(item):
    """Title from a file name, or from the song's folder when the file name is a code
    like 'SOYOUWO1.pdf' (folder '…/So You Would Come')."""
    stem = re.sub(r"\.(?:pdf|docx?)$", "", item["name"], flags=re.I)
    stem = KEY_IN_NAME.sub("", stem).strip(" -_")  # "FORALLY1 in G" -> "FORALLY1"
    folder = item.get("path", "").split("/")[-1]
    is_code = re.fullmatch(r"[A-Z0-9_\-]{4,14}", stem) and not re.fullmatch(r"[A-Z]+", stem)
    song_folder = item.get("path", "").count("/") >= 1 and not re.match(r"^\d+\.\s|^00 ", folder)
    if is_code and song_folder:
        return title_from_name(folder)
    return title_from_name(item["name"])


def title_from_name(name):
    """'一生敬拜 - G.pdf' -> '一生敬拜'; 'Song (Bb).pdf' -> 'Song'."""
    stem = title = re.sub(r"(?:\.docx?)?(?:\s+pdf)?\.pdf$|\.docx?$", "", name, flags=re.I)  # "X.docx.pdf", "X pdf.pdf", "X.docx"
    title = re.sub(r"\s*\(\d\)\s*$", "", title)  # "Song (1).pdf": a duplicate download's copy number
    title = re.sub(r"^(?:\(default\)\s*|emailing\s+|\([A-G][#b]?m?\)\s*)", "", title, flags=re.I)  # "(C) X", "Emailing X"
    title = re.sub(r"-\s*chord\s*sheet-.*$|\s+copy$|_\d+$", "", title, flags=re.I)  # "X- CHORD SHEET- Key G copy"
    for _ in range(3):  # peel "(D) - Guitar Chords", "-chords-B", etc. in any order
        m = KEY_IN_NAME.search(title)
        if m:
            title = title[:m.start()]
        title = re.sub(r"[\s_-]*(?:guitar\s+)?chords?\s*$", "", title, flags=re.I).strip(" -_")
        title = re.sub(r"\s+[A-G][#b]?m?$", "", title)  # "Jesus At The Center E"
    # Pinyin initials next to a Chinese title: "SF 顺服" / "ZMZXX讚美…" / "尽情地微笑 JQDWX".
    if p2c.CJK_RE.search(title):
        title = re.sub(r"^[A-Z]{1,12}\s*(?=[\u3400-\u9fff])", "", title)
        title = re.sub(r"(?<=[\u3400-\u9fff])\s+[A-Z]{1,12}$", "", title)
    elif re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)+", title):  # "he-reigns" -> "He Reigns"
        title = " ".join(w.capitalize() for w in re.split(r"[-_]", title))
    title = re.sub(r"(?<=[A-Za-z])_(?=(?:s|ve|re|ll|t|d|m)\b)", "'", title)  # "Father_s", "You_ve" -> apostrophe
    title = re.sub(r"\s+_\s+", " / ", title)                    # "Pursue _ all i need is you"
    # Library style: every word capitalized ("Pursue / all i need is you" -> "Pursue / All I Need Is You").
    title = " ".join(w[0].upper() + w[1:] if w[:1].islower() and not p2c.CJK_RE.search(w) else w for w in title.split())
    return p2c.clean_title(title) or title or stem


def as_is_song(item, title=None):
    """A library entry for a PDF we can't read (a picture of a sheet, or a chart drawn as
    vector outlines): searchable by its title and usable in setlists as the original PDF,
    but not transposable. Other keys of the same song are added as extra PDFs later."""
    title = title or title_for(item)
    key = name_key(item["name"])
    orig = original_key_hint(item)
    lines = [f"{{title: {title}}}"] + ([f"{{key: {key}}}"] if key else []) + [
        f"{{meta: source_file {item['name']}}}",
        *([f"{{meta: original_key {orig}}}"] if orig else []),
        f"{{meta: source_folder {item['path']}}}",
        "{meta: transpose no}",
        "",
        "{comment: Original PDF only - this sheet is an image, so it can't be read or transposed.}",
    ]
    return "\n".join(lines) + "\n"


def name_key(name):
    hint = key_from_name(name)
    return ct.key_name(*ct.parse_key(hint)) if hint and ct.parse_key(hint) else None


def drawn_as_outlines(pdf_path):
    """True if the page is mostly vector shapes rather than text: a chart whose chords and
    lyrics were exported as outlines (e.g. many SongSelect PDFs) - readable by eye only."""
    with p2c.PDF_LOCK, pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        return len(page.curves) >= 100 and len(page.curves) > 2 * len(page.chars)


def _words(text):
    text = p2c.to_simplified(unicodedata.normalize("NFKC", text)).lower()
    latin = {w for w in re.findall(r"[a-z]{3,}", text)}
    cjk = "".join(ch for ch in text if p2c.is_cjk(ch))
    return latin | {cjk[i:i + 2] for i in range(len(cjk) - 1)}


def repair_ligatures(title, file_title):
    """'Beau%ful' / 'Founda+on' / 'Resurrec'on' -> the file name's spelling.

    Some fonts draw 'ti' as one glyph that extracts as %, +, . or '. A word with one
    of those inside is replaced by the file name's word when that one has 'ti' there.
    """
    file_words = {w.lower(): w for w in re.findall(r"[A-Za-z']+", file_title)}
    def fix(m):
        word = m[0]
        cand = re.sub(r"[%+.'’]", "ti", word, count=1)
        if cand.lower() in file_words or re.search(r"[%+]", word):
            return cand
        return word
    return re.sub(r"[A-Za-z]+[%+.'’][A-Za-z]+", fix, title)


def reconcile_title(title, item):
    """Check an extracted title against the file/folder name, which people typed by hand.

    - fix broken 'ti' ligatures using the file name's spelling;
    - if the title shares no words with the file name (e.g. an album name like
      'Hillsong Live' was the biggest text), use the file name's title instead;
    - a Chinese title followed by extra text (credits, a website note) is cut
      back to the file name's Chinese title.
    Returns (title, subtitle_to_keep_or_None).
    """
    file_title = title_for(item)
    if not title:
        return file_title, None
    title = repair_ligatures(title, file_title)
    is_code = bool(re.fullmatch(r"[A-Z0-9_\-]{4,14}", file_title))
    if not is_code and file_title and not (_words(title) & _words(file_title)):
        return file_title, title
    # Same length but some words differ, e.g. a scan's OCR "Broken Vessel.s (rinazing Grace)" vs the
    # typed file name "Broken Vessels (Amazing Grace)": trust the file name.
    tw = re.findall(r"[a-z]{2,}", re.sub(r"(?<=[a-z])[.'](?=[a-z])", "", title.lower()))
    fw = re.findall(r"[a-z]{2,}", file_title.lower())
    looks_garbled = bool(re.search(r"[a-z][.'’][a-z]{1,2}\b", title.lower()))  # "Vessel.s": an OCR slip
    if looks_garbled and not is_code and tw and len(tw) == len(fw) and 0 < len(set(tw) - set(fw)) < len(tw) \
            and not p2c.CJK_RE.search(title):
        return file_title, None
    fc = "".join(ch for ch in file_title if p2c.is_cjk(ch))
    if fc and title.startswith(fc) and len(title) > len(fc) + 1 and re.match(r"\s", title[len(fc):]):
        rest = title[len(fc):].strip()
        if p2c.CJK_RE.search(rest):  # a Latin translation after it is kept; more Chinese is cut
            return fc, None
    return title, None


ORIG_TEXT_RE = re.compile(r"(?i)original\s+key\s*[:\-=]?\s*([A-G][#b♭♯]?m?)(?![a-z])")
ORIG_NAME_RES = [
    re.compile(r"(?i)original\s*[(\[]?\s*([A-G])\s*(flat|sharp|b|#)?\b"),        # "(Original F)", "Original (B flat)"
    re.compile(r"(?i)\b([A-G][#b]?)\s*-\s*original"),                            # "(A - Original)"
    re.compile(r"(?i)\b([A-G][#b]?m?)\s*[)\]]?\s*[(\[]\s*original\s*[)\]]"),    # "- F (Original)"
]


def original_key_hint(item, pdf_path=None, lines=None):
    """The song's original (recording) key, if a chart states it: 'original key: Db' on the
    sheet (SongSelect prints this), or 'Original' in the file name. None if unknown."""
    if lines:
        for ln in lines[:12]:
            m = ORIG_TEXT_RE.search(ln.text)
            if m and ct.parse_key(m[1]):
                return ct.key_name(*ct.parse_key(m[1]))
    for rx in ORIG_NAME_RES:
        m = rx.search(item["name"])
        if m:
            acc = {"flat": "b", "sharp": "#"}.get((m.group(2) or "").lower(), m.group(2) or "") if m.lastindex and m.lastindex >= 2 else ""
            k = m[1] + acc
            if ct.parse_key(k):
                return ct.key_name(*ct.parse_key(k))
    return None


def convert_one(pdf_path, item):
    """Return (cho_text, None, note) or (None, reason_skipped, None)."""
    if has_notation(pdf_path):
        return None, "sheet music (staff notation)", None
    lines = p2c.extract_lines(pdf_path)
    if not lines:
        return as_is_song(item), None, "as-is: no text (scanned image) - added as the original PDF, can't be transposed"
    if not any(p2c.chord_line_groups(ln.tokens) for ln in lines):
        if drawn_as_outlines(pdf_path):
            header = p2c.convert(lines, source_name=title_for(item), guess_key=False)
            return (as_is_song(item, directive(header, "title")), None,
                    "as-is: chart drawn as outlines (no text) - added as the original PDF, can't be transposed")
        return None, "no chords found", None
    readable = p2c.text_readable(lines)
    cho = p2c.convert(lines, source_name=title_for(item), guess_key=False,
                      title=None if readable else title_for(item))
    fixed, extra = reconcile_title(directive(cho, "title") or "", item)
    cho = re.sub(r"^\{title:.*\}$", lambda _: f"{{title: {fixed}}}", cho, count=1, flags=re.M)
    if extra:
        cho = cho.replace(f"{{title: {fixed}}}", f"{{title: {fixed}}}\n{{subtitle: {extra}}}", 1)
    key = None
    if not ct.declared_key(cho):
        hint = key_from_name(item["name"])
        detected = ct.detect_key(cho)
        key = ct.key_name(*ct.parse_key(hint)) if hint and ct.parse_key(hint) else (detected or {}).get("key")
    meta = {"key": key, "source_file": item["name"], "source_folder": item["path"],
            "drive_id": None if item.get("local") else item["id"],
            "original_key": original_key_hint(item, pdf_path, lines)}
    note = None
    if not readable:
        # The PDF's font has no text mapping: chords are fine (original layout works),
        # but the lyrics came out as symbols. Uniform layout falls back to the PDF.
        meta["lyrics"] = "unreadable"
        note = "lyrics unreadable (font has no text mapping) - original layout only; OCR would fix"
    return with_meta(cho, meta), None, note


# --------------------------------------------------------------------------
# One chart per song
# --------------------------------------------------------------------------

def meta_value(cho, key):
    m = re.search(r"^\{meta:\s*" + re.escape(key) + r"\s+(.*?)\s*\}$", cho, re.M)
    return m[1] if m else None


def directive(cho, name):
    m = re.search(r"^\{" + name + r":\s*(.*?)\s*\}$", cho, re.M)
    return m[1] if m else None


def song_artist(cho):
    return directive(cho, "artist") or directive(cho, "composer") or ""


def title_key(title):
    """Loose identity of a song title.

    Chinese titles compare by their Chinese characters only, in Simplified form, so
    '神恩良善 (Good Grace)' and '神恩良善' match, as do 永恆唯一的盼望 and 永恒唯一的盼望; others by letters/digits without bracketed extras, so
    'Firm Foundation (He Won't)' and 'Firm Foundation' match.
    """
    return title_keys(title)[0]


# Variant spellings that shouldn't split a song in two.
SPELLING = {"centre": "center", "saviour": "savior", "honour": "honor", "colour": "color",
            "favour": "favor", "splendour": "splendor", "hossana": "hosanna"}


def title_keys(title):
    """All loose identities of a title (a match on any of them = same song):
    without bracketed extras, and with them - so 'Oceans (Where Feet May Fail)' matches
    'Oceans Where Feet May Fail'. Chinese: Simplified, 祢/袮 -> 你, 祂 -> 他."""
    base = p2c.to_simplified(unicodedata.normalize("NFKC", p2c.clean_title(title) or title)).casefold()
    base = base.translate(str.maketrans({"祢": "你", "袮": "你", "祂": "他"}))
    keys = []
    for variant in (re.sub(r"[(\[【（].*?[)\]】）]", " ", base), base):
        cjk = "".join(ch for ch in variant if p2c.is_cjk(ch))
        if len(cjk) >= 2:
            keys.append(cjk)
            continue
        words = [SPELLING.get(w, w) for w in re.findall(r"[a-z0-9]+", variant)]
        words = [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words]
        if words:
            keys.append("".join(words))
    return keys or [base]


def artist_words(artist):
    return {w for w in re.findall(r"\w+", unicodedata.normalize("NFKC", artist).casefold()) if len(w) > 2}


class SongIndex:
    """Songs already chosen, so further charts of the same song become alternates.

    Same title = same song, unless both charts name artists that share no words
    (two different songs called "Holy").
    """

    def __init__(self):
        self._by_title = {}

    def add(self, title, artist, slug):
        for key in title_keys(title):
            self._by_title.setdefault(key, []).append((artist_words(artist), slug))

    def find(self, title, artist):
        words = artist_words(artist)
        for key in title_keys(title):
            for other_words, slug in self._by_title.get(key, []):
                if not words or not other_words or words & other_words:
                    return slug
        return None


def chart_score(item, cho, note):
    """Higher = better chart to keep when a song has several."""
    if meta_value(cho, "transpose") == "no":
        return -1000  # an as-is image PDF only wins if the song has no readable chart
    body = [l for l in cho.splitlines() if not l.startswith("{")]
    chords = sum(len(re.findall(r"\[[^\]*][^\]]*\]", l)) for l in body)
    lyric_chords = sum(1 for l in body if "[" in l and re.sub(r"\[[^\]]*\]", "", l).strip(" |.-:"))
    score = 0
    score += 0 if note else 100                     # readable lyrics
    score += 10 if directive(cho, "key") else 0     # key known
    score += min(lyric_chords, 60) / 3              # real chords-over-lyrics chart (up to +20)
    score += min(chords, 200) / 20                  # (up to +10)
    name = item["name"].lower()
    score += 5 if "chord" in name else 0            # SongSelect-style chord charts
    score -= 5 if re.search(r"capo|simplified|nashville|nns", name) else 0
    score -= 1 if doc2pdf.is_document(item["name"], item["mime"]) else 0  # a PDF of the same chart wins
    return score


class LocalSource:
    """Song folders downloaded from Drive, used instead of the Drive API. Read-only."""

    def __init__(self, folders):
        self.items, self.placeholders = [], []
        self.converted = Path(tempfile.mkdtemp(prefix="import-docs-"))  # Word files as PDFs
        for root in folders:
            root = Path(root).expanduser().resolve()
            if not root.is_dir():
                sys.exit(f"Not a folder: {root}")
            for f in sorted(root.rglob("*")):
                if not f.is_file():
                    continue
                folder = "/".join([root.name, *f.parent.relative_to(root).parts])
                if re.search(r"\.(?:pdf|docx?)\.icloud$", f.name, re.I):
                    # iCloud placeholder: the real file isn't on this Mac yet.
                    self.placeholders.append({"name": f.name[1:-len(".icloud")], "path": folder})
                elif (f.suffix.lower() == ".pdf" or doc2pdf.is_word(f.name)) and not f.name.startswith((".", "~$")):
                    self.items.append({"id": str(f), "local": True, "name": f.name, "path": folder,
                                       "mime": "application/pdf" if f.suffix.lower() == ".pdf" else "application/msword",
                                       "modified": str(int(f.stat().st_mtime))})

    def fetch(self, item):
        src = Path(item["id"])
        if not doc2pdf.is_word(item["name"]):
            return src
        out = self.converted / (hashlib.sha1(item["id"].encode()).hexdigest() + ".pdf")
        if not out.exists():
            out.write_bytes(doc2pdf.word_to_pdf(src.read_bytes(), item["name"]))
        return out

    def is_cached(self, item):
        return True


ORIGINAL_KEYS_CSV = "_original_keys.csv"
CSV_FIELDS = ["title", "original_key", "chart_key", "source", "slug"]


def write_original_keys_csv(library_dir):
    """library/_original_keys.csv: one row per song, for the team to fill in / correct.

    Existing values are never overwritten - only blanks are filled (from the charts) and
    rows are added for new songs - so the team's edits survive re-imports.
    """
    import csv
    path = library_dir / ORIGINAL_KEYS_CSV
    rows = {}
    if path.exists():
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = {r["slug"]: r for r in csv.DictReader(f) if r.get("slug")}
    for cho_file in library_dir.glob("*.cho"):
        text = cho_file.read_text(encoding="utf-8")
        slug = cho_file.stem
        row = rows.setdefault(slug, {"slug": slug, "original_key": "", "source": ""})
        row["title"] = directive(text, "title") or slug
        row["chart_key"] = directive(text, "key") or ""
        found = meta_value(text, "original_key")
        if found and not (row.get("original_key") or "").strip():
            row["original_key"], row["source"] = found, "chart"
    rows = {k: v for k, v in rows.items() if (library_dir / f"{k}.cho").exists()}
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows.values(), key=lambda r: r["title"].lower()):
            w.writerow(r)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--library", type=Path, default=Path(os.environ.get("LIBRARY_DIR", ROOT / "library")))
    ap.add_argument("--overwrite", action="store_true", help="replace songs that already exist in the library")
    ap.add_argument("--limit", type=int, help="only import the first N files (for a trial run)")
    ap.add_argument("--workers", type=int, default=2, help="parallel conversions (downloads are throttled anyway)")
    ap.add_argument("--no-crawl", action="store_true", help="reuse the last crawl instead of listing Drive again")
    ap.add_argument("--from-folder", action="append", type=Path, metavar="FOLDER",
                    help="import from a local copy of a Drive folder instead of Drive (repeatable)")
    args = ap.parse_args()

    report = []
    if args.from_folder:
        drive = LocalSource(args.from_folder)
        print(f"Reading {len(args.from_folder)} local folder(s).")
        report += [("skipped: not downloaded (iCloud placeholder)", it) for it in drive.placeholders]
    else:
        drive = DriveLibrary()
        if args.no_crawl and drive.items:
            print("Using the saved crawl.")
        else:
            print("Crawling Drive…")
            try:
                drive.crawl()
            except DriveError as e:
                sys.exit(f"Drive error: {e}")
    items = [it for it in drive.items if it["mime"] == "application/pdf" or it["name"].lower().endswith(".pdf")
             or doc2pdf.is_document(it["name"], it["mime"])]
    # Files you've removed from the library stay out: library/_exclude.txt lists them
    # ("folder/file.pdf" or just "file.pdf", one per line; # for comments).
    exclude_file = args.library / "_exclude.txt"
    excluded = {l.strip() for l in exclude_file.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.startswith("#")} if exclude_file.exists() else set()
    kept = []
    for it in items:
        if it["name"] in excluded or f"{it['path']}/{it['name']}" in excluded:
            report.append(("excluded: listed in _exclude.txt", it))
        else:
            kept.append(it)
    items = kept
    print(f"Found {len(items)} PDFs, Word files and Google Docs.")
    if args.limit:
        items = items[:args.limit]

    # Unique slugs from file names; a folder suffix only when two files share a name.
    # Songs imported earlier keep their slug (matched by source folder + file name).
    args.library.mkdir(parents=True, exist_ok=True)
    known = {}
    for cho_file in args.library.glob("*.cho"):
        text = cho_file.read_text(encoding="utf-8")
        known[cho_file.stem] = (meta_value(text, "source_folder"), meta_value(text, "source_file"))
    taken = set(known)
    for it in items:
        s = slugify(it["name"])
        if known.get(s) == (it["path"], it["name"]):
            it["slug"] = s  # this exact file was imported before
            continue
        if s in taken:
            s = f"{s}--{slugify(it['path'].split('/')[-1])}"
        n = 2
        while s in taken:
            s, n = f"{s}-{n}", n + 1
        taken.add(s)
        it["slug"] = s

    # Songs already in the library (from earlier runs, or added by hand) keep their place:
    # new charts of the same song become alternates.
    songs = SongIndex()
    as_is = {}  # slug -> keys available as PDFs, for songs that can't be transposed
    for cho_file in sorted(args.library.glob("*.cho")):
        text = cho_file.read_text(encoding="utf-8")
        songs.add(directive(text, "title") or cho_file.stem, song_artist(text), cho_file.stem)
        if meta_value(text, "transpose") == "no":
            keys = (meta_value(text, "keys") or directive(text, "key") or "").split(",")
            as_is[cho_file.stem] = {k.strip() for k in keys if k.strip()}

    digest = lambda p: hashlib.sha1(Path(p).read_bytes()).hexdigest()
    seen = {digest(p): p.stem for p in args.library.glob("*.pdf")}
    seen_lock = threading.Lock()
    stop = threading.Event()  # set when Google keeps rate-limiting: stop, resume later

    # Phase 1: download (threads, throttled) then convert (separate processes,
    # since PDF parsing isn't thread-safe). Nothing is written yet.
    def process(it):
        if (args.library / f"{it['slug']}.cho").exists() and not args.overwrite:
            return it, "exists", None
        if stop.is_set() and not drive.is_cached(it):  # already-downloaded files still convert
            return it, "pending", None
        try:
            pdf = drive.fetch(it)
            with seen_lock:
                h = digest(pdf)
                if h in seen and seen[h] != it["slug"]:
                    return it, f"duplicate: same file as {seen[h]}", None
                seen[h] = it["slug"]
        except RateLimited:
            stop.set()
            return it, "pending", None
        except Exception as e:
            return it, f"failed: {e}", None
        return it, "fetched", pdf

    fetched = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for it, result, pdf in pool.map(process, items):
            if pdf:
                fetched.append((it, pdf))
            else:
                report.append((result, it))

    candidates, original_keys = [], {}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [(it, pdf, pool.submit(convert_one, pdf, it)) for it, pdf in fetched]
        for i, (it, pdf, fut) in enumerate(futures, 1):
            try:
                cho, skipped, note = fut.result()
            except Exception as e:
                cho, skipped, note = None, None, None
                report.append((f"failed: {e}", it))
            if skipped:
                report.append((f"skipped: {skipped}", it))
            elif cho:
                candidates.append((it, {"pdf": pdf, "cho": cho, "note": note, "score": chart_score(it, cho, note)}))
            print(f"[{i}/{len(fetched)}] {'converted' if cho else 'skipped':<9} {it['path']}/{it['name']}")

    # Phase 2: one chart per song - the best-scoring one; the rest are listed as alternates.
    # As-is songs can't be transposed, so their other keys are kept as extra PDFs
    # (library/<slug>@<Key>.pdf) and the setlist picks the one for the chosen key.
    for it, cand in sorted(candidates, key=lambda c: -c[1]["score"]):
        title = directive(cand["cho"], "title") or it["slug"]
        existing = songs.find(title, song_artist(cand["cho"]))
        orig = meta_value(cand["cho"], "original_key")
        if orig:
            original_keys.setdefault(existing or it["slug"], (orig, it["name"]))
        if existing:
            key = name_key(it["name"])
            if existing in as_is and meta_value(cand["cho"], "transpose") == "no" and key and key not in as_is[existing]:
                shutil.copy(cand["pdf"], args.library / f"{existing}@{key}.pdf")
                as_is[existing].add(key)
                report.append((f"key version: {key} of {existing}", it))
            else:
                report.append((f"alternate: another chart of {existing}", it))
            continue
        cho_path = args.library / f"{it['slug']}.cho"
        cho_path.write_text(cand["cho"], encoding="utf-8")
        shutil.copy(cand["pdf"], cho_path.with_suffix(".pdf"))
        songs.add(title, song_artist(cand["cho"]), it["slug"])
        if meta_value(cand["cho"], "transpose") == "no":
            as_is[it["slug"]] = {k for k in [directive(cand["cho"], "key")] if k}
        note = cand["note"]
        report.append((note if note and note.startswith("as-is") else f"imported: {note}" if note else "imported", it))

    # Original keys found on any chart of a song (incl. alternates) go on the kept song.
    for slug, (orig, from_file) in original_keys.items():
        cho_path = args.library / f"{slug}.cho"
        if not cho_path.exists():
            continue
        text = cho_path.read_text(encoding="utf-8")
        if meta_value(text, "original_key") is None:
            text = re.sub(r"^(\{title:.*\})$", lambda m: f"{m[1]}\n{{meta: original_key {orig}}}", text, count=1, flags=re.M)
            cho_path.write_text(text, encoding="utf-8")
    write_original_keys_csv(args.library)

    # Record each as-is song's available keys in its .cho.
    for slug, keys in as_is.items():
        cho_path = args.library / f"{slug}.cho"
        text = cho_path.read_text(encoding="utf-8")
        line = f"{{meta: keys {','.join(sorted(keys, key=lambda k: ct.parse_key(k)[0]))}}}"
        if len(keys) > 1 and line not in text:
            text = re.sub(r"^\{meta: keys .*\}\n", "", text, flags=re.M)
            text = text.replace("{meta: transpose no}", "{meta: transpose no}\n" + line)
            cho_path.write_text(text, encoding="utf-8")

    summary = {}
    for result, _ in report:
        summary[result.split(":")[0]] = summary.get(result.split(":")[0], 0) + 1
    with open(args.library / "_import_report.txt", "w", encoding="utf-8") as f:
        f.write("Import report: " + ", ".join(f"{v} {k}" for k, v in sorted(summary.items())) + "\n\n")
        for result, it in sorted(report, key=lambda r: (r[0], r[1]["path"], r[1]["name"])):
            if result not in ("imported", "exists", "pending") and not result.startswith("key version"):
                f.write(f"{result}\t{it['path']}/{it['name']}\n")
    print("\nDone: " + ", ".join(f"{v} {k}" for k, v in sorted(summary.items())))
    if stop.is_set():
        print("Stopped early: Google is rate-limiting downloads. Re-run later with --no-crawl to resume.")
    print(f"Skipped, failed and alternate charts are listed in {args.library / '_import_report.txt'}")


if __name__ == "__main__":
    main()
