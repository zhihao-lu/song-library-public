#!/usr/bin/env python3
"""Convert chord-over-lyric song sheet PDFs to ChordPro.

Usage:
    pdf2chordpro.py song.pdf [more.pdf ...]      # writes song.cho next to each PDF
    pdf2chordpro.py songs/ -o out/               # every PDF in a folder, into out/
    pdf2chordpro.py song.pdf --stdout            # print instead of writing

Chords are placed by matching each chord's x-coordinate to the closest lyric
character, so proportional fonts line up correctly. Scanned PDFs are OCR'd
first with ocrmypdf if it is installed (use --ocr to force it).
"""

import argparse
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------

_ACC = r"(?:\#|b|♯|♭)"
CHORD_RE = re.compile(
    rf"""^\(?
    [A-G]{_ACC}?                                   # root
    (?:maj|min|mi|m|M|dim|aug|sus|add|°|ø|\+|-|Δ)? # quality
    \d{{0,2}}                                      # extension
    (?:sus\d{{0,2}}|(?:add|maj|no|{_ACC}|\+|-)\d{{1,2}})*  # alterations
    (?:\([^)]*\))?                                 # parenthesised alterations
    (?:/[A-G]{_ACC}?)?                             # slash bass
    \)?\*?$""",
    re.X,
)
# Tokens allowed on a chord line that aren't chords themselves.
FILLER_RE = re.compile(r"^(?:\|+:?|:?\|+|/+|-+|\.+|%|N\.?C\.?|\(?x\d+\)?|\(?\d+x\)?)$", re.I)

SECTION_WORDS = (
    r"verse|chorus|pre[- ]?chorus|bridge|intro|outro|interlude|instrumental|tag|"
    r"refrain|coda|ending|solo|hook|vamp|turnaround|breakdown|post[- ]?chorus|ending|"
    r"主歌|副歌|桥段|橋段|前奏|间奏|間奏|尾奏|结尾|結尾|导歌|導歌|过门|過門|预副歌|預副歌|独奏|獨奏|和声|和聲"
)
# Chinese section names -> the English kinds used for ChordPro environments.
CN_SECTIONS = {"主歌": "verse", "副歌": "chorus", "桥段": "bridge", "橋段": "bridge", "前奏": "intro",
               "间奏": "interlude", "間奏": "interlude", "尾奏": "outro", "结尾": "ending", "結尾": "ending",
               "导歌": "prechorus", "導歌": "prechorus", "预副歌": "prechorus", "預副歌": "prechorus",
               "过门": "turnaround", "過門": "turnaround", "独奏": "solo", "獨奏": "solo"}
HEADING_RE = re.compile(
    rf"""^[\[(]?\s*
    (?P<repeat>repeat\s+)?
    (?P<name>{SECTION_WORDS})
    (?:\s*(?P<num>\d+[a-dA-D]?))?                    # "Verse 1", "VERSE 1B"
    (?:\s*(?P<alt>{SECTION_WORDS})(?:\s*\d+[a-dA-D]?)?)?   # bilingual: "副歌 Chorus"
    \s*[\])]?\s*:?\s*
    (?P<times>\(?\s*x\s*\d+\s*\)?|\(?\s*\d+\s*x\s*\)?)?
    \s*(?:\((?P<note>[^)]*)\)|[–—-]\s*(?P<note2>[^\]]+?)\s*\]?)?
    \s*$""",
    re.I | re.X,
)
# A section label as the first token of a chord line: "Intro:", "[Bridge]", "前奏:"
LABEL_TOKEN_RE = re.compile(rf"^[\[(]?(?:{SECTION_WORDS})(?:\s*\d+[a-dA-D]?)?[\])]?[:：]?$", re.I)
# Same, but allowing trailing chords: "Intro: G D Em C"
HEADING_PREFIX_RE = re.compile(
    rf"^[\[(]?\s*(?P<label>(?:{SECTION_WORDS})(?:\s*\d+)?)\s*[\])]?\s*:?\s+(?P<rest>.+)$", re.I
)

META_RE = re.compile(
    r"\b(?P<k>key|capo|tempo|bpm|time(?:\s+signature)?|artist|author|words|music|"
    r"words\s+(?:and|&)\s+music|composer|lyricist|album|year|ccli(?:\s+(?:song\s+)?(?:#|no\.?|number))?)"
    r"\s*[:\-=]\s*(?P<v>.+?)(?=\s{2,}\S+\s*[:\-=]|\s+(?:key|capo|tempo|bpm|time)\s*:|$)",
    re.I,
)
LICENSE_RE = re.compile(r"songselect|ccli\.com|all rights reserved|used by permission|terms of use", re.I)
CCLI_RE = re.compile(r"^\s*CCLI\s+(Song|License)\s*(?:#|No\.?|Number)?\s*:?\s*(\d+)\s*$", re.I)
KEY_OF_RE = re.compile(r"^\s*(?:in\s+the\s+)?key\s+of\s+([A-G][#b♯♭]?m?)\b", re.I)
BY_RE = re.compile(r"^\s*(?:by|written\s+by|(?:words\s*(?:and|&)\s*music|music\s*(?:and|&)\s*words|w/m)\s*(?:by)?\s*:?)\s*(.+)$", re.I)
COPYRIGHT_RE = re.compile(r"^\s*(?:©|\(c\)|copyright\b)", re.I)
PAGE_NUM_RE = re.compile(r"^\s*(?:page\s*)?-?\s*\d+\s*(?:of\s*\d+)?\s*-?\s*$", re.I)

META_MAP = {
    "key": "key", "capo": "capo", "tempo": "tempo", "bpm": "tempo",
    "time": "time", "time signature": "time", "artist": "artist",
    "author": "lyricist", "words": "lyricist", "lyricist": "lyricist",
    "music": "composer", "composer": "composer", "album": "album", "year": "year",
}


# Rhythm/strum marks written after a chord: "F\\", "C/E\\", "G.", "Am'", "D>", "C/", "Bb_"
RHYTHM_MARKS_RE = re.compile(r"(?:[\\.'*>~_]|/(?![A-G]))+$")


def split_marks(tok: str):
    """'C/E\\\\' -> ('C/E', '\\\\'); a chord without marks -> (tok, '')."""
    m = RHYTHM_MARKS_RE.search(tok)
    if m and m.start() > 0:
        return tok[:m.start()], tok[m.start():]
    return tok, ""


