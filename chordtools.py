"""ChordPro helpers: key detection, transposition, and PDF rendering."""

import io
import os
import re

from music21 import harmony, stream
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

SHARPS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLATS = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
PC = {n: i for i, n in enumerate(SHARPS)} | {n: i for i, n in enumerate(FLATS)}
PC |= {"Cb": 11, "Fb": 4, "E#": 5, "B#": 0}

# Conventional spelling of each key, and which ones are written with flats.
MAJOR_KEYS = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
MINOR_KEYS = ["Cm", "C#m", "Dm", "Ebm", "Em", "Fm", "F#m", "Gm", "G#m", "Am", "Bbm", "Bm"]
FLAT_KEYS = {"F", "Bb", "Eb", "Ab", "Db", "Gb", "Dm", "Gm", "Cm", "Fm", "Bbm", "Ebm"}

ROOT_RE = re.compile(r"^([A-G])([#b♯♭]?)(.*?)(?:/([A-G])([#b♯♭]?))?$")
CHORD_IN_LINE = re.compile(r"\[([^\]]+)\]")
DIRECTIVE_RE = re.compile(r"^\{\s*([\w-]+)\s*(?::\s*(.*?))?\s*\}$")


def _acc(a):
    return {"♯": "#", "♭": "b"}.get(a, a)


def parse_key(name):
    """'Bbm' -> (10, True). Returns (pitch_class, is_minor) or None."""
    m = re.match(r"^\s*([A-G])([#b♯♭]?)\s*(m|min|minor)?(?![a-z])", name or "")
    if not m:
        return None
    return PC[m[1] + _acc(m[2])], bool(m[3])


def key_name(pc, minor):
    return (MINOR_KEYS if minor else MAJOR_KEYS)[pc % 12]


def chords_in(cho):
    """Chord names in the song body, without annotations ([*...]) or parentheses."""
    out = []
    for line in cho.splitlines():
        if not line.lstrip().startswith("{"):
            out.extend(c.strip("()") for c in CHORD_IN_LINE.findall(line) if not c.startswith("*"))
    return out


# --------------------------------------------------------------------------
# Key detection
# --------------------------------------------------------------------------

def _chord_pitches(chord):
    """Pitch names for a chord symbol, via music21 with a simple fallback."""
    m = ROOT_RE.match(chord.strip())
    if not m:
        return []
    root, acc, quality, bass, bass_acc = m.groups()
    # music21 writes flats as '-': Bb -> B-, and dislikes parenthesised alterations.
    m21 = root + _acc(acc).replace("b", "-") + quality.replace("(", "").replace(")", "")
    if bass:
        m21 += "/" + bass + _acc(bass_acc).replace("b", "-")
    try:
        return [p.name for p in harmony.ChordSymbol(m21).pitches]
    except Exception:
        # Fallback: plain triad on the root (minor if the quality starts with m, not maj).
        r = PC[root + _acc(acc)]
        third = 3 if re.match(r"m(?!aj)", quality) else 4
        return [SHARPS[r], SHARPS[(r + third) % 12], SHARPS[(r + 7) % 12]]


def detect_key(cho):
    """Estimate the key from the chords with music21's Krumhansl-Schmuckler analysis.

    Pitch profiles alone often confuse a key with its IV (D vs G in a song that
    leans on G) or its relative minor (D vs Bm). Songs nearly always start or end
    on the tonic, so among candidates scoring within 0.1 of the best, a bonus
    goes to keys whose tonic chord is the song's first or last chord.
    """
    chords = chords_in(cho)
    s = stream.Stream()
    for c in chords:
        pitches = _chord_pitches(c)
        if pitches:
            s.append(harmony.chord.Chord(pitches, quarterLength=1))
    if not len(s):
        return None
    k = s.analyze("key")
    candidates = [k] + list(k.alternateInterpretations[:4])
    top = candidates[0].correlationCoefficient

    def tonic_of(chord):
        m = ROOT_RE.match(chord)
        return (PC[m[1] + _acc(m[2])], bool(re.match(r"m(?!aj)", m[3]))) if m else None

    first, last = tonic_of(chords[0]), tonic_of(chords[-1])

    def score(c):
        tonic = (c.tonic.pitchClass, c.mode == "minor")
        return c.correlationCoefficient + 0.05 * (tonic == first) + 0.08 * (tonic == last)

    best = max((c for c in candidates if top - c.correlationCoefficient < 0.1), key=score)
    candidates.remove(best)
    candidates.insert(0, best)
    minor = best.mode == "minor"
    return {
        "key": key_name(best.tonic.pitchClass, minor),
        "confidence": round(best.correlationCoefficient, 2),
        "alternatives": [key_name(c.tonic.pitchClass, c.mode == "minor") for c in candidates[1:3]],
    }


