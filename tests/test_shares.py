"""Share links: the song picker's songs as a hidden snapshot, opened only through its link.

No date, never listed with the sets. The same songs and keys give the same link, and a
link is forgotten 30 days after it was last opened.
"""

from datetime import timedelta

SONGS = [{"slug": "morning-light", "to": "A"}, {"slug": "quiet-waters"}]


def share(client, songs=SONGS, **options):
    return client.post("/api/shares", json={"songs": songs, **options})


def test_a_share_link_opens_the_songs_in_their_keys(sets_client):
    r = share(sets_client, layout="uniform", columns=1, paper="Letter")
    assert r.status_code == 201
    code = r.json["code"]
    assert len(code) == 6 and code.isalnum()
    opened = sets_client.get(f"/api/shares/{code}").json
    assert [(s["title"], s["to"]) for s in opened["songs"]] == [("Morning Light", "A"), ("Quiet Waters", None)]
    assert (opened["layout"], opened["columns"], opened["paper"]) == ("uniform", 1, "Letter")
    assert opened["songs"][0]["capos"] == [{"fret": 2, "shape": "G"}, {"fret": 5, "shape": "E"}]


def test_shared_songs_are_not_listed_with_the_sets(sets_client):
    share(sets_client)
    assert sets_client.get("/api/sets").json == {"upcoming": [], "past": []}


def test_the_same_songs_and_keys_give_the_same_link(sets_client):
    first = share(sets_client).json["code"]
    assert share(sets_client).json["code"] == first
    assert share(sets_client, songs=[{"slug": "quiet-waters"}]).json["code"] != first


def test_a_share_link_needs_songs(sets_client):
    r = share(sets_client, songs=[])
    assert r.status_code == 400
    assert r.json["error"] == "Add at least one song."


def test_an_unknown_link_is_not_found(sets_client):
    assert sets_client.get("/api/shares/zzzzzz").status_code == 404


def test_a_link_is_forgotten_30_days_after_it_was_last_opened(sets_client, clock):
    code = share(sets_client).json["code"]
    clock.now += timedelta(days=29)
    assert sets_client.get(f"/api/shares/{code}").status_code == 200  # opened: 30 more days
    clock.now += timedelta(days=29)
    assert sets_client.get(f"/api/shares/{code}").status_code == 200
    clock.now += timedelta(days=31)
    assert sets_client.get(f"/api/shares/{code}").status_code == 404


def test_sharing_again_after_a_link_expired_makes_a_working_link(sets_client, clock):
    code = share(sets_client).json["code"]
    clock.now += timedelta(days=31)
    again = share(sets_client).json["code"]
    assert sets_client.get(f"/api/shares/{again}").status_code == 200


def test_a_share_link_has_a_page(sets_client):
    code = share(sets_client).json["code"]
    r = sets_client.get(f"/s/{code}")
    assert r.status_code == 200 and b"<title>YFM" in r.data


def test_the_link_to_copy_starts_with_the_configured_address(sets_client, app_module, monkeypatch):
    monkeypatch.setitem(app_module.app.config, "SHARE_LINK_BASE", "https://example.org/songlibrary")
    assert sets_client.get("/api/features").json["share_link_base"] == "https://example.org/songlibrary"


# ---------------------------------------------------------------- a one-time setlist's name

def test_a_share_link_keeps_its_name(sets_client):
    code = share(sets_client, name="Cell group 12 Oct").json["code"]
    assert sets_client.get(f"/api/shares/{code}").json["name"] == "Cell group 12 Oct"


def test_a_share_link_without_a_name_has_none(sets_client):
    for name in (None, "", "   "):
        code = share(sets_client, **({} if name is None else {"name": name})).json["code"]
        assert sets_client.get(f"/api/shares/{code}").json["name"] is None


def test_a_name_is_tidied(sets_client):
    code = share(sets_client, name="  Cell   group\t12 Oct ").json["code"]
    assert sets_client.get(f"/api/shares/{code}").json["name"] == "Cell group 12 Oct"


def test_the_same_songs_under_another_name_give_another_link(sets_client):
    plain = share(sets_client).json["code"]
    named = share(sets_client, name="Cell group").json["code"]
    assert named != plain
    assert share(sets_client, name="Cell group").json["code"] == named
    assert share(sets_client, name="Youth camp").json["code"] not in (plain, named)
    assert share(sets_client).json["code"] == plain


def test_a_name_can_be_at_most_60_characters(sets_client):
    assert share(sets_client, name="x" * 60).status_code == 201
    r = share(sets_client, name="x" * 61)
    assert r.status_code == 400
    assert r.json["error"] == "Keep the name to 60 characters."


def test_a_link_made_before_names_still_opens(sets_client, tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "sets.db")
    db.executescript("""
        CREATE TABLE shares (code TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE, songs TEXT NOT NULL,
                             layout TEXT NOT NULL, columns INTEGER NOT NULL, paper TEXT NOT NULL,
                             last_opened TEXT NOT NULL);
        INSERT INTO shares VALUES ('OLDONE', 'd', '[{"slug": "quiet-waters", "to": null}]',
                                   'original', 2, 'A4', '2026-09-29T10:00:00');""")
    db.commit(); db.close()
    opened = sets_client.get("/api/shares/OLDONE").json
    assert opened["name"] is None and [s["title"] for s in opened["songs"]] == ["Quiet Waters"]
    assert share(sets_client, name="Now with a name").status_code == 201


def test_a_set_takes_no_name(sets_client):
    r = sets_client.post("/api/sets", json={"date": "2026-10-03", "songs": SONGS, "name": "Ignored"})
    assert r.status_code == 201
    assert "name" not in sets_client.get(f"/api/sets/{r.json['id']}").json
