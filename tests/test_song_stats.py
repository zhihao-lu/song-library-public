"""/analytics: which songs the team sings most, from the sets. A past set is what was sung; the
page pivots its rows (by year, month, leader, key…), so the API gives one row per song per set."""



def new_set(client, date, *songs):
    r = client.post("/api/sets", json={"date": date, "songs": list(songs)})
    assert r.status_code == 201, r.json
    return r.json


def rows(client):
    return client.get("/api/analytics/sung").json["rows"]


def test_each_song_in_each_set_is_a_row(sets_client):
    new_set(sets_client, "2025-12-27", {"slug": "morning-light", "to": "A"})
    new_set(sets_client, "2026-09-26", {"slug": "morning-light"}, {"slug": "quiet-waters", "to": "D"})
    got = sorted((r["date"], r["title"], r["key"]) for r in rows(sets_client))
    assert got == [("2025-12-27", "Morning Light", "A"), ("2026-09-26", "Morning Light", "G"),
                   ("2026-09-26", "Quiet Waters", "D")]  # no key picked: the chart's own


def test_a_set_still_to_come_is_marked_as_not_sung_yet(sets_client):
    new_set(sets_client, "2026-09-26", {"slug": "morning-light"})
    new_set(sets_client, "2026-10-03", {"slug": "morning-light"})
    assert {(r["date"], r["past"]) for r in rows(sets_client)} == {("2026-09-26", True), ("2026-10-03", False)}


def test_the_same_song_is_one_song_however_it_was_kept(sets_client):
    new_set(sets_client, "2026-09-19", {"slug": "morning-light"})
    new_set(sets_client, "2026-09-26", {"slug": "morning-light", "to": "A"})
    assert len({r["song"] for r in rows(sets_client)}) == 1


def test_a_song_kept_only_by_its_title_still_counts(sets_client):
    new_set(sets_client, "2026-09-26", {"title": "Old Hymn"})
    (row,) = rows(sets_client)
    assert row["title"] == "Old Hymn" and row["song"]


def test_rows_carry_who_led(sets_client, app_module):
    import sets_api
    with app_module.app.app_context():
        sets_api._store().set_leaders({"2026-09-26": "Jolene"})
    new_set(sets_client, "2026-09-26", {"slug": "morning-light"})
    assert rows(sets_client)[0]["leader"] == "Jolene"


def test_the_page_needs_no_password(sets_client):
    r = sets_client.get("/analytics")
    assert r.status_code == 200 and b"Songs we sing" in r.data


def test_a_credit_line_of_only_by_is_no_artist(sets_client, monkeypatch, app_module):
    lib = app_module.app.extensions["library"]
    real = lib.get
    monkeypatch.setattr(lib, "get", lambda slug: {**real(slug), "artist": "by"} if real(slug) else None)
    new_set(sets_client, "2026-09-26", {"slug": "morning-light"})
    assert rows(sets_client)[0]["artist"] == ""


def find(client, q):
    return client.get("/api/analytics/find", query_string={"q": q}).json["songs"]


def test_songs_are_found_the_way_the_main_page_finds_them(sets_client):
    new_set(sets_client, "2026-09-19", {"slug": "morning-light"}, {"slug": "quiet-waters"}, {"title": "Old Hymn"})
    ids = {r["title"]: r["song"] for r in rows(sets_client)}
    library = sets_client.get("/api/library/search", query_string={"q": "morn"}).json["results"]
    assert [s["title"] for s in library][:1] == ["Morning Light"]
    assert find(sets_client, "morn") == [ids["Morning Light"]]       # a title's first letters, as there
    assert find(sets_client, "old hy") == [ids["Old Hymn"]]           # songs only the sets know too
    assert find(sets_client, "") == []