def key_info(cho):
    """{'declared', 'detected', 'source'}: the sheet's stated key, the analysed key, and which to use."""
    detected = detect_key(cho)
    declared = declared_key(cho)
    parsed = parse_key(declared) if declared else None
    declared_norm = key_name(*parsed) if parsed else None
    return {"declared": declared_norm, "detected": detected,
            "source": declared_norm or (detected or {}).get("key") or "C"}


def declared_key(cho):
    for line in cho.splitlines():
        m = DIRECTIVE_RE.match(line.strip())
        if m and m[1].lower() == "key" and m[2]:
            return m[2]
    return None


# --------------------------------------------------------------------------
# Transposition
# --------------------------------------------------------------------------

def transpose_chord(chord, semis, flats):
    chord = chord.strip()
    if chord.startswith("(") and chord.endswith(")"):  # optional chord: (C)
        return "(" + transpose_chord(chord[1:-1], semis, flats) + ")"
    m = ROOT_RE.match(chord)
    if not m:
        return chord  # N.C., [*annotation], etc.
    names = FLATS if flats else SHARPS
    root, acc, quality, bass, bass_acc = m.groups()
    out = names[(PC[root + _acc(acc)] + semis) % 12] + quality
    if bass:
        out += "/" + names[(PC[bass + _acc(bass_acc)] + semis) % 12]
    return out


def set_key(cho, value):
    """The song with its {key:} line saying `value` (added under the title if it has none)."""
    lines = cho.splitlines()
    for i, line in enumerate(lines):
        m = DIRECTIVE_RE.match(line.strip())
        if m and m[1].lower() == "key":
            lines[i] = f"{{key: {value}}}"
            break
    else:
        i = next((i + 1 for i, l in enumerate(lines) if l.startswith("{title")), 0)
        lines.insert(i, f"{{key: {value}}}")
    return "\n".join(lines) + "\n"


# Capo: shapes guitarists find easy, and how far up the neck a capo may go.
CAPO_SHAPES = {False: ("C", "A", "G", "E"), True: ("Am", "Em", "Dm")}
MAX_CAPO_FRET = 7


def capo_options(key):
    """The capos for a song in `key`: [{"fret", "shape"}], lowest fret first.
    Each easy shape (C A G E, or Am Em Dm in a minor key) whose fret is 1-7."""
    parsed = parse_key(key)
    if not parsed:
        return []
    pc, minor = parsed
    options = []
    for shape in CAPO_SHAPES[minor]:
        fret = (pc - parse_key(shape)[0]) % 12
        if 1 <= fret <= MAX_CAPO_FRET:
            options.append({"fret": fret, "shape": shape})
    return sorted(options, key=lambda o: o["fret"])


def capo_fret(key, shape):
    """The fret for playing `key` with `shape` chords, or None if that isn't one of its capos."""
    return next((o["fret"] for o in capo_options(key) if o["shape"] == shape), None)


def _moved_key(key, semis):
    parsed = parse_key(key)
    if not parsed:
        return key
    name = key_name(parsed[0] + semis, parsed[1])
    return name


