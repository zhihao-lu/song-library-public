"""Capo: a guitarist's own chords for a song, in shapes that are easy to play.

A capo is chosen as a shape (C, A, G or E; Am, Em or Dm in a minor key). The fret follows
from the key the team plays the song in, so the sheet shows the shape's chords and says
where the capo goes.
"""

import re

from conftest import pdf_bookmarks, pdf_text


def capos_in_set(client, songs):
    saved = client.post("/api/sets", json={"date": "2026-10-03", "songs": songs}).json
    return [s["capos"] for s in saved["songs"]]


def test_each_song_offers_the_frets_that_give_easy_shapes(sets_client):
    a_major, e_major, e_minor = capos_in_set(sets_client, [
        {"slug": "morning-light", "to": "A"}, {"slug": "morning-light", "to": "E"}, {"slug": "quiet-waters"}])
    assert a_major == [{"fret": 2, "shape": "G"}, {"fret": 5, "shape": "E"}]
    # G shapes would need the 9th fret: only frets 1-7 are offered.
    assert e_major == [{"fret": 4, "shape": "C"}, {"fret": 7, "shape": "A"}]
    assert e_minor == [{"fret": 2, "shape": "Dm"}, {"fret": 7, "shape": "Am"}]


def test_the_song_in_its_own_key_gets_capo_options_for_that_key(sets_client):
    (g_major,) = capos_in_set(sets_client, [{"slug": "morning-light"}])
    assert g_major == [{"fret": 3, "shape": "E"}, {"fret": 7, "shape": "C"}]


def test_a_fixed_chart_offers_no_capo(sets_client):
    (fixed,) = capos_in_set(sets_client, [{"slug": "fixed-chart"}])
    assert fixed == []


def download(client, songs, **options):
    return client.post("/api/setlist/pdf", json={"songs": songs, **options})


def squashed(data):
    """PDF text without whitespace: the original layout redraws text a character at a time."""
    return re.sub(r"\s+", "", pdf_text(data))


def test_capo_in_the_charts_own_shapes_keeps_its_chords_and_says_where_the_capo_goes(client):
    # The team plays in A; capo 2 with G shapes is the chart as written.
    for layout in ("original", "uniform"):
        r = download(client, [{"slug": "morning-light", "to": "A", "capo": "G"}], layout=layout)
        text = squashed(r.data)
        assert "A·Capo2(playG)" in text, layout  # drawn over the old key in original layout
        assert "Cmaj7" in text and "D/F#" in text, layout
        assert "Dmaj7" not in text, layout


def test_capo_draws_the_chords_in_the_chosen_shape(client):
    r = download(client, [{"slug": "morning-light", "to": "A", "capo": "E"}])
    text = squashed(r.data)
    assert "A·Capo5(playE)" in text
    assert "Amaj7" in text and "B/D#" in text and "C#m7" in text


def test_capo_in_a_minor_key_uses_minor_shapes(client):
    r = download(client, [{"slug": "quiet-waters", "capo": "Dm"}], layout="uniform")
    text = pdf_text(r.data)
    assert "Key: Em · Capo 2 (play Dm)" in text
    assert "Gm7" in text and "A7" in text


def test_the_bookmark_says_the_bands_key_and_the_capo(client):
    r = download(client, [{"slug": "morning-light", "to": "A", "capo": "E"}, {"slug": "quiet-waters"}])
    assert pdf_bookmarks(r.data) == ["Morning Light (A, capo 5)", "Quiet Waters (Em)"]


def test_a_fixed_chart_ignores_a_capo(client):
    r = download(client, [{"slug": "fixed-chart", "capo": "C"}])
    assert pdf_bookmarks(r.data) == ["Fixed Chart (D)"]


def test_a_shape_that_doesnt_fit_the_key_is_refused(client):
    r = download(client, [{"slug": "morning-light", "to": "A", "capo": "Am"}])
    assert r.status_code == 400
    assert r.json["error"] == "Morning Light can't be played with Am shapes in A."


def test_a_sheet_without_a_key_line_gets_the_capo_at_the_top(client):
    r = download(client, [{"slug": "open-sky", "capo": "A"}])
    text = squashed(r.data)
    assert "Key:D·Capo5(playA)" in text


def test_capo_on_a_scanned_sheet_typesets_the_shape_chords(client):
    # The scan in C would show C chords but not the capo, so the song is typeset from its .cho.
    r = download(client, [{"slug": "old-hymn", "to": "E", "capo": "C"}])
    text = pdf_text(r.data)
    assert "Key: E · Capo 4 (play C)" in text
    assert "Fmaj7" in text and "G7" in text
    assert "Cmaj7" not in text


def test_after_a_key_change_the_same_shape_is_offered_at_its_new_fret(sets_client):
    saved = sets_client.post("/api/sets", json={"date": "2026-10-03", "songs": [{"slug": "morning-light", "to": "A"}]}).json
    assert {"fret": 5, "shape": "E"} in saved["songs"][0]["capos"]
    moved = sets_client.put(f"/api/sets/{saved['id']}", json={
        "date": "2026-10-03", "songs": [{"slug": "morning-light", "to": "Bb"}], "version": saved["version"]}).json
    assert {"fret": 6, "shape": "E"} in moved["songs"][0]["capos"]
