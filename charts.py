"""Charts as the three-source library sees them: what one uploaded file is, and how two compare.

analyse() turns one downloaded chart (a PDF; Word files and Google Docs arrive as PDFs) into a
record: its ChordPro conversion (import_drive.convert_one, the same converter as always), or
why it was skipped; its title, artist and key; its page count; and two fingerprints:

  same music   the lyrics, and the chord progression in order, whatever the key, the layout,
               the word a chord sits over, or how much detail the chords are written in. Two
               charts are the same music when most of one's lyric runs turn up in the other and
               its progression lines up with the other's (so a 1-page chart with its repeats
               written once matches the 2-page one, and a hand-typed G - D - Em matches
               SongSelect's G - D/F# - D - Em).
  same shape   how the chart sits on the page, key aside: each lyric line's page, height and
               words, with chords left out. An as-is chart can't be read, so its pages are
               compared as small pictures instead.

Records are plain JSON, kept with the local copy so a file is analysed once per version.
"""

import re
import unicodedata
import zlib

import chordtools as ct
import pdf2chordpro as p2c

# Bump when the analysis changes, so every chart is analysed again.
ANALYSIS_VERSION = 1

# Same music: the progression lines up well and the lyrics match reasonably, or the progression
# fairly and the lyrics well. (Tuned on the real sources, where a PDF and the Word file it came
# from, or a hand-typed chart and its SongSelect export, are one arrangement.)
SAME_MUSIC = ((0.8, 0.4), (0.6, 0.7))  # (progression, lyrics)
SHORT = 6            # a progression of fewer chords than this is measured against the longer one
MUSIC_VERSION = 4
SAME_SHAPE = 0.85    # share of lyric lines that must sit in the same place
SAME_PICTURE = 0.1   # share of a page picture's bits that may differ
MAX_PAGES = 12       # longer than this is a songbook, not a chart: left out (and too big to read)


# ------------------------------------------------------------------ same music

def _word_tokens(text):
    """Lyric words: Latin words lowercased, each Chinese character on its own, Simplified."""
    text = p2c.to_simplified(unicodedata.normalize("NFKC", text)).casefold()
    return re.findall(r"[㐀-鿿]|[a-z0-9']+", text)


def _chord_root(chord):
    m = ct.ROOT_RE.match(chord.strip("()"))
    if not m:
        return None
    root = ct.PC[m[1] + ct._acc(m[2])]
    minor = m[3].startswith("m") and not m[3].startswith("maj")
    return root, minor


def _hash(text):
    return zlib.crc32(text.encode())


def music_fingerprint(cho):
    """{"lyrics": hashed runs of 4 words, "chords": the chord roots in order, [pitch class, minor]}.

    A chord on the same root as the one before (D/F# then D, D7sus then D7) is left out: it's the
    same step of the harmony written in more detail, as SongSelect does and hand-typed charts don't."""
    words, roots = [], []
    for line in cho.splitlines():
        if line.lstrip().startswith("{"):
            continue
        for chord, text in _segments(line):
            if chord and not chord.startswith("*") and (root := _chord_root(chord)) and (not roots or roots[-1] != root):
                roots.append(root)
            words += _word_tokens(text)
    runs = sorted({_hash(" ".join(words[i:i + 4])) for i in range(max(0, len(words) - 3))})
    return {"version": MUSIC_VERSION, "lyrics": runs, "chords": [list(r) for r in roots]}


def _segments(line):
    parts = re.split(r"\[([^\]]*)\]", line)
    yield None, parts[0]
    for i in range(1, len(parts), 2):
        yield parts[i], parts[i + 1]


def _containment(a, b):
    """How much of the smaller set is in the other one (1.0 if both are empty)."""
    if not a or not b:
        return 1.0 if not a and not b else 0.0
    return len(a & b) / min(len(a), len(b))


