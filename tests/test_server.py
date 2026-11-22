"""The web backend, with fake Crustdata and LLM replies: these tests spend no credits."""

import json
import re

import pytest
from fastapi.testclient import TestClient

from app import server
from tests.test_robustness import CV, RECIPE, World, url


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


@pytest.fixture
def client():
    return TestClient(server.app)


def read_events(response):
    events = []
    for block in response.text.strip().split("\n\n"):
        lines = block.split("\n")
        events.append((lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: "))))
    return events


def of_kind(events, kind):
    return [payload for name, payload in events if name == kind]


def test_check_cv_streams_stages_in_order_and_a_result(world, client):
    response = client.post("/api/check-cv", json={"cv_text": CV, "profile_url": url(1)})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = read_events(response)
    assert [s["stage"] for s in of_kind(events, "stage")] == [
        "reading_cv", "reading_dates", "looking_up", "checking", "writing", "done",
    ]
    assert events[-1][0] == "result"
    result = events[-1][1]
    assert result["ok"] is True and result["kind"] == "check_cv"
    assert result["experts"][0]["rows"][0]["employer"] == "Acme"
    assert result["experts"][0]["status"] == "CONSISTENT"


def test_check_cv_credits_come_from_the_ledger(world, client):
    events = read_events(client.post("/api/check-cv", json={"cv_text": CV, "profile_url": url(1)}))

    calls = {call["service"]: call for call in of_kind(events, "call")}
    enrich = calls["Crustdata /person/enrich"]
    ledger = [row for row in world.ledger_rows() if row["path"] == "/person/enrich"]
    assert len(ledger) == 1
    assert enrich["credits"] == float(ledger[0]["credits_used"])
    assert enrich["source"] == "live" and isinstance(enrich["ms"], int)
    assert calls["Claude CV reading"]["credits"] is None
    assert events[-1][1]["credits"] == float(ledger[0]["credits_used"])


def test_blank_ledger_credit_is_reported_as_null(world, client, monkeypatch):
    original = world.fake_request

    def no_credit_header(*args, **kwargs):
        response = original(*args, **kwargs)
        del response.headers["x-credits-used"]
        return response

    monkeypatch.setattr(server.crustdata.httpx, "request", no_credit_header)
    events = read_events(client.post("/api/check-cv", json={"cv_text": CV, "profile_url": url(1)}))

    enrich = next(c for c in of_kind(events, "call") if c["service"] == "Crustdata /person/enrich")
    assert enrich["credits"] is None


def test_second_run_reports_cache_with_fetch_time_and_no_credits(world, client):
    body = {"cv_text": CV, "profile_url": url(1)}
    client.post("/api/check-cv", json=body)
    world.calls.clear()

    events = read_events(client.post("/api/check-cv", json=body))

    assert world.calls == []
    calls = of_kind(events, "call")
    assert len(calls) == 2
    assert all(call["source"].startswith("cache (fetched 20") for call in calls)
    assert next(c for c in calls if c["service"].startswith("Crustdata"))["credits"] == 0
    assert events[-1][1]["credits"] == 0


def test_fresh_check_cv_calls_services_again(world, client):
    body = {"cv_text": CV, "profile_url": url(1)}
    client.post("/api/check-cv", json=body)
    world.calls.clear()

    events = read_events(client.post("/api/check-cv", json={**body, "fresh": True}))

    assert len(world.calls) == 1
    assert all(call["source"] == "live" for call in of_kind(events, "call"))


def test_audit_streams_each_expert_and_the_firm(world, client):
    world.add_expert("E1", url(1))
    world.add_expert("E2", url(1))
    world.add_expert("E3")
    world.write_bid()

    events = read_events(client.post("/api/audit", json={"bid": "bid.json", "fresh": False}))

    # The bid path above is outside data/bid in the real tree, so resolve the temp bid explicitly.
    assert of_kind(events, "stage")[-1]["stage"] == "error"
    bid_path = str(world.bid_dir / "bid.json")
    events = read_events(client.post("/api/audit", json={"bid": bid_path}))

    stages = of_kind(events, "stage")
    assert stages[-1]["stage"] == "done" and stages[-2]["stage"] == "writing"
    assert {"E1", "E2", "E3"} <= {s["expert"] for s in stages}
    result = events[-1][1]
    assert result["ok"] is True and len(result["experts"]) == 3
    services = [c["service"] for c in of_kind(events, "call")]
    assert services.count("Crustdata /person/enrich") == 1
    assert services.count("Crustdata /company/identify") == 1
    assert services.count("Claude CV reading") == 3
    assert result["firm"]["name"] == "Acme"
    assert result["letter"].startswith("To: Acme")


def test_report_html_is_served_after_an_audit(world, client):
    assert client.get("/api/report.html").status_code == 404
    world.add_expert("E1")
    world.write_bid()
    client.post("/api/audit", json={"bid": str(world.bid_dir / "bid.json")})

    response = client.get("/api/report.html")

    assert response.status_code == 200 and "Tender Bid Audit" in response.text


def test_recipe_gives_a_clean_error_event(world, client):
    events = read_events(client.post("/api/check-cv", json={"cv_text": RECIPE}))

    stage = of_kind(events, "stage")[-1]
    assert stage["stage"] == "error" and "does not look like a CV" in stage["detail"]
    assert events[-1][1]["ok"] is False


def test_unknown_position_gives_a_clean_error_event(world, client):
    events = read_events(client.post("/api/check-cv", json={"cv_text": CV, "position": "astronaut"}))

    assert "astronaut" in of_kind(events, "stage")[-1]["detail"]
    assert world.calls == []


def test_bid_outside_the_bid_folder_is_refused(world, client):
    events = read_events(client.post("/api/audit", json={"bid": "../secret.json"}))

    assert of_kind(events, "stage")[-1]["stage"] == "error"
    assert world.calls == []


def test_second_run_while_busy_gets_409(world, client):
    assert server._run_lock.acquire(blocking=False)
    try:
        response = client.post("/api/check-cv", json={"cv_text": CV})
    finally:
        server._run_lock.release()

    assert response.status_code == 409


def test_lock_is_released_after_an_error(world, client):
    client.post("/api/check-cv", json={"cv_text": RECIPE})

    assert client.post("/api/check-cv", json={"cv_text": CV}).status_code == 200


def test_page_is_served_and_avoids_banned_words_and_red(client):
    response = client.get("/")

    assert response.status_code == 200 and "TrueNorth" in response.text
    assert not re.search(r"verified|pass|fail|fraud|fake", response.text, re.IGNORECASE)
    assert not re.search(r"\b(?:text|bg|border)-(?:red|rose)-", response.text)


def test_page_escapes_server_text(client):
    page = client.get("/").text

    assert "const esc =" in page
    for field in ("row.employer", "row.title", "q.text", "expert.name", "item.message"):
        assert f"${{esc({field})}}" in page


def test_options_come_from_the_data_files(world, client):
    world.add_expert("E1")
    world.write_bid()

    data = client.get("/api/options").json()

    assert "team_leader" in {p["key"] for p in data["positions"]}
    assert len(data["bids"]) == 1