def is_chord(tok: str) -> bool:
    return bool(CHORD_RE.match(tok)) or bool(CHORD_RE.match(split_marks(tok)[0]))


def chord_line_groups(tokens):
    """Classify a line's tokens as chord / filler / annotation groups.

    Returns [(kind, text, first_idx)] or None if the line isn't a chord line.
    Parenthesised non-chord words ("(Last x)") become one annotation group.
    """
    groups, i = [], 0
    if tokens and len(tokens) > 1 and LABEL_TOKEN_RE.match(tokens[0]):
        groups.append(("label", tokens[0], 0))
        i = 1
    while i < len(tokens):
        t = tokens[i]
        if is_chord(t):
            groups.append(("chord", split_marks(t.rstrip("*"))[0], i))
        elif FILLER_RE.match(t):
            groups.append(("filler", t, i))
        elif t.startswith("("):
            j = next((j for j in range(i, min(i + 6, len(tokens))) if tokens[j].endswith(")")), None)
            if j is None:
                return None
            groups.append(("annot", " ".join(tokens[i:j + 1])[1:-1].strip(), i))
            i = j
        else:
            return None
        i += 1
    return groups if any(k == "chord" for k, _, _ in groups) else None


def is_chord_tokens(tokens) -> bool:
    return chord_line_groups(tokens) is not None


# --------------------------------------------------------------------------
# PDF -> lines with geometry
# --------------------------------------------------------------------------

CJK_RE = re.compile(r"[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]")


def is_cjk(ch):
    return bool(ch) and bool(CJK_RE.match(ch))


_T2S = []


def to_simplified(text):
    """Traditional -> Simplified Chinese, for matching and search only (永恆 -> 永恒)."""
    if not CJK_RE.search(text or ""):
        return text
    if not _T2S:
        from opencc import OpenCC
        _T2S.append(OpenCC("t2s"))
    return _T2S[0].convert(text)


def _needs_space(prev, word):
    """Join two adjacent words with a space only if there's a visible gap.

    Latin words always had a gap (that's why they were split). Chinese/Japanese/
    Korean text has no spaces between characters, so words split by a font
    change or zero-width gap are joined directly; a gap of a fifth of a
    character or more (a typed space, or a pause in the lyric) is kept.
    """
    gap = word["x0"] - prev["x1"]
    size = max(prev.get("size", 10), word.get("size", 10))
    if is_cjk(prev["text"][-1:]) or is_cjk(word["text"][:1]):
        return gap > 0.2 * size
    return gap > 0.1 * size


@dataclass
class Line:
    words: list                      # pdfplumber word dicts (with "chars")
    top: float
    bottom: float
    size: float
    page: int
    gap_before: float = 0.0
    page_h: float = 0.0
    text: str = field(init=False)

    def __post_init__(self):
        self.words.sort(key=lambda w: w["x0"])
        # Pieces with no visible gap between them are one word: a chord like "Dm" with
        # a smaller "m", or text split by a font change. (Otherwise "D" + "m" isn't a chord.)
        merged = []
        for w in self.words:
            if merged and not _needs_space(merged[-1], w):
                prev = merged[-1]
                merged[-1] = {**prev, "text": prev["text"] + w["text"], "x1": max(prev["x1"], w["x1"]),
                              "top": min(prev["top"], w["top"]), "bottom": max(prev["bottom"], w["bottom"]),
                              "size": max(prev["size"], w["size"]), "chars": prev["chars"] + w["chars"]}
            else:
                merged.append(w)
        self.words = merged
        self.text = " ".join(w["text"] for w in self.words)

    @property
    def tokens(self):
        return [w["text"] for w in self.words]

    def char_positions(self):
        """Return (text, xs) where xs[i] is the left x of text[i]."""
        chars, xs = [], []
        for i, w in enumerate(self.words):
            if i:
                chars.append(" ")
                xs.append(self.words[i - 1]["x1"])
            for c in w["chars"]:
                t = c["text"]  # may be >1 char after ligature expansion
                for k, ch in enumerate(t):
                    chars.append(ch)
                    xs.append(c["x0"] + k * (c["x1"] - c["x0"]) / len(t))
        return "".join(chars), xs


def _group_lines(words, page_no):
    words = sorted(words, key=lambda w: (round(w["top"]), w["x0"]))
    lines, cur = [], []
    for w in words:
        if cur and abs(w["top"] - cur[0]["top"]) > max(2.0, 0.4 * cur[0]["size"]):
            lines.append(cur)
            cur = []
        cur.append(w)
    if cur:
        lines.append(cur)
    return [
        Line(ws, min(w["top"] for w in ws), max(w["bottom"] for w in ws),
             max(w["size"] for w in ws), page_no)
        for ws in lines
    ]


def _find_gutter(words, width):
    """Return x of a vertical gutter splitting two columns, or None."""
    if len(words) < 6:
        return None
    lo, hi = int(width * 0.3), int(width * 0.7)
    covered = [0] * (int(width) + 2)
    for w in words:
        for x in range(max(0, int(w["x0"])), min(len(covered), int(w["x1"]) + 1)):
            covered[x] += 1
    best, run_start, best_len = None, None, 0
    for x in range(lo, hi + 1):
        if covered[x] == 0:
            run_start = x if run_start is None else run_start
            if x - run_start > best_len:
                best_len, best = x - run_start, (run_start + x) / 2
        else:
            run_start = None
    if best_len < 15:
        return None
    # Both sides must actually hold a meaningful amount of text.
    left = sum(1 for w in words if w["x1"] < best)
    right = len(words) - left
    return best if min(left, right) >= max(3, 0.2 * len(words)) else None


def _gutter_near(rows, g, reach=20, min_gap=6):
    """x of a column gap near a neighbouring band's gutter `g`, or None.

    The neighbour confirms a two-column layout, so a narrower gap than
    _find_gutter asks for will do: a long lyric line can end a few points short
    of the next column. Spaces between words can line up by chance too, so the
    band must look like two columns: at least 4 rows, most with text on both
    sides, and most right-hand rows starting at the same x (the column's edge).
    """
    words = [w for ln in rows for w in ln.words]
    if len(rows) < 4:
        return None
    lo = max(0, int(g - reach))
    covered = [False] * (2 * reach + 2)
    for w in words:
        for x in range(max(lo, int(w["x0"])), min(lo + len(covered), int(w["x1"]) + 1)):
            covered[x - lo] = True
    best, run_start, best_len = None, None, -1
    for i, c in enumerate(covered):
        if not c:
            run_start = i if run_start is None else run_start
            if i - run_start > best_len:
                best_len, best = i - run_start, lo + (run_start + i) / 2
        else:
            run_start = None
    if best is None or best_len < min_gap:
        return None
    both = sum(1 for ln in rows if any(w["x1"] < best for w in ln.words) and any(w["x0"] > best for w in ln.words))
    starts = [min(w["x0"] for w in ln.words if w["x0"] > best) for ln in rows if any(w["x0"] > best for w in ln.words)]
    edge = min(starts, default=0)
    aligned = sum(1 for x in starts if x - edge < 3)
    return best if both >= 0.5 * len(rows) and aligned >= 0.5 * len(starts) else None


