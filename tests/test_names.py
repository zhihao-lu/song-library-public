"""A name, typed in the corner, makes a viewer's capos follow them to their other devices.

It's just a name, with no password: anyone who types it gets its capos.
"""

from urllib.parse import quote


def capos_url(name):
    return f"/api/people/{quote(name)}/capos"


E_ON_4 = {"shape": "C", "fret": 4}


def test_a_capo_saved_under_a_name_comes_back_on_another_device(sets_client):
    assert sets_client.put(capos_url("Wei") + "/1/morning-light", json=E_ON_4).status_code == 200
    assert sets_client.get(capos_url("  wei ")).json == {"capos": {"1": {"morning-light": E_ON_4}}}


def test_a_new_name_has_no_capos(sets_client):
    assert sets_client.get(capos_url("Jess")).json == {"capos": {}}


def test_clearing_a_capo(sets_client):
    sets_client.put(capos_url("Wei") + "/1/morning-light", json=E_ON_4)
    assert sets_client.delete(capos_url("Wei") + "/1/morning-light").status_code == 200
    assert sets_client.get(capos_url("Wei")).json == {"capos": {}}


def test_entering_a_name_adds_this_devices_capos_and_keeps_the_saved_ones(sets_client):
    sets_client.put(capos_url("Wei") + "/1/morning-light", json=E_ON_4)
    device = {"1": {"morning-light": {"shape": "A", "fret": 7}, "quiet-waters": {"shape": "Dm", "fret": 2}}}
    r = sets_client.post(capos_url("Wei"), json={"capos": device})
    assert r.json == {"capos": {"1": {"morning-light": E_ON_4, "quiet-waters": {"shape": "Dm", "fret": 2}}}}
    assert sets_client.get(capos_url("Wei")).json == r.json


def test_a_name_must_have_letters(sets_client):
    r = sets_client.get(capos_url(" "))
    assert r.status_code == 400 and r.json == {"error": "Type a name."}


def test_a_capo_must_be_a_shape_and_a_fret(sets_client):
    r = sets_client.put(capos_url("Wei") + "/1/morning-light", json={"shape": "X", "fret": 40})
    assert r.status_code == 400


def test_deleting_a_set_forgets_the_capos_saved_for_it(sets_client):
    saved = sets_client.post("/api/sets", json={"date": "2026-10-03", "songs": [{"slug": "morning-light"}]}).json
    sets_client.put(capos_url("Wei") + f"/{saved['id']}/morning-light", json={"shape": "E", "fret": 3})
    sets_client.delete(f"/api/sets/{saved['id']}")
    assert sets_client.get(capos_url("Wei")).json == {"capos": {}}
