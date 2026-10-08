"""Sets: setlists saved on the server, named by date and shared with the whole team."""

from conftest import pdf_bookmarks

SAT = "2026-10-03"


def new_set(client, date=SAT, songs=None, **options):
    songs = songs if songs is not None else [{"slug": "morning-light", "to": "A"}, {"slug": "quiet-waters"}]
    return client.post("/api/sets", json={"date": date, "songs": songs, **options})


def test_a_saved_set_can_be_opened_by_anyone(sets_client):
    r = new_set(sets_client, layout="uniform", columns=1, paper="Letter")
    assert r.status_code == 201
    saved = sets_client.get(f"/api/sets/{r.json['id']}").json
    assert saved["date"] == SAT
    assert [(s["slug"], s["to"]) for s in saved["songs"]] == [("morning-light", "A"), ("quiet-waters", None)]
    assert (saved["layout"], saved["columns"], saved["paper"]) == ("uniform", 1, "Letter")


def test_a_set_shows_each_songs_title(sets_client):
    saved = new_set(sets_client).json
    assert [s["title"] for s in saved["songs"]] == ["Morning Light", "Quiet Waters"]


def test_an_unknown_set_is_not_found(sets_client):
    assert sets_client.get("/api/sets/999").status_code == 404


def dates(sets):
    return [s["date"] for s in sets]


def test_sets_list_upcoming_soonest_first_then_past_newest_first(sets_client):
    for d in ("2026-10-10", "2026-09-19", SAT, "2026-09-26", "2026-09-30"):
        new_set(sets_client, date=d)
    listing = sets_client.get("/api/sets").json
    # It's Wednesday 30 September: a set for today is still to come.
    assert dates(listing["upcoming"]) == ["2026-09-30", SAT, "2026-10-10"]
    assert dates(listing["past"]) == ["2026-09-26", "2026-09-19"]


def test_listed_sets_show_their_songs(sets_client):
    new_set(sets_client)
    (card,) = sets_client.get("/api/sets").json["upcoming"]
    assert [s["title"] for s in card["songs"]] == ["Morning Light", "Quiet Waters"]


def test_a_date_that_already_has_a_set_is_refused_and_names_that_set(sets_client):
    first = new_set(sets_client).json
    r = new_set(sets_client, songs=[{"slug": "fixed-chart"}])
    assert r.status_code == 409
    assert r.json["error"] == "2026-10-03 already has a set."
    assert r.json["existing"]["id"] == first["id"]


def test_replacing_the_set_on_a_date_keeps_its_date_and_bumps_its_version(sets_client):
    first = new_set(sets_client).json
    r = sets_client.put(f"/api/sets/{first['id']}", json={
        "date": SAT, "songs": [{"slug": "fixed-chart"}], "version": first["version"]})
    assert r.status_code == 200
    saved = sets_client.get(f"/api/sets/{first['id']}").json
    assert [s["slug"] for s in saved["songs"]] == ["fixed-chart"]
    assert saved["version"] == first["version"] + 1


def test_a_set_needs_a_real_date(sets_client):
    r = new_set(sets_client, date="next saturday")
    assert r.status_code == 400
    assert r.json["error"] == "Pick the set's date."


def test_the_sets_tab_is_a_page(sets_client):
    r = sets_client.get("/sets")
    assert r.status_code == 200 and b"<title>YFM" in r.data


def update(client, saved, **changes):
    body = {"date": saved["date"], "songs": [{"slug": s["slug"], "to": s["to"]} for s in saved["songs"]],
            "version": saved["version"], **changes}
    return client.put(f"/api/sets/{saved['id']}", json=body)


def test_changing_a_key_during_practice_updates_the_set_for_everyone(sets_client):
    saved = new_set(sets_client).json
    r = update(sets_client, saved, songs=[{"slug": "morning-light", "to": "Bb"}, {"slug": "quiet-waters"}])
    assert r.status_code == 200
    reopened = sets_client.get(f"/api/sets/{saved['id']}").json
    assert reopened["songs"][0]["to"] == "Bb"
    assert reopened["version"] == saved["version"] + 1