def transpose_cho(cho, from_key, to_key):
    """Transpose every [chord] and the {key:} directive from one key to another."""
    src, dst = parse_key(from_key), parse_key(to_key)
    if not src or not dst:
        raise ValueError(f"can't parse key {from_key!r} -> {to_key!r}")
    semis = (dst[0] - src[0]) % 12
    target = key_name(dst[0], src[1])  # keep the song's mode
    flats = target in FLAT_KEYS
    out, has_key = [], False
    for line in cho.splitlines():
        m = DIRECTIVE_RE.match(line.strip())
        if m:
            if m[1].lower() == "key":
                # The first {key:} is the song's key; later ones are key changes, moved by the same
                # interval - and chords after a key change are spelled for the new key (Bb, not A#).
                if has_key:
                    moved = _moved_key(m[2], semis)
                    line = f"{{key: {moved}}}"
                    flats = moved in FLAT_KEYS
                else:
                    line = f"{{key: {target}}}"
                has_key = True
            out.append(line)
            continue
        out.append(CHORD_IN_LINE.sub(lambda c: f"[{transpose_chord(c[1], semis, flats)}]", line))
    if not has_key:
        i = next((i + 1 for i, l in enumerate(out) if l.startswith("{title")), 0)
        out.insert(i, f"{{key: {target}}}")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# Sections and arrangement
# --------------------------------------------------------------------------

ENV_SHORT = {"soc": "chorus", "sov": "verse", "sob": "bridge"}


def _section_start(line):
    """(kind, label) if this line starts a section, else None. kind 'ref' = {chorus} recall."""
    from pdf2chordpro import parse_heading

    m = DIRECTIVE_RE.match(line.strip())
    if not m:
        return None
    name, val = m[1].lower(), (m[2] or "").strip()
    if name.startswith("start_of_") or name in ENV_SHORT:
        env = ENV_SHORT.get(name) or name[9:]
        return env, val or env.title()
    if name == "chorus":
        return "ref", val or "Chorus"
    if name in ("comment", "c", "comment_italic", "ci") and parse_heading(val):
        return "comment", val
    return None


def _norm(label):
    return re.sub(r"[^a-z0-9]", "", label.lower())


def split_sections(cho):
    """Split ChordPro into (header_lines, sections, doc_order).

    Each section is {"id", "label", "kind", "lines"}; ids are the labels, made
    unique. doc_order lists section ids in document order, with {chorus}
    recalls resolved to the chorus they refer to.
    """
    header, sections, doc_order, cur = [], [], [], None
    for line in cho.splitlines():
        start = _section_start(line)
        if start:
            kind, label = start
            if kind == "ref":
                target = next((s for s in sections if _norm(s["label"]) == _norm(label)), None) or \
                         next((s for s in reversed(sections) if s["kind"] == "chorus"), None)
                if target:
                    doc_order.append(target["id"])
                cur = None
                continue
            cur = {"label": label, "kind": kind, "lines": [line]}
            sections.append(cur)
        elif cur is None:
            if not sections and (not line.strip() or DIRECTIVE_RE.match(line.strip())):
                header.append(line)
                continue
            if not line.strip():
                continue
            cur = {"label": "Start", "kind": "text", "lines": [line]}
            sections.append(cur)
        else:
            cur["lines"].append(line)
            continue
        # Assign a unique id to the section just opened.
        ids = {s["id"] for s in sections if "id" in s}
        cid, n = cur["label"], 2
        while cid in ids:
            cid, n = f"{cur['label']} #{n}", n + 1
        cur["id"] = cid
        doc_order.append(cid)
    for s in sections:
        while s["lines"] and not s["lines"][-1].strip():
            s["lines"].pop()
    while header and not header[-1].strip():
        header.pop()
    return header, sections, doc_order


