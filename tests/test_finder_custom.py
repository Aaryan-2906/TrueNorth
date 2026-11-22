"""Find experts with a custom role, using mocked search replies. No paid calls."""

import json

import pytest
from fastapi.testclient import TestClient

from app import checker, finder, server
from tests.test_finder import job, person
from tests.test_robustness import World
from tests.test_server import of_kind, read_events

TODAY = (2026, 11)
ROLE = {
    "titles": " Data Engineer, analytics   engineer, data engineer ",
    "sectors": "fintech, Payments, financial services",
    "min_total_years": "6",
    "min_similar_years": 3,
    "region": "India",
}


def industry_job(title, start, end, employer, industries):
    return {**job(title, start, end, employer), "company_industries": industries}


FINTECH = person(1, "Asha Rao", past=[
    job("Software Engineer", "2014-01-01", "2017-12-31", "Acme"),
    job("Data Engineer", "2018-01-01", "2023-12-31", "Nova Fintech Pvt Ltd"),
])
BY_INDUSTRY = person(2, "Ravi Iyer", past=[
    job("Analyst", "2015-01-01", "2018-12-31", "Acme"),
    industry_job("Analytics Engineer", "2019-01-01", "2022-12-31", "Blue Bank", ["Financial Services"]),
])
NO_SECTOR = person(3, "No Sector", past=[
    job("Software Engineer", "2010-01-01", "2015-12-31", "Acme"),
    job("Data Engineer", "2016-01-01", "2024-12-31", "Retail Co"),
])
SHORT_IN_TITLE = person(4, "Short Title", past=[
    job("Software Engineer", "2010-01-01", "2021-12-31", "Acme"),
    job("Data Engineer", "2022-01-01", "2023-12-31", "Nova Fintech"),
])
SHORT_TOTAL = person(5, "Short Total", past=[job("Data Engineer", "2020-01-01", "2024-12-31", "Nova Fintech")])
REPLY = {"profiles": [BY_INDUSTRY, NO_SECTOR, FINTECH, SHORT_IN_TITLE, SHORT_TOTAL], "total_count": 5}


@pytest.fixture
def world(tmp_path, monkeypatch):
    built = World(tmp_path, monkeypatch)
    built.http = lambda _url, _body: (200, REPLY)
    return built


@pytest.fixture
def client():
    return TestClient(server.app)


def custom_run(**changes):
    position, region = finder.build_custom_position({**ROLE, **changes})
    return finder.find_candidates(finder.CUSTOM_KEY, region=region, today=TODAY, custom_position=position)


def stream(client, **changes):
    return client.post("/api/find-experts", json={"position": "custom", "custom": {**ROLE, **changes}})


def test_position_has_the_same_shape_as_a_tender_position():
    position, region = finder.build_custom_position(ROLE)

    assert region == "India"
    assert position["similar_title_keywords"] == ["data engineer", "analytics engineer"]
    assert position["sector_keywords"] == ["fintech", "payments", "financial services"]
    assert (position["min_total_years"], position["min_similar_years"]) == (6, 3)
    assert {"name", "min_total_years", "min_similar_years", "similar_title_keywords", "sector_keywords"} == set(position)


def test_defaults():
    position, region = finder.build_custom_position({"titles": "welder"})

    assert region == "India" and position["sector_keywords"] == []
    assert (position["min_total_years"], position["min_similar_years"]) == (0, 0)


@pytest.mark.parametrize(
    "custom, message",
    [
        ({}, "role title"),
        ({"titles": " , ,"}, "role title"),
        ({"titles": "!!!"}, "readable"),
        ({"titles": 5}, "must be text"),
        ({"titles": ",".join(f"t{n}" for n in range(11))}, "at most 10"),
        ({"titles": "x" * 61}, "at most 60"),
        ({"titles": "a", "sectors": "ok, ???"}, "readable"),
        ({"titles": "a", "min_total_years": -1}, "0 to 50"),
        ({"titles": "a", "min_total_years": 51}, "0 to 50"),
        ({"titles": "a", "min_similar_years": "abc"}, "0 to 50"),
        ({"titles": "a", "min_similar_years": True}, "0 to 50"),
        ({"titles": "a", "min_total_years": float("nan")}, "0 to 50"),
        ({"titles": "a", "min_total_years": float("inf")}, "0 to 50"),
        ({"titles": "a", "region": "<script>"}, "place name"),
        ({"titles": "a", "region": 7}, "must be text"),
    ],
)
def test_bad_inputs_are_refused(custom, message):
    with pytest.raises(finder.FinderInputError, match=message):
        finder.build_custom_position(custom)


@pytest.mark.parametrize("years", [0, 50, "12.5", ""])
def test_years_at_the_limits_are_accepted(years):
    finder.build_custom_position({"titles": "a", "min_total_years": years, "min_similar_years": years})


