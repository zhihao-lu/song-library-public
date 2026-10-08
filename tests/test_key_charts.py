"""A set plays each song's shown chart, transposed, so every key looks the same; only a song
that can't be transposed in its own look uses its chart in that key."""

import re

from conftest import pdf_bookmarks, pdf_text, results
from make_charts import cho, long_pdf, pdf, picture


def folder(drive):
    return drive.path("YFM resources", "3. English Songs", "Holy Ground")


def build(client, slug, to):
    r = client.post("/api/setlist/pdf", json={"songs": [{"slug": slug, "to": to}]})
    assert r.status_code == 200, r.json
    return r.data


def with_subtitle(text, subtitle):
    return text.replace("{key:", f"{{subtitle: {subtitle}}}\n{{key:", 1)


def test_a_key_with_a_chart_of_its_own_still_plays_the_shown_chart_transposed(sources_client, drive):
    drive.upload(folder(drive), "Holy Ground - A.pdf", pdf(with_subtitle(cho("Holy Ground", key="A"), "The A team's sheet")))
    drive.upload(folder(drive), "Holy Ground - G.pdf", pdf(cho("Holy Ground")))  # the newest: shown
    sources_client.post("/api/library/sync")
    (song,) = results(sources_client, "holy ground")
    assert song["key"] == "G" and sorted(song["chart_keys"]) == ["A", "G"]
    data = build(sources_client, song["slug"], "A")
    text = re.sub(r"\s+", "", pdf_text(data))
    assert pdf_bookmarks(data) == ["Holy Ground (A)"]
    assert "Ateam" not in text and "F#m" in text  # G's chart in A, so every key looks the same


def test_any_other_key_transposes_the_shown_chart(sources_client, drive):
    drive.upload(folder(drive), "Holy Ground - A.pdf", pdf(with_subtitle(cho("Holy Ground", key="A"), "The A team's sheet")))
    drive.upload(folder(drive), "Holy Ground - G.pdf", pdf(cho("Holy Ground")))  # the newest: shown
    sources_client.post("/api/library/sync")
    (song,) = results(sources_client, "holy ground")
    assert song["key"] == "G"
    data = build(sources_client, song["slug"], "B")
    text = re.sub(r"\s+", "", pdf_text(data))
    assert pdf_bookmarks(data) == ["Holy Ground (B)"]
    assert "G#m" in text and "F#" in text and "Ateam" not in text


def test_a_one_page_chart_is_shown_and_transposed_over_a_longer_newer_one(sources_client, drive):
    drive.upload(folder(drive), "Holy Ground - G.pdf", pdf(cho("Holy Ground")))
    drive.upload(folder(drive), "Holy Ground - A.pdf", long_pdf(with_subtitle(cho("Holy Ground", key="A"), "The A team's sheet")))
    sources_client.post("/api/library/sync")
    (song,) = results(sources_client, "holy ground")
    assert song["key"] == "G" and song["source_file"] == "Holy Ground - G.pdf"
    data = build(sources_client, song["slug"], "A")
    text = re.sub(r"\s+", "", pdf_text(data))
    assert "Ateam" not in text and "F#m" in text


def test_an_as_is_arrangement_offers_only_the_keys_it_has_charts_for(sources_client, drive):
    drive.upload(folder(drive), "Holy Ground - C.pdf", picture(pdf(cho("Holy Ground", key="C"))))
    drive.upload(folder(drive), "Holy Ground - E.pdf", picture(pdf(cho("Holy Ground", key="E"))))
    sources_client.post("/api/library/sync")
    (song,) = results(sources_client, "holy ground")
    assert song["transposable"] is False and song["keys"] == ["C", "E"]
    assert pdf_bookmarks(build(sources_client, song["slug"], "E")) == ["Holy Ground (E)"]
    assert pdf_bookmarks(build(sources_client, song["slug"], "F")) == [f"Holy Ground ({song['key']})"]


def test_todays_library_offers_an_as_is_songs_other_keys_as_charts(client):
    (song,) = [r for r in results(client, "old hymn")]
    assert song["chart_keys"] == ["G", "C"]
    assert pdf_bookmarks(build(client, "old-hymn", "C")) == ["Old Hymn (C)"]
