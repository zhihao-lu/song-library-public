"""IDs: saved sets, capos and share links keep opening the same songs, whatever happens in Drive."""

from urllib.parse import quote

from conftest import pdf_bookmarks, results
from make_charts import OTHER_CHORDS, cho, pdf

FOLDER = "3. English Songs/Holy Ground"


def todays_song(drive, slug, name, text):
    """A song as today's library has it: a chart in YFM, and <slug>.cho (saying which file it came
    from) and <slug>.pdf (a copy of that file) in the two flat folders."""
    data = pdf(text)
    drive.upload(drive.path("YFM resources", *FOLDER.split("/")), name, data)
    drive.upload(drive.cho, f"{slug}.cho", text.replace("{key:", f"{{meta: source_folder {FOLDER}}}\n"
                                                              f"{{meta: source_file {name}}}\n{{key:", 1))
    drive.upload(drive.flat, f"{slug}.pdf", data)


def sync(client):
    return client.post("/api/library/sync").json


def save_set(client, slugs):
    r = client.post("/api/sets", json={"date": "2026-10-03", "songs": [{"slug": s} for s in slugs]})
    assert r.status_code == 201
    return r.json["id"]


def capos(client, name="Wei"):
    return client.get(f"/api/people/{quote(name)}/capos").json["capos"]


def test_a_set_capo_and_share_link_saved_against_todays_slugs_open_the_same_songs(sources_client, drive):
    todays_song(drive, "holy-ground-g", "Holy Ground - G.pdf", cho("Holy Ground"))
    set_id = save_set(sources_client, ["holy-ground-g"])
    sources_client.put(f"/api/people/Wei/capos/{set_id}/holy-ground-g", json={"shape": "E", "fret": 3})
    code = sources_client.post("/api/shares", json={"songs": [{"slug": "holy-ground-g"}]}).json["code"]
    sync(sources_client)
    assert [r["slug"] for r in results(sources_client, "holy ground")] == ["holy-ground-g"]
    assert [s["title"] for s in sources_client.get(f"/api/sets/{set_id}").json["songs"]] == ["Holy Ground"]
    assert capos(sources_client) == {str(set_id): {"holy-ground-g": {"shape": "E", "fret": 3}}}
    assert [s["title"] for s in sources_client.get(f"/api/shares/{code}").json["songs"]] == ["Holy Ground"]


def test_renaming_a_file_keeps_its_arrangements_id(sources_client, drive):
    song = drive.upload(drive.path("YFM resources", "3. English Songs"), "Holy Ground.pdf", pdf(cho("Holy Ground")))
    sync(sources_client)
    (before,) = results(sources_client, "holy ground")
    drive.rename(song, "HOLY GROUND final (1).pdf")
    drive.move(song, drive.path("YFM resources", "3. English Songs", "H"))
    sync(sources_client)
    assert [r["slug"] for r in results(sources_client, "holy ground")] == [before["slug"]]


def test_a_slug_folded_into_another_arrangement_still_works_everywhere(sources_client, drive):
    todays_song(drive, "holy-ground-a", "Holy Ground - A.pdf", cho("Holy Ground", key="A"))
    todays_song(drive, "holy-ground-g", "Holy Ground - G.pdf", cho("Holy Ground"))
    set_id = save_set(sources_client, ["holy-ground-a"])
    sources_client.put(f"/api/people/Wei/capos/{set_id}/holy-ground-a", json={"shape": "G", "fret": 2})
    code = sources_client.post("/api/shares", json={"songs": [{"slug": "holy-ground-a", "to": "A"}]}).json["code"]
    sync(sources_client)
    (song,) = results(sources_client, "holy ground")
    kept = song["slug"]
    assert kept == "holy-ground-g" and sorted(song["chart_keys"]) == ["A", "G"]
    r = sources_client.post("/api/setlist/pdf", json={"songs": [{"slug": "holy-ground-a", "to": "A"}]})
    assert pdf_bookmarks(r.data) == ["Holy Ground (A)"]
    assert [s["slug"] for s in sources_client.get(f"/api/sets/{set_id}").json["songs"]] == [kept]
    assert capos(sources_client) == {str(set_id): {kept: {"shape": "G", "fret": 2}}}
    assert [s["slug"] for s in sources_client.get(f"/api/shares/{code}").json["songs"]] == [kept]


def test_a_removed_song_shows_as_removed_and_comes_back_under_its_id(sources_client, drive):
    folder = drive.path("YFM resources", "3. English Songs")
    gone = drive.upload(folder, "Holy Ground.pdf", pdf(cho("Holy Ground")))
    drive.upload(folder, "Open Sky.pdf", pdf(cho("Open Sky", **OTHER_CHORDS)))
    sync(sources_client)
    slugs = [results(sources_client, q)[0]["slug"] for q in ("holy ground", "open sky")]
    set_id = save_set(sources_client, slugs)
    drive.delete(gone)
    sync(sources_client)
    songs = sources_client.get(f"/api/sets/{set_id}").json["songs"]
    assert [(s["title"], s["missing"]) for s in songs] == [("Holy Ground (removed from library)", True),
                                                           ("Open Sky", False)]
    r = sources_client.post("/api/setlist/pdf", json={"songs": [{"slug": slugs[1]}]})
    assert pdf_bookmarks(r.data) == ["Open Sky (G)"]
    drive.upload(folder, "Holy Ground (again).pdf", pdf(cho("Holy Ground", key="A")))
    sync(sources_client)
    assert [(s["title"], s["missing"]) for s in sources_client.get(f"/api/sets/{set_id}").json["songs"]] == [
        ("Holy Ground", False), ("Open Sky", False)]