def test_search_uses_titles_region_and_sector_words(world):
    custom_run(region="Singapore")

    body = world.calls[0][1]
    country, titles, sectors = body["filters"]["conditions"]
    assert country["value"] == "Singapore"
    assert {c["value"] for c in titles["conditions"]} == {"data engineer", "analytics engineer"}
    assert {c["field"].rsplit(".", 1)[1] for c in sectors["conditions"]} == {"title", "name", "company_industries"}
    assert {c["value"] for c in sectors["conditions"]} == {"fintech", "payments", "financial services"}
    assert all(c["type"] == "[.]" for c in sectors["conditions"])
    assert "experience.employment_details.past.company_industries" in body["fields"]


def test_search_has_no_contact_fields(world):
    custom_run()

    assert not any(word in field.lower() for field in world.calls[0][1]["fields"] for word in ("email", "phone", "contact"))


def test_no_sector_words_means_no_sector_condition(world):
    world.http = lambda _url, _body: (200, {"profiles": [NO_SECTOR]})

    run = custom_run(sectors="")

    assert len(world.calls[0][1]["filters"]["conditions"]) == 2
    assert "company_industries" not in json.dumps(world.calls[0][1])
    assert len(run.candidates) == 1 and run.candidates[0].sector_job is None


def test_years_sector_filter_and_order(world):
    run = custom_run()

    assert run.searched == 5
    assert [c.profile.name for c in run.candidates] == ["Asha Rao", "Ravi Iyer"]
    asha, ravi = run.candidates
    assert (asha.total_months, asha.similar_months) == (120, 72)
    assert (ravi.total_months, ravi.similar_months) == (96, 48)


def test_sector_evidence_from_employer_and_from_industry(world):
    asha, ravi = custom_run().candidates

    assert (asha.sector_job.employer, asha.sector_keyword) == ("Nova Fintech Pvt Ltd", "fintech")
    assert (ravi.sector_job.employer, ravi.sector_keyword) == ("Blue Bank", "financial services")


def test_sector_on_a_different_job_from_the_role_is_left_out(world):
    split = person(6, "Split Roles", past=[
        job("Operations Lead", "2010-01-01", "2015-12-31", "Nova Fintech"),
        job("Data Engineer", "2016-01-01", "2024-12-31", "Retail Co"),
    ])
    world.http = lambda _url, _body: (200, {"profiles": [split]})

    assert custom_run().candidates == []


def test_industry_on_a_different_job_from_the_role_is_left_out(world):
    split = person(7, "Split Industry", past=[
        industry_job("Operations Lead", "2010-01-01", "2015-12-31", "Blue Bank", ["Financial Services"]),
        industry_job("Data Engineer", "2016-01-01", "2024-12-31", "Retail Co", ["Retail"]),
    ])
    world.http = lambda _url, _body: (200, {"profiles": [split]})

    assert custom_run().candidates == []


def test_phrase_matches_across_extra_spaces():
    assert finder._sector_word("Global  Financial   Services Ltd", ["financial services"]) == "financial services"
    assert finder._sector_word("Financial Advisors", ["financial services"]) is None


def test_keyword_starting_with_a_symbol_matches():
    assert finder._sector_word("Senior .NET Developer", [".net"]) == ".net"


def test_tender_search_body_is_unchanged_by_custom_support():
    tender = {"similar_title_keywords": ["team leader"], "sector_keywords": ["road"]}

    body = finder.search_body(tender, "India", 5)

    assert len(body["filters"]["conditions"]) == 2 and body["fields"] == finder.SEARCH_FIELDS


def test_no_new_rules_in_the_checker():
    assert "sector" not in open(checker.__file__, encoding="utf-8").read().lower()


def test_api_streams_the_custom_result(world, client):
    events = read_events(stream(client))

    assert [s["stage"] for s in of_kind(events, "stage")] == ["looking_up", "checking", "writing", "done"]
    result = events[-1][1]
    assert result["ok"] is True and result["kind"] == "finder" and result["label"] == finder.LABEL
    assert result["position"]["key"] == "custom" and result["position"]["min_total_years"] == 6
    assert result["headline"] == "2 of 5 public records searched appear to meet the role's minimums and sector check"
    assert result["candidates"][0]["initials"] == "A.R."
    assert result["candidates"][1]["sector_evidence"]["keyword"] == "financial services"
    assert "linkedin.com" not in json.dumps(result)


@pytest.mark.parametrize("changes", [{"titles": ""}, {"min_total_years": 99}, {"region": "<b>"}])
def test_api_refuses_bad_inputs_without_a_paid_call(world, client, changes):
    response = stream(client, **changes)

    assert response.status_code == 422 and isinstance(response.json()["error"], str)
    assert world.calls == []


def test_api_refuses_custom_with_no_details(world, client):
    assert client.post("/api/find-experts", json={"position": "custom"}).status_code == 422
    assert world.calls == []


def test_api_refuses_cross_site_custom_search(world, client):
    response = client.post(
        "/api/find-experts",
        json={"position": "custom", "custom": ROLE},
        headers={"origin": "https://evil.example"},
    )

    assert response.status_code == 403 and world.calls == []


def test_page_has_the_custom_fields(client):
    page = client.get("/").text

    for marker in ("Custom role…", 'id="findTitles"', 'id="findSectors"', 'id="findMinTotal"', 'id="findMinSimilar"', 'id="findRegion"'):
        assert marker in page
