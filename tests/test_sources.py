"""The library from Drive sources: uploads anywhere in a source show up, tagged with it."""

from datetime import timedelta

from conftest import pdf_bookmarks, pdf_text, results, tagged
from make_charts import cho, pdf, picture, sheet_music


def english(drive, *more):
    return drive.path("YFM resources", "3. English Songs", *more)


def sync(client):
    r = client.post("/api/library/sync")
    assert r.status_code == 200
    return r.json


def test_a_chart_in_a_yfm_subfolder_is_found_tagged_yfm_after_sync_now(sources_client, drive):
    drive.upload(english(drive, "Holy Ground"), "Holy Ground - G.pdf", pdf(cho("Holy Ground")))
    assert tagged(sources_client, "holy ground") == []
    sync(sources_client)
    assert tagged(sources_client, "holy ground") == ["Holy Ground · YFM"]
    (song,) = results(sources_client, "holy ground")
    r = sources_client.post("/api/setlist/pdf", json={"songs": [{"slug": song["slug"], "to": "A"}]})
    assert pdf_bookmarks(r.data) == ["Holy Ground (A)"]


def test_folders_outside_the_sources_include_filter_are_not_taken_in(sources_client, drive):
    drive.upload(drive.path("YFM resources", "Meeting minutes"), "Holy Ground.pdf", pdf(cho("Holy Ground")))
    drive.upload(drive.yfm, "Loose Song.pdf", pdf(cho("Loose Song")))
    sync(sources_client)
    assert results(sources_client, "holy") == [] and results(sources_client, "loose") == []


def test_word_files_and_google_docs_are_charts_too(sources_client, drive):
    drive.upload(english(drive, "Word Song"), "Word Song.docx", pdf(cho("Word Song", key="D")))
    drive.google_doc(english(drive, "Doc Song"), "Doc Song", pdf(cho("Doc Song", key="E")))
    sync(sources_client)
    assert [(r["title"], r["key"]) for r in results(sources_client, "song")] == [("Doc Song", "E"), ("Word Song", "D")]


def test_a_real_pdf_wins_over_a_document_of_the_same_name(sources_client, drive):
    folder = english(drive, "Holy Ground")
    drive.upload(folder, "Holy Ground.docx", pdf(cho("Holy Ground", key="D")))
    drive.upload(folder, "Holy Ground.pdf", pdf(cho("Holy Ground", key="A")))
    sync(sources_client)
    assert [r["key"] for r in results(sources_client, "holy ground")] == ["A"]


def test_sheet_music_is_left_out_and_sync_now_says_why(sources_client, drive):
    drive.upload(drive.path("YFM resources", "5. Christmas Hymns"), "Silent Night.pdf", sheet_music())
    r = sync(sources_client)
    assert r["failed"] == ["Silent Night.pdf: sheet music (staff notation)"]
    assert results(sources_client, "silent") == []


def test_an_unreadable_chart_is_used_exactly_as_uploaded(sources_client, drive):
    drive.upload(english(drive, "Holy Ground"), "Holy Ground - C.pdf", picture(pdf(cho("Holy Ground", key="C"))))
    sync(sources_client)
    (song,) = results(sources_client, "holy ground")
    assert (song["transposable"], song["key"]) == (False, "C")
    r = sources_client.post("/api/setlist/pdf", json={"songs": [{"slug": song["slug"], "to": "E"}]})
    assert pdf_bookmarks(r.data) == ["Holy Ground (C)"]
    assert pdf_text(r.data).strip() == ""  # the picture, as it is


def test_only_new_and_changed_files_are_downloaded_and_deleted_ones_go(sources_client, drive):
    folder = english(drive, "Holy Ground")
    kept = drive.upload(folder, "Holy Ground.pdf", pdf(cho("Holy Ground", key="G")))
    changed = drive.upload(english(drive, "Open Sky"), "Open Sky.pdf", pdf(cho("Open Sky", key="G")))
    sync(sources_client)
    drive.downloads.clear()
    drive.replace(changed, pdf(cho("Open Sky", key="B")))
    sync(sources_client)
    assert drive.downloads == [changed]
    assert [r["key"] for r in results(sources_client, "open sky")] == ["B"]
    drive.delete(kept)
    sync(sources_client)
    assert results(sources_client, "holy ground") == []


def test_the_sources_are_checked_every_hour(sources_client, drive, clock):
    sync(sources_client)
    drive.upload(english(drive, "Holy Ground"), "Holy Ground.pdf", pdf(cho("Holy Ground")))
    clock.now += timedelta(minutes=59)
    assert results(sources_client, "holy ground") == []
    clock.now += timedelta(minutes=1)
    assert tagged(sources_client, "holy ground") == ["Holy Ground · YFM"]