def test_an_update_from_an_old_copy_is_refused_and_returns_the_current_set(sets_client):
    opened_on_laptop = new_set(sets_client).json
    on_ipad = update(sets_client, opened_on_laptop, songs=[{"slug": "fixed-chart"}]).json
    r = update(sets_client, opened_on_laptop, songs=[{"slug": "quiet-waters"}])
    assert r.status_code == 409
    assert r.json["error"] == "Someone changed this set since you opened it."
    assert r.json["set"]["version"] == on_ipad["version"]
    assert [s["slug"] for s in r.json["set"]["songs"]] == ["fixed-chart"]


def test_overwriting_sends_the_current_version(sets_client):
    opened_on_laptop = new_set(sets_client).json
    update(sets_client, opened_on_laptop, songs=[{"slug": "fixed-chart"}])
    current = update(sets_client, opened_on_laptop, songs=[{"slug": "quiet-waters"}]).json["set"]
    r = update(sets_client, opened_on_laptop, songs=[{"slug": "quiet-waters"}], version=current["version"])
    assert r.status_code == 200
    assert [s["slug"] for s in r.json["songs"]] == ["quiet-waters"]


def test_moving_a_set_to_a_date_that_has_one_is_refused(sets_client):
    saved = new_set(sets_client).json
    new_set(sets_client, date="2026-10-10")
    r = update(sets_client, saved, date="2026-10-10")
    assert r.status_code == 409
    assert r.json["existing"]["date"] == "2026-10-10"


def test_moving_a_set_onto_a_taken_date_can_replace_the_set_there(sets_client):
    there = new_set(sets_client, songs=[{"slug": "fixed-chart"}]).json
    moving = new_set(sets_client, date="2026-10-10").json
    sets_client.put(f"/api/people/Wei/capos/{there['id']}/fixed-chart", json={"shape": "C", "fret": 2})
    r = sets_client.put(f"/api/sets/{moving['id']}", json={
        "date": SAT, "songs": [{"slug": "quiet-waters"}], "version": moving["version"], "replace": there["id"]})
    assert r.status_code == 200 and (r.json["id"], r.json["date"]) == (moving["id"], SAT)
    assert sets_client.get(f"/api/sets/{there['id']}").status_code == 404
    assert sets_client.get("/api/people/Wei/capos").json == {"capos": {}}


def test_replace_only_replaces_the_set_it_names(sets_client):
    there = new_set(sets_client).json
    moving = new_set(sets_client, date="2026-10-10").json
    r = sets_client.put(f"/api/sets/{moving['id']}", json={
        "date": SAT, "songs": [], "version": moving["version"], "replace": there["id"] + 100})
    assert r.status_code == 409 and r.json["existing"]["id"] == there["id"]


def test_a_set_carries_what_the_builder_needs_to_edit_each_song(sets_client):
    saved = new_set(sets_client, songs=[{"slug": "morning-light", "to": "A"}, {"slug": "fixed-chart"}]).json
    song, fixed = saved["songs"]
    assert (song["key"], song["to"], song["transposable"], song["artist"]) == ("G", "A", True, "Test Band")
    assert fixed["transposable"] is False


def test_a_song_gone_from_the_library_still_opens_marked_missing(sets_client):
    saved = new_set(sets_client, songs=[{"slug": "deleted-song", "to": "C"}, {"slug": "quiet-waters"}]).json
    gone = sets_client.get(f"/api/sets/{saved['id']}").json["songs"][0]
    assert (gone["slug"], gone["title"], gone["missing"]) == ("deleted-song", "deleted-song", True)


def test_a_song_not_in_the_library_is_kept_by_its_title_and_key(sets_client):
    saved = new_set(sets_client, songs=[{"slug": "morning-light"}, {"title": "Let Everything", "to": "D"}]).json
    _, sung = sets_client.get(f"/api/sets/{saved['id']}").json["songs"]
    assert (sung["slug"], sung["title"], sung["to"], sung["missing"]) == (None, "Let Everything", "D", True)


