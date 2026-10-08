"""YFM, MM and ASE: every chart tagged with its source, listed in that fixed order."""

from conftest import results, tagged
from make_charts import cho, pdf


def sync(client):
    return client.post("/api/library/sync").json


def test_charts_from_all_three_sources_are_tagged_with_their_source(sources_client, drive):
    drive.upload(drive.path("YFM resources", "3. English Songs", "Open Sky"), "Open Sky.pdf", pdf(cho("Open Sky")))
    drive.upload(drive.path("ASE", "Still Waters"), "Still Waters - C.pdf", pdf(cho("Still Waters", key="C")))
    drive.upload(drive.path("MM ministry", "02 English Song Scores"), "Morning Star.pdf", pdf(cho("Morning Star")))
    sync(sources_client)
    assert tagged(sources_client, "open sky") == ["Open Sky · YFM"]
    assert tagged(sources_client, "still waters") == ["Still Waters · ASE"]
    assert tagged(sources_client, "morning star") == ["Morning Star · MM"]


def test_mm_only_takes_in_its_four_song_folders_and_ase_leaves_out_its_songbook(sources_client, drive):
    for folder in ("05 Training", "06 Slides", "Lyrics", "Song Lists"):
        drive.upload(drive.path("MM ministry", folder), "Morning Star.pdf", pdf(cho("Morning Star")))
    drive.upload(drive.path("MM ministry", "01 Chinese Song Scores", "Old"), "Morning Star.pdf", pdf(cho("Morning Star")))
    drive.upload(drive.ase, "Song_Book+March+2014.pdf", pdf(cho("Every Song In The Book")))
    sync(sources_client)
    assert tagged(sources_client, "morning star") == ["Morning Star · MM"]
    assert results(sources_client, "every song") == []


def test_a_song_in_several_sources_lists_them_in_the_order_yfm_mm_ase(sources_client, drive):
    # Laid out differently in each, so they stay separate sheets.
    drive.upload(drive.path("MM ministry", "02 English Song Scores"), "Open Sky.pdf", pdf(cho("Open Sky"), size=12))
    drive.upload(drive.path("ASE", "Open Sky"), "Open Sky.pdf", pdf(cho("Open Sky"), size=14))
    drive.upload(drive.path("YFM resources", "3. English Songs"), "Open Sky.pdf", pdf(cho("Open Sky")))
    sync(sources_client)
    assert tagged(sources_client, "open sky") == ["Open Sky · YFM", "Open Sky · MM", "Open Sky · ASE"]


def test_library_status_lists_the_sources_in_order_with_their_song_counts(sources_client, drive):
    drive.upload(drive.path("ASE", "Still Waters"), "Still Waters.pdf", pdf(cho("Still Waters")))
    drive.upload(drive.path("ASE", "Open Sky"), "Open Sky.pdf", pdf(cho("Open Sky"), size=14))
    drive.upload(drive.path("YFM resources", "3. English Songs"), "Open Sky.pdf", pdf(cho("Open Sky")))
    sync(sources_client)
    status = sources_client.get("/api/library/status").json
    assert status["sources"] == [{"name": "YFM", "songs": 1}, {"name": "MM", "songs": 0}, {"name": "ASE", "songs": 2}]


def test_library_status_counts_songs_and_their_arrangements(sources_client, drive):
    from make_charts import OTHER_CHORDS
    drive.upload(drive.path("YFM resources", "3. English Songs"), "Open Sky.pdf", pdf(cho("Open Sky")))
    drive.upload(drive.path("YFM resources", "3. English Songs"), "Open Sky v2.pdf", pdf(cho("Open Sky", **OTHER_CHORDS)))
    sync(sources_client)
    status = sources_client.get("/api/library/status").json
    assert (status["songs"], status["arrangements"]) == (1, 2)