def sheet_order(cho):
    """Map the sheet's 'Order: ...' roadmap onto sections: ([{id, times}], [unmatched names])."""
    header, sections, _ = split_sections(cho)
    text = next((DIRECTIVE_RE.match(l.strip())[2] for l in header
                 if DIRECTIVE_RE.match(l.strip()) and (DIRECTIVE_RE.match(l.strip())[2] or "").lower().startswith("order:")),
                None)
    if not text:
        return None, []
    order, missing = [], []
    for item in re.split(r"[,.;]\s*", text[6:]):
        item = item.strip()
        if not item:
            continue
        m = re.match(r"^(.*?)\s*\(?\s*x\s*(\d+)\s*\)?$", item, re.I)
        name, times = (m[1], int(m[2])) if m else (item, 1)
        sec = next((s for s in sections if _norm(s["label"]) == _norm(name)), None) or \
              next((s for s in sections if _norm(s["label"]).startswith(_norm(name))), None)
        if sec:
            order.append({"id": sec["id"], "times": times})
        else:
            missing.append(name)
    return order, missing


def _relabel(line, label):
    m = DIRECTIVE_RE.match(line.strip())
    return f"{{{m[1]}: {label}}}" if m else line


def arrange(cho, order, expand_repeats=True):
    """Rebuild ChordPro with sections in the given order ([{id, times}]).

    Repeats beyond the first are written out in full, or with expand_repeats
    False, as a one-line recall ({chorus: ...} / "Verse 1 (repeat)").
    """
    header, sections, _ = split_sections(cho)
    by_id = {s["id"]: s for s in sections}
    items = [(by_id[o["id"]], max(1, int(o.get("times", 1)))) for o in order if o.get("id") in by_id]

    def label(sec, times):
        return sec["label"] + (f" (x{times})" if times > 1 else "")

    out = [l for l in header if not re.match(r"^\{\s*(?:comment|c)\s*:\s*order:", l.strip(), re.I)]
    out.append(f"{{comment: Order: {', '.join(label(s, t) for s, t in items)}}}")
    seen = set()
    for sec, times in items:
        out.append("")
        lab = label(sec, times)
        if sec["id"] in seen and not expand_repeats:
            out.append(f"{{chorus: {lab}}}" if sec["kind"] == "chorus" else f"{{comment: {lab} (repeat)}}")
        else:
            lines = list(sec["lines"])
            if sec["kind"] != "text":
                lines[0] = _relabel(lines[0], lab)
            out.extend(lines)
        seen.add(sec["id"])
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# PDF rendering
# --------------------------------------------------------------------------

FONT, BOLD, ITALIC = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"
CHORD_COLOR = (0.11, 0.30, 0.60)
_WINANSI_SAFE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


# Fonts that can draw Chinese/Japanese/Korean (TrueType outlines, which reportlab needs).
UNICODE_FONTS = [
    os.environ.get("UNICODE_FONT", ""),
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simsun.ttc",
]
_unicode_font = []


def _get_unicode_font():
    if not _unicode_font:
        name = None
        for path in filter(None, UNICODE_FONTS):
            if os.path.exists(path):
                try:
                    pdfmetrics.registerFont(TTFont("UnicodeText", path, subfontIndex=0))
                    name = "UnicodeText"
                    break
                except Exception:
                    continue
        _unicode_font.append(name)
    return _unicode_font[0]


def _fit(text, font):
    """(text, font) that can actually be drawn.

    The built-in PDF fonts only cover Western European text; anything else
    (e.g. Chinese lyrics) switches to a Unicode TrueType font if one is installed.
    """
    t = text.translate(_WINANSI_SAFE)
    try:
        t.encode("cp1252")
        return t, font
    except UnicodeEncodeError:
        uf = _get_unicode_font()
        if uf:
            return t, uf
        return t.encode("cp1252", "replace").decode("cp1252"), font


def _draw(c, x, y, text, font, size):
    t, f = _fit(text, font)
    c.setFont(f, size)
    c.drawString(x, y, t)


def _safe(s):
    # The built-in PDF fonts only cover Latin-1-ish text.
    return s.translate(_WINANSI_SAFE).encode("cp1252", "replace").decode("cp1252")


