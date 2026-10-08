"""Usage tracking: the pages send what happens; a private page shows what people do.

Events are kept per browser (a random id) and visit, with the name in the bar when there is one.
The stats need STATS_PASSWORD, and don't exist without it.
"""
import base64
from datetime import timedelta

import pytest

PASSWORD = "s3cret"


@pytest.fixture
def events_client(sets_client, app_module, tmp_path, monkeypatch):
    monkeypatch.setitem(app_module.app.config, "EVENTS_DB", tmp_path / "events.db")
    monkeypatch.setitem(app_module.app.config, "STATS_PASSWORD", PASSWORD)
    return sets_client


def send(client, *events, device="dev1", visit="v1"):
    return client.post("/api/events", json={"device": device, "visit": visit,
                                            "events": [{"page": p, "event": e, "data": d} for p, e, d in events]})


def stats(client, password=PASSWORD, days=30):
    auth = base64.b64encode(f"x:{password}".encode()).decode()
    return client.get(f"/api/stats?days={days}", headers={"Authorization": f"Basic {auth}"})


def test_events_are_kept_with_the_name_in_the_bar(events_client):
    events_client.set_cookie("yfm_name", "Wei%20Ling")
    assert send(events_client, ("/", "page_view", {"touch": True})).status_code == 204
    s = stats(events_client).json
    assert s["totals"]["visits"] == 1 and s["totals"]["people"] == 1
    assert s["people"][0]["name"] == "Wei Ling"
    assert s["journeys"][0]["name"] == "Wei Ling" and s["journeys"][0]["phone"]


def test_a_beacon_sends_its_events_as_text(events_client):
    r = events_client.post("/api/events", data='{"device":"d","visit":"v","events":[{"page":"/","event":"page_view"}]}',
                           content_type="text/plain")
    assert r.status_code == 204
    assert stats(events_client).json["totals"]["page_views"] == 1


def test_a_bad_batch_is_refused(events_client):
    assert events_client.post("/api/events", json={"events": []}).status_code == 400
    assert events_client.post("/api/events", data="nonsense", content_type="text/plain").status_code == 400


def test_the_stats_need_the_password(events_client):
    assert events_client.get("/api/stats").status_code == 401
    assert stats(events_client, password="wrong").status_code == 401
    assert events_client.get("/stats").status_code == 401
    auth = base64.b64encode(f"x:{PASSWORD}".encode()).decode()
    assert events_client.get("/stats", headers={"Authorization": f"Basic {auth}"}).status_code == 200


def test_without_a_stats_password_there_are_no_stats(sets_client, app_module, monkeypatch):
    monkeypatch.setitem(app_module.app.config, "STATS_PASSWORD", None)
    monkeypatch.delenv("STATS_PASSWORD", raising=False)
    assert sets_client.get("/stats").status_code == 404
    assert sets_client.get("/api/stats").status_code == 404


def test_the_stats_password_gets_past_the_sites_own(events_client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "APP_PASSWORD", "site")
    assert stats(events_client).status_code == 200


def test_the_builder_funnel_counts_visits_by_how_far_they_got(events_client):
    send(events_client, ("/", "page_view", {}), ("/", "song_added", {"slug": "a", "title": "A"}),
         ("/", "pdf_downloaded", {"mode": "once"}), visit="v1")
    send(events_client, ("/", "page_view", {}), ("/", "song_added", {"slug": "a", "title": "A"}), visit="v2")
    send(events_client, ("/", "page_view", {}), ("/", "tab", {"mode": "service"}),
         ("/", "set_saved", {"mode": "service"}), visit="v3")
    send(events_client, ("/sets", "page_view", {}), visit="v4")
    s = stats(events_client).json
    assert dict(s["funnel"]) == {"Opened the builder": 3, "Added a song": 2, "Downloaded a PDF": 1,
                                 "Made a share link": 0, "Saved a set": 1}
    assert s["outcomes_by_tab"] == {"once": {"pdf_downloaded": 1}, "service": {"set_saved": 1}}
    assert s["songs_added"] == [["A", 2]]


def test_searches_and_what_was_pressed_are_counted(events_client):
    send(events_client, ("/", "search", {"q": "Hope", "results": 4}), ("/", "search", {"q": "hope ", "results": 4}),
         ("/", "search", {"q": "xyzzy", "results": 0}), ("/", "click", {"label": "Download PDF"}),
         ("/", "click", {"label": "Download PDF"}), ("/", "click", {"label": "Share link"}))
    s = stats(events_client).json
    assert s["searches"][0] == ["hope", 2]
    assert s["searches_no_results"] == [["xyzzy", 1]]
    assert s["clicks"][:2] == [["Download PDF", 2], ["Share link", 1]]


def test_only_the_days_asked_for_are_counted(events_client, clock):
    send(events_client, ("/", "page_view", {}), visit="old")
    clock.now += timedelta(days=10)
    send(events_client, ("/", "page_view", {}), visit="new")
    assert stats(events_client, days=7).json["totals"]["visits"] == 1
    assert stats(events_client, days=30).json["totals"]["visits"] == 2
    by_day = stats(events_client, days=30).json["by_day"]
    assert len(by_day) == 30 and sum(d["visits"] for d in by_day) == 2