def test_while_google_blocks_downloads_the_last_good_copy_is_served(sources_client, drive):
    song = drive.upload(english(drive, "Holy Ground"), "Holy Ground.pdf", pdf(cho("Holy Ground", key="G")))
    sync(sources_client)
    drive.blocked = True
    drive.replace(song, pdf(cho("Holy Ground", key="B")))
    drive.upload(english(drive, "Open Sky"), "Open Sky.pdf", pdf(cho("Open Sky")))
    r = sync(sources_client)
    assert "rate-limiting" in r["error"]
    assert [r["key"] for r in results(sources_client, "holy ground")] == ["G"]
    drive.blocked = False
    sync(sources_client)
    assert [r["key"] for r in results(sources_client, "holy ground")] == ["B"]
    assert tagged(sources_client, "open sky") == ["Open Sky · YFM"]


def test_library_status_says_which_library_is_served(sources_client, drive):
    drive.upload(english(drive, "Holy Ground"), "Holy Ground.pdf", pdf(cho("Holy Ground")))
    sync(sources_client)
    status = sources_client.get("/api/library/status").json
    assert status["mode"] == "sources" and status["songs"] == 1


def test_a_setting_picks_the_library_and_each_keeps_its_own_copy(app_module, tmp_path, monkeypatch):
    from library import SongLibrary, library_from_env
    from sources_library import SourcesLibrary
    monkeypatch.delenv("LIBRARY_DIR")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("DRIVE_CACHE_DIR", str(tmp_path / "drive-library"))
    monkeypatch.setenv("SOURCES_CACHE_DIR", str(tmp_path / "sources-library"))
    monkeypatch.setenv("SETS_DB", str(tmp_path / "sets.db"))
    monkeypatch.delenv("LIBRARY_MODE", raising=False)
    today = library_from_env()
    assert isinstance(today, SongLibrary) and today.folder == tmp_path / "drive-library"
    monkeypatch.setenv("LIBRARY_MODE", "sources")
    new = library_from_env()
    assert isinstance(new, SourcesLibrary) and new.folder == tmp_path / "sources-library"
    monkeypatch.setenv("LIBRARY_MODE", "mirror")
    assert isinstance(library_from_env(), SongLibrary)


def test_a_restarted_site_serves_its_local_copy_while_the_first_check_runs(make_sources_library, drive, monkeypatch):
    import threading
    import time
    drive.upload(english(drive, "Holy Ground"), "Holy Ground.pdf", pdf(cho("Holy Ground")))
    make_sources_library().sync()
    listing = threading.Event()
    slow = drive.children
    monkeypatch.setattr(drive, "children", lambda *a: (listing.wait(10), slow(*a))[1])  # a check taking minutes
    restarted = make_sources_library()
    restarted.background = True
    started = time.monotonic()
    assert [s["title"] for s in restarted.search("holy ground")] == ["Holy Ground"]
    assert time.monotonic() - started < 2
    listing.set()


def test_the_first_start_takes_over_the_test_runs_copy(make_sources_library, drive, tmp_path, monkeypatch):
    import threading
    from library_ids import IdStore
    from sources_library import SourcesLibrary
    drive.upload(english(drive, "Holy Ground"), "Holy Ground.pdf", pdf(cho("Holy Ground")))
    test_run = make_sources_library("test-run")
    test_run.sync()
    listing = threading.Event()
    slow = drive.children
    monkeypatch.setattr(drive, "children", lambda *a: (listing.wait(10), slow(*a))[1])
    drive.downloads.clear()
    live = SourcesLibrary(tmp_path / "sources-library", drive, IdStore(tmp_path / "sets.db"), sources=test_run.sources,
                          cho_url=drive.url(drive.cho), flat_url=drive.url(drive.flat), clock=test_run.clock,
                          adopt_from=[tmp_path / "test-run"])
    assert [s["title"] for s in live.search("holy ground")] == ["Holy Ground"]
    listing.set()
    assert drive.downloads == []


def test_hand_fixed_charts_the_mirror_has_are_copied_not_downloaded(drive, sources, tmp_path, clock):
    import hashlib
    import json
    from library_ids import IdStore
    from sources_library import SourcesLibrary
    text = cho("Holy Ground")
    drive.upload(drive.cho, "holy-ground.cho", text)
    mirror = tmp_path / "drive-library"
    mirror.mkdir()
    (mirror / "holy-ground.cho").write_text(text)
    (mirror / "_drive.json").write_text(json.dumps({"holy-ground.cho": hashlib.md5(text.encode()).hexdigest()}))
    library = SourcesLibrary(tmp_path / "test-run", drive, IdStore(":memory:"), sources=sources,
                             cho_url=drive.url(drive.cho), flat_url=drive.url(drive.flat),
                             clock=lambda: clock().timestamp(), background=False, adopt_from=[mirror])
    library.sync()
    assert drive.downloads == [] and [s["title"] for s in library.search("holy ground")] == ["Holy Ground"]