def progression_match(a, b):
    """How much of the shorter progression lines up, in order, with the other (0-1), in whichever
    key fits best; a few chords one chart has and the other doesn't are skipped over."""
    import difflib
    if not a or not b:
        return 1.0 if not a and not b else 0.0
    theirs = [tuple(c) for c in b]
    lined_up = max(sum(m.size for m in difflib.SequenceMatcher(
        None, [((p + shift) % 12, minor) for p, minor in a], theirs, autojunk=False).get_matching_blocks())
        for shift in range(12))
    shorter = min(len(a), len(b))
    return lined_up / (shorter if shorter >= SHORT else max(len(a), len(b)))


def same_music(a, b):
    """Are two charts' music fingerprints the same music? (See SAME_MUSIC.)"""
    lyrics = _containment(set(a["lyrics"]), set(b["lyrics"]))
    progression = progression_match(a["chords"], b["chords"])
    return any(progression >= p and lyrics >= l for p, l in SAME_MUSIC)


# ------------------------------------------------------------------ same shape

def text_shape(lines):
    """The lyric lines of a readable chart: [page, height (to 2 points), left edge (to 10 points),
    hash of its words]."""
    out = set()
    for ln in lines:
        words = [t for t in ln.tokens if not p2c.is_chord(t)]
        if not words or p2c.is_chord_tokens(ln.tokens):
            continue
        left = min(w["x0"] for w in ln.words)
        out.add((ln.page, round(ln.top / 2), round(left / 10), _hash(" ".join(_word_tokens(" ".join(words))))))
    return sorted(out)


def picture_shape(pdf_path, pages=4):
    """Each page as a 256-bit picture (a difference hash), for charts that can't be read."""
    import pypdfium2 as pdfium
    hashes = []
    with p2c.PDF_LOCK:
        doc = pdfium.PdfDocument(str(pdf_path))
        try:
            for i in range(min(len(doc), pages)):
                img = doc[i].render(scale=0.25).to_pil().convert("L").resize((17, 16))
                px = img.tobytes()
                bits = 0
                for row in range(16):
                    for col in range(16):
                        bits = bits << 1 | (px[row * 17 + col] > px[row * 17 + col + 1])
                hashes.append(f"{bits:064x}")
        finally:
            doc.close()
    return hashes


def same_shape(a, b):
    """Do two charts look the same on the page, key aside? `a` and `b` are analysis records."""
    if a.get("md5") and a.get("md5") == b.get("md5"):
        return True
    if a["pages"] != b["pages"] or a["as_is"] != b["as_is"]:
        return False
    if a["as_is"]:
        pa, pb = a["shape"], b["shape"]
        return bool(pa) and len(pa) == len(pb) and all(
            bin(int(x, 16) ^ int(y, 16)).count("1") <= 256 * SAME_PICTURE for x, y in zip(pa, pb))
    la, lb = {tuple(x) for x in a["shape"]}, {tuple(x) for x in b["shape"]}
    return bool(la) and len(la & lb) / max(len(la), len(lb)) >= SAME_SHAPE


# ------------------------------------------------------------------ one chart

GENERIC_FOLDER = re.compile(r"^(?:chords?|lead(?: sheets?)?|lyrics?|words?|pdfs?|charts?|chord charts?|"
                            r".*\bversion|keys?|old|archive|scores?)$", re.I)
CATEGORY_FOLDER = re.compile(r"^\d+[.\s]|^zz\.|songs$|scores$|hymns$|songbooks?$", re.I)


def file_path(chart):
    """A chart's file as its folders inside its source, then its name: '3. English Songs/Holy Ground.pdf'."""
    return "/".join(p for p in (chart.get("path"), chart.get("name")) if p)


def song_folder(path):
    """The song folder a file sits in ('ASE/Living Hope/Chords' -> 'Living Hope'), if any.
    `path` is its folders inside the source; category folders ('2. Chinese Songs') don't count."""
    for name in reversed([p for p in path.split("/") if p]):
        if GENERIC_FOLDER.match(name.strip()):
            continue
        return None if CATEGORY_FOLDER.search(name.strip()) else name.strip()
    return None