NEW_BLOCK = 1e9  # gap_before marker: first line of a page or column


def _page_words(page):
    """Extract words, joining superscripts onto the chord they belong to.

    Sheets like SongSelect print F², G⁽⁴⁾, Dm⁷⁽⁴⁾ with the extension in a
    smaller, raised font. Left alone, those characters sit on a different
    baseline and become a separate "line". Here each raised small character
    directly right of a bigger one (or of another superscript) is moved onto
    its base character's line. The original geometry is kept in _top/_bottom
    and _sup marks it, so faithful re-rendering can reproduce the superscript.
    """
    # Some PDFs fake bold by printing each character several times, slightly
    # offset; keep one copy of each.
    chars = [dict(c) for c in page.dedupe_chars(tolerance=0.1 * 12).chars]
    by_x1 = {}
    for c in chars:
        c["_top"], c["_bottom"], c["_sup"] = c["top"], c["bottom"], False
        by_x1.setdefault(int(c["x1"]), []).append(c)
    for c in sorted(chars, key=lambda c: c["x0"]):
        base = None
        # Tolerances scale with font size so other sheets' spacing still matches.
        reach = 0.25 * c["size"]
        for k in range(int(c["x0"] - reach) - 1, int(c["x0"]) + 2):
            for b in by_x1.get(k, ()):
                if b is c or not (c["x0"] - reach <= b["x1"] <= c["x0"] + 0.1 * c["size"]):
                    continue
                if (b["_sup"] and abs(b["size"] - c["size"]) < 0.05 * c["size"]
                        and abs(b["_top"] - c["_top"]) < 0.1 * c["size"]):
                    base = b["_base"]  # continuing a superscript run
                elif (not b["_sup"] and c["size"] <= b["size"] * 0.9
                      and b["top"] < c["bottom"] < b["bottom"] - 0.12 * b["size"]):
                    base = b
                if base:
                    break
            if base:
                break
        if base:
            c["_sup"], c["_base"] = True, base
            c["top"], c["bottom"], c["doctop"] = base["top"], base["bottom"], base["doctop"]
    words = pdfplumber.utils.extract_words(
        chars, x_tolerance=2, y_tolerance=3, keep_blank_chars=False, return_chars=True)
    for w in words:
        sizes = [c["size"] for c in w["chars"] if not c["_sup"]] or [c["size"] for c in w["chars"]]
        w["size"] = max(sizes)
    return words


def _clean_word(w):
    """Return the word as a list of words (usually just itself). Expand ligatures (ﬀ -> ff) and fix Word/Calibri's 'ft' ligature extracting as 'V'."""
    chars = w["chars"]
    for i, c in enumerate(chars):
        t = unicodedata.normalize("NFKC", c["text"])
        if t == "\x00":
            t = "fi"  # unmapped glyph in Type3 fonts; almost always the fi ligature
        if re.fullmatch(r"\(cid:\d+\)", t):
            c["_cid"] = True  # glyph without a text mapping; counted by text_readable()
            t = " "  # inside readable text it's a space: split the word there (below)
        if (t == "V" and 0 < i < len(chars) - 1
                and chars[i - 1]["text"].islower() and chars[i + 1]["text"].islower()):
            t = "ft"
        c["text"] = t
    # Some fonts draw "ti" as one glyph that extracts as = % or +: "Eleva=on", "Beau%ful".
    for i in range(1, len(chars) - 1):
        if chars[i]["text"] in ("=", "%", "+") and chars[i - 1]["text"][-1:].islower() and chars[i + 1]["text"][:1].islower():
            chars[i]["text"] = "ti"
    w["text"] = "".join(c["text"] for c in chars)
    if " " not in w["text"].strip() or all(c.get("_cid") for c in chars):
        return [w]
    # Split at the space-like glyphs, keeping the unmapped glyphs on the pieces
    # (as empty text) so text_readable() can still count them.
    pieces, cur, spare = [], [], []
    for c in chars:
        if c.get("_cid") or c["text"] == " ":
            c["text"] = ""
            if cur:
                cur.append(c)  # the glyph ends the word before it (kept only for counting)
                pieces.append(cur)
                cur = []
            else:
                spare.append(c)
        else:
            cur.append(c)
    if cur:
        pieces.append(cur)
    if not pieces:
        return [w]
    pieces[0] = spare + pieces[0] if spare and not pieces[0][0]["text"] else pieces[0]
    out = []
    for cs in pieces:
        real = [c for c in cs if c["text"]]
        out.append({**w, "text": "".join(c["text"] for c in real), "chars": real,
                    "x0": real[0]["x0"], "x1": real[-1]["x1"], "_glyphs": cs})
    return out


def _bands(rows):
    """Split full-width rows into vertical bands at blank-line-sized gaps."""
    bands = [[rows[0]]]
    for prev, ln in zip(rows, rows[1:]):
        if ln.top - prev.bottom > 0.9 * prev.size:
            bands.append([])
        bands[-1].append(ln)
    return bands


# pdfplumber/pdfminer isn't thread-safe: parallel reads in one process can come
# back empty. Every PDF read in this process goes through this lock; the import
# uses separate processes for parallelism instead.
PDF_LOCK = threading.RLock()


def extract_lines(pdf_path, columns="auto", stats=None):
    with PDF_LOCK:
        return _extract_lines(pdf_path, columns, stats)