def test_what_a_check_took_in_before_a_block_is_kept(make_sources_library, drive, monkeypatch):
    for n in range(3):
        drive.upload(drive.cho, f"song-{n}.cho", cho(f"Song {n}"))
    real = drive.download
    calls = []

    def block_after_two(file_id, rk=None):
        calls.append(file_id)
        if len(calls) > 2:
            drive.blocked = True
        return real(file_id, rk)
    monkeypatch.setattr(drive, "download", block_after_two)
    make_sources_library().sync()
    drive.blocked = False
    calls.clear()
    make_sources_library().sync()  # (a restart)
    assert len(calls) == 1


def test_files_downloaded_by_hand_are_copied_not_downloaded(drive, sources, tmp_path, clock):
    from library_ids import IdStore
    from sources_library import SourcesLibrary
    sheet = pdf(cho("Holy Ground"))
    drive.upload(drive.path("ASE", "Holy Ground"), "Holy Ground - G.pdf", sheet)
    by_hand = tmp_path / "drive-downloads" / "ASE-20261001T1200Z-001" / "ASE" / "Holy Ground"
    by_hand.mkdir(parents=True)
    (by_hand / "Holy Ground - G.pdf").write_bytes(sheet)
    library = SourcesLibrary(tmp_path / "test-run", drive, IdStore(":memory:"), sources=sources,
                             cho_url=drive.url(drive.cho), flat_url=drive.url(drive.flat),
                             clock=lambda: clock().timestamp(), background=False,
                             adopt_from=[tmp_path / "drive-downloads"])
    library.sync()
    assert drive.downloads == [] and [s["title"] for s in library.search("holy ground")] == ["Holy Ground"]


def test_a_block_doesnt_stop_files_that_need_no_download(drive, sources, tmp_path, clock):
    from fake_drive import WORD_MAGIC
    from library_ids import IdStore
    from sources_library import SourcesLibrary
    folder = drive.path("YFM resources", "3. English Songs")
    drive.upload(folder, "A Needs Downloading.pdf", pdf(cho("Needs Downloading")))
    by_hand = tmp_path / "drive-downloads"
    by_hand.mkdir()
    for name, text in (("Open Sky.pdf", cho("Open Sky")), ("Word Song.docx", cho("Word Song"))):
        data = pdf(text)
        drive.upload(folder, name, data)
        (by_hand / name).write_bytes(WORD_MAGIC + data if name.endswith(".docx") else data)
    drive.blocked = True
    library = SourcesLibrary(tmp_path / "test-run", drive, IdStore(":memory:"), sources=sources,
                             cho_url=drive.url(drive.cho), flat_url=drive.url(drive.flat),
                             clock=lambda: clock().timestamp(), background=False, adopt_from=[by_hand])
    r = library.sync()
    assert "rate-limiting" in r["error"]
    assert [s["title"] for q in ("open sky", "word song", "needs") for s in library.search(q)] == ["Open Sky", "Word Song"]


def test_a_book_of_many_songs_is_left_out_before_it_is_read(sources_client, drive):
    import io
    from pypdf import PdfReader, PdfWriter
    page = PdfReader(io.BytesIO(pdf(cho("Holy Ground")))).pages[0]
    book = PdfWriter()
    for _ in range(40):
        book.add_page(page)
    out = io.BytesIO()
    book.write(out)
    drive.upload(drive.path("MM ministry", "04 赞美之泉 SongBooks"), "18 从心合一.pdf", out.getvalue())
    r = sync(sources_client)
    assert r["failed"] == ["18 从心合一.pdf: a book of many songs (40 pages): left out"]
    assert results(sources_client, "holy ground") == []


def test_until_the_first_check_after_a_restart_ends_there_is_no_sync_time(make_sources_library, drive, monkeypatch):
    import threading
    drive.upload(english(drive, "Holy Ground"), "Holy Ground.pdf", pdf(cho("Holy Ground")))
    make_sources_library().sync()
    listing = threading.Event()
    slow = drive.children
    monkeypatch.setattr(drive, "children", lambda *a: (listing.wait(10), slow(*a))[1])
    restarted = make_sources_library()
    restarted.background = True
    assert restarted.status()["synced_at"] is None  # not 0: the page showed "synced 497668 h ago"
    listing.set()
    restarted.sync(force=True)  # waits for the check already running, then checks again
    assert restarted.status()["synced_at"]
