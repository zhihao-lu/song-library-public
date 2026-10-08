"""Setlists: merge songs from the library into one PDF, each in the chosen key."""

import io

import pdfplumber
from pypdf import PdfReader, PdfWriter

import chordtools as ct
import faithful
from pdf2chordpro import PDF_LOCK


def has_notation(pdf_path, pages=2):
    """True if the PDF looks like sheet music (staff lines) rather than a chord chart.

    Five-line staves show up as many long, thin horizontal lines. Transposing
    only the chord symbols on such a page would leave the notes in the old key,
    so the import skips these files.
    """
    with PDF_LOCK, pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[:pages]:
            min_len = 0.3 * page.width
            long_lines = [l for l in page.lines if abs(l["top"] - l["bottom"]) < 1 and l["width"] > min_len]
            thin_rects = [r for r in page.rects if r["height"] < 1.5 and r["width"] > min_len]
            if len(long_lines) + len(thin_rects) >= 10:
                return True
    return False


def render_song(library, slug, to=None, layout="original", paper="A4", columns=1, capo=None):
    """One song as PDF bytes, in key `to` (None = its chart's key). Returns (bytes, key).

    The song's shown chart is transposed, so every key looks like it. Only a chart that can't
    be transposed in its own look (a fixed chart, or a scan) gives way to a chart of its own
    in `to`, used as it is. With `capo`, a shape such as "C" (see chordtools.capo_options),
    the chords are drawn in that shape and the Key line says where the capo goes:
    "A · Capo 2 (play G)". The key returned is then "A, capo 2". A fixed chart can't be
    redrawn, so it ignores the capo.
    """
    song = library.get(slug)
    if not song:
        raise ValueError(f"'{slug}' is not in the library.")
    # A scan can't show a capo, so with one the song is typeset instead (below).
    fixed_look = not song["transposable"] or song.get("pdf_scan")
    own = library.chart(slug, to) if to and not capo and to != song["key"] and fixed_look else None
    chart = own or library.chart(slug)
    src = chart["key"]
    capo = capo if chart["transposable"] else None
    label = None
    if capo:
        sounding = ct.key_name(*ct.parse_key(to or src))
        fret = ct.capo_fret(sounding, capo)
        if fret is None:
            raise ValueError(f"{song['title']} can't be played with {capo} shapes in {sounding}.")
        label, to = f"{sounding} · Capo {fret} (play {capo})", capo
    change = bool(chart["transposable"] and to and ct.parse_key(to) != ct.parse_key(src))
    pdf_path = chart["pdf"]()
    if own and not pdf_path and own["has_pdf"]:
        raise ValueError(f"Couldn't get the {to} PDF of '{song['title']}' from Google Drive. Try again in a few minutes.")
    scan = chart["pdf_scan"]  # the PDF is a picture: its chords can't be redrawn in another key
    if scan and (change or capo):
        pdf_path = None  # typeset from the .cho in the new key
    if not pdf_path and chart["has_pdf"] and not (chart["lyrics_ok"] and chart["transposable"]):
        # Only the PDF shows this song properly; its .cho can't stand in for it.
        raise ValueError(f"Couldn't get the PDF of '{song['title']}' from Google Drive. Try again in a few minutes.")
    if pdf_path and (layout == "original" or not chart["lyrics_ok"] or not chart["transposable"]):
        data = faithful.transpose_pdf(pdf_path, src, to, label) if change or label else pdf_path.read_bytes()
        if chart["pdf_pages"]:
            data = _page_range(data, chart["pdf_pages"])
    else:
        cho = chart["cho"]()
        target, cho_key = (to if change else src), chart["cho_key"]
        if target and cho_key and ct.parse_key(target) != ct.parse_key(cho_key):
            cho = ct.transpose_cho(cho, cho_key, target)
        if label:
            cho = ct.set_key(cho, label)
        data = ct.render_pdf(cho, paper=paper, columns=columns, size=10 if columns > 1 else 11)
    if capo:
        return data, f"{sounding}, capo {fret}"
    return data, (to if change else src)


def _page_range(data, spec):
    """Keep pages 'first-last' (1-based) of a PDF, e.g. to drop a license page."""
    first, last = (int(x) for x in spec.split("-"))
    reader, writer = PdfReader(io.BytesIO(data)), PdfWriter()
    for page in reader.pages[first - 1:last]:
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def build_setlist(library, entries, layout="original", paper="A4", columns=1):
    """entries: [{"slug", "to"?, "capo"?}] -> one PDF with a bookmark per song, and where each song's
    pages are: (pdf, [{"first", "pages"}]), 0-based."""
    # One song at a time: PDF reading isn't thread-safe (see pdf2chordpro.PDF_LOCK).
    rendered = [render_song(library, e["slug"], e.get("to"), layout, paper, columns, e.get("capo")) for e in entries]
    writer, spans = PdfWriter(), []
    for e, (data, key) in zip(entries, rendered):
        start = len(writer.pages)
        for page in PdfReader(io.BytesIO(data)).pages:
            writer.add_page(page)
        spans.append({"first": start, "pages": len(writer.pages) - start})
        title = library.get(e["slug"])["title"]
        writer.add_outline_item(f"{title} ({key})" if key else title, start)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), spans
