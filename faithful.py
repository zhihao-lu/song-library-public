"""Transpose a chord sheet PDF in place, keeping the original layout.

Instead of re-typesetting the song, every chord on the page is covered with a
white box and the transposed chord is drawn at the same position, baseline and
size (superscript extensions stay superscript). Everything else on the page -
lyrics, logos, fonts, columns, footers - is untouched.
"""

import io
import os
import re
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path

import pdfplumber
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

import chordtools as ct
import pdf2chordpro as p2c

FONT_DIRS = [
    "/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts",
    os.path.expanduser("~/Library/Fonts"),
    "/Applications/Microsoft Word.app/Contents/Resources/DFonts",
    "/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
    "C:/Windows/Fonts",
]
KEY_LABELS = {"key", "key:"}
KEY_SEPARATORS = {"-", "–", "—", ":", "=", "of", "|"}
# A key word with whatever brackets or punctuation it picked up from its header:
# "(Key: F)" tokenises as "(Key:" then "F)".
KEY_VALUE_RE = re.compile(r"([(\[{]*)([A-G][#b♯♭]?m?)([)\]},;.]*)")


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


@lru_cache(maxsize=1)
def _system_fonts():
    """Map normalized font names (file stem and PostScript name) to .ttf paths."""
    index = {}
    try:
        from fontTools.ttLib import TTFont as FTFont
    except ImportError:
        FTFont = None
    for d in FONT_DIRS:
        for path in Path(d).rglob("*.ttf") if Path(d).is_dir() else ():
            index.setdefault(_norm(path.stem), str(path))
            if FTFont:
                try:
                    ps = FTFont(str(path), lazy=True)["name"].getDebugName(6)
                    if ps:
                        index.setdefault(_norm(ps), str(path))
                except Exception:
                    pass
    return index


@lru_cache(maxsize=None)
def _register(path):
    name = "F_" + _norm(Path(path).stem)
    try:
        pdfmetrics.registerFont(TTFont(name, path))
        return name
    except Exception:
        return None


def resolve_font(pdf_font, bold_hint=False):
    """Pick a font to draw with: the original if installed, else the nearest sans."""
    base = pdf_font.split("+")[-1]
    path = _system_fonts().get(_norm(base))
    if path and _register(path):
        return _register(path)
    bold = bold_hint or re.search(r"bold|black|heavy|semibold|demi", base, re.I)
    italic = re.search(r"italic|oblique", base, re.I)
    style = " ".join(s for s, on in (("Bold", bold), ("Italic", italic)) if on)
    # Arial on macOS/Windows; its metric-compatible Liberation Sans on Linux.
    for family in ("Arial", "LiberationSans", "DejaVuSans"):
        suffix = {"Bold Italic": "-BoldItalic", "Bold": "-Bold", "Italic": "-Italic"}.get(style, "-Regular")
        for name in (f"{family} {style}".strip(), family + suffix):
            path = _system_fonts().get(_norm(name))
            if path and _register(path):
                return _register(path)
    return "Helvetica" + ({(True, True): "-BoldOblique", (True, False): "-Bold",
                          (False, True): "-Oblique"}.get((bool(bold), bool(italic)), ""))


def _color(c):
    col = c.get("non_stroking_color")
    if not isinstance(col, (tuple, list)) or not all(isinstance(v, (int, float)) for v in col):
        return ("rgb", (0, 0, 0))
    if len(col) == 1:
        return ("rgb", (col[0],) * 3)
    if len(col) == 4:
        return ("cmyk", tuple(col))
    return ("rgb", tuple(col[:3]))


def _baseline(c, page_h):
    m = c.get("matrix")
    if m and m[0] > 0 and m[3] > 0:
        return m[5]
    return page_h - c["_bottom"] + 0.2 * c["size"]


def _chord_spans(text):
    """Character spans of a chord's parts: (paren, root, bass) as (start, end) or None."""
    off = 0
    inner = p2c.split_marks(text)[0]  # strum marks after the chord stay as they are
    paren = inner.startswith("(") and inner.endswith(")") and p2c.is_chord(inner[1:-1])
    if paren:
        inner, off = inner[1:-1], 1
    m = re.match(r"^([A-G][#b♯♭]?)(.*?)(?:/([A-G][#b♯♭]?))?$", inner)
    if not m:
        return None
    root = (off + m.start(1), off + m.end(1))
    bass = (off + m.start(3), off + m.end(3)) if m[3] else None
    return paren, root, bass