def _extract_lines(pdf_path, columns="auto", stats=None):
    """Return the PDF's text lines in reading order, with geometry.

    Columns are detected per band (a run of lines between blank gaps), so a
    page can mix side-by-side sections (Verse 1 | Verse 2) with full-width ones.
    If a `stats` dict is passed, it's filled with page size, column count and
    whether chords use superscripts, for matching the original's layout.
    """
    out = []
    if stats is not None:
        stats.update(columns=1, superscripts=False)
    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages):
            if stats is not None and page_no == 0:
                stats["page_size"] = (float(page.width), float(page.height))
            words = _page_words(page)
            if stats is not None and any(c["_sup"] for w in words for c in w["chars"]):
                stats["superscripts"] = True
            if not words:
                continue
            words = [piece for w in words for piece in _clean_word(w)]
            if not words:
                continue
            rows = _group_lines(words, page_no)
            if columns == "1":
                regions = [(rows, None)]
            elif columns == "2":
                regions = [(rows, page.width / 2)]
            else:
                regions = []
                bands = _bands(rows)
                gutters = [_find_gutter([w for ln in b for w in ln.words], page.width) for b in bands]
                # A band with no gutter of its own borrows a neighbour's: a short
                # one (a row of two headings) has too few words to find one, and
                # in a long one a lyric line can nearly touch the next column.
                for bi, band in enumerate(bands):
                    if gutters[bi]:
                        continue
                    ws = [w for ln in band for w in ln.words]
                    for g in (gutters[bi - 1] if bi else None, gutters[bi + 1] if bi + 1 < len(bands) else None):
                        if not g:
                            continue
                        if (not any(w["x0"] < g < w["x1"] for w in ws)
                                and any(w["x1"] < g for w in ws) and any(w["x0"] > g for w in ws)):
                            gutters[bi] = g
                            break
                        near = _gutter_near(band, g)
                        if near:
                            gutters[bi] = near
                            break
                for band, g in zip(bands, gutters):
                    # Consecutive 2-column bands with the same gutter are one
                    # column layout: read the whole left side, then the right.
                    if g and regions and regions[-1][1] and abs(regions[-1][1] - g) < 30:
                        regions[-1] = (regions[-1][0] + band, regions[-1][1])
                    else:
                        regions.append((band, g))

            page_lines = []
            for region_rows, gutter in regions:
                ws = [w for ln in region_rows for w in ln.words]
                cols = ([[w for w in ws if w["x1"] <= gutter], [w for w in ws if w["x1"] > gutter]]
                        if gutter else [ws])
                for ci, col in enumerate(cols):
                    col_lines = _group_lines(col, page_no)
                    if not col_lines:
                        continue
                    if ci > 0 or not page_lines:
                        col_lines[0].gap_before = NEW_BLOCK
                    else:
                        col_lines[0].gap_before = col_lines[0].top - max(l.bottom for l in page_lines)
                    for prev, ln in zip(col_lines, col_lines[1:]):
                        ln.gap_before = ln.top - prev.bottom
                    page_lines.extend(col_lines)
            for ln in page_lines:
                ln.page_h = page.height
            out.extend(page_lines)
            if stats is not None:
                two_col = sum(len(r) for r, g in regions if g)
                if two_col >= 0.5 * sum(len(r) for r, _ in regions):
                    stats["columns"] = 2
    if stats is not None and out:
        # Body font size: the size most characters are set in.
        weights = {}
        for ln in out:
            for w in ln.words:
                weights[round(w["size"], 1)] = weights.get(round(w["size"], 1), 0) + len(w["text"])
        stats["font_size"] = max(weights, key=weights.get)
    return out


def is_order_line(text):
    """A song-order/roadmap line like 'Intro, Verse 1, Chorus, Verse 2, ...'."""
    return "," in text and len(re.findall(rf"\b(?:{SECTION_WORDS})\b", text, re.I)) >= 3


def pull_order_lines(lines):
    """Remove song-order lines (often repeated on every page); return (lines, order_text)."""
    order, seen, kept = [], set(), []
    for ln in lines:
        if is_order_line(ln.text) or (order and ln.page == order[-1][0]
                                      and ln.gap_before < ln.size and is_order_line(order[-1][1] + " " + ln.text)
                                      and ln.text[:1].isdigit()):
            if ln.text not in seen:
                seen.add(ln.text)
                order.append((ln.page, ln.text))
            continue
        kept.append(ln)
    text = " ".join(t for _, t in order).strip(" ,")
    return kept, text


def strip_repeated_headers(lines):
    """Drop page numbers and header/footer lines that repeat on several pages."""
    pages = sorted({ln.page for ln in lines})
    norm = lambda s: re.sub(r"\d+", "#", s.strip().lower())
    edge_counts = {}
    if len(pages) > 1:
        for p in pages:
            pl = [ln for ln in lines if ln.page == p]
            for ln in pl[:2] + pl[-2:]:
                edge_counts.setdefault(norm(ln.text), set()).add(p)
    kept = []
    for ln in lines:
        n = norm(ln.text)
        if PAGE_NUM_RE.match(ln.text):
            continue
        if len(edge_counts.get(n, ())) > 1:
            # A repeated line at the top of page 1 may be the title: keep it once.
            if ln.page == pages[0] and ln.top < 0.2 * ln.page_h and n not in {norm(k.text) for k in kept}:
                kept.append(ln)
            continue
        kept.append(ln)
    return kept


# --------------------------------------------------------------------------
# Chord placement
# --------------------------------------------------------------------------

def _snap_to_word_start(text, idx):
    """Move a chord that landed a little way into a word back to the word's start.

    Chord lines are usually typed with spaces, so chords drift a letter or two
    right of the syllable. Short words (<= 4 letters) always snap; longer
    words snap only if the chord is in the first 40%, so real mid-word
    placements ("Hal[G]lelujah") survive. A chord over the space before a word
    belongs to that word ("a [Dm]dark", not "a[Dm] dark").
    """
    if idx < len(text) and text[idx] == " ":
        nxt = idx
        while nxt < len(text) and text[nxt] == " ":
            nxt += 1
        return nxt if nxt < len(text) else idx
    if idx >= len(text) or is_cjk(text[idx]):
        return idx  # each CJK character is its own syllable: keep exact placement
    start = idx
    while start > 0 and text[start - 1] != " ":
        start -= 1
    end = idx
    while end < len(text) and text[end] != " ":
        end += 1
    word_len = end - start
    if word_len <= 4 or (idx - start) <= 0.4 * word_len:
        return start
    return idx


def _chord_text(kind, text):
    return f"*{text}" if kind == "annot" else text  # [*...] is a ChordPro annotation


