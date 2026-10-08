"""Corrections: one Sheet in '.cho Files' (and hand-fixed .cho files) fix what the site worked out."""

from conftest import pdf_text, results, tagged
from make_charts import OTHER_CHORDS, cho, pdf

HEADER = "id,title,artist,original_key,label,same_as,different_song,leave_out"


def english(drive):
    return drive.path("YFM resources", "3. English Songs")


def sync(client):
    return client.post("/api/library/sync").json


def corrections(drive, *rows, header=HEADER):
    old = [i for i, f in drive.items.items() if f["name"] == "_corrections"]
    csv = "\n".join((header,) + rows) + "\n"
    if old:
        drive.replace(old[0], csv)
    else:
        drive.sheet(drive.cho, "_corrections", csv)


def slug_of(client, q):
    (song,) = results(client, q)
    return song["slug"]


def test_title_artist_and_original_key_corrections(sources_client, drive):
    drive.upload(english(drive), "Holy Ground.pdf", pdf(cho("Holy Ground")))
    sync(sources_client)
    slug = slug_of(sources_client, "holy ground")
    corrections(drive, f"{slug},Holy Ground (Live),Test Band,Bb,,,,")
    sync(sources_client)
    (song,) = results(sources_client, "holy ground")
    assert (song["slug"], song["title"], song["artist"], song["original_key"]) == (slug, "Holy Ground (Live)", "Test Band", "Bb")


def test_a_label_replaces_the_number_in_the_tag(sources_client, drive):
    drive.upload(english(drive), "Holy Ground.pdf", pdf(cho("Holy Ground")))
    sync(sources_client)
    drive.upload(english(drive), "Holy Ground (piano).pdf", pdf(cho("Holy Ground", **OTHER_CHORDS)))
    sync(sources_client)
    v2 = [r["slug"] for r in results(sources_client, "holy ground") if r["number"] == 2][0]
    corrections(drive, f"{v2},,,,piano intro,,,")
    sync(sources_client)
    assert tagged(sources_client, "holy ground") == ["Holy Ground · YFM", "Holy Ground · piano intro · YFM"]


def test_same_as_puts_two_songs_together(sources_client, drive):
    drive.upload(english(drive), "Holy Ground.pdf", pdf(cho("Holy Ground")))
    drive.upload(english(drive), "Sacred Place.pdf", pdf(cho("Sacred Place", **OTHER_CHORDS)))
    sync(sources_client)
    ours, theirs = slug_of(sources_client, "sacred place"), slug_of(sources_client, "holy ground")
    corrections(drive, f"{ours},,,,,{theirs},,")
    sync(sources_client)
    found = results(sources_client, "holy ground")
    assert [(r["slug"], r["tag"]) for r in found] == [(theirs, "YFM"), (ours, "v2 · YFM")]
    assert found[0]["song_id"] == found[1]["song_id"]


def test_different_song_splits_a_wrong_match(sources_client, drive):
    drive.upload(english(drive), "Holy.pdf", pdf(cho("Holy")))
    sync(sources_client)
    drive.upload(english(drive), "Holy (Kari Jobe).pdf", pdf(cho("Holy", **OTHER_CHORDS)))
    sync(sources_client)
    assert tagged(sources_client, "holy") == ["Holy · YFM", "Holy · v2 · YFM"]
    corrections(drive, "3. English Songs/Holy (Kari Jobe).pdf,,Kari Jobe,,,,yes,")
    sync(sources_client)
    found = results(sources_client, "holy")
    assert len({r["song_id"] for r in found}) == 2
    assert sorted((r["tag"], r["artist"]) for r in found) == [("YFM", ""), ("YFM", "Kari Jobe")]


def test_leave_out_removes_a_file(sources_client, drive):
    drive.upload(english(drive), "Holy Ground.pdf", pdf(cho("Holy Ground")))
    drive.upload(english(drive), "Open Sky.pdf", pdf(cho("Open Sky")))
    corrections(drive, "YFM/3. English Songs/Open Sky.pdf,,,,,,,yes")
    sync(sources_client)
    assert results(sources_client, "open sky") == [] and len(results(sources_client, "holy ground")) == 1


def test_a_hand_fixed_cho_wins_over_a_newer_upload_of_the_same_music(sources_client, drive):
    drive.upload(english(drive), "Holy Ground.pdf", pdf(cho("Holy Ground")))
    sync(sources_client)
    slug = slug_of(sources_client, "holy ground")
    fixed = cho("Holy Ground").replace("{key: G}", "{subtitle: Checked by hand}\n{key: G}")
    drive.upload(drive.cho, f"{slug}.cho", fixed)
    sync(sources_client)
    drive.upload(english(drive), "Holy Ground (new).pdf", pdf(cho("Holy Ground")))
    sync(sources_client)
    (song,) = results(sources_client, "holy ground")
    assert song["auto"] is False
    r = sources_client.post("/api/setlist/pdf", json={"songs": [{"slug": slug}], "layout": "uniform"})
    assert "Checked by hand" in pdf_text(r.data)


def test_todays_original_keys_and_exclude_rows_moved_into_the_sheet_work_as_before(sources_client, drive):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
    from corrections_sheet import rows_from, to_csv

    drive.upload(drive.path("YFM resources", "3. English Songs", "Holy Ground"), "Holy Ground - G.pdf",
                 pdf(cho("Holy Ground")))
    drive.upload(drive.path("YFM resources", "2. Chinese Songs", "YHWYDPW 永恒唯一的盼望"),
                 "YHWYDPW 永恆唯一的盼望 (G).pdf", pdf(cho("永恆唯一的盼望")))
    drive.upload(drive.cho, "holy-ground-g.cho", cho("Holy Ground").replace(
        "{key:", "{meta: source_folder 3. English Songs/Holy Ground}\n{meta: source_file Holy Ground - G.pdf}\n{key:", 1))
    original_keys = "\ufefftitle,original_key,chart_key,source,slug\nHoly Ground,Ab,G,web,holy-ground-g\n"
    exclude = ("# Source files that should never be imported into the library.\n\n"
               "# Traditional-Chinese version; the Simplified charts are preferred\n"
               "2. Chinese Songs/YHWYDPW 永恒唯一的盼望/YHWYDPW 永恆唯一的盼望 (G).pdf\n")
    drive.sheet(drive.cho, "_corrections", to_csv(rows_from(original_keys, exclude)))
    sync(sources_client)
    assert [(r["slug"], r["original_key"]) for r in results(sources_client, "holy ground")] == [("holy-ground-g", "Ab")]
    assert results(sources_client, "永恒") == []


def test_a_row_for_a_slug_folded_into_another_arrangement_still_applies(sources_client, drive):
    from test_ids import todays_song
    todays_song(drive, "holy-ground-g", "Holy Ground - G.pdf", cho("Holy Ground"))
    todays_song(drive, "holy-ground-a", "Holy Ground - A.pdf", cho("Holy Ground", key="A"))  # kept: the newest
    corrections(drive, "holy-ground-g,,,Bb,,,,")
    sync(sources_client)
    assert [(r["slug"], r["original_key"]) for r in results(sources_client, "holy ground")] == [("holy-ground-a", "Bb")]
