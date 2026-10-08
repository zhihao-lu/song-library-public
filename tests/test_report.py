"""The test run: the new library built beside the site, compared with today's in one report."""

import hashlib

from conftest import results
from library_ids import IdStore
from library_report import findings, render, todays_songs
from make_charts import OTHER_CHORDS, cho, pdf, sheet_music
from test_ids import todays_song


def snapshot(folder):
    return {p.relative_to(folder): hashlib.md5(p.read_bytes()).hexdigest() for p in folder.rglob("*") if p.is_file()}


def todays_library(tmp_path, songs):
    """Today's library's local copy: a folder of .cho files."""
    folder = tmp_path / "drive-library"
    folder.mkdir()
    for slug, text in songs.items():
        (folder / f"{slug}.cho").write_text(text, encoding="utf-8")
    return folder


def mixed_drive(drive):
    """A known mix: kept, folded, merged, new, v2, only-in-the-flat-folder and skipped files."""
    todays_song(drive, "holy-ground-g", "Holy Ground - G.pdf", cho("Holy Ground"))
    todays_song(drive, "holy-ground-a", "Holy Ground - A.pdf", cho("Holy Ground", key="A"))
    drive.upload(drive.path("YFM resources", "3. English Songs"), "Cornerstone (acoustic).pdf",
                 pdf(cho("Cornerstone", **OTHER_CHORDS)))
    shared = pdf(cho("Cornerstone"))  # (the newest: v1)
    drive.upload(drive.path("YFM resources", "3. English Songs"), "Cornerstone.pdf", shared)
    drive.upload(drive.path("ASE", "Cornerstone"), "Cornerstone.pdf", shared)
    drive.upload(drive.path("MM ministry", "02 English Song Scores"), "Morning Star.pdf", pdf(cho("Morning Star")))
    drive.upload(drive.flat, "old-song.pdf", pdf(cho("Old Song")))
    drive.upload(drive.path("YFM resources", "5. Christmas Hymns"), "Silent Night.pdf", sheet_music())


def run(make_sources_library, tmp_path, today):
    ids_before = IdStore(tmp_path / "sets.db").load()
    library = make_sources_library("test-run")
    library.ids = IdStore(":memory:", copy_of=tmp_path / "sets.db")
    library.sync(force=True)
    return findings(library, todays_songs(today), set(ids_before["aliases"]))


def test_the_report_lists_each_kind_of_finding(drive, make_sources_library, tmp_path):
    mixed_drive(drive)
    today = todays_library(tmp_path, {"holy-ground-g": cho("Holy Ground"), "holy-ground-a": cho("Holy Ground", key="A"),
                                      "old-song": cho("Old Song")})
    f = run(make_sources_library, tmp_path, today)
    assert [m["slug"] for m in f["missing"]] == ["old-song"]
    assert sorted(s["title"] for s in f["new_songs"]) == ["Cornerstone", "Morning Star"]
    assert sorted((m["tag"], tuple(m["folded"])) for m in f["merges"]) == [
        ("YFM", ("holy-ground-g",)), ("YFM · ASE", ())]
    assert [(a["title"], a["tag"]) for a in f["new_arrangements"]] == [("Cornerstone", "v2 · YFM")]
    assert [(a["alias"], a["slug"], a["new"]) for a in f["aliases"]] == [("holy-ground-g", "holy-ground-a", True)]
    assert [x["name"] for x in f["flat_only"]] == ["old-song.pdf"]
    assert [(x["name"], x["reason"]) for x in f["skipped"]] == [
        ("YFM/5. Christmas Hymns/Silent Night.pdf", "sheet music (staff notation)")]


def test_every_item_links_to_its_drive_file(drive, make_sources_library, tmp_path):
    mixed_drive(drive)
    today = todays_library(tmp_path, {"old-song": cho("Old Song")})
    f = run(make_sources_library, tmp_path, today)
    links = [i["link"] for key in ("missing", "new_arrangements", "flat_only", "skipped") for i in f[key]]
    links += [a["link"] for s in f["new_songs"] for a in s["arrangements"]]
    links += [c["link"] for m in f["merges"] for c in m["charts"]]
    assert links and all(link.startswith("https://drive.google.com/file/d/id") for link in links)
    page = render(f)
    assert all(link in page for link in links)


def test_running_it_changes_nothing_the_site_serves_or_keeps(sources_client, drive, make_sources_library, tmp_path):
    todays_song(drive, "holy-ground-g", "Holy Ground - G.pdf", cho("Holy Ground"))
    sources_client.post("/api/library/sync")
    served = results(sources_client, "holy ground")
    copy, ids = snapshot(tmp_path / "sources-library"), IdStore(tmp_path / "sets.db").load()
    mixed_drive(drive)  # more in Drive since the site's last check
    run(make_sources_library, tmp_path, todays_library(tmp_path, {"holy-ground-g": cho("Holy Ground")}))
    assert snapshot(tmp_path / "sources-library") == copy
    assert IdStore(tmp_path / "sets.db").load() == ids
    assert results(sources_client, "holy ground") == served


def test_files_that_could_not_be_taken_in_are_in_the_report_too(drive, make_sources_library, tmp_path):
    drive.upload(drive.path("YFM resources", "3. English Songs"), "Broken.pdf", b"not a pdf at all")
    f = run(make_sources_library, tmp_path, todays_library(tmp_path, {}))
    assert [(x["name"], x["reason"]) for x in f["skipped"]] == [("YFM/3. English Songs/Broken.pdf", "not a PDF")]
    assert f["skipped"][0]["link"].startswith("https://drive.google.com/file/d/")


def test_a_missing_songs_link_finds_its_pdf_in_another_key(drive, make_sources_library, tmp_path):
    drive.upload(drive.flat, "old-hymn@C.pdf", pdf(cho("Old Hymn", key="C")))
    f = run(make_sources_library, tmp_path, todays_library(tmp_path, {"old-hymn": cho("Old Hymn")}))
    (missing,) = f["missing"]
    assert missing["slug"] == "old-hymn" and missing["link"].startswith("https://drive.google.com/file/d/")