def merge_chords(chord_line: Line, lyric_line: Line, snap=True) -> str:
    text, xs = lyric_line.char_positions()
    inserts = {}  # char index -> list of chords
    tail = []
    end_x = lyric_line.words[-1]["x1"]
    for kind, gtext, wi in chord_line_groups(chord_line.tokens):
        if kind in ("filler", "label"):
            continue
        w = chord_line.words[wi]
        chord = _chord_text(kind, gtext)
        if w["x0"] > end_x + 2:
            tail.append(chord)
            continue
        idx = min(range(len(xs)), key=lambda i: abs(xs[i] - w["x0"]))
        if snap:
            idx = _snap_to_word_start(text, idx)
        inserts.setdefault(idx, []).append(chord)
    out = []
    for i, ch in enumerate(text):
        for c in inserts.get(i, []):
            out.append(f"[{c}]")
        out.append(ch)
    if tail:
        out.append(" " + " ".join(f"[{c}]" for c in tail))
    return dehyphenate("".join(out))


def dehyphenate(line):
    """Join syllables split for chord spacing: 'be - [G]cause' -> 'be[G]cause'."""
    return re.sub(r"(?<=[a-z’'])\s+((?:\[[^\]]*\]\s*)*)-\s+((?:\[[^\]]*\]\s*)*)(?=[a-z\[])",
                  lambda m: re.sub(r"\s+", "", m[1] + m[2]), line)


def chords_only(line: Line, tokens=None) -> str:
    parts = []
    toks = tokens if tokens is not None else line.tokens
    for kind, text, i in chord_line_groups(toks):
        marks = split_marks(toks[i])[1] if kind == "chord" else ""  # keep strum marks: [F]\\
        parts.append(text if kind in ("filler", "label") else f"[{_chord_text(kind, text)}]{marks}")
    return " ".join(parts)


# --------------------------------------------------------------------------
# Song assembly
# --------------------------------------------------------------------------

def parse_heading(text):
    m = HEADING_RE.match(text.strip())
    if not m:
        return None
    name = CN_SECTIONS.get(m["name"]) or re.sub(r"[- ]", "", m["name"].lower())
    label = m["name"].title() + (f" {m['num']}" if m["num"] else "") + (f" {m['alt'].title()}" if m["alt"] else "")
    return {"kind": name, "label": label, "repeat": bool(m["repeat"] or m["times"]),
            "note": (m["note"] or m["note2"] or "").strip()}


def env_for(kind):
    if kind == "chorus":
        return "chorus"
    if kind == "verse":
        return "verse"
    if kind == "bridge":
        return "bridge"
    return None


CN_CREDIT_RE = re.compile(r"^\s*(作?词\s*[,、/&]?\s*作?曲|作?词|作?曲|原唱|演唱)\s*[:]\s*(.+)$")


PAREN_META_RE = re.compile(r"[(\[]\s*(?:key|capo|tempo|bpm|time)\b[^)\]]*[)\]]", re.I)


KNOWN_ARTISTS = (r"bethel(?:\s+music)?|hillsong(?:\s+(?:united|worship|young\s*(?:&|and)\s*free|y&f))?|"
                 r"chris\s+tomlin|housefires|elevation(?:\s+worship)?|phil\s+wickham|passion|jesus\s+culture|"
                 r"planetshakers|maverick\s+city(?:\s+music)?|leeland|matt\s+redman|kari\s+jobe|"
                 r"brooke\s+ligertwood|cody\s+carnes|sovereign\s+grace|vertical\s+worship|upperroom")


def smart_title_case(text):
    """'GRAVES INTO GARDENS' -> 'Graves Into Gardens'; words without vowels (YHWH) stay."""
    def word(w):
        core = re.sub(r"[^A-Za-z]", "", w)
        if len(core) <= 1 or (len(core) >= 4 and not re.search(r"[AEIOU]", core, re.I)):
            return w  # "I", "YHWH"
        return w[0].upper() + w[1:].lower()
    return re.sub(r"[^\s/()\[\]–-]+", lambda m: word(m[0]), text)


def clean_title(text):
    """Title text without metadata and decorations.

    'Song (Key: F)', 'TITLE (G) 1', '"Song"', 'Song (3)', 'Song by Hillsong',
    'Song – Chris Tomlin', 'Song - Original (B flat)', 'Song (K=G)', 'Song 72bpm',
    '歌名 词:某某', '歌名 (诗篇 63:4,7)', '歌名 Guitar', 'Title (The' (cut off), 'ALL CAPS'.
    """
    text = unicodedata.normalize("NFKC", text).strip()
    text = text.strip("“”\"'‘’「」『』")
    text = PAREN_META_RE.sub("", text)
    text = re.sub(r"\s*[(\[]\s*K\s*=\s*[A-G][#b]?m?\s*[)\]]", "", text)                      # (K=G)
    text = re.sub(r"\s*[-–]?\s*[(\[]?\s*original\s*[(\[]?\s*[A-G][#b]?(?:\s*(?:flat|sharp))?\s*[)\]]?\s*[)\]]?\s*$",
                  "", text, flags=re.I)                                                         # - Original (B flat)
    text = re.sub(r"\s*[(\[]\s*[A-G][#b]?\s*-\s*original\s*[)\]]\s*$", "", text, flags=re.I)   # (A - Original)
    text = re.sub(r"\s*[(\[]\s*[A-G][#b♯♭]?m?\s*[)\]]\s*\d{0,2}\s*$", "", text)                # "TITLE (G) 1"
    text = re.sub(r"\s*\(\d\)\s*$", "", text)                                                 # copy number "(3)"
    text = re.sub(r"\s*\d{2,3}\s*bpm\s*$", "", text, flags=re.I)                               # 72bpm
    text = re.sub(r"\s*[(（][^)）]*\d+\s*[:：]\s*\d+[^)）]*[)）]", "", text)                      # (诗篇 63:4,7)
    text = re.sub(r"\s+(?:作?词|作?曲)\s*[:：].*$", "", text)                                      # 词: 某某
    text = re.sub(r"\s*[-–—]\s*by\s+.+$|\s+(?:chords\s+)?by\s+(?=[A-Z]).+$", "", text)         # – by Phil Wickham / by Hillsong
    text = re.sub(rf"\s*[-–—]\s*(?:{KNOWN_ARTISTS})\s*$", "", text, flags=re.I)                 # – Chris Tomlin
    text = re.sub(r"\s*[-–(]?\s*(?:guitar(?:\s+chords)?|chord\s+sheet)\s*\)?\s*$", "", text, flags=re.I)
    text = re.sub(r"\s*[-–(]?\s*(?:raised?\s*key|raise\s+to\s+[A-G][#b]?)\s*\)?\s*$", "", text, flags=re.I)  # "- Raise Key"
    text = re.sub(r"\s*[(\[]\s*[A-G][#b♯♭]?m?\s*[)\]]\s*$", "", text)                             # a key left before it
    text = re.sub(r"\s*[(\[][^)\]]*$", "", text)                                                 # cut-off "(The"
    text = re.sub(r"(?<=[A-Za-z])\(", " (", text)                                                # "Room(Bryan"
    text = META_RE.sub("", text)
    text = re.sub(r"\b(?:key|capo|tempo)\s*[-:=]\s*$", "", text, flags=re.I)  # "Title  Key -" (value not text)
    text = text.strip(" -–|/")
    letters = re.sub(r"[^A-Za-z]", "", text)
    if len(letters) >= 3 and letters.isupper():
        text = smart_title_case(text)
    return text