def _parse(cho):
    """Parse ChordPro into (meta, blocks). A block is a list of body items."""
    meta, blocks, cur, env = {}, [], [], None

    def flush():
        nonlocal cur
        if cur:
            blocks.append(cur)
        cur = []

    for raw in cho.splitlines():
        line = raw.rstrip()
        m = DIRECTIVE_RE.match(line.strip())
        if m:
            name, val = m[1].lower(), (m[2] or "").strip()
            if name == "key" and "key" in meta:
                flush()
                cur.append(("label", f"Key: {val}", "note"))  # key change mid-song
                continue
            if name in ("title", "t", "subtitle", "st", "artist", "key", "capo", "tempo", "time",
                        "composer", "lyricist", "copyright"):
                name = {"t": "title", "st": "subtitle"}.get(name, name)
                meta.setdefault(name, []).append(val)
            elif name.startswith("start_of_") or name in ("soc", "sov", "sob"):
                flush()
                env = {"soc": "chorus", "sov": "verse", "sob": "bridge"}.get(name, name[9:])
                cur.append(("label", val or env.title(), env))
            elif name.startswith("end_of_") or name in ("eoc", "eov", "eob"):
                flush()
                env = None
            elif name == "chorus":
                flush()
                cur.append(("label", val or "Chorus", "ref"))
                flush()
            elif name in ("comment", "c", "comment_italic", "ci"):
                if val.lower().startswith("order:"):
                    meta.setdefault("order", []).append(val[6:].strip())
                    continue
                from pdf2chordpro import parse_heading
                if parse_heading(val) or not cur:
                    flush()
                    cur.append(("label", val, None))
                else:
                    cur.append(("label", val, "note"))
            continue
        if not line.strip():
            flush()
            continue
        cur.append(("line", line, env))
    flush()
    return meta, blocks


def _segments(line):
    """'A [G]love that' -> [(None, 'A '), ('G', 'love that')]."""
    parts = re.split(r"\[([^\]]*)\]", line)
    segs = [(None, parts[0])] if parts[0] else []
    for i in range(1, len(parts), 2):
        segs.append((parts[i], parts[i + 1]))
    return segs


SUP_SPLIT = re.compile(r"^(\(?[A-G][#b♯♭]?(?:m(?!aj))?)([^/]*)(/.*)?$")


def _chord_runs(chord, size, sup):
    """Split a chord into (text, font, size, rise) runs.

    With sup=True the extension is set as a superscript like the source sheet
    (Am7 -> Am + raised 7, G(4) -> G + raised (4)); slash basses stay full size.
    Annotations ([*Last x]) are italic.
    """
    if chord.startswith("*"):
        return [(_safe(chord[1:]), ITALIC, size, 0)]
    m = SUP_SPLIT.match(chord)
    if not sup or not m or not m[2]:
        return [(_safe(chord), BOLD, size, 0)]
    runs = [(_safe(m[1]), BOLD, size, 0), (_safe(m[2].rstrip(")") if m[1].startswith("(") else m[2]),
                                           BOLD, size * 0.72, size * 0.38)]
    tail = (m[3] or "") + (")" if m[1].startswith("(") and m[2].endswith(")") else "")
    if tail:
        runs.append((_safe(tail), BOLD, size, 0))
    return runs


def _runs_width(runs):
    return sum(stringWidth(t, f, s) for t, f, s, _ in runs)


def _chord_ops(text, size, gap=4, sup=False):
    """Lay out a line with inline [chords]: returns (ops, width, kind).

    ops are (x, font, size, rise, is_chord, text) relative to the line start.
    kind is 'lyric' (chords above lyrics), 'chords' (chord-only bar line) or 'text'.
    """
    segs = _segments(text)
    lyric_only = re.sub(r"\[[^\]]*\]", "", text)
    ops = []

    def put_chord(x, ch):
        for t, f, s, rise in _chord_runs(ch, size, sup):
            ops.append((x, f, s, rise, True, t))
            x += stringWidth(t, f, s)
        return x

    if not any(ch for ch, _ in segs):
        t, f = _fit(text, FONT)
        return [(0, f, size, 0, False, t)], stringWidth(t, f, size), "text"
    if not lyric_only.strip(" |.:"):
        x = 0
        for ch, t in segs:
            if ch:
                x = put_chord(x, ch)
            st, sf = _fit(t, FONT)
            ops.append((x, sf, size, 0, False, st))
            x += stringWidth(st, sf, size)
        return ops, x, "chords"
    # Chords above lyrics; lyric segments get pushed right if chords would collide.
    x = chord_end = width = 0
    for ch, t in segs:
        if ch:
            x = max(x, chord_end)
            chord_end = put_chord(x, ch) + gap
            width = max(width, chord_end - gap)
        st, sf = _fit(t, FONT)
        ops.append((x, sf, size, 0, False, st))
        x += stringWidth(st, sf, size)
    return ops, max(width, x), "lyric"


