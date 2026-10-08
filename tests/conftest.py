"""Test fixtures: the app, run through Flask's test client against a tiny song library.

The library is written into a temp folder at the start of the run (song sheets stay out of
the repo): a major-key song with its own PDF, a minor-key song with only a .cho, a
fixed chart (an as-is song, which can't be transposed), and a song titled
in English and Chinese.
"""

import importlib
import io
import os

import pytest
from pypdf import PdfReader

SONGS = {
    "morning-light": """{title: Morning Light}
{artist: Test Band}
{key: G}

{start_of_verse: Verse 1}
[G]Morning [Cmaj7]light is [D/F#]breaking
[Em7]All the [C]earth is [D]waking
{end_of_verse}

{start_of_chorus}
[C]Sing a[G]loud, [D]sing a[Em7]gain
{end_of_chorus}
""",
    "quiet-waters": """{title: Quiet Waters}
{key: Em}

{start_of_verse}
[Em]Quiet [Am7]waters [B7]flow
[Em]Deep and [D]slow
{end_of_verse}
""",
    # Long enough to need several pages.
    "long-road": "{title: Long Road}\n{key: C}\n\n" + "".join(
        f"{{start_of_verse: Verse {n}}}\n" + "[C]Walking the [F]long road [G]home\n" * 6 + "{end_of_verse}\n\n" for n in range(1, 13)),
    "open-sky": """{title: Open Sky}
{key: D}

[D]Under an [G]open [A]sky
""",
    # A scanned sheet (shown as the scan in its own key), with another scan in C.
    "old-hymn": """{title: Old Hymn}
{key: G}
{meta: pdf scan}
{meta: keys G, C}

[G]Old [Cmaj7]hymn we [D7]sing
""",
    # A song titled in English and Chinese, written with 祢 (for God).
    "ywwydpw": """{title: Only Hope 祢是永恒唯一的盼望}
{key: F}

[F]永恒唯一的[Bb]盼望
""",
    "fixed-chart": """{title: Fixed Chart}
{key: D}
{meta: transpose no}

[D]Only as it [A]is
""",
}


@pytest.fixture(scope="session")
def library_dir(tmp_path_factory):
    import chordtools as ct

    folder = tmp_path_factory.mktemp("library")
    for slug, cho in SONGS.items():
        (folder / f"{slug}.cho").write_text(cho, encoding="utf-8")
    # PDFs for original layout: the song's own chart, and the fixed chart as-is.
    for slug in ("morning-light", "fixed-chart"):
        (folder / f"{slug}.pdf").write_bytes(ct.render_pdf(SONGS[slug]))
    (folder / "old-hymn.pdf").write_bytes(ct.render_pdf(SONGS["old-hymn"]))
    (folder / "old-hymn@C.pdf").write_bytes(ct.render_pdf(ct.transpose_cho(SONGS["old-hymn"], "G", "C")))
    # A chart whose sheet doesn't say its key.
    (folder / "open-sky.pdf").write_bytes(ct.render_pdf(SONGS["open-sky"].replace("{key: D}\n", "")))
    return folder


@pytest.fixture(scope="session")
def app_module(library_dir):
    os.environ["LIBRARY_DIR"] = str(library_dir)
    os.environ["APP_PASSWORD"] = ""
    return importlib.import_module("app")


@pytest.fixture
def client(sets_client):
    return sets_client


def pdf_text(data):
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def pdf_bookmarks(data):
    return [item.title for item in PdfReader(io.BytesIO(data)).outline]


class Clock:
    """The app's 'now', which tests move by hand."""

    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    from datetime import datetime
    return Clock(datetime(2026, 9, 30, 20, 0))  # a Wednesday evening


@pytest.fixture
def sets_client(app_module, tmp_path, clock, monkeypatch):
    """The app with a fresh sets database and a clock tests control."""
    config = app_module.app.config
    monkeypatch.setitem(config, "SETS_DB", tmp_path / "sets.db")
    monkeypatch.setitem(config, "NOW", clock)
    return app_module.app.test_client()


# ---------------------------------------------------------------- the three-source library

@pytest.fixture
def drive(monkeypatch):
    """A fake Google Drive, with LibreOffice's part played by the fake's Word files."""
    import doc2pdf
    from fake_drive import FakeDrive, word_to_pdf
    monkeypatch.setattr(doc2pdf, "word_to_pdf", word_to_pdf)
    return FakeDrive()


@pytest.fixture
def sources(drive):
    """The three sources' folders in the fake Drive, plus '.cho Files' and the flat PDF folder."""
    from sources_library import Source
    drive.yfm = drive.folder("YFM resources")
    drive.ase = drive.folder("ASE")
    drive.mm = drive.folder("MM ministry")
    drive.cho = drive.folder(".cho Files")
    drive.flat = drive.folder("PDF folder")
    return (
        Source("YFM", drive.url(drive.yfm), include=r"song|composition|hymn|music book|vocal",
               only_new=r"^7\. Hillsong Digital Music Book(?:/|$)"),
        Source("MM", drive.url(drive.mm), include=r"^(?:01 Chinese Song Scores|02 English Song Scores|"
                                                  r"03 Hymns Scores|04 赞美之泉 SongBooks)$"),
        Source("ASE", drive.url(drive.ase), leave_out=r"^Song_Book\+March\+2014\.pdf$"),
    )


@pytest.fixture
def make_sources_library(drive, sources, tmp_path, clock):
    """Build a sources library over the fake Drive (as a restart of the site would)."""
    from library_ids import IdStore
    from sources_library import SourcesLibrary

    def make(folder="sources-library"):
        return SourcesLibrary(tmp_path / folder, drive, IdStore(tmp_path / "sets.db"), sources=sources,
                              cho_url=drive.url(drive.cho), flat_url=drive.url(drive.flat),
                              clock=lambda: clock().timestamp(), background=False)
    return make


@pytest.fixture
def sources_client(app_module, sets_client, make_sources_library, monkeypatch):
    """The site serving the three-source library from the fake Drive."""
    library = make_sources_library()
    library.sync()  # the start-up check, before anything is uploaded
    monkeypatch.setitem(app_module.app.extensions, "library", library)
    sets_client.library = library
    return sets_client


def results(client, q, **params):
    return client.get("/api/library/search", query_string={"q": q, **params}).json["results"]


def tagged(client, q, **params):
    """Search results as the builder shows them: 'Title · tag'."""
    return [f"{r['title']} · {r['tag']}" for r in results(client, q, **params)]
