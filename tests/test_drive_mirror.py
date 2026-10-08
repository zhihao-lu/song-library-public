"""Today's library, read from Google Drive (a fake one here) through the site's own API."""

import pytest

import chordtools as ct
from conftest import pdf_bookmarks
from fake_drive import FakeDrive

HOLY_GROUND = """{title: Holy Ground}
{artist: Test Band}
{key: A}

{start_of_verse}
[A]We are standing on [D]holy [E]ground
[F#m]Angels all a[D]round
{end_of_verse}
"""


@pytest.fixture
def drive():
    return FakeDrive()


@pytest.fixture
def mirror_client(app_module, sets_client, drive, tmp_path, monkeypatch):
    """The site with today's library: a .cho folder and a flat PDF folder in the fake Drive."""
    from drive_mirror import DriveMirror
    from library import SongLibrary

    drive.cho = drive.folder(".cho Files")
    drive.pdfs = drive.folder("PDF folder")
    mirror = DriveMirror(tmp_path / "drive-library", drive.url(drive.cho), drive.url(drive.pdfs), client=drive,
                         background=False)
    mirror.sync()  # the start-up sync, before anything is uploaded
    monkeypatch.setitem(app_module.app.extensions, "library", SongLibrary(mirror=mirror))
    return sets_client


def search(client, q):
    return [r["title"] for r in client.get("/api/library/search", query_string={"q": q}).json["results"]]


def test_an_uploaded_pdf_is_searchable_after_sync_now(mirror_client, drive):
    drive.upload(drive.pdfs, "holy-ground.pdf", ct.render_pdf(HOLY_GROUND))
    assert search(mirror_client, "holy ground") == []
    r = mirror_client.post("/api/library/sync")
    assert r.status_code == 200 and r.json["new_songs"] == ["Holy Ground"]
    assert search(mirror_client, "holy ground") == ["Holy Ground"]


def test_a_deleted_pdf_disappears_after_sync_now(mirror_client, drive):
    pdf = drive.upload(drive.pdfs, "holy-ground.pdf", ct.render_pdf(HOLY_GROUND))
    mirror_client.post("/api/library/sync")
    drive.delete(pdf)
    mirror_client.post("/api/library/sync")
    assert search(mirror_client, "holy ground") == []


def test_while_google_blocks_downloads_the_last_good_copy_is_served(mirror_client, drive):
    drive.upload(drive.pdfs, "holy-ground.pdf", ct.render_pdf(HOLY_GROUND))
    drive.upload(drive.cho, "holy-ground.cho", HOLY_GROUND)
    mirror_client.post("/api/library/sync")
    drive.blocked = True
    drive.upload(drive.cho, "new-song.cho", "{title: New Song}\n{key: C}\n\n[C]New\n")
    r = mirror_client.post("/api/library/sync")
    assert "rate-limiting" in r.json["error"]
    assert search(mirror_client, "holy ground") == ["Holy Ground"]
    built = mirror_client.post("/api/setlist/pdf", json={"songs": [{"slug": "holy-ground", "to": "B"}]})
    assert pdf_bookmarks(built.data) == ["Holy Ground (B)"]
