"""Custom requirements and approved-tender positions on Check one CV and Upload CVs. Mocked replies only."""

import json

import pytest
from fastapi.testclient import TestClient

from app import firm, parse_tender, requirements, server
from tests.test_audit_upload import FIRM_RECORD, cv
from tests.test_robustness import CV, World, url
from tests.test_server import read_events

CUSTOM = {
    "name": "Senior Data Engineer",
    "min_total_years": 10,
    "min_similar_years": 3,
    "similar_title_keywords": ["Data Engineer", " analytics engineer "],
    "postgraduate": "none",
}


def enrich_reply(title="Engineer", degree=None):
    schools = [{"school": "Sample University", "degree": degree, "field_of_study": "Civil"}] if degree else []
    return [{
        "matched_on": url(1),
        "match_status": "matched",
        "matches": [{
            "confidence_score": 0.9,
            "person_data": {
                "crustdata_person_id": 1,
                "basic_profile": {"name": "Expert"},
                "experience": {"employment_details": {
                    "current": [{"title": title, "name": "Acme", "start_date": "2020-01-01T00:00:00+00:00", "end_date": None}],
                    "past": [{"title": "Trainee", "name": "Beta", "start_date": "2018-01-01", "end_date": "2019-12-31"}],
                }},
                "education": {"schools": schools},
                "social_handles": {"professional_network_identifier": {"profile_url": url(1)}},
            },
        }],
    }]


@pytest.fixture
def world(tmp_path, monkeypatch):
    built = World(tmp_path, monkeypatch)
    built.http = lambda _url, _body: (200, enrich_reply())
    monkeypatch.setattr(parse_tender, "APPROVED_PATH", tmp_path / "out" / "tender_approved.json")
    monkeypatch.setattr(firm, "lookup_firm", lambda bid, fresh=None: ([FIRM_RECORD], None))
    return built


@pytest.fixture
def client():
    return TestClient(server.app)


def check(client, custom, position="custom"):
    response = client.post(
        "/api/check-cv",
        json={"cv_text": CV, "profile_url": url(1), "position": position, "custom_position": custom},
    )
    return read_events(response)[-1][1]


def codes(result):
    return {item["code"] for item in result["experts"][0]["why"]}


def approve(path_positions=None):
    positions = path_positions or {
        "site_lead": {
            "name": "Site Lead",
            "min_total_years": 20,
            "min_similar_years": None,
            "similar_title_keywords": ["site lead"],
            "postgraduate": "none",
        }
    }
    parse_tender.approve(positions, "sample tender")


def test_inline_position_fires_years_shortfall_and_similar_role(world, client):
    result = check(client, CUSTOM)

    assert {"YEARS_SHORTFALL", "SIMILAR_ROLE_UNPROVEN"} <= codes(result)
    assert result["experts"][0]["position"] == "Senior Data Engineer"
    assert result["experts"][0]["status"] == "QUESTIONS"


def test_inline_position_met_raises_neither(world, client):
    world.http = lambda _url, _body: (200, enrich_reply("Lead Data Engineer"))

    result = check(client, {**CUSTOM, "min_total_years": 5})

    assert not {"YEARS_SHORTFALL", "SIMILAR_ROLE_UNPROVEN"} & codes(result)


def test_only_the_total_can_fall_short(world, client):
    world.http = lambda _url, _body: (200, enrich_reply("Data Engineer"))

    found = codes(check(client, CUSTOM))

    assert "YEARS_SHORTFALL" in found and "SIMILAR_ROLE_UNPROVEN" not in found


def test_only_the_similar_role_can_fall_short(world, client):
    found = codes(check(client, {**CUSTOM, "min_total_years": 5}))

    assert "SIMILAR_ROLE_UNPROVEN" in found and "YEARS_SHORTFALL" not in found


@pytest.mark.parametrize(
    "level, code",
    [("required", "EDUCATION_NOT_SHOWN"), ("preferential", "POSTGRADUATE_PREFERENTIAL")],
)
def test_postgraduate_levels_use_the_existing_rules(world, client, level, code):
    assert code in codes(check(client, {**CUSTOM, "postgraduate": level}))


def test_postgraduate_none_raises_nothing(world, client):
    found = codes(check(client, CUSTOM))

    assert not {"EDUCATION_NOT_SHOWN", "POSTGRADUATE_PREFERENTIAL"} & found


def test_multipart_upload_carries_the_inline_position(world, client):
    response = client.post(
        "/api/check-cv",
        data={"profile_url": url(1), "position": "custom", "custom_position": json.dumps(CUSTOM)},
        files={"file": ("cv.txt", CV.encode(), "text/plain")},
    )

    assert "YEARS_SHORTFALL" in codes(read_events(response)[-1][1])