def ct_key(k):
    """Normalized key name for comparisons ('Bb' == 'A#')."""
    m = re.match(r"([A-G])([#b♯♭]?)(m?)", k or "")
    if not m:
        return k
    pcs = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    return ((pcs[m[1]] + {"#": 1, "♯": 1, "b": -1, "♭": -1}.get(m[2], 0)) % 12, m[3])


KEY_WORD_RE = re.compile(r"^\s*([A-G][#b♯♭]?)\s*(major|minor|maj|min|大调|小调)\b", re.I)


JIANPU_KEY_RE = re.compile(r"(?:^|\s)1\s*=\s*([A-G][#b♯♭]?)(?![a-z])")
SONG_TITLE_RE = re.compile(r"^\s*(?:song|title|song\s+title|歌名)\s*[:：]\s*(.+)$", re.I)


TEMPO_MARK_RE = re.compile(r"^\s*[q♩♪]?\s*=\s*(\d{2,3})\s*$")                 # "q = 116" (a note symbol = 116)
TEMPO_HEADING_RE = re.compile(rf"^\s*(\d{{2,3}})\s+(?=(?:{SECTION_WORDS})\b)", re.I)  # "129 VERSE"


def parse_meta(text):
    m = TEMPO_MARK_RE.match(text)
    if m:
        return {"tempo": m[1]}
    # Numbered-notation (jianpu) key: "1 = F" means do = F.
    m = JIANPU_KEY_RE.search(text)
    if m and len(text.strip()) <= 24:
        found = {"key": m[1].replace("♭", "b").replace("♯", "#")}
        t = re.search(r"(?<![\d/])(\d{1,2}/(?:2|4|8))(?![\d/])", text)
        if t:
            found["time"] = t[1]
        return found
    m = SONG_TITLE_RE.match(text)
    if m:
        return {"title": clean_title(m[1]) or m[1].strip()}
    m = KEY_WORD_RE.match(text)
    if m:  # "F major  4/4  ♩= 156"
        found = {"key": m[1] + ("m" if m[2].lower().startswith("min") or m[2] == "小调" else "")}
        t = re.search(r"(?<![\d/])(\d{1,2}/(?:2|4|8))(?![\d/])", text)
        if t:
            found["time"] = t[1]
        bpm = re.search(r"=\s*(\d{2,3})\b", text)
        if bpm:
            found["tempo"] = bpm[1]
        return found
    # Metadata in brackets ("(Key: F)") is parsed without its brackets.
    for m in PAREN_META_RE.finditer(text):
        inner = parse_meta(m[0][1:-1])
        if inner:
            return {**inner, **parse_meta(PAREN_META_RE.sub("", text))}
    found = {}
    m = CN_CREDIT_RE.match(text)
    if m:
        label, who = re.sub(r"\s", "", m[1]), m[2].strip()
        if "词" in label and "曲" in label:
            found["lyricist"] = found["composer"] = who
        elif "词" in label:
            found["lyricist"] = who
        elif "曲" in label:
            found["composer"] = who
        else:
            found["artist"] = who
        return found
    m = KEY_OF_RE.match(text)
    if m:
        found["key"] = m[1]
    m = CCLI_RE.match(text)
    if m:
        found["ccli_license" if m[1].lower() == "license" else "ccli"] = m[2]
        return found
    for m in (m for part in re.split(r"\s[|•·]\s", text) for m in META_RE.finditer(part)):
        k = re.sub(r"\s+", " ", m["k"].lower())
        v = m["v"].strip(" ,;")
        if k.startswith("ccli"):
            found["ccli"] = v
        elif k.startswith("words and") or k.startswith("words &"):
            found["lyricist"] = found["composer"] = v
        elif k in META_MAP:
            found[META_MAP[k]] = v
    return found


def is_body_line(ln):
    return is_chord_tokens(ln.tokens) or parse_heading(ln.text) or HEADING_PREFIX_RE.match(ln.text)


def music_pages(lines):
    """(first, last) 0-based page range holding the song, skipping leading pages
    without chords (e.g. a "Terms and Conditions" page). None if nothing to skip."""
    chord_pages = sorted({ln.page for ln in lines if chord_line_groups(ln.tokens)})
    if not chord_pages or chord_pages[0] == 0:
        return None
    return chord_pages[0], max(ln.page for ln in lines)


CJK_FONT_RE = re.compile(
    r"sim(?:hei|sun|kai|fang|li|you)|kaiti|fangsong|yahei|pingfang|hiragino|heiti|stsong|stkaiti|"
    r"stxihei|stfangsong|stzhongsong|mingliu|dfkai|songti|sourcehan|source-?han|notosans(?:cjk|sc|tc|jp|kr)|"
    r"ms-?p?gothic|ms-?p?mincho|malgun|batang|gulim|dotum|meiryo|yugothic|wenquanyi|wqy|droidsansfallback",
    re.I)


def text_readable(lines):
    """False if the lyric text is garbage.

    Two tell-tales: (1) text drawn in a Chinese/Japanese/Korean font that
    extracts as almost no CJK characters (a broken text mapping turns 一生敬拜
    into '!"#$%'); (2) text that is mostly symbols rather than letters.
    """
    all_chars = [c for ln in lines for w in ln.words for c in w.get("_glyphs", w["chars"])]
    if all_chars and sum(bool(c.get("_cid")) for c in all_chars) > 0.2 * len(all_chars):
        return False  # glyphs without a text mapping extract as "(cid:69)"
    cjk_font_chars = [c["text"] for ln in lines for w in ln.words for c in w["chars"]
                      if CJK_FONT_RE.search(c["fontname"].split("+")[-1].replace(" ", ""))]
    if len(cjk_font_chars) >= 20 and sum(map(is_cjk, cjk_font_chars)) < 0.2 * len(cjk_font_chars):
        return False
    text = "".join(ln.text for ln in lines if not chord_line_groups(ln.tokens)).replace(" ", "")
    if len(text) < 20:
        return True
    letters = sum(1 for ch in text if unicodedata.category(ch)[0] == "L")
    return letters / len(text) >= 0.5


