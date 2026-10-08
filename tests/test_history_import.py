"""Importing the team's past sets from the history kept in the Telegram chat (history_import.py)."""

import pytest

from history_import import import_history, import_schedule


def row(date, song, key="", reprise=False, leader="Ann"):
    return {"date": date, "song": song, "key": key, "reprise": reprise, "leader": leader}


@pytest.fixture
def run(sets_client, app_module, clock):
    """Import rows into the test app's sets; returns the report."""
    def run(rows, **options):
        with app_module.app.app_context():
            import sets_api
            return import_history(rows, sets_api._library(), sets_api._store(), clock(), **options)
    return run


def past(client):
    return [(s["date"], [(x["title"], x["to"]) for x in s["songs"]]) for s in client.get("/api/sets").json["past"]]


def test_each_service_becomes_a_past_set_with_its_songs_in_order_and_keys(sets_client, run):
    run([row("2026-09-19", "Quiet Waters", "Fm"), row("2026-09-19", "Morning Light", "A"),
         row("2026-09-26", "Open Sky", "E")])
    assert past(sets_client) == [("2026-09-26", [("Open Sky", "E")]),
                                 ("2026-09-19", [("Quiet Waters", "Fm"), ("Morning Light", "A")])]


def test_a_reprise_is_left_out(sets_client, run):
    run([row("2026-09-26", "Open Sky", "D"), row("2026-09-26", "Morning Light", "G"),
         row("2026-09-26", "Open Sky", "D", reprise=True)])
    assert past(sets_client) == [("2026-09-26", [("Open Sky", "D"), ("Morning Light", "G")])]


def test_a_song_with_no_key_written_is_in_its_chart_key(sets_client, run):
    run([row("2026-09-26", "Open Sky")])
    assert past(sets_client) == [("2026-09-26", [("Open Sky", None)])]


def test_a_title_written_in_traditional_characters_or_with_你_is_still_the_librarys_song(sets_client, run):
    run([row("2026-09-26", "你是永恆唯一的盼望", "E")])
    assert past(sets_client) == [("2026-09-26", [("Only Hope 祢是永恒唯一的盼望", "E")])]


def test_a_song_titled_in_english_and_chinese_is_found_by_its_english_title(sets_client, run):
    run([row("2026-09-26", "Only Hope", "E")])
    assert past(sets_client) == [("2026-09-26", [("Only Hope 祢是永恒唯一的盼望", "E")])]


def test_a_title_the_library_doesnt_have_is_kept_by_its_title(sets_client, run):
    report = run([row("2026-09-26", "Let Everything", "D")])
    assert past(sets_client) == [("2026-09-26", [("Let Everything", "D")])]
    assert report["not_in_library"] == ["Let Everything"]


def test_the_mapping_says_which_library_song_a_title_is(sets_client, run):
    # "The Joy" is how the chat wrote it; the mapping wins over the title "Morning Light" too.
    report = run([row("2026-09-26", "The Joy", "C"), row("2026-09-26", "Morning Light", "A")],
                 mapping={"The Joy": "open-sky", "Morning Light": None})
    assert past(sets_client) == [("2026-09-26", [("Open Sky", "C"), ("Morning Light", "A")])]
    assert sets_client.get("/api/sets").json["past"][0]["songs"][1]["slug"] is None
    assert report["mapped"] == {"The Joy": "open-sky"}
    assert report["not_in_library"] == ["Morning Light"]


def test_a_mapping_to_a_song_the_library_doesnt_have_is_refused(sets_client, run):
    with pytest.raises(ValueError, match="'no-such-song'"):
        run([row("2026-09-26", "The Joy")], mapping={"The Joy": "no-such-song"})
    assert past(sets_client) == []


def test_a_date_that_already_has_a_set_is_left_as_it_is(sets_client, run):
    made = sets_client.post("/api/sets", json={"date": "2026-09-26", "songs": [{"slug": "quiet-waters"}]}).json
    report = run([row("2026-09-19", "Open Sky"), row("2026-09-26", "Morning Light")])
    assert past(sets_client) == [("2026-09-26", [("Quiet Waters", None)]), ("2026-09-19", [("Open Sky", None)])]
    assert sets_client.get(f"/api/sets/{made['id']}").json["version"] == made["version"]
    assert (report["created"], report["skipped"]) == (["2026-09-19"], ["2026-09-26"])