def _replacement_runs(word, semis, flats):
    """Rebuild a chord word as styled runs [(text, style_char)], keeping each part's style."""
    chars = word["chars"]
    old = "".join(c["text"] for c in chars)
    if len(old) != len(chars):  # a char expanded to several; map by char instead
        return None
    spans = _chord_spans(old)
    if not spans:
        return None
    _, (r0, r1), bass = spans
    runs = []
    i = 0
    while i < len(chars):
        if i == r0:
            runs.append((ct.transpose_chord(old[r0:r1], semis, flats), chars[r0]))
            i = r1
        elif bass and i == bass[0]:
            runs.append((ct.transpose_chord(old[bass[0]:bass[1]], semis, flats), chars[i]))
            i = bass[1]
        else:
            runs.append((chars[i]["text"], chars[i]))
            i += 1
    return runs


def _styled(runs, lyric_font, ph):
    return [(text, resolve_font(sc["fontname"], sc["fontname"] != lyric_font), sc["size"],
             _baseline(sc, ph), _color(sc)) for text, sc in runs]


def _width(styled, hscale=1.0):
    return sum(stringWidth(t, f, s) for t, f, s, _, _ in styled) * hscale


def _cover(c, w, ph, y_off):
    """White out a word's glyphs.

    Boxes are built from each character's baseline and size (descenders to
    ~25% below, caps to ~85% above) rather than the PDF's reported glyph box,
    which some fonts (e.g. Type3) place wrongly. That covers bar lines and
    parentheses fully without clipping the lyric line underneath.
    """
    c.setFillColorRGB(1, 1, 1)
    for ch in w["chars"]:
        size, base = ch["size"], _baseline(ch, ph) - y_off
        pad = 0.06 * size
        c.rect(ch["x0"] - pad, base - 0.25 * size, ch["x1"] - ch["x0"] + 2 * pad, 1.1 * size, stroke=0, fill=1)


def _put(c, styled, x, hscale, y_off):
    for text, font, size, base_y, (space, col) in styled:
        t = c.beginText()
        t.setFont(font, size)
        t.setHorizScale(hscale * 100)
        t.setTextOrigin(x, base_y - y_off)
        if space == "cmyk":
            t.setFillColorCMYK(*col)
        else:
            t.setFillColorRGB(*col)
        t.textOut(text)
        c.drawText(t)
        x += stringWidth(text, font, size) * hscale
    return x


def _draw_line(c, items, lyric_font, pw, ph, y_off):
    """Redraw the changed words of one line, keeping original positions.

    A new chord that's wider than the gap before the next word is first
    condensed (down to 85% width). If that isn't enough, the whole line is
    re-laid out: every word keeps its original x unless the previous word now
    reaches it, in which case it moves right just enough to keep its
    original gap. That keeps bar lines like "| Bb | Eb |" readable.
    """
    styled = [_styled(runs, lyric_font, ph) for _, runs, _ in items]
    hscales, reflow = [], False
    for i, (w, _, changed) in enumerate(items):
        if not changed:
            hscales.append(1.0)
            continue
        size = w["chars"][0]["size"]
        room = (items[i + 1][0]["x0"] - w["x0"] - 0.2 * size) if i + 1 < len(items) else (pw - w["x0"] - 20)
        fit = room / _width(styled[i]) if _width(styled[i]) else 1.0
        hscales.append(min(1.0, max(0.85, fit)))
        reflow |= fit < 0.85
    if not reflow:
        for (w, _, changed), st, hs in zip(items, styled, hscales):
            if changed:
                _cover(c, w, ph, y_off)
                _put(c, st, w["x0"], hs, y_off)
        return
    for w, _, _ in items:
        _cover(c, w, ph, y_off)
    # Re-laid-out lines get room by moving words, so draw them full width (condensed
    # chords look smaller than the rest); only condense if the line would run off the page.
    full = [1.0] * len(items)
    hscales = full if _reflow(c, items, styled, full, y_off, dry=True) <= pw - 20 else hscales
    _reflow(c, items, styled, hscales, y_off)


