"""One look for everyone: the night look. The classic one, and the switch between them (three taps
on the logo, ?look=, a look cookie), are retired."""

import pytest

PAGES = ["/", "/convert", "/sets", "/sets/1", "/s/abc123"]


def is_night(response):
    return response.status_code == 200 and "/static/theme.css" in response.get_data(as_text=True)


@pytest.mark.parametrize("path", PAGES)
def test_everyone_gets_the_night_look(client, path):
    assert is_night(client.get(path))


@pytest.mark.parametrize("path", PAGES)
def test_the_old_switch_no_longer_picks_a_look(client, path):
    client.set_cookie("look", "classic")
    assert is_night(client.get(path))
    assert is_night(client.get(path + "?look=classic"))


def test_the_classic_pages_are_gone(client):
    assert client.get("/static/classic/setlist.html").status_code == 404


def test_pages_are_checked_each_time_so_a_deploy_shows_at_once(client):
    assert "no-cache" in client.get("/sets").headers["Cache-Control"]