def convert(lines, title=None, key=None, guess_key=True, source_name="song", snap=True):
    pages = music_pages(lines)
    if pages:
        lines = [ln for ln in lines if ln.page >= pages[0]]
    lines, order = pull_order_lines(lines)
    lines = strip_repeated_headers(lines)
    meta = {}
    subtitles = []
    footer = []

    # --- Header block: everything before the first chord line / section heading.
    first_body = next((i for i, ln in enumerate(lines) if is_body_line(ln)), len(lines))
    header, body = lines[:first_body], lines[first_body:]
    # If the header is long it's probably lyrics without chords; keep only the top.
    if len(header) > 6:
        body = header[4:] + body
        header = header[:4]
    # A tempo number glued to the first heading ("129 VERSE"): tempo, and the heading stays.
    for ln in header + body[:1]:
        m = TEMPO_HEADING_RE.match(ln.text)
        if m:
            meta.setdefault("tempo", m[1])
            ln.text = ln.text[m.end():]
            if ln in header:
                header.remove(ln)
                body.insert(0, ln)
            break
    # A "(note)" line right above the first heading belongs to that section.
    if header and body and re.fullmatch(r"\(.*\)", header[-1].text):
        body.insert(0, header.pop())
    if header:
        def title_candidate(ln):
            t = ln.text.strip()
            return (not parse_meta(t) and sum(ch.isalpha() for ch in t) >= 2
                    and not BY_RE.match(t) and not COPYRIGHT_RE.match(t))
        candidates = [ln for ln in header if title_candidate(ln)]
        title_line = max(candidates, key=lambda ln: (ln.size, -ln.top)) if candidates and not title else None
        for ln in header:
            if ln is title_line:
                t_meta = parse_meta(ln.text)
                meta.update(t_meta)
                meta.setdefault("title", clean_title(ln.text) or ln.text)
                continue
            m = parse_meta(ln.text)
            if m:
                meta.update(m)
            elif len(ln.text.strip()) <= 2 and not any(is_cjk(ch) for ch in ln.text):
                continue  # a stray glyph from a music font (e.g. a note symbol)
            elif BY_RE.match(ln.text):
                who = BY_RE.match(ln.text)[1].strip()
                if "©" in who:  # "Robert Gay © 1989 Integrity's Hosanna! Music"
                    who, rest = who.split("©", 1)
                    footer.append("©" + rest)
                meta["artist"] = who.strip(" ,;")
            elif COPYRIGHT_RE.match(ln.text):
                footer.append(ln.text)
            else:
                subtitles.append(ln.text)

    # --- Pull metadata / copyright / licensing lines out of the body.
    body_size = statistics.median([ln.size for ln in body]) if body else 0
    kept, extra_footer, in_footer = [], [], False
    for ln in body:
        if COPYRIGHT_RE.match(ln.text):
            footer.append(ln.text)
            in_footer = True
            continue
        if not is_chord_tokens(ln.tokens):
            m = parse_meta(ln.text)
            # Only treat as metadata if the whole line is "Label: value" pairs.
            if m and (CCLI_RE.match(ln.text) or len(ln.text) < 80 and
                      re.match(r"^\s*(?:\w+(?:\s\w+)?\s*[:\-=]|key\s+of)", ln.text, re.I)):
                if "key" in m and "key" in meta and ct_key(m["key"]) != ct_key(meta["key"]):
                    ln.key_change = m["key"]  # a later "Key - C": the song changes key here
                    kept.append(ln)
                    continue
                meta.update({k: v for k, v in m.items() if k not in meta or k != "key"})
                continue
            if LICENSE_RE.search(ln.text) or (in_footer and ln.size < 0.9 * body_size):
                # Wrapped copyright text continues in lowercase.
                if footer and ln.text[:1].islower() and not extra_footer:
                    footer[-1] += " " + ln.text
                else:
                    extra_footer.append(ln.text)
                in_footer = True
                continue
        kept.append(ln)
    body = kept
    last_chord = max((i for i, ln in enumerate(body) if is_chord_tokens(ln.tokens)), default=-1)
    while body and len(body) - 1 > last_chord and body[-1].size < 0.85 * body_size \
            and not parse_heading(body[-1].text):
        # Stop at a small line that directly follows the song (e.g. an English
        # translation under the last Chinese line) rather than standing apart.
        if 0 <= body[-1].gap_before < 0.8 * body[-1].size and len(body) >= 2 \
                and body[-2].size >= 0.85 * body_size:
            break
        extra_footer.insert(0, body.pop().text)

    # --- Paragraph detection from vertical gaps.
    # A break is roughly a blank line's worth of extra space.
    gaps = [ln.gap_before for ln in body if 0 < ln.gap_before < NEW_BLOCK]
    base_gap = statistics.median(gaps) if gaps else 0
    def is_break(ln):
        return ln.gap_before > max(base_gap * 2, ln.size * 0.9)

    out_body = []
    open_env = None

    def close_env():
        nonlocal open_env
        if open_env:
            while out_body and out_body[-1] == "":
                out_body.pop()
            out_body.append(f"{{end_of_{open_env}}}")
            open_env = None

    i, pending_note = 0, ""
    while i < len(body):
        ln = body[i]
        nxt = body[i + 1] if i + 1 < len(body) else None

        # "(first time a cappella)" printed just above a heading belongs to it.
        if (re.fullmatch(r"\(.*\)", ln.text) and nxt and parse_heading(nxt.text)
                and nxt.page == ln.page and 0 <= nxt.top - ln.top < 1.5 * ln.size):
            pending_note = ln.text[1:-1].strip()
            i += 1
            continue

        if (i and is_break(ln) and out_body and out_body[-1] != ""
                and not out_body[-1].startswith(("{start_of", "{comment"))):
            out_body.append("")

        if getattr(ln, "key_change", None):
            out_body.append(f"{{key: {ln.key_change}}}")  # ChordPro: a mid-song {key:} is a key change
            i += 1
            continue

        head = parse_heading(ln.text)
        if head:
            close_env()
            if out_body and out_body[-1] != "":
                out_body.append("")
            following = nxt is None or parse_heading(nxt.text) or head["repeat"]
            env = env_for(head["kind"])
            if env == "chorus" and following:
                out_body.append("{chorus}" if head["label"] == "Chorus" else f"{{chorus: {head['label']}}}")
            elif env and not following:
                out_body.append(f"{{start_of_{env}: {head['label']}}}")
                open_env = env
            else:
                out_body.append(f"{{comment: {head['label']}{' (repeat)' if head['repeat'] and not env else ''}}}")
            note = head["note"] or pending_note
            if note:
                out_body.append(f"{{comment: {note}}}")
            pending_note = ""
            i += 1
            continue

        pm = HEADING_PREFIX_RE.match(ln.text)
        if pm and is_chord_tokens(pm["rest"].split()):
            close_env()
            if out_body and out_body[-1] != "":
                out_body.append("")
            out_body.append(f"{{comment: {pm['label'].title()}}}")
            out_body.append(chords_only(ln, pm["rest"].split()))
            i += 1
            continue

        if is_chord_tokens(ln.tokens):
            if (nxt is not None and not is_chord_tokens(nxt.tokens)
                    and not parse_heading(nxt.text) and not is_break(nxt)
                    and nxt.gap_before >= 0):
                out_body.append(merge_chords(ln, nxt, snap=snap))
                i += 2
                continue
            out_body.append(chords_only(ln))
            i += 1
            continue

        out_body.append(dehyphenate(ln.text))
        i += 1
    close_env()
    while out_body and out_body[-1] == "":
        out_body.pop()

    # --- Title / key fallbacks.
    if title:
        meta["title"] = title
    meta.setdefault("title", source_name)
    if key:
        meta["key"] = key
    if "key" not in meta and guess_key:
        first = next((m for ln in out_body for m in re.findall(r"\[([^\]]+)\]", ln)), None)
        if first:
            root = re.match(rf"[A-G]{_ACC}?(m(?!aj))?", first)
            if root:
                meta["key"] = root[0]
                print(f"  note: no key found, guessed {{key: {meta['key']}}} from first chord", file=sys.stderr)

    if meta.get("key"):  # just the key ("A, ♪ = 80" -> "A")
        km = re.match(r"\s*([A-G][#b♯♭]?m?)(?![a-z])", meta["key"])
        if km:
            meta["key"] = km[1].replace("♭", "b").replace("♯", "#")
        else:
            meta.pop("key")
    out = [f"{{title: {meta.pop('title')}}}"]
    if pages:
        out.append(f"{{meta: pdf_pages {pages[0] + 1}-{pages[1] + 1}}}")
    for s in subtitles:
        out.append(f"{{subtitle: {s}}}")
    for k in ("artist", "composer", "lyricist", "album", "year", "key", "capo", "tempo", "time"):
        if k in meta:
            out.append(f"{{{k}: {meta.pop(k)}}}")
    for k in ("ccli", "ccli_license"):
        if k in meta:
            out.append(f"{{meta: {k} {meta.pop(k)}}}")
    for c in footer:
        out.append(f"{{copyright: {c}}}")
    for f in extra_footer:
        out.append(f"{{meta: footer {f}}}")
    if order:
        out.append(f"{{comment: Order: {order}}}")
    out.append("")
    out.extend(out_body)
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def ocr_pdf(path: Path) -> Path:
    if not shutil.which("ocrmypdf"):
        sys.exit(f"{path}: no text layer found and ocrmypdf is not installed.\n"
                 "  Install it (brew install ocrmypdf) or OCR the PDF yourself first.")
    tmp = Path(tempfile.mkdtemp()) / path.name
    print(f"  running OCR on {path.name} ...", file=sys.stderr)
    subprocess.run(["ocrmypdf", "--force-ocr", "-q", str(path), str(tmp)], check=True)
    return tmp


