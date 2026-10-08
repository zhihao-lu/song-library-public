#!/usr/bin/env python3
"""Web portal: build setlists from the song library, or convert a single PDF.

  /          setlist builder - search the song library, pick keys, download one PDF
  /convert   upload one PDF -> ChordPro, or a transposed PDF

PDF output modes:
  original - the source PDF with only the chords (and Key line) swapped,
             so fonts, columns, spacing and logos stay exactly as they were.
  custom   - re-typeset from ChordPro, with adjustable columns / paper / order.

Run:  .venv/bin/python app.py   then open http://localhost:5050
"""

import base64
import hmac
import io
import os
import re
import shutil
import tempfile
import uuid
from collections import OrderedDict
from pathlib import Path

import pypdfium2 as pdfium
from flask import Flask, Response, current_app, jsonify, request, send_file, send_from_directory

import config  # noqa: F401  (loads .env before anything reads settings)
import analytics
import chordtools as ct
import faithful
from pages import page
import pdf2chordpro as p2c
import sets_api
import song_stats
from library import library_from_env
from setlist import build_setlist

app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

# Share links are copied as <this>#s=<code>, e.g. a church site's redirect to this one (the
# #... part survives redirects). Unset: this site's own address.
app.config["SHARE_LINK_BASE"] = os.environ.get("SHARE_LINK_BASE") or None
app.register_blueprint(sets_api.bp)
app.register_blueprint(analytics.bp)
app.register_blueprint(song_stats.bp)

# Optional shared password (HTTP basic auth) for public deployments.
APP_PASSWORD = os.environ.get("APP_PASSWORD")


@app.get("/healthz")
def healthz():
    """Liveness check for the host (Render); needs no password and reveals nothing."""
    return "ok"


@app.before_request
def require_password():
    if not APP_PASSWORD or request.path == "/healthz" or analytics.stats_allowed():
        return None
    auth = request.authorization
    if auth and hmac.compare_digest(auth.password or "", APP_PASSWORD):
        return None
    return Response("Password required.", 401, {"WWW-Authenticate": 'Basic realm="Song Sheet Converter"'})

# Uploaded PDFs, kept so "original" mode can redraw them. Oldest are dropped.
UPLOAD_DIR = Path(tempfile.mkdtemp(prefix="songsheets-"))
UPLOADS = OrderedDict()
MAX_UPLOADS = 30


# The song library the site serves. Built here from the environment; tests put their own in.
app.extensions["library"] = library_from_env()


def _library():
    return current_app.extensions["library"]


@app.get("/api/features")
def features():
    """Settings the pages need: the address share links start with (None: this site's own)."""
    return jsonify(share_link_base=app.config.get("SHARE_LINK_BASE"))


@app.get("/")
def setlist_page():
    return page("setlist.html")


@app.get("/convert")
def convert_page():
    return page("index.html")


# ---------------------------------------------------------------- song library

@app.get("/api/library/status")
def library_status():
    return jsonify(_library().status())


@app.post("/api/library/sync")
def library_sync():
    """Pick up files just dropped into Drive, instead of waiting for the next automatic sync."""
    result = _library().sync()
    if result is None:
        return jsonify(error="The library isn't in Google Drive."), 400
    return jsonify(songs=_library().count(), **result)


@app.get("/api/library/search")
def library_search():
    """?q=<words>, and optionally &sources=YFM,ASE: only arrangements tagged with one of those."""
    sources = [s.strip() for s in request.args.get("sources", "").split(",") if s.strip()]
    return jsonify(results=_library().search(request.args.get("q", ""), sources=sources or None))


def _setlist_from(body):
    """The setlist as (pdf, where each song's pages are). Songs not in the library (a past set's,
    kept by title) have no pages and are left out."""
    songs = [e for e in body.get("songs") or [] if e.get("slug")]
    if not songs:
        raise ValueError("Add at least one song.")
    return build_setlist(_library(), songs, layout=body.get("layout", "original"),
                         paper=body.get("paper", "A4"), columns=int(body.get("columns", 1)))