def test_position_has_the_tender_shape():
    position = requirements.validate_inline(CUSTOM)

    assert position["similar_title_keywords"] == ["data engineer", "analytics engineer"]
    assert {"min_total_years", "min_similar_years", "similar_title_keywords", "postgraduate"} <= set(position)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"name": ""}, "role name"),
        ({"name": "   "}, "role name"),
        ({"name": 7}, "role name"),
        ({"name": "x" * 81}, "at most 80"),
        ({"name": "???"}, "readable"),
        ({"min_total_years": -1}, "0 to 50"),
        ({"min_total_years": 51}, "0 to 50"),
        ({"min_similar_years": "abc"}, "0 to 50"),
        ({"min_similar_years": True}, "0 to 50"),
        ({"similar_title_keywords": [f"title {n}" for n in range(11)]}, "at most 10"),
        ({"similar_title_keywords": [5]}, "must be text"),
        ({"similar_title_keywords": []}, "similar-role titles"),
        ({"postgraduate": "maybe"}, "none, preferential or required"),
    ],
)
def test_invalid_custom_input_is_refused_with_a_clear_message(world, client, changes, message):
    response = client.post(
        "/api/check-cv",
        json={"cv_text": CV, "profile_url": url(1), "position": "custom", "custom_position": {**CUSTOM, **changes}},
    )

    assert response.status_code == 422 and message in response.json()["error"]
    assert world.calls == []


def test_custom_with_no_details_is_refused(world, client):
    response = client.post("/api/check-cv", json={"cv_text": CV, "position": "custom"})

    assert response.status_code == 422 and "missing" in response.json()["error"]


def test_bad_json_in_a_form_is_refused(world, client):
    response = client.post(
        "/api/check-cv",
        data={"position": "custom", "custom_position": "{not json"},
        files={"file": ("cv.txt", CV.encode(), "text/plain")},
    )

    assert response.status_code == 422 and "could not be read" in response.json()["error"]


def test_role_name_is_escaped_in_the_report(world, client):
    hostile = "Lead <img src=x onerror=alert(1)>"
    files = [("a.md", cv("Expert A", 12, "Alpha Roads").encode(), "text/markdown")]

    client.post(
        "/api/audit-upload",
        data={"firm_name": "Firm", "position": "custom", "custom_position": json.dumps({"name": hostile})},
        files=[("file", item) for item in files],
    )

    page = client.get("/api/report.html").text
    assert "<img" not in page and "&lt;img" in page


def test_options_list_approved_positions(world, client):
    assert client.get("/api/options").json()["approved_positions"] == []

    approve()

    assert client.get("/api/options").json()["approved_positions"] == [{"key": "approved:site_lead", "name": "Site Lead"}]


def test_approved_position_is_checked(world, client):
    approve()

    result = check(client, None, "approved:site_lead")

    assert "YEARS_SHORTFALL" in codes(result) and result["experts"][0]["position"] == "approved:site_lead"


def test_missing_approved_position_is_refused(world, client):
    response = client.post("/api/check-cv", json={"cv_text": CV, "position": "approved:site_lead"})

    assert response.status_code == 422 and "no longer available" in response.json()["error"]
    assert world.calls == []


def bid_upload(client, positions, customs):
    files = [
        (f"cv{n}.md", cv(f"Expert {n}", 12, f"Employer {n}").encode(), "text/markdown")
        for n in range(1, len(positions) + 1)
    ]
    return client.post(
        "/api/audit-upload",
        data={
            "firm_name": "Firm",
            "profile_url": [url(n) for n in range(1, len(positions) + 1)],
            "position": positions,
            "custom_position": customs,
        },
        files=[("file", item) for item in files],
    )


def test_each_upload_can_have_its_own_requirement(world, client):
    approve()
    second = {**CUSTOM, "min_total_years": 2, "min_similar_years": 0}

    result = read_events(bid_upload(
        client,
        ["custom", "custom", "approved:site_lead", "team_leader"],
        [json.dumps(CUSTOM), json.dumps(second), "", ""],
    ))[-1][1]

    assert result["ok"] is True
    assert [e["position"] for e in result["experts"]] == [
        "Senior Data Engineer", "Senior Data Engineer (2)", "approved:site_lead", "team_leader",
    ]
    first, other, approved, _ = result["experts"]
    assert "YEARS_SHORTFALL" in {w["code"] for w in first["why"]}
    assert "YEARS_SHORTFALL" not in {w["code"] for w in other["why"]}
    assert "YEARS_SHORTFALL" in {w["code"] for w in approved["why"]}
    assert "custom or approved requirements" in result["tender_id"]


def test_invalid_custom_upload_names_the_file(world, client):
    response = bid_upload(client, ["team_leader", "custom"], ["", json.dumps({**CUSTOM, "min_total_years": 99})])

    assert response.status_code == 422
    assert response.json()["error"] == "File 2: Minimum total years must be a number from 0 to 50."


def test_page_has_the_custom_requirement(client):
    page = client.get("/").text

    for marker in ("Custom requirement…", "'Approved tender'", 'id="cvCustom"', "custom_position", "Minimum years in similar roles"):
        assert marker in page
