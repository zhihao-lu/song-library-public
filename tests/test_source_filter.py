"""Search can be narrowed to some sources; a sheet several sources share matches any of them."""

from conftest import tagged
from make_charts import cho, pdf


def setup_songs(client, drive):
    shared = pdf(cho("Cornerstone"))
    drive.upload(drive.path("YFM resources", "3. English Songs"), "Cornerstone.pdf", shared)
    drive.upload(drive.path("ASE", "Cornerstone"), "Cornerstone.pdf", shared)
    drive.upload(drive.path("MM ministry", "02 English Song Scores"), "Cornerstone.pdf", pdf(cho("Cornerstone"), size=14))
    drive.upload(drive.path("MM ministry", "02 English Song Scores"), "Harbour Light.pdf", pdf(cho("Harbour Light")))
    client.post("/api/library/sync")


def test_search_with_sources_returns_only_arrangements_tagged_with_one_of_them(sources_client, drive):
    setup_songs(sources_client, drive)
    assert tagged(sources_client, "corner") == ["Cornerstone · YFM · ASE", "Cornerstone · MM"]
    assert tagged(sources_client, "corner", sources="YFM") == ["Cornerstone · YFM · ASE"]
    assert tagged(sources_client, "corner", sources="ASE") == ["Cornerstone · YFM · ASE"]
    assert tagged(sources_client, "corner", sources="MM,YFM") == [
        "Cornerstone · YFM · ASE", "Cornerstone · MM"]


def test_the_filter_never_changes_what_a_set_shows(sources_client, drive):
    setup_songs(sources_client, drive)
    slug = [r for r in sources_client.get("/api/library/search?q=harbour").json["results"]][0]["slug"]
    set_id = sources_client.post("/api/sets", json={"date": "2026-10-03", "songs": [{"slug": slug}]}).json["id"]
    assert tagged(sources_client, "harbour", sources="YFM") == []
    assert [s["title"] for s in sources_client.get(f"/api/sets/{set_id}").json["songs"]] == ["Harbour Light"]


def test_todays_library_has_no_sources_to_filter_by(client):
    assert client.get("/api/library/status").json["sources"] == []
    assert [r["title"] for r in client.get("/api/library/search?q=morning&sources=YFM").json["results"]] == ["Morning Light"]