def test_importing_again_changes_nothing(sets_client, run):
    rows = [row("2026-09-19", "Open Sky"), row("2026-09-26", "Morning Light")]
    run(rows)
    before = sets_client.get("/api/sets").json
    assert run(rows)["created"] == []
    assert sets_client.get("/api/sets").json == before


def test_a_dry_run_reports_without_saving_anything(sets_client, run):
    report = run([row("2026-09-26", "Morning Light"), row("2026-09-26", "Let Everything")], write=False)
    assert report["created"] == ["2026-09-26"] and report["not_in_library"] == ["Let Everything"]
    assert past(sets_client) == []


def test_a_key_that_doesnt_fit_the_chart_is_its_chart_key_and_reported(sets_client, run):
    # Quiet Waters is charted in Em: "G" can't say how far to move it, nor can "H".
    report = run([row("2026-09-26", "Quiet Waters", "G"), row("2026-09-26", "Morning Light", "H")])
    assert past(sets_client) == [("2026-09-26", [("Quiet Waters", None), ("Morning Light", None)])]
    assert report["keys_left_out"] == [("2026-09-26", "Quiet Waters", "G"), ("2026-09-26", "Morning Light", "H")]


def test_a_song_with_several_arrangements_is_its_first(sources_client, drive, run):
    from make_charts import OTHER_CHORDS, cho, pdf
    yfm = drive.path("YFM resources", "3. English Songs", "Holy Ground")
    drive.upload(yfm, "Holy Ground.pdf", pdf(cho("Holy Ground")))
    sources_client.post("/api/library/sync")
    drive.upload(yfm, "Holy Ground (acoustic).pdf", pdf(cho("Holy Ground", **OTHER_CHORDS)))
    drive.upload(drive.path("ASE", "Holy Ground"), "Holy Ground.pdf", pdf(cho("Holy Ground", key="C", chorus=["[C]Other [G]words to [D]sing"])))
    sources_client.post("/api/library/sync")
    run([row("2026-09-26", "Holy Ground", "A")])
    (song,) = sources_client.get("/api/sets").json["past"][0]["songs"]
    assert (song["tag"], song["source_file"]) == ("YFM", "Holy Ground.pdf")


# ---------------------------------------------------------------- who led: kept, never shown

@pytest.fixture
def store(app_module, sets_client):
    with app_module.app.app_context():
        import sets_api
        return sets_api._store()


def test_importing_records_who_led_each_service_and_a_co_led_one_under_its_first_leader(sets_client, run, store):
    report = run([row("2026-09-12", "Open Sky", leader="Ann"), row("2026-09-19", "Open Sky", leader="Bea & Cy")])
    assert (store.leader_on("2026-09-12"), store.leader_on("2026-09-19")) == ("Ann", "Bea")
    assert report["leaders"] == {"2026-09-12": "Ann", "2026-09-19": "Bea"}


def test_a_schedule_says_who_leads_and_loading_it_again_replaces_them(sets_client, store):
    sets_client.post("/api/sets", json={"date": "2026-10-03", "songs": [{"slug": "open-sky"}]})  # made in the app
    import_schedule([{"date": "2026-10-03", "leader": "Dan"}, {"date": "2026-10-10", "leader": "Ann"}], store)
    assert (store.leader_on("2026-10-03"), store.leader_on("2026-10-10")) == ("Dan", "Ann")
    assert import_schedule([{"date": "2026-10-10", "leader": "Eve & Ann"}], store) == {"2026-10-10": "Eve"}
    assert (store.leader_on("2026-10-03"), store.leader_on("2026-10-10")) == ("Dan", "Eve")


def test_past_sets_are_listed_with_who_led_them(sets_client, run, store):
    run([row("2026-09-19", "Open Sky", leader="Bea & Cy"), row("2026-09-26", "Open Sky")])
    sets_client.post("/api/sets", json={"date": "2026-09-12", "songs": [{"slug": "open-sky"}]})  # no one recorded
    listing = sets_client.get("/api/sets").json
    assert [(s["date"], s["leader"]) for s in listing["past"]] == [
        ("2026-09-26", "Ann"), ("2026-09-19", "Bea"), ("2026-09-12", None)]


def test_coming_sets_dont_say_who_leads(sets_client, store):
    sets_client.post("/api/sets", json={"date": "2026-10-03", "songs": [{"slug": "open-sky"}]})
    import_schedule([{"date": "2026-10-03", "leader": "Dan"}], store)
    (coming,) = sets_client.get("/api/sets").json["upcoming"]
    assert "leader" not in coming
