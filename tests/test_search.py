"""Finding songs: by title words, pinyin, and initials, in Chinese and English. The set builder
and /analytics share this (library.search_fields + library.matches)."""

import pytest


def titles(client, q):
    return [s["title"] for s in client.get("/api/library/search", query_string={"q": q}).json["results"]]


@pytest.mark.parametrize("q", ["ml", "ML", "m l"])
def test_an_english_title_is_found_by_its_initials(client, q):
    assert titles(client, q)[0] == "Morning Light"


def test_initials_find_only_titles_they_begin(client):
    assert titles(client, "qw") == ["Quiet Waters"]
    assert titles(client, "lr") == ["Long Road"]
    assert titles(client, "xq") == []


def test_words_still_come_first(client):
    # "op" begins Open Sky and Only Hope; neither is beaten by a title whose initials are "op".
    assert set(titles(client, "op")[:2]) == {"Open Sky", "Only Hope 祢是永恒唯一的盼望"}
    assert titles(client, "morning")[0] == "Morning Light"


def test_chinese_initials_still_work(client):
    assert titles(client, "msyhwydpw")[0] == "Only Hope 祢是永恒唯一的盼望"


def test_the_english_part_of_a_mixed_title_has_initials_too(client):
    assert set(titles(client, "oh")) == {"Only Hope 祢是永恒唯一的盼望", "Old Hymn"}  # both are "oh"


def test_analytics_finds_songs_by_initials_too(sets_client):
    sets_client.post("/api/sets", json={"date": "2026-09-26", "songs": [{"slug": "morning-light"}, {"title": "Just That Good"}]})
    rows = sets_client.get("/api/analytics/sung").json["rows"]
    ids = {r["title"]: r["song"] for r in rows}
    found = sets_client.get("/api/analytics/find", query_string={"q": "jtg"}).json["songs"]
    assert found == [ids["Just That Good"]]


def test_each_song_knows_its_opening_line(client):
    (song,) = [s for s in client.get("/api/library/search", query_string={"q": "morning light"}).json["results"]]
    assert song["first_line"] and not song["first_line"].startswith(("{", "["))


@pytest.mark.parametrize("cho, line", [
    ("{title: X}\n{start_of_verse}\n[G]Holy is the [C]Lord our God\n", "Holy is the Lord our God"),
    ("Intro: | G | D | Em | C |\nVerse 1:\n[G]Amazing grace how sweet\n", "Amazing grace how sweet"),
    ("Words and Music by Someone\nCCLI 1234\n[D]永在的圣父上帝 创造天地的主\n", "永在的圣父上帝 创造天地的主"),
    ("Keith Getty and Stuart Townend\n[D]In Christ a[A]lone my hope is found\n", "In Christ alone my hope is found"),
    ("C | D | G/B | Em If you've been forgiven\n", "If you've been forgiven"),
    ("[F]雷[C]声轰隆 在风雨中\n", "雷声轰隆 在风雨中"),
    ("[G]A thousand generations falling down\n", "A thousand generations falling down"),
    ("[G] [D] [Em]\n\nChorus\n[C]Sing it out and sing again and again and again and again and on\n",
     "Sing it out and sing again and again and again and…"),
])
def test_the_opening_line_skips_chords_labels_and_credits(cho, line):
    from library import opening_line
    assert opening_line(cho) == line
