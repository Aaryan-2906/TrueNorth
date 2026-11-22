"""Find experts (preview), with a mocked person-search reply: these tests spend no credits."""

import json

import pytest
from fastapi.testclient import TestClient

from app import cli, crustdata, finder, server
from app.models import Job, Profile, YearMonth
from tests.test_robustness import World
from tests.test_server import of_kind, read_events

TODAY = (2026, 11)
POSITION = {
    "name": "Team Leader",
    "min_total_years": 12,
    "min_similar_years": 5,
    "similar_title_keywords": ["team leader", "resident engineer"],
    "postgraduate": "preferential",
}


def job(title, start, end, employer="Acme"):
    return {"name": employer, "title": title, "start_date": start, "end_date": end}


def person(number, name, current=(), past=()):
    return {
        "crustdata_person_id": number,
        "basic_profile": {"name": name},
        "social_handles": {"professional_network_identifier": {"profile_url": f"https://www.linkedin.com/in/p{number}"}},
        "experience": {"employment_details": {"current": list(current), "past": list(past)}},
    }


QUALIFIED_LOW = person(1, "Asha Rao", past=[
    job("Site Engineer", "2005-01-01T00:00:00+00:00", "2014-12-31T00:00:00+00:00"),
    job("Team Leader", "2015-01-01T00:00:00+00:00", "2020-12-31T00:00:00+00:00"),
])
QUALIFIED_HIGH = person(2, "Dr. Vikram Singh", current=[job("Resident Engineer", "2016-11-01", None)], past=[
    job("Junior Engineer", "2004-01-01", "2016-10-31"),
])
SHORT_TOTAL = person(3, "Short Total", past=[job("Team Leader", "2016-01-01", "2023-12-31")])
SHORT_SIMILAR = person(4, "Short Similar", past=[
    job("Site Engineer", "2000-01-01", "2019-12-31"),
    job("Team Leader", "2020-01-01", "2021-12-31"),
])
UNDATED = person(5, "Undated Role", past=[
    job("Site Engineer", "2000-01-01", "2019-12-31"),
    job("Team Leader", None, None),
])
REPLY = {"profiles": [QUALIFIED_LOW, SHORT_TOTAL, QUALIFIED_HIGH, SHORT_SIMILAR, UNDATED], "total_count": 900, "next_cursor": "x"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    built = World(tmp_path, monkeypatch)
    built.http = lambda _url, _body: (200, REPLY)
    monkeypatch.setattr(cli, "_load_tender", lambda path=None: {"positions": {"team_leader": POSITION}})
    return built


@pytest.fixture
def client():
    return TestClient(server.app)


def test_search_asks_only_for_listed_fields_and_no_contact_details(world):
    finder.find_candidates("team_leader", limit=5, today=TODAY)

    url, body = world.calls[0]
    assert url.endswith("/person/search") and body["limit"] == 5
    assert "basic_profile.name" in body["fields"]
    assert "experience.employment_details.past.start_date" in body["fields"]
    assert not any(word in field.lower() for field in body["fields"] for word in ("email", "phone", "contact"))


def test_filter_uses_region_and_every_keyword_in_current_and_past(world):
    finder.find_candidates("team_leader", today=TODAY)

    conditions = world.calls[0][1]["filters"]["conditions"]
    assert conditions[0] == {"field": "basic_profile.location.country", "type": "=", "value": "India"}
    titles = {(c["field"].split(".")[2], c["value"]) for c in conditions[1]["conditions"]}
    assert conditions[1]["op"] == "or"
    assert titles == {(g, k) for g in ("current", "past") for k in POSITION["similar_title_keywords"]}


def test_years_maths_filtering_and_order(world):
    run = finder.find_candidates("team_leader", today=TODAY)

    assert run.searched == 5 and run.total_matching == 900
    assert [c.profile.name for c in run.candidates] == ["Dr. Vikram Singh", "Asha Rao"]
    high, low = run.candidates
    assert (high.total_months, high.similar_months) == (275, 121)  # 2004-01 to 2026-11; 2016-11 to 2026-11
    assert (low.total_months, low.similar_months) == (192, 72)  # 2005-01 to 2020-12; 2015-01 to 2020-12


def test_overlapping_similar_jobs_count_once():
    both = [
        Job(employer="A", title="Team Leader", start=YearMonth(2010, 1), end=YearMonth(2015, 12), is_current=False, source="profile.past[0]"),
        Job(employer="B", title="Resident Engineer", start=YearMonth(2014, 1), end=YearMonth(2016, 12), is_current=False, source="profile.past[1]"),
        Job(employer="C", title="Engineer", start=YearMonth(2000, 1), end=YearMonth(2009, 12), is_current=False, source="profile.past[2]"),
    ]

    profile = Profile(person_id=1, name="X", profile_url=None, jobs=both, education=[], duplicates_collapsed=[])

    candidate = finder.assess(profile, POSITION, TODAY)

    assert candidate.similar_months == 84 and candidate.total_months == 204


@pytest.mark.parametrize("sample", [SHORT_TOTAL, SHORT_SIMILAR, UNDATED])
def test_below_a_minimum_is_left_out(world, sample):
    world.http = lambda _url, _body: (200, {"profiles": [sample]})

    assert finder.find_candidates("team_leader", today=TODAY).candidates == []


def test_exactly_at_the_minimums_is_kept(world):
    exact = person(9, "Exact", past=[
        job("Engineer", "2000-01-01", "2006-12-31"),
        job("Team Leader", "2007-01-01", "2011-12-31"),
    ])
    world.http = lambda _url, _body: (200, {"profiles": [exact]})

    run = finder.find_candidates("team_leader", today=TODAY)

    assert (run.candidates[0].total_months, run.candidates[0].similar_months) == (144, 60)


def test_same_person_twice_is_listed_once(world):
    world.http = lambda _url, _body: (200, {"profiles": [QUALIFIED_LOW, QUALIFIED_LOW]})

    assert len(finder.find_candidates("team_leader", today=TODAY).candidates) == 1


@pytest.mark.parametrize(
    "name, expected",
    [("Daniel Ahmadizadeh", "D.A."), ("Dr. Vikram Singh", "V.S."), ("A B C D", "A.B.C."), ("Asha (PMP) Rao", "A.R."), ("", "Name not listed"), (None, "Name not listed")],
)
def test_initials(name, expected):
    assert finder.initials(name) == expected


def test_second_run_uses_the_cache(world):
    finder.find_candidates("team_leader", today=TODAY)
    world.calls.clear()

    finder.find_candidates("team_leader", today=TODAY)

    assert world.calls == []


def test_unexpected_reply_is_a_clear_error(world):
    world.http = lambda _url, _body: (200, {"rows": []})

    with pytest.raises(crustdata.CrustdataError, match="unexpected shape"):
        finder.find_candidates("team_leader", today=TODAY)


def test_api_streams_stages_a_call_and_a_result(world, client):
    events = read_events(client.post("/api/find-experts", json={"position": "team_leader"}))

    assert [s["stage"] for s in of_kind(events, "stage")] == ["looking_up", "checking", "writing", "done"]
    assert [c["service"] for c in of_kind(events, "call")] == ["Crustdata /person/search"]
    result = events[-1][1]
    assert result["ok"] is True and result["kind"] == "finder"
    assert result["label"] == finder.LABEL
    assert result["credits"] == float(world.ledger_rows()[-1]["credits_used"])
    assert result["candidates"][0]["initials"] == "V.S."
    assert result["candidates"][0]["evidence"][0]["title"] == "Resident Engineer"


def test_api_result_has_no_profile_urls_or_employers(world, client):
    result = read_events(client.post("/api/find-experts", json={"position": "team_leader"}))[-1][1]

    text = json.dumps(result)
    assert "linkedin.com" not in text and "Acme" not in text


def test_api_reports_a_crustdata_error_cleanly(world, client):
    world.http = lambda _url, _body: (402, {})

    result = read_events(client.post("/api/find-experts", json={"position": "team_leader"}))[-1][1]

    assert result["ok"] is False and "credits have run out" in result["error"]


def test_api_refuses_an_unknown_position_without_a_call(world, client):
    response = client.post("/api/find-experts", json={"position": "astronaut"})

    assert response.status_code == 422 and world.calls == []


def test_api_refuses_cross_site_requests(world, client):
    response = client.post("/api/find-experts", json={"position": "team_leader"}, headers={"origin": "https://evil.example"})

    assert response.status_code == 403 and world.calls == []


def test_page_has_the_preview_tab(world, client):
    page = client.get("/").text

    for marker in ('id="tabFind"', "Find experts (preview)", "/api/find-experts", "Show name"):
        assert marker in page
