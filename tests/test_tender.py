"""The corrected tender, postgraduate levels, tender reading and approval. Fake LLM except the marked live test."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import checker, cli, config, parse_tender, report, server
from app.models import Claims, Lookup
from tests.test_golden import GOLDEN, build_claims, build_lookup
from tests.test_robustness import World, url
from tests.test_server import of_kind, read_events

ROOT = Path(__file__).resolve().parent.parent
TENDER = json.loads((ROOT / "data" / "tender.json").read_text(encoding="utf-8"))
EXCERPT = (ROOT / "data" / "tender_source_excerpt.txt").read_text(encoding="utf-8")
SIX = {"team_leader", "resident_engineer", "bridge_engineer", "pavement_specialist", "quality_material_expert", "road_safety_expert"}

TENDER_TEXT = """Team Leader cum Senior Highway Engineer
Total Professional Experience of 12 years in handling Highway Projects.
At least 5 years experience as Team Leader or similar capacity.
Preferential Qualifications: Post Graduate Degree in Highway Engineering.
II) Post Graduation in Management/ 03
(1) Similar Capacity includes the following positions i) On behalf of Consultant: Team Leader/Resident Engineer (Construction Supervision/IE/AE/DPR). ii) In Government Organizations: Superintending Engineer (or equivalent) and above
"""


def extract(**changes):
    position = {
        "name": "Team Leader cum Senior Highway Engineer",
        "min_total_years": 12,
        "min_total_years_source": "Total Professional Experience of 12 years in handling Highway Projects.",
        "min_similar_years": 5,
        "min_similar_years_source": "At least 5 years experience as Team Leader or similar capacity.",
        "similar_capacity_wording": "Similar Capacity includes the following positions i) On behalf of Consultant: Team Leader/Resident Engineer (Construction Supervision/IE/AE/DPR). ii) In Government Organizations: Superintending Engineer (or equivalent) and above",
        "postgraduate": "preferential",
        "postgraduate_source": "Post Graduate Degree in Highway Engineering.",
        "postgraduate_marks": 3,
        "postgraduate_marks_source": "II) Post Graduation in Management/ 03",
    }
    position.update(changes)
    return json.dumps({"positions": [position]})


@pytest.fixture
def world(tmp_path, monkeypatch):
    built = World(tmp_path, monkeypatch)
    monkeypatch.setattr(parse_tender, "CACHE_DIR", tmp_path / "cache" / "llm")
    monkeypatch.setattr(parse_tender, "APPROVED_PATH", tmp_path / "out" / "tender_approved.json")
    return built


def fake_model(monkeypatch, answer=None):
    calls = []

    def ask(text, correction):
        calls.append(correction)
        return answer if answer is not None else extract()

    monkeypatch.setattr(parse_tender, "_ask_model", ask)
    return calls


def e10_with(level, marks=4):
    case = next(c for c in GOLDEN["cases"] if c["id"] == "E10")
    position = {"min_total_years": 12, "min_similar_years": 5, "similar_title_keywords": ["team leader"], "postgraduate": level, "postgraduate_marks": marks}
    return checker.check_expert(
        build_lookup(case["lookup"]), build_claims(case), position, case["bidder_name"], case["employment_with_bidder"], today=(2026, 11)
    )


# The corrected tender.json


def test_tender_has_all_six_positions():
    assert set(TENDER["positions"]) == SIX


def test_every_source_sentence_is_verbatim_in_the_excerpt():
    excerpt = " ".join(EXCERPT.split())
    for position in TENDER["positions"].values():
        for source in position["_source_sentences"].values():
            assert " ".join(source["text"].split()) in excerpt, source["text"]
            assert f"[PDF page {source['page']}]" in excerpt


def test_every_number_is_in_its_source_sentence():
    for position in TENDER["positions"].values():
        sources = position["_source_sentences"]
        for field in ("min_total_years", "min_similar_years", "postgraduate_marks"):
            assert parse_tender._number_in(position[field], sources[field]["text"]), field


def test_postgraduate_is_preferential_for_all_positions():
    assert {p["postgraduate"] for p in TENDER["positions"].values()} == {"preferential"}
    assert all("requires_postgraduate" not in p for p in TENDER["positions"].values())


def test_keywords_follow_the_pdf_wording():
    for key, position in TENDER["positions"].items():
        derived = parse_tender.derive_keywords(position["name"], position["similar_capacity_wording"])
        assert set(derived) == set(position["similar_title_keywords"]), key


def test_quality_quantity_typo_is_recorded():
    assert "Senior Quantity cum Material Expert" in TENDER["_note"]


def test_golden_positions_are_frozen_separately():
    frozen = json.loads((ROOT / "data" / "golden" / "tender_golden.json").read_text(encoding="utf-8"))["positions"]
    assert frozen["team_leader"]["similar_title_keywords"] == ["team leader", "project director"]
    assert {p["postgraduate"] for p in frozen.values()} == {"required"}


# Postgraduate levels


def test_required_still_raises_a_question():
    result = e10_with("required")
    assert {f.code for f in result.findings} == {"EDUCATION_NOT_SHOWN"} and result.status == "QUESTIONS"


def test_preferential_is_an_info_note_and_never_a_question():
    result = e10_with("preferential", 3)

    assert result.status == "CONSISTENT"
    finding = result.findings[0]
    assert (finding.code, finding.severity) == ("POSTGRADUATE_PREFERENTIAL", "info")
    assert finding.message == (
        "Postgraduate degree is a preferential qualification worth 3 marks; none shown on the profile. "
        "This affects scoring, not eligibility."
    )


def test_none_level_raises_nothing():
    assert e10_with("none").findings == []


def test_equivalent_grades_note_appears_only_where_the_pdf_says_so():
    claims = Claims(expert_id="A", name="A", claimed_total_years=1, claimed_similar_years=None, rows=[])
    result = checker.check_expert(Lookup(status="not_found"), claims, None, None, None, today=(2026, 11))
    with_note = report.ExpertEntry({"id": "A"}, claims, Lookup(status="not_found"), result, "x", TENDER["positions"]["team_leader"])
    without = report.ExpertEntry({"id": "A"}, claims, Lookup(status="not_found"), result, "x", TENDER["positions"]["bridge_engineer"])

    assert "Equivalent grades are for the committee to judge." in report.expert_view(with_note)["notes"]
    assert report.expert_view(without)["notes"] == []


def test_tender_with_no_stated_minimum_does_not_crash():
    case = next(c for c in GOLDEN["cases"] if c["id"] == "E01")
    position = {"min_total_years": None, "min_similar_years": None, "similar_title_keywords": ["team leader"], "postgraduate": "none"}
    result = checker.check_expert(build_lookup(case["lookup"]), build_claims(case), position, case["bidder_name"], "full_time", today=(2026, 11))
    assert result.status == "CONSISTENT"


# parse_tender


def test_parse_builds_the_tender_shape_with_proposed_keywords(world, monkeypatch):
    fake_model(monkeypatch)

    position = parse_tender.parse_tender(TENDER_TEXT)["team_leader"]

    assert (position["min_total_years"], position["min_similar_years"], position["postgraduate_marks"]) == (12, 5, 3)
    assert position["postgraduate"] == "preferential" and position["equivalent_grades"] is True
    assert position["similar_title_keywords_status"] == "proposed"
    assert set(position["similar_title_keywords"]) == {"team leader", "resident engineer", "superintending engineer"}
    assert position["_source_sentences"]["min_total_years"]["text"].startswith("Total Professional Experience of 12")


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"min_total_years": 15}, "not in its source"),
        ({"min_total_years_source": "Total experience of 12 years is mandatory."}, "not written in the tender"),
        ({"min_total_years_source": None}, "no source sentence"),
        ({"similar_capacity_wording": "Anyone senior counts"}, "not written in the tender"),
        ({"postgraduate_source": "A masters degree is essential."}, "not written in the tender"),
        ({"name": "Invented Director"}, "name is not written"),
    ],
)
def test_validation_rejects_anything_not_in_the_text(changes, message):
    with pytest.raises(ValueError, match=message):
        parse_tender.validate_extract(extract(**changes), TENDER_TEXT)


def test_number_match_is_a_whole_number():
    assert parse_tender._number_in(3, "Management/ 03") is True
    assert parse_tender._number_in(2, "12 years") is False
    assert parse_tender._number_in(5, "5.5 years") is False


def test_null_number_needs_no_source():
    answer = extract(min_similar_years=None, min_similar_years_source=None)
    assert parse_tender.validate_extract(answer, TENDER_TEXT).positions[0].min_similar_years is None


def test_invalid_answer_is_retried_once_then_stops(world, monkeypatch):
    calls = fake_model(monkeypatch, extract(min_total_years=15))

    with pytest.raises(parse_tender.TenderParseError, match="validation twice"):
        parse_tender.parse_tender(TENDER_TEXT)

    assert len(calls) == 2 and calls[0] is None and "not in its source" in calls[1]


def test_second_read_comes_from_the_cache(world, monkeypatch):
    calls = fake_model(monkeypatch)
    parse_tender.parse_tender(TENDER_TEXT)
    parse_tender.parse_tender(TENDER_TEXT)
    assert len(calls) == 1


def test_empty_tender_is_a_clear_error(world):
    with pytest.raises(parse_tender.TenderParseError, match="empty"):
        parse_tender.parse_tender("  ")


@pytest.mark.parametrize("key", ["team_leader", "resident_engineer"])
def test_live_parse_of_the_excerpt_matches_tender_json(key):
    if not config.ANTHROPIC_API_KEY and not parse_tender.cache_path(EXCERPT).exists():
        pytest.skip("no API key and no cached reading")

    parsed = parse_tender.parse_tender(EXCERPT)[key]
    expected = TENDER["positions"][key]

    assert parsed["name"].casefold() == expected["name"].casefold()
    for field in ("min_total_years", "min_similar_years", "postgraduate", "postgraduate_marks", "equivalent_grades"):
        assert parsed[field] == expected[field], field
    assert set(parsed["similar_title_keywords"]) == set(expected["similar_title_keywords"])


# Approval from the command line


def write_tender(tmp_path):
    path = tmp_path / "tender.txt"
    path.write_text(TENDER_TEXT, encoding="utf-8")
    return path


@pytest.mark.parametrize("answer", ["n", "", "yes please"])
def test_read_tender_writes_nothing_without_y(world, monkeypatch, tmp_path, capsys, answer):
    fake_model(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)

    assert cli.main(["read-tender", str(write_tender(tmp_path))]) == 0

    assert not parse_tender.APPROVED_PATH.exists()
    assert "Nothing was written" in capsys.readouterr().out


def test_read_tender_without_an_answer_writes_nothing(world, monkeypatch, tmp_path):
    fake_model(monkeypatch)

    def closed(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", closed)
    assert cli.main(["read-tender", str(write_tender(tmp_path))]) == 0
    assert not parse_tender.APPROVED_PATH.exists()


@pytest.mark.parametrize("answer", ["y", "Y", " y ", "﻿y"])
def test_read_tender_shows_sources_and_writes_on_y(world, monkeypatch, tmp_path, capsys, answer):
    fake_model(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)

    assert cli.main(["read-tender", str(write_tender(tmp_path))]) == 0

    output = capsys.readouterr().out
    assert "source: Total Professional Experience of 12 years" in output and "(proposed)" in output
    approved = json.loads(parse_tender.APPROVED_PATH.read_text(encoding="utf-8"))
    assert approved["_approved"] is True and "team_leader" in approved["positions"]


def test_audit_refuses_a_tender_that_was_not_approved(world, tmp_path, capsys):
    world.add_expert("E1")
    path = tmp_path / "not_approved.json"
    path.write_text(json.dumps({"positions": TENDER["positions"]}), encoding="utf-8")

    assert world.audit("--tender", str(path)) == 2

    assert "not an approved tender" in capsys.readouterr().err
    assert world.calls == []


def test_audit_uses_the_approved_tender(world, monkeypatch, tmp_path):
    fake_model(monkeypatch)
    positions = parse_tender.parse_tender(TENDER_TEXT)
    positions["team_leader"]["min_total_years"] = 40
    path = parse_tender.approve(positions, "tender.txt")
    world.add_expert("E1", url(1), position="team_leader")

    assert world.audit("--tender", str(path)) == 0

    assert "YEARS_SHORTFALL" in world.report() and "approved tender (tender.txt)" in world.report()


# Approval from the web UI


@pytest.fixture
def client():
    return TestClient(server.app)


def test_ui_read_proposes_and_writes_nothing(world, monkeypatch, client):
    fake_model(monkeypatch)

    events = read_events(client.post("/api/read-tender", json={"text": TENDER_TEXT}))

    assert [s["stage"] for s in of_kind(events, "stage")] == ["reading_tender", "writing", "done"]
    result = events[-1][1]
    assert result["kind"] == "tender" and "team_leader" in result["positions"]
    assert not parse_tender.APPROVED_PATH.exists()


def test_ui_approve_writes_the_servers_copy(world, monkeypatch, client):
    fake_model(monkeypatch)
    token = read_events(client.post("/api/read-tender", json={"text": TENDER_TEXT}))[-1][1]["token"]

    response = client.post("/api/tender/approve", json={"token": token, "positions": {"team_leader": {"min_total_years": 1}}})

    assert response.status_code == 200 and response.json()["approved"]["positions"] == 1
    approved = json.loads(parse_tender.APPROVED_PATH.read_text(encoding="utf-8"))
    assert approved["positions"]["team_leader"]["min_total_years"] == 12
    assert client.post("/api/tender/approve", json={"token": token}).status_code == 404


def test_ui_unknown_token_is_refused(world, client):
    assert client.post("/api/tender/approve", json={"token": "nope"}).status_code == 404
    assert not parse_tender.APPROVED_PATH.exists()


def test_ui_audit_with_no_approved_tender_is_a_clear_error(world, client):
    world.add_expert("E1")
    world.write_bid()

    events = read_events(client.post("/api/audit", json={"bid": str(world.bid_dir / "bid.json"), "use_approved_tender": True}))

    assert of_kind(events, "stage")[-1]["stage"] == "error"
    assert "tender_approved.json" in of_kind(events, "stage")[-1]["detail"]
    assert world.calls == []


def test_ui_tender_error_is_clean(world, monkeypatch, client):
    fake_model(monkeypatch, "not json")

    events = read_events(client.post("/api/read-tender", json={"text": TENDER_TEXT}))

    assert of_kind(events, "stage")[-1]["stage"] == "error" and events[-1][1]["ok"] is False
