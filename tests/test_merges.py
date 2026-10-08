"""Across sources, arrangements merge only when their charts look the same on the page."""

from conftest import results, tagged
from make_charts import OTHER_CHORDS, cho, pdf, picture
from test_ids import todays_song


def sync(client):
    return client.post("/api/library/sync").json


def yfm(drive):
    return drive.path("YFM resources", "3. English Songs", "Cornerstone")


def ase(drive):
    return drive.path("ASE", "Cornerstone")


def test_byte_identical_charts_in_yfm_and_ase_are_one_result_tagged_with_both(sources_client, drive):
    sheet = pdf(cho("Cornerstone"))
    drive.upload(ase(drive), "Cornerstone.pdf", sheet)
    drive.upload(yfm(drive), "Cornerstone - G.pdf", sheet)
    sync(sources_client)
    assert tagged(sources_client, "cornerstone") == ["Cornerstone · YFM · ASE"]


def test_key_exports_in_ase_merge_with_yfms_copy_and_offer_every_key(sources_client, drive):
    for key in ("C", "D", "E"):
        drive.upload(ase(drive), f"Cornerstone - {key}.pdf", pdf(cho("Cornerstone", key=key)))
    drive.upload(yfm(drive), "Cornerstone.pdf", pdf(cho("Cornerstone", key="G")))
    sync(sources_client)
    (song,) = results(sources_client, "cornerstone")
    assert song["tag"] == "YFM · ASE" and sorted(song["chart_keys"]) == ["C", "D", "E", "G"]


def test_same_music_laid_out_differently_stays_separate(sources_client, drive):
    drive.upload(ase(drive), "Cornerstone.pdf", pdf(cho("Cornerstone"), size=14))
    drive.upload(yfm(drive), "Cornerstone.pdf", pdf(cho("Cornerstone")))
    sync(sources_client)
    assert tagged(sources_client, "cornerstone") == ["Cornerstone · YFM", "Cornerstone · ASE"]


def test_two_unreadable_charts_that_look_the_same_merge(sources_client, drive):
    drive.upload(yfm(drive), "Cornerstone - C.pdf", picture(pdf(cho("Cornerstone", key="C"))))
    drive.upload(ase(drive), "Cornerstone - D.pdf", picture(pdf(cho("Cornerstone", key="D"))))
    sync(sources_client)
    (song,) = results(sources_client, "cornerstone")
    assert song["tag"] == "YFM · ASE" and song["keys"] == ["C", "D"]


def test_a_typed_chart_and_an_unreadable_export_of_the_same_song_do_not_merge(sources_client, drive):
    drive.upload(yfm(drive), "Cornerstone.pdf", pdf(cho("Cornerstone")))
    drive.upload(ase(drive), "Cornerstone - G.pdf", picture(pdf(cho("Cornerstone"))))
    sync(sources_client)
    assert tagged(sources_client, "cornerstone") == ["Cornerstone · YFM", "Cornerstone · ASE"]


def test_a_merged_arrangement_keeps_yfms_slug_and_old_sets_still_open_it(sources_client, drive):
    todays_song(drive, "cornerstone-g", "Cornerstone - G.pdf", cho("Cornerstone"))
    set_id = sources_client.post("/api/sets", json={"date": "2026-10-03", "songs": [{"slug": "cornerstone-g"}]}).json["id"]
    drive.upload(drive.path("ASE", "Cornerstone"), "Cornerstone - A.pdf", pdf(cho("Cornerstone", key="A")))
    sync(sources_client)
    (song,) = results(sources_client, "cornerstone")
    assert (song["slug"], song["tag"]) == ("cornerstone-g", "YFM · ASE")
    assert [s["slug"] for s in sources_client.get(f"/api/sets/{set_id}").json["songs"]] == ["cornerstone-g"]


def test_other_chords_over_the_same_lyric_lines_are_not_merged_in(sources_client, drive):
    sheet = pdf(cho("Cornerstone"))
    drive.upload(yfm(drive), "Cornerstone.pdf", sheet)
    drive.upload(ase(drive), "Cornerstone.pdf", sheet)
    drive.upload(yfm(drive), "Cornerstone (acoustic).pdf", pdf(cho("Cornerstone", **OTHER_CHORDS)))
    sync(sources_client)
    assert sorted(tagged(sources_client, "cornerstone")) == ["Cornerstone · YFM", "Cornerstone · v2 · YFM · ASE"]