def has_text(path: Path) -> bool:
    with PDF_LOCK, pdfplumber.open(path) as pdf:
        return any(p.chars for p in pdf.pages)


def convert_file(path: Path, args) -> str:
    src = path
    if args.ocr or not has_text(path):
        src = ocr_pdf(path)
    lines = extract_lines(src, columns=args.columns)
    if not lines:
        raise ValueError("no text found")
    return convert(lines, title=args.title, key=args.key,
                   guess_key=not args.no_guess_key, source_name=path.stem,
                   snap=not args.no_snap)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path, help="PDF files or folders of PDFs")
    ap.add_argument("-o", "--out-dir", type=Path, help="write .cho files here (default: next to each PDF)")
    ap.add_argument("--stdout", action="store_true", help="print ChordPro to stdout instead of writing files")
    ap.add_argument("--ext", default=".cho", help="output extension (default .cho)")
    ap.add_argument("--title", help="override the detected title")
    ap.add_argument("--key", help="override the detected key")
    ap.add_argument("--no-guess-key", action="store_true", help="don't guess the key from the first chord")
    ap.add_argument("--no-snap", action="store_true",
                    help="place chords at the exact nearest letter instead of snapping to word starts")
    ap.add_argument("--columns", choices=["auto", "1", "2"], default="auto",
                    help="page column layout (default: auto-detect)")
    ap.add_argument("--ocr", action="store_true", help="force OCR even if the PDF has a text layer")
    args = ap.parse_args()

    pdfs = []
    for p in args.inputs:
        pdfs.extend(sorted(p.glob("*.pdf")) + sorted(p.glob("*.PDF")) if p.is_dir() else [p])
    if not pdfs:
        sys.exit("no PDFs found")
    if args.out_dir:
        args.out_dir.mkdir(parents=True, exist_ok=True)

    failed = 0
    for pdf in pdfs:
        print(pdf.name, file=sys.stderr)
        try:
            result = convert_file(pdf, args)
        except Exception as e:  # keep going on batch runs
            print(f"  FAILED: {e}", file=sys.stderr)
            failed += 1
            continue
        if args.stdout:
            if len(pdfs) > 1:
                print(f"# ---- {pdf.name} ----")
            print(result)
        else:
            dest = (args.out_dir or pdf.parent) / (pdf.stem + args.ext)
            dest.write_text(result, encoding="utf-8")
            print(f"  -> {dest}", file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
