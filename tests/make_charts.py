"""Charts made on the fly for the three-source library tests (charts stay out of the repo)."""

import io

import chordtools as ct
from pdf2chordpro import PDF_LOCK


def cho(title, key="G", artist=None, verses=None, chorus=None):
    """A chord chart in `key`. The music (lyrics and chords relative to the key) is set by
    `verses` / `chorus`: lists of lines written in G."""
    verses = verses or [
        "[G]Holy is the [C]Lord our [D]God",
        "[Em]Every morning [C]new his [D]mercy",
        "[G]Light upon the [C]hills of [D]gold",
        "[Em]Hear the people [C]sing his [D]glory",
    ]
    chorus = chorus or ["[C]Hallelu[G]jah, [D]hallelu[Em]jah", "[C]Sing it [G]out and [D]sing a[G]gain"]
    text = f"{{title: {title}}}\n" + (f"{{artist: {artist}}}\n" if artist else "") + "{key: G}\n\n"
    text += "{start_of_verse: Verse 1}\n" + "\n".join(verses) + "\n{end_of_verse}\n\n"
    text += "{start_of_chorus}\n" + "\n".join(chorus) + "\n{end_of_chorus}\n"
    return ct.transpose_cho(text, "G", key) if key != "G" else text


OTHER_CHORDS = {  # the same words with another progression: a different arrangement
    "verses": [
        "[Am]Holy is the [F]Lord our [C]God [G]",
        "[Am]Every morning [F]new his [C]mer[E]cy",
        "[F]Light upon the [G]hills of [Em]gold [Am]",
        "[Dm]Hear the people [G]sing his [C]glo[E7]ry",
    ],
    "chorus": ["[F]Halle[G]lujah, [Em]halle[Am]lujah", "[Dm]Sing it [G]out and [C]sing a[C/E]gain"],
}


def pdf(text, **options):
    """A readable chart: `text` (ChordPro) typeset."""
    return ct.render_pdf(text, **options)


def long_pdf(text):
    """The same chart over two pages: its repeats written out, in a bigger font."""
    head, body = text.split("\n\n", 1)
    verse, chorus = body.split("\n\n", 1)
    return ct.render_pdf("\n\n".join([head, verse, chorus, verse.replace("Verse 1", "Verse 2"), chorus, chorus]),
                         size=20)


def picture(pdf_bytes):
    """A chart as a scanned sheet: its pages as pictures, with no text to read."""
    import pypdfium2 as pdfium
    with PDF_LOCK:
        doc = pdfium.PdfDocument(pdf_bytes)
        images = [page.render(scale=1.5).to_pil().convert("RGB") for page in doc]
        doc.close()
    out = io.BytesIO()
    images[0].save(out, "PDF", save_all=True, append_images=images[1:], resolution=108)
    return out.getvalue()


def sheet_music():
    """A page of sheet music: staves of five long lines, with chord names above them."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=A4)
    c.drawString(72, 800, "Hymn Tune")
    for staff in range(4):
        y = 700 - staff * 120
        c.drawString(80, y + 50, "G      C      D      G")
        for line in range(5):
            c.line(60, y - line * 7, 540, y - line * 7)
    c.save()
    return out.getvalue()