def _reflow(c, items, styled, hscales, y_off, dry=False):
    """Draw a line's words at their original x, pushed right where the previous word
    now reaches them. Returns where the line ends; dry=True only measures."""
    end = None
    for i, ((w, _, _), st, hs) in enumerate(zip(items, styled, hscales)):
        x = w["x0"]
        if end is not None:
            prev = items[i - 1][0]
            gap = max(0.2 * w["chars"][0]["size"], w["x0"] - prev["x1"])
            x = max(x, end + gap)
        end = x + _width(st, hs) if dry else _put(c, st, x, hs, y_off)
    return end


def chord_diagram_images(pdf_path, lines):
    """Per page: boxes (x0, top, x1, bottom) of chord-diagram strips.

    Many Chinese sheets put a row of guitar chord diagrams (a picture) above the
    music. Those diagrams show the original key, so after transposing they'd be
    wrong. A strip is a picture at least a quarter of the page wide, short, and
    entirely above the page's first chord line, with text (the title) above it:
    a publisher's logo in the page header (Bethel Music, KUA Global) is the same
    shape, but nothing is above it.
    """
    first_chord, first_text = {}, {}
    for ln in lines:
        first_text[ln.page] = min(first_text.get(ln.page, 1e9), ln.top)
        if p2c.chord_line_groups(ln.tokens):
            first_chord[ln.page] = min(first_chord.get(ln.page, 1e9), ln.top)
    boxes = {}
    with p2c.PDF_LOCK, pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages):
            if page_no not in first_chord:
                continue
            for im in page.images:
                if (im["width"] >= 0.25 * page.width and im["height"] <= 0.15 * page.height
                        and first_text[page_no] < im["top"] and im["bottom"] <= first_chord[page_no] + 2):
                    boxes.setdefault(page_no, []).append((im["x0"], im["top"], im["x1"], im["bottom"]))
    return boxes


def _hide_diagrams(c, boxes, ph, y_off):
    for x0, top, x1, bottom in boxes:
        c.setFillColorRGB(1, 1, 1)
        c.rect(x0 - 1, ph - bottom - 1 - y_off, x1 - x0 + 2, bottom - top + 2, stroke=0, fill=1)
        c.setFillColorRGB(0.45, 0.45, 0.45)
        c.setFont("Helvetica-Oblique", 7.5)
        c.drawCentredString((x0 + x1) / 2, ph - (top + bottom) / 2 - 3 - y_off,
                            "Chord diagrams hidden: they show the original key")


def _split_key(text):
    """(head, key, tail) for a key word like '(F)', 'D,' or 'Bb', else None."""
    m = KEY_VALUE_RE.fullmatch(text)
    return m.groups() if m else None


def _key_words(lines):
    """Every key label: the 'C' in 'Key - C' / 'Key: D' / 'F major', as [(line, word_index)].
    The first is the song's key; later ones are key changes."""
    return [kw for ln in lines if (kw := _key_word([ln]))]


def _key_word(lines):
    """The 'C' in a header like 'Key - C' / 'Key: D', as (line, word_index)."""
    for ln in lines[:12]:
        toks = ln.tokens
        for i, t in enumerate(toks):
            if t.lower().lstrip("([{") not in KEY_LABELS:
                continue
            j = i + 1
            while j < len(toks) and toks[j].lower() in KEY_SEPARATORS:
                j += 1
            if j < len(toks) and _split_key(toks[j]):
                return ln, j
        # "F major  4/4  ♩=156" style
        for i, t in enumerate(toks[:-1]):
            sp = _split_key(t)
            if sp and not sp[1].endswith("m") and re.fullmatch(r"(?i)major|minor|maj|min", toks[i + 1]):
                return ln, i
    return None


