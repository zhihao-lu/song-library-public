"""Word files and Google Docs as PDFs, so they go through the same steps as any PDF.

  Word (.doc, .docx)   converted with LibreOffice (soffice), which must be installed;
                       the Docker image has it
  Google Docs          exported as PDF by Drive

A converted document is named like the PDF it stands for: "Song (G).docx" and a
Google Doc called "Song (G)" both become "Song (G).pdf".

Chords over lyrics survive only as well as the document lines them up: a Google
Doc converted from Word has often lost its spacing already, and then so has the PDF.
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

GDOC = "application/vnd.google-apps.document"
WORD_RE = re.compile(r"\.docx?$", re.I)
SOFFICE_PATHS = ("soffice", "libreoffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice")


class ConversionError(RuntimeError):
    pass


def is_word(name):
    return bool(WORD_RE.search(name))


def is_document(name, mime=""):
    """A Word file or a Google Doc: something to turn into a PDF first."""
    return mime == GDOC or is_word(name)


def pdf_name(name):
    """The PDF a document stands for: 'Song (G).docx' / Google Doc 'Song (G)' -> 'Song (G).pdf'."""
    return WORD_RE.sub("", name) + ".pdf"


def soffice():
    for cmd in SOFFICE_PATHS:
        found = shutil.which(cmd) or (cmd if os.path.isfile(cmd) else None)
        if found:
            return found
    raise ConversionError("LibreOffice isn't installed, so Word files can't be converted "
                          "(brew install --cask libreoffice, or run this in the Docker image).")


def word_to_pdf(data, name="document.docx"):
    """A Word file's bytes -> PDF bytes."""
    with tempfile.TemporaryDirectory(prefix="doc2pdf-") as tmp:
        tmp = Path(tmp)
        src = tmp / ("in" + Path(name).suffix.lower())
        src.write_bytes(data)
        # A profile of its own, so conversions can run side by side.
        cmd = [soffice(), f"-env:UserInstallation={(tmp / 'profile').as_uri()}",
               "--headless", "--convert-to", "pdf", "--outdir", str(tmp), str(src)]
        try:
            run = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            raise ConversionError(f"LibreOffice took too long converting {name}")
        out = tmp / "in.pdf"
        if not out.exists():
            raise ConversionError(f"LibreOffice couldn't convert {name}: {(run.stderr or run.stdout).strip()[:200]}")
        return out.read_bytes()


def fetch_pdf(client, file_id, name, mime, rk=None):
    """A Drive file as PDF bytes, converting a Word file or Google Doc on the way."""
    if mime == GDOC:
        return client.export(file_id, "application/pdf", rk)
    data = client.download(file_id, rk)
    return word_to_pdf(data, name) if is_word(name) else data