def test_saving_a_set_again_keeps_its_songs_not_in_the_library(sets_client):
    saved = new_set(sets_client, songs=[{"title": "Let Everything", "to": "D"}, {"slug": "quiet-waters"}]).json
    # The builder sends back each song as it got it.
    songs = [{"slug": s["slug"], "title": s["title"], "to": s["to"]} for s in saved["songs"]]
    again = update(sets_client, saved, songs=songs).json
    assert [(s["slug"], s["title"], s["to"]) for s in again["songs"]] == [
        (None, "Let Everything", "D"), ("quiet-waters", "Quiet Waters", None)]


def test_a_song_with_neither_a_slug_nor_a_title_is_refused(sets_client):
    assert new_set(sets_client, songs=[{"to": "D"}]).status_code == 400
    assert new_set(sets_client, songs=[{"title": "  "}]).status_code == 400


def test_a_sets_pdf_leaves_out_songs_not_in_the_library(sets_client):
    r = sets_client.post("/api/setlist/pdf", json={"songs": [{"title": "Let Everything", "to": "D"},
                                                             {"slug": "morning-light"}]})
    assert r.status_code == 200
    assert pdf_bookmarks(r.data) == ["Morning Light (G)"]


def test_each_set_has_a_view_page(sets_client):
    saved = new_set(sets_client).json
    r = sets_client.get(f"/sets/{saved['id']}")
    assert r.status_code == 200 and b"<title>YFM" in r.data


def test_a_deleted_set_is_gone_for_good(sets_client):
    saved = new_set(sets_client).json
    assert sets_client.delete(f"/api/sets/{saved['id']}").status_code == 200
    assert sets_client.get(f"/api/sets/{saved['id']}").status_code == 404
    assert sets_client.get("/api/sets").json == {"upcoming": [], "past": []}
    assert sets_client.post(f"/api/sets/{saved['id']}/restore").status_code == 404


def test_deleting_a_set_frees_its_date(sets_client):
    saved = new_set(sets_client).json
    sets_client.delete(f"/api/sets/{saved['id']}")
    assert new_set(sets_client).status_code == 201


def test_deleting_a_set_twice_is_not_found(sets_client):
    saved = new_set(sets_client).json
    sets_client.delete(f"/api/sets/{saved['id']}")
    assert sets_client.delete(f"/api/sets/{saved['id']}").status_code == 404


def test_a_past_set_cant_be_deleted(sets_client):
    # It's Wednesday 30 September: last Saturday's set is the team's history now.
    saved = new_set(sets_client, date="2026-09-26").json
    r = sets_client.delete(f"/api/sets/{saved['id']}")
    assert r.status_code == 409
    assert r.json["error"] == "A past set can't be deleted."
    assert sets_client.get(f"/api/sets/{saved['id']}").status_code == 200


def test_todays_set_can_still_be_deleted(sets_client):
    saved = new_set(sets_client, date="2026-09-30").json
    assert sets_client.delete(f"/api/sets/{saved['id']}").status_code == 200


def test_moving_a_set_cant_replace_a_past_set(sets_client):
    past = new_set(sets_client, date="2026-09-26").json
    moving = new_set(sets_client).json
    r = sets_client.put(f"/api/sets/{moving['id']}", json={
        "date": "2026-09-26", "songs": [], "version": moving["version"], "replace": past["id"]})
    assert r.status_code == 409 and r.json["error"] == "A past set can't be replaced."
    assert sets_client.get(f"/api/sets/{past['id']}").json["songs"] == past["songs"]
    assert sets_client.get(f"/api/sets/{moving['id']}").json["date"] == SAT


def test_a_past_set_can_still_be_changed(sets_client):
    past = new_set(sets_client, date="2026-09-26").json
    r = update(sets_client, past, songs=[{"slug": "quiet-waters", "to": "Fm"}])
    assert r.status_code == 200 and [s["to"] for s in r.json["songs"]] == ["Fm"]