def transpose_pdf(pdf_path, from_key, to_key, key_label=None):
    """The PDF with its chords (and key) moved from one key to another.

    `key_label` replaces the song's key instead, e.g. "A · Capo 2 (play G)" for a capo, even
    when the chords stay as they are. A sheet with no key line gets it at the top of page 1.
    """
    src, dst = ct.parse_key(from_key), ct.parse_key(to_key)
    if not src or not dst:
        raise ValueError(f"can't parse key {from_key!r} -> {to_key!r}")
    data = Path(pdf_path).read_bytes()
    semis = (dst[0] - src[0]) % 12
    if semis == 0 and not key_label:
        return data
    target = ct.key_name(dst[0], src[1])
    flats = target in ct.FLAT_KEYS

    lines = p2c.extract_lines(pdf_path)
    diagrams = chord_diagram_images(pdf_path, lines) if semis else {}
    chord_lines = [ln for ln in lines if p2c.chord_line_groups(ln.tokens)]
    # Chords set in a different font from the lyrics are usually emphasised
    # (often faux-bold), so the fallback for them is bold.
    lyric_font = Counter(c["fontname"] for ln in lines if ln not in chord_lines
                         for w in ln.words for c in w["chars"]).most_common(1)
    lyric_font = lyric_font[0][0] if lyric_font else ""

    # Each edit is one line's worth of words to redraw: [(word, runs)].
    edits = {}
    # Chords are spelled for the key they're in: after a "Key - C" key change, the rest of the
    # song (in reading order) uses the moved key's sharps or flats.
    key_change_at = {id(ln): ct._moved_key(_split_key(ln.words[j]["text"])[1], semis)
                     for ln, j in _key_words([l for l in lines if not p2c.chord_line_groups(l.tokens)])[1:]}
    line_flats, cur = {}, flats
    for ln in lines:
        if id(ln) in key_change_at:
            cur = ct.key_name(*ct.parse_key(key_change_at[id(ln)])) in ct.FLAT_KEYS
        line_flats[id(ln)] = cur
    for ln in chord_lines if semis else []:
        flats_here = line_flats.get(id(ln), flats)
        groups = p2c.chord_line_groups(ln.tokens)
        chord_idx = {wi for kind, _, wi in groups if kind == "chord"}
        items = []
        for wi, w in enumerate(ln.words):
            runs = _replacement_runs(w, semis, flats_here) if wi in chord_idx else None
            items.append((w, runs or [(ch["text"], ch) for ch in w["chars"]], wi in chord_idx and runs))
        edits.setdefault(ln.page, []).append(items)
    key_words = _key_words([ln for ln in lines if not p2c.chord_line_groups(ln.tokens)])
    for n, (ln, j) in enumerate(key_words):
        w = ln.words[j]
        head, body, tail = _split_key(w["text"])
        followed_by_mode = j + 1 < len(ln.words) and re.fullmatch(r"(?i)major|minor|maj|min", ln.words[j + 1]["text"])
        items = [(x, [(ch["text"], ch) for ch in x["chars"]], False) for x in ln.words]
        if n == 0 and key_label:
            new_key = key_label
            if followed_by_mode:  # the label says the mode itself: drop "major"
                items[j + 1] = (ln.words[j + 1], [], True)
        elif semis:
            moved = ct._moved_key(body, semis)
            new_key = moved.rstrip("m") if followed_by_mode else moved  # "A minor", not "Am minor"
        else:
            continue
        items[j] = (w, [(head + new_key + tail, w["chars"][0])], True)
        edits.setdefault(ln.page, []).append(items)
    stamp = key_label if key_label and not key_words else None

    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter(clone_from=reader)
    for page_no, page in enumerate(writer.pages):
        if page_no not in edits and page_no not in diagrams and not (stamp and page_no == 0):
            continue
        mb = page.mediabox
        pw, ph = float(mb.width), float(mb.height)
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(pw, ph))
        _hide_diagrams(c, diagrams.get(page_no, []), ph, float(mb.bottom))
        for items in edits.get(page_no, []):
            _draw_line(c, items, lyric_font, pw, ph, float(mb.bottom))
        if stamp and page_no == 0:  # no key line to put it in: top right of the first page
            c.setFillColorRGB(0, 0, 0)
            c.setFont("Helvetica-Bold", 11)
            c.drawRightString(pw - 36, ph - 30, "Key: " + stamp)
        c.save()
        page.merge_page(PdfReader(buf).pages[0])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


if __name__ == "__main__":
    src_pdf, frm, to = sys.argv[1:4]
    out = Path(src_pdf).with_name(f"{Path(src_pdf).stem} ({to}).pdf")
    out.write_bytes(transpose_pdf(src_pdf, frm, to))
    print(out)