# Words after a title that aren't the title: "Gratitude - Brandon Lake", "Joy To The World Chords".
TITLE_TAIL = re.compile(r"\s+[-–—]\s+.*$|\s+(?:acoustic\s+|guitar\s+)?chords?\b.*$", re.I)


def song_title_keys(title):
    """The loose identities a chart's title matches songs by: import_drive.title_keys, and the
    same without a trailing " - artist" or "... Chords" (a match on any of them = same song)."""
    from import_drive import title_keys
    keys = title_keys(title)
    trimmed = TITLE_TAIL.sub("", title).strip()
    if trimmed and trimmed != title:
        keys += [k for k in title_keys(trimmed) if k not in keys]
    return keys


CHORDS_TAIL = re.compile(r"\s+(?:acoustic\s+|guitar\s+)?chords?\b.*$", re.I)


def better_title(title, path, name):
    """A chart's title as the site shows it: its song folder's name when the title is only the code
    its file is named by ('FORALLY1.pdf' -> 'Forally1') and shares no word with the folder's;
    otherwise without a trailing "Chords..." ('Fall Chords') and with underscores as spaces."""
    from drive import KEY_IN_NAME
    folder = song_folder(path or "")
    stem = KEY_IN_NAME.sub("", re.sub(r"\.(?:pdf|docx?)$", "", name or "", flags=re.I)).strip(" -_")
    is_code = bool(re.fullmatch(r"[A-Z]{4,}|[A-Za-z]{3,}\d+", stem))
    if (folder and is_code and (title or "").replace(" ", "").lower() == stem.lower()
            and not set(re.findall(r"[a-z]+", title.lower())) & set(re.findall(r"[a-z]+", folder.lower()))):
        return folder
    tidy = re.sub(r"\s*_+\s*", " ", title or "").strip()
    return CHORDS_TAIL.sub("", tidy).strip() or tidy or title


def _directive(cho, name):
    m = re.search(r"^\{" + name + r":\s*(.*?)\s*\}$", cho, re.M)
    return m[1] if m else None


def _meta(cho, name):
    m = re.search(r"^\{meta:\s*" + name + r"\s+(.*?)\s*\}$", cho, re.M)
    return m[1] if m else None


def page_count(pdf_path):
    from pypdf import PdfReader
    try:
        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        return 1


def analyse(pdf_path, item):
    """One chart file -> its record (see the module docstring), with its ChordPro as "cho".

    `item` is the Drive file: {"id", "name", "path" (its folders inside the source), "mime"}.
    """
    from import_drive import convert_one, title_from_name
    pages = page_count(pdf_path)
    if pages > MAX_PAGES:
        return {"skipped": f"a book of many songs ({pages} pages): left out", "version": ANALYSIS_VERSION}
    try:
        cho, skipped, _ = convert_one(pdf_path, item)
    except Exception as e:
        cho, skipped = None, f"conversion failed ({e})"
    if not cho:
        return {"skipped": skipped or "not a chord chart", "version": ANALYSIS_VERSION}
    as_is = _meta(cho, "transpose") == "no"
    title = _directive(cho, "title") or title_from_name(item["name"])
    if as_is and (folder := song_folder(item.get("path", ""))):
        title = folder  # an as-is chart's title only comes from its file name; the folder says it better
        cho = re.sub(r"^\{title:.*\}$", lambda _: f"{{title: {title}}}", cho, count=1, flags=re.M)
    key = ct.parse_key(_directive(cho, "key") or "")
    record = {
        "version": ANALYSIS_VERSION,
        "cho": cho,
        "title": title,
        "artist": _directive(cho, "artist") or _directive(cho, "composer") or "",
        "key": ct.key_name(*key) if key else None,
        "as_is": as_is,
        "lyrics_ok": _meta(cho, "lyrics") != "unreadable",
        "original_key": _meta(cho, "original_key"),
        "pages": pages,
        "music": None if as_is else music_fingerprint(cho),
    }
    if as_is:
        record["shape"] = picture_shape(pdf_path)
    else:
        record["shape"] = text_shape(p2c.extract_lines(pdf_path))
    return record

