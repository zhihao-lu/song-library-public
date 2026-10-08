"""The setlist builder's API as it stood before sets: preview and download."""

import re

from conftest import pdf_bookmarks, pdf_text


def download(client, songs, **options):
    return client.post("/api/setlist/pdf", json={"songs": songs, **options})


def test_setlist_pdf_has_a_bookmark_per_song_with_its_key(client):
    r = download(client, [{"slug": "quiet-waters"}, {"slug": "morning-light", "to": "A"}])
    assert r.status_code == 200
    assert r.mimetype == "application/pdf"
    assert pdf_bookmarks(r.data) == ["Quiet Waters (Em)", "Morning Light (A)"]


def test_original_layout_redraws_the_chords_in_the_new_key(client):
    r = download(client, [{"slug": "morning-light", "to": "A"}])
    # The new chords are drawn over the old ones a character at a time.
    text = re.sub(r"\s+", "", pdf_text(r.data))
    assert "Dmaj7" in text and "E/G#" in text and "F#m7" in text


def test_uniform_layout_typesets_the_song_in_the_new_key(client):
    r = download(client, [{"slug": "quiet-waters", "to": "F#m"}], layout="uniform")
    text = pdf_text(r.data)
    assert "Key: F#m" in text
    assert "Bm7" in text and "C#7" in text
    assert "Am7" not in text


def test_fixed_chart_stays_in_its_own_key(client):
    r = download(client, [{"slug": "fixed-chart", "to": "E"}])
    assert pdf_bookmarks(r.data) == ["Fixed Chart (D)"]


def test_download_is_named_after_the_setlist(client):
    r = download(client, [{"slug": "quiet-waters"}], name="03-10-2026 Setlist")
    assert 'filename="03-10-2026 Setlist.pdf"' in r.headers["Content-Disposition"]


def test_setlist_needs_at_least_one_song(client):
    r = download(client, [])
    assert r.status_code == 400
    assert r.json["error"] == "Add at least one song."


def test_unknown_song_is_an_error(client):
    r = download(client, [{"slug": "no-such-song"}])
    assert r.status_code == 400
    assert "no-such-song" in r.json["error"]


def test_preview_is_one_image_per_page(client):
    r = client.post("/api/setlist/preview", json={"songs": [{"slug": "morning-light"}, {"slug": "quiet-waters"}]})
    assert r.status_code == 200
    assert len(r.json["pages"]) == 2
    assert all(p.startswith("data:image/png;base64,") for p in r.json["pages"])


def test_preview_says_which_pages_belong_to_which_song(client):
    r = client.post("/api/setlist/preview", json={"songs": [{"slug": "long-road"}, {"slug": "quiet-waters"}]})
    long_road, quiet = r.json["songs"]
    assert long_road["first"] == 0 and long_road["pages"] > 1
    assert quiet == {"first": long_road["pages"], "pages": 1}
    assert len(r.json["pages"]) == long_road["pages"] + 1


def png_width(data_uri):
    import base64
    import struct
    return struct.unpack(">I", base64.b64decode(data_uri.split(",", 1)[1])[16:20])[0]


def preview_width(client, **options):
    r = client.post("/api/setlist/preview", json={"songs": [{"slug": "quiet-waters"}], **options})
    return png_width(r.json["pages"][0])


def test_preview_can_be_sharper_for_small_screens(client):
    # A4 is 595.28 points wide: 1.6 pixels a point by default, up to 3 on request.
    for options, pixels_per_point in (({}, 1.6), ({"scale": 2.5}, 2.5), ({"scale": 10}, 3)):
        assert abs(preview_width(client, **options) - 595.28 * pixels_per_point) <= 1, options