def render_pdf(cho, paper="A4", size=11, columns=1, sup=False):
    page_w, page_h = A4 if paper.upper() == "A4" else LETTER
    margin, col_gap = 44, 24
    columns = max(1, min(4, int(columns)))
    col_w = (page_w - 2 * margin - (columns - 1) * col_gap) / columns
    meta, blocks = _parse(cho)

    # Shrink the body font if the widest line doesn't fit its column.
    widest = max((_chord_ops(t, size, sup=sup)[1] for b in blocks for k, t, _ in b if k == "line"), default=0)
    if widest > col_w:
        size = max(6.5, size * col_w / widest)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))
    title = meta.get("title", ["Untitled"])[0]
    c.setTitle(title)

    # --- Header (full width, first page only)
    hs = 11
    y = page_h - margin
    y -= hs + 6
    _draw(c, margin, y, title, BOLD, hs + 9)
    y -= 8
    for sub in meta.get("subtitle", []) + meta.get("artist", []):
        y -= hs + 3
        _draw(c, margin, y, sub, FONT, hs)
    info = [f"{k.title()}: {meta[k][0]}" for k in ("key", "capo", "tempo", "time") if k in meta]
    if info:
        y -= hs + 5
        _draw(c, margin, y, "    ".join(info), BOLD, hs)
    for o in meta.get("order", []):
        y -= hs + 3
        text, font = _fit("Order: " + o, ITALIC)
        c.setFont(font, hs - 2)
        line = ""
        for w in text.split():
            if line and stringWidth(line + " " + w, font, hs - 2) > page_w - 2 * margin:
                c.drawString(margin, y, line)
                y -= hs
                line = w
            else:
                line = (line + " " + w).strip()
        c.drawString(margin, y, line)
    y -= hs * 0.6

    # --- Body, flowed down columns
    def item_height(kind, text):
        if kind == "label":
            return size + 6
        return (size + 3) * (2 if _chord_ops(text, size, sup=sup)[2] == "lyric" else 1) + 2

    body_top = y
    col = 0

    def next_column():
        nonlocal col, y, body_top
        col += 1
        if col >= columns:
            c.showPage()
            col, body_top = 0, page_h - margin
        y = body_top

    col_h = page_h - 2 * margin
    for block in blocks:
        h = sum(item_height(k, t) for k, t, _ in block) + size * 0.8
        if y - h < margin and h < col_h and y < body_top:
            next_column()
        if y < body_top:
            y -= size * 0.8
        for kind, text, env in block:
            ih = item_height(kind, text)
            if y - ih < margin:
                next_column()
            x0 = margin + col * (col_w + col_gap)
            y -= ih
            if kind == "label":
                c.setFillColorRGB(0, 0, 0)
                _draw(c, x0, y + 4, text, ITALIC if env in ("ref", "note") else BOLD, size)
                continue
            ops, _, lk = _chord_ops(text, size, sup=sup)
            for x, font, fsize, rise, is_chord, t in ops:
                c.setFont(font, fsize)
                c.setFillColorRGB(*(CHORD_COLOR if is_chord else (0, 0, 0)))
                cy = y + 3 + rise + (size + 2 if lk == "lyric" and is_chord else 0)
                c.drawString(x0 + x, cy, t)
    for cp in meta.get("copyright", []):
        c.setFillColorRGB(0.4, 0.4, 0.4)
        _draw(c, margin, margin / 2, cp, FONT, 7)
    c.save()
    return buf.getvalue()
