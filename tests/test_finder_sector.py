"""Find experts: the sector filter, with mocked search replies. No paid calls."""

import json

import pytest
from fastapi.testclient import TestClient

from app import cli, config, finder, server
from tests.test_finder import job, person
from tests.test_robustness import World
from tests.test_server import read_events

TODAY = (2026, 11)
KEYWORDS = ["highway", "road", "nh", "nhai", "bridge", "pavement", "civil", "infra", "construction", "expressway"]
POSITION = {
    "name": "Team Leader",
    "min_total_years": 12,
    "min_similar_years": 5,
    "similar_title_keywords": ["project manager"],
    "sector_keywords": KEYWORDS,
    "postgraduate": "preferential",
}


def candidate(number, similar_title="Project Manager", similar_employer="Acme Software", early_title="Developer", early_employer="Beta Systems"):
    return person(number, f"Person {number}", past=[
        job(early_title, "2000-01-01", "2012-12-31", early_employer),
        job(similar_title, "2013-01-01", "2021-12-31", similar_employer),
    ])


@pytest.fixture
def world(tmp_path, monkeypatch):
    built = World(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "_load_tender", lambda path=None: {"positions": {"team_leader": POSITION}})
    return built


def search(world, *people):
    world.http = lambda _url, _body: (200, {"profiles": list(people)})
    return finder.find_candidates("team_leader", today=TODAY).candidates


def test_every_tender_position_has_the_sector_keywords():
    positions = json.loads((config.ROOT_DIR / "data" / "tender.json").read_text(encoding="utf-8"))["positions"]

    assert len(positions) == 6
    assert all(position["sector_keywords"] == KEYWORDS for position in positions.values())


def test_no_sector_job_is_left_out(world):
    assert search(world, candidate(1)) == []


def test_sector_word_in_the_employer_keeps_the_candidate(world):
    kept = search(world, candidate(1, similar_employer="Delta Highways Pvt Ltd"))

    assert kept[0].sector_job.employer == "Delta Highways Pvt Ltd" and kept[0].sector_keyword == "highway"


def test_sector_word_in_the_title_keeps_the_candidate(world):
    kept = search(world, candidate(1, similar_title="Project Manager (Bridge Works)"))

    assert kept[0].sector_job.title == "Project Manager (Bridge Works)" and kept[0].sector_keyword == "bridge"


def test_a_sector_job_outside_the_similar_roles_counts(world):
    kept = search(world, candidate(1, early_title="Civil Engineer"))

    assert kept[0].sector_job.title == "Civil Engineer" and kept[0].sector_keyword == "civil"


def test_a_similar_role_is_preferred_as_evidence(world):
    kept = search(world, candidate(1, similar_employer="Omega Construction", early_title="Civil Engineer"))

    assert kept[0].sector_job.employer == "Omega Construction"


@pytest.mark.parametrize("employer", ["Manhattan Software", "Broadcom", "Broadridge", "Bharat Civilian", "NHS Trust", "Roadrunner"])
def test_keyword_matching_by_word(employer):
    found = finder._sector_word(employer, KEYWORDS)

    expected = {"Bharat Civilian": "civil", "Roadrunner": "road"}.get(employer)
    assert found == expected


@pytest.mark.parametrize(
    "text, keyword",
    [("NH-27 Project", "nh"), ("NHAI", "nhai"), ("Roadways Ltd", "road"), ("Sigma Infratech", "infra"), ("EXPRESSWAY WORKS", "expressway")],
)
def test_real_sector_wording_matches(text, keyword):
    assert finder._sector_word(text, KEYWORDS) == keyword


def test_sector_job_must_still_meet_the_year_minimums(world):
    short = person(1, "Short", past=[job("Project Manager", "2020-01-01", "2021-12-31", "Road Builders")])

    assert search(world, short) == []


def test_a_position_without_keywords_is_not_filtered(world, monkeypatch):
    plain = {key: value for key, value in POSITION.items() if key != "sector_keywords"}
    monkeypatch.setattr(cli, "_load_tender", lambda path=None: {"positions": {"team_leader": plain}})

    kept = search(world, candidate(1))

    assert len(kept) == 1 and kept[0].sector_job is None


def test_search_request_is_unchanged_so_the_cache_still_applies(world):
    search(world, candidate(1))

    assert "sector" not in json.dumps(world.calls[0][1]).lower()
    assert "highway" not in json.dumps(world.calls[0][1]).lower()


def test_api_shows_the_sector_evidence_line(world):
    world.http = lambda _url, _body: (200, {"profiles": [candidate(1, similar_employer="Road Builders Ltd"), candidate(2)]})

    result = read_events(TestClient(server.app).post("/api/find-experts", json={"position": "team_leader"}))[-1][1]

    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["sector_evidence"] == {
        "title": "Project Manager",
        "employer": "Road Builders Ltd",
        "period": "2013-01 to 2021-12",
        "keyword": "road",
    }
    assert "linkedin.com" not in json.dumps(result)


def test_page_renders_the_sector_evidence():
    assert "Sector evidence:" in TestClient(server.app).get("/").text