def test_a_past_set_cant_be_moved_to_a_coming_date(sets_client):
    # Where it could then be deleted, and its Saturday would have no set.
    past = new_set(sets_client, date="2026-09-26").json
    r = update(sets_client, past, date="2026-10-17")
    assert r.status_code == 409 and r.json["error"] == "A past set can't be moved to a coming date."
    assert sets_client.get(f"/api/sets/{past['id']}").json["date"] == "2026-09-26"


def test_a_past_set_can_move_to_another_past_date(sets_client):
    past = new_set(sets_client, date="2026-09-26").json
    assert update(sets_client, past, date="2026-09-19").json["date"] == "2026-09-19"


def test_a_saturday_set_is_past_once_the_service_is_over_at_5pm(sets_client, clock):
    from datetime import datetime
    saved = new_set(sets_client).json  # Saturday 3 October
    clock.now = datetime(2026, 10, 3, 16, 59)
    assert [s["date"] for s in sets_client.get("/api/sets").json["upcoming"]] == [SAT]
    clock.now = datetime(2026, 10, 3, 17, 0)
    listing = sets_client.get("/api/sets").json
    assert listing["upcoming"] == [] and [s["date"] for s in listing["past"]] == [SAT]
    assert sets_client.delete(f"/api/sets/{saved['id']}").status_code == 409  # a past set is kept


def test_another_days_set_stays_upcoming_until_midnight(sets_client, clock):
    from datetime import datetime
    new_set(sets_client, date="2026-10-02")  # a Friday evening
    clock.now = datetime(2026, 10, 2, 21, 0)
    assert [s["date"] for s in sets_client.get("/api/sets").json["upcoming"]] == ["2026-10-02"]


# ---------------------------------------------------------------- who made a set (kept, never shown)

def _created_by(tmp_path, set_id):
    import sqlite3
    db = sqlite3.connect(tmp_path / "sets.db")
    try:
        return db.execute("SELECT created_by FROM sets WHERE id = ?", (set_id,)).fetchone()[0]
    finally:
        db.close()


def test_a_set_remembers_the_name_of_whoever_made_it(sets_client, tmp_path):
    sets_client.set_cookie("yfm_name", "Wei%20Ling")  # as the pages write it (encodeURIComponent)
    r = sets_client.post("/api/sets", json={"date": "2026-10-03", "songs": [{"slug": "quiet-waters"}]})
    assert r.status_code == 201
    assert _created_by(tmp_path, r.json["id"]) == "Wei Ling"


def test_who_made_a_set_is_not_shown(sets_client):
    sets_client.set_cookie("yfm_name", "Wei")
    r = sets_client.post("/api/sets", json={"date": "2026-10-03", "songs": [{"slug": "quiet-waters"}]})
    assert "Wei" not in r.get_data(as_text=True)
    assert "Wei" not in sets_client.get(f"/api/sets/{r.json['id']}").get_data(as_text=True)
    assert "Wei" not in sets_client.get("/api/sets").get_data(as_text=True)


def test_a_set_made_by_a_guest_has_no_maker(sets_client, tmp_path):
    r = sets_client.post("/api/sets", json={"date": "2026-10-03", "songs": [{"slug": "quiet-waters"}]})
    assert _created_by(tmp_path, r.json["id"]) is None


def test_changing_a_set_keeps_who_made_it(sets_client, tmp_path):
    sets_client.set_cookie("yfm_name", "Wei")
    made = sets_client.post("/api/sets", json={"date": "2026-10-03", "songs": [{"slug": "quiet-waters"}]}).json
    sets_client.set_cookie("yfm_name", "Grace")
    r = sets_client.put(f"/api/sets/{made['id']}", json={"date": "2026-10-03", "songs": [{"slug": "morning-light"}],
                                                          "version": made["version"]})
    assert r.status_code == 200
    assert _created_by(tmp_path, made["id"]) == "Wei"
