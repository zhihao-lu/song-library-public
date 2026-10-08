"""An unsaved setlist, kept under the name in the bar so it can be offered back on any device
(the builder also keeps one in the browser). One draft per name; it's forgotten after 14 days."""
from datetime import timedelta

DRAFT = {"songs": [{"slug": "morning-light", "title": "Morning Light", "to": "A"}], "mode": "service",
         "date": "2026-10-10", "editing": None}


def put(client, name, draft=DRAFT):
    return client.put(f"/api/people/{name}/draft", json={"draft": draft})


def get(client, name):
    return client.get(f"/api/people/{name}/draft").json


def test_a_draft_kept_under_a_name_comes_back(sets_client):
    assert put(sets_client, "Jolene").status_code == 200
    got = get(sets_client, "Jolene")
    assert got["draft"] == DRAFT and got["saved_at"] == "2026-09-30T20:00:00"


def test_names_match_whatever_their_case_and_spacing(sets_client):
    put(sets_client, "Jolene")
    assert get(sets_client, "  jolene ")["draft"] == DRAFT


def test_a_name_has_one_draft_the_latest(sets_client, clock):
    put(sets_client, "Jolene")
    clock.now += timedelta(hours=1)
    later = {**DRAFT, "songs": []}
    put(sets_client, "Jolene", later)
    assert get(sets_client, "Jolene")["draft"] == later


def test_no_draft_is_none(sets_client):
    assert get(sets_client, "Nobody") == {"draft": None, "saved_at": None}


def test_a_draft_is_forgotten_after_14_days(sets_client, clock):
    put(sets_client, "Jolene")
    clock.now += timedelta(days=13, hours=23)
    assert get(sets_client, "Jolene")["draft"] == DRAFT
    clock.now += timedelta(hours=2)
    assert get(sets_client, "Jolene")["draft"] is None


def test_clearing_a_draft(sets_client):
    put(sets_client, "Jolene")
    assert sets_client.delete("/api/people/Jolene/draft").status_code == 204
    assert get(sets_client, "Jolene")["draft"] is None


def test_a_draft_must_be_a_setlist_and_not_huge(sets_client):
    assert put(sets_client, "Jolene", "nonsense").status_code == 400
    assert put(sets_client, "Jolene", {"songs": "x"}).status_code == 400
    assert put(sets_client, "Jolene", {"songs": [{"title": "x" * 100}] * 2000}).status_code == 413


def test_someone_elses_draft_is_theirs(sets_client):
    put(sets_client, "Jolene")
    assert get(sets_client, "Wei")["draft"] is None