@app.post("/api/setlist/preview")
def setlist_preview():
    """The setlist rendered as page images, which display in any browser (no PDF viewer needed).
    `scale` (pixels per point, up to 3) asks for sharper images, e.g. for a phone held close."""
    body = request.json or {}
    try:
        pdf, spans = _setlist_from(body)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    scale = min(3.0, max(1.0, float(body.get("scale") or 1.6)))  # 1.6: ~115 dpi, crisp on screen, small to send
    images = []
    with p2c.PDF_LOCK:  # pdfium isn't thread-safe either
        doc = pdfium.PdfDocument(pdf)
        for pdf_page in doc:
            images.append(pdf_page.render(scale=scale).to_pil())
        doc.close()
    pages = []
    for img in images:
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        pages.append("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode())
    return jsonify(pages=pages, songs=spans)


@app.post("/api/setlist/pdf")
def setlist_pdf():
    body = request.json or {}
    try:
        pdf, _ = _setlist_from(body)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    name = re.sub(r'[\\/:*?"<>|]+', "", body.get("name") or "Setlist").strip() or "Setlist"
    return send_file(io.BytesIO(pdf), mimetype="application/pdf", download_name=f"{name}.pdf")


# ---------------------------------------------------------------- single-PDF converter


@app.post("/api/convert")
def api_convert():
    f = request.files.get("pdf")
    if not f or not f.filename.lower().endswith(".pdf"):
        return jsonify(error="Please upload a PDF file."), 400
    # "Song (D).pdf" / "Song-chords-C.pdf" -> "Song" / "Song-chords", so exports can be named "Song (Eb)".
    stem = re.sub(r"(?:\s*[(\[]\s*[A-G][#b]?m?\s*[)\]]|[_-]+[A-G][#b]?m?)\s*$", "", Path(f.filename).stem) or "song"
    upload_id = uuid.uuid4().hex
    path = UPLOAD_DIR / f"{upload_id}.pdf"
    f.save(path)
    try:
        if not p2c.has_text(path):
            shutil.move(p2c.ocr_pdf(path), path)
        stats = {}
        lines = p2c.extract_lines(path, stats=stats)
        if not lines:
            raise ValueError("No text found in the PDF.")
        cho = p2c.convert(lines, source_name=stem, guess_key=False)
    except SystemExit as e:  # ocr_pdf exits when ocrmypdf is missing
        path.unlink(missing_ok=True)
        return jsonify(error=str(e)), 422
    except Exception as e:
        path.unlink(missing_ok=True)
        return jsonify(error=f"Conversion failed: {e}"), 422
    UPLOADS[upload_id] = path
    while len(UPLOADS) > MAX_UPLOADS:
        UPLOADS.popitem(last=False)[1].unlink(missing_ok=True)
    w, _ = stats.get("page_size", (595, 842))
    layout = {
        "columns": stats.get("columns", 1),
        "font_size": stats.get("font_size", 11),
        "paper": "Letter" if abs(w - 612) < abs(w - 595) else "A4",
        "superscripts": stats.get("superscripts", False),
    }
    return jsonify(cho=cho, name=stem, upload_id=upload_id, layout=layout,
                   **_key_info(cho), **_section_info(cho))


@app.post("/api/sections")
def api_sections():
    return jsonify(**_section_info(request.json.get("cho", "")))


def _section_info(cho):
    _, sections, doc_order = ct.split_sections(cho)
    order, missing = ct.sheet_order(cho)
    return {
        "sections": [{"id": s["id"], "kind": s["kind"]} for s in sections],
        "doc_order": [{"id": i, "times": 1} for i in doc_order],
        "sheet_order": order,
        "sheet_missing": missing,
    }


def _key_info(cho):
    return ct.key_info(cho)


def _transposed(body):
    """Apply the requested arrangement, then transposition."""
    cho, src, dst = body.get("cho", ""), body.get("from"), body.get("to")
    if body.get("order") is not None:
        cho = ct.arrange(cho, body["order"], expand_repeats=body.get("expand_repeats", True))
    if src and dst and ct.parse_key(src) != ct.parse_key(dst):
        return ct.transpose_cho(cho, src, dst)
    return cho


@app.post("/api/transpose")
def api_transpose():
    try:
        return jsonify(cho=_transposed(request.json))
    except ValueError as e:
        return jsonify(error=str(e)), 400


@app.post("/api/pdf")
def api_pdf():
    body = request.json
    try:
        if body.get("mode", "original") == "original":
            path = UPLOADS.get(body.get("upload_id", ""))
            if not path or not path.exists():
                return jsonify(error="The original PDF is no longer available — please upload it again."), 410
            pdf = faithful.transpose_pdf(path, body["from"], body["to"])
        else:
            pdf = ct.render_pdf(_transposed(body), paper=body.get("paper", "A4"),
                                size=float(body.get("font_size", 11)),
                                columns=int(body.get("columns", 1)),
                                sup=bool(body.get("superscripts")))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return send_file(io.BytesIO(pdf), mimetype="application/pdf",
                     download_name=f"{body.get('name', 'song')}.pdf")


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5050)), debug=False)
