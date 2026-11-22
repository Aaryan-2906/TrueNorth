"""The deterministic CV reader, and Claude as its fallback."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import cli, config, parse_cv, parse_tender, server
from app.models import Claims
from app.parse_rules import parse_cv_rules, read_extract
from tests.test_robustness import CV as UNKNOWN_LAYOUT_CV
from tests.test_robustness import World, url
from tests.test_server import of_kind, read_events

ROOT = Path(__file__).resolve().parent.parent
BID = ROOT / "data" / "bid"
TODAY = (2026, 11)
HEADER = """Proposed Position: Team Leader cum Senior Highway Engineer
Name of Staff: Expert T
Key Qualifications: Civil engineer with over 20 years of experience in highways.

Employment Record:
"""


def cv_text(case):
    return (BID / "cvs" / f"{case}.md").read_text(encoding="utf-8")


def written(case):
    return Claims.model_validate(json.loads((BID / "claims" / f"{case}.json").read_text(encoding="utf-8")))


def same(parsed, expected):
    return parsed.model_dump(exclude={"read_by"}) == expected.model_dump(exclude={"read_by"})


def no_ai(_text):
    raise AssertionError("the AI reader must not be called")


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


@pytest.fixture
def client():
    return TestClient(server.app)


# The real files


@pytest.mark.parametrize("case", ["X3", "X4", "SAMPLE_real_format"])
def test_rules_give_exactly_the_hand_written_claims(case):
    expected = written(case)

    parsed = parse_cv_rules(cv_text(case), expected.expert_id, expected.proposed_position, today=TODAY)

    assert parsed is not None and parsed.read_by == "rules"
    assert same(parsed, expected)


@pytest.mark.parametrize("case", ["X3", "X4", "SAMPLE_real_format"])
def test_position_can_come_from_the_cv_heading(case):
    expected = written(case)
    assert parse_cv_rules(cv_text(case), expected.expert_id, today=TODAY).proposed_position == expected.proposed_position


@pytest.mark.parametrize("case", ["X3", "X4", "SAMPLE_real_format"])
def test_parse_cv_uses_rules_with_no_ai_call(world, case):
    world.llm = no_ai
    expected = written(case)

    parsed = parse_cv.parse_cv(cv_text(case), expected.expert_id, expected.proposed_position, fresh=True, today=TODAY)

    assert parsed.read_by == "rules" and same(parsed, expected)
    assert parse_cv.run_stats() == {"live": 0, "from_cache": 0, "rules": 1}


@pytest.mark.parametrize("case", ["SAMPLE_modern_resume", "X1", "X2"])
def test_other_layouts_are_left_to_claude(case):
    assert parse_cv_rules(cv_text(case), today=TODAY) is None


@pytest.mark.parametrize("case", ["SAMPLE_modern_resume", "X1", "X2"])
def test_fallback_claims_still_match(case):
    text = cv_text(case)
    if not config.ANTHROPIC_API_KEY and not parse_cv.cache_path(text).exists():
        pytest.skip("no API key and no cached reading")
    expected = written(case)

    parsed = parse_cv.parse_cv(text, expected.expert_id, expected.proposed_position, today=TODAY)

    assert parsed.read_by == "claude"
    # The modern resume prints the name in capitals and the reader copies text verbatim.
    assert parsed.name.casefold() == expected.name.casefold()
    assert same(parsed.model_copy(update={"name": expected.name}), expected)


def test_every_value_read_by_rules_is_written_in_the_cv():
    text = " ".join(cv_text("X3").split())
    extract = read_extract(cv_text("X3"), TODAY)
    for row in extract.rows:
        for value in (row.employer, row.title, row.start_raw, row.end_raw, row.duration_raw):
            assert value is None or " ".join(value.split()) in text


# Layouts


def test_numbered_rows_with_duration_lines():
    text = HEADER + (
        "1. 01/04/2022 to Till Date | Kestrel Infra Pvt. Ltd. | Team Leader\n"
        "Project: NH-27 (Km 45.000 to Km 112.500)\n"
        "Duration: 01/04/2020 to Till Date\n"
        "Activities performed: Led supervision from 2022 to date.\n"
        "2. 06/2011 to 03/2022 | Falcon Works | Site Engineer\n"
    )
    claims = parse_cv_rules(text, today=TODAY)
    assert [r.employer for r in claims.rows] == ["Kestrel Infra Pvt. Ltd.", "Falcon Works"]
    assert claims.rows[0].end == "present" and claims.rows[0].duration_start == (2020, 4)
    assert claims.rows[1].duration_start is None


def test_from_blocks_with_labels():
    text = HEADER + (
        "From: May 2019 to Till Date\nName of employer: Alpha Roads Ltd\nPosition held: Resident Engineer\n"
        "Duration: May 2018 to Till Date\n\n"
        "From: Jan 2015 to Apr 2019\nName of employer: Beta Infra\nPosition held: Site Engineer\n"
    )
    claims = parse_cv_rules(text, today=TODAY)
    assert [(r.employer, r.title) for r in claims.rows] == [("Alpha Roads Ltd", "Resident Engineer"), ("Beta Infra", "Site Engineer")]
    assert claims.rows[0].duration_start == (2018, 5) and claims.rows[1].start == (2015, 1)


def test_signature_date_is_read():
    text = HEADER + "01/2020 to 03/2022 | Acme | Engineer\nCertification: I certify.\nPlace: Pune   Date: 15/04/2025\n"
    assert parse_cv_rules(text, today=TODAY).cv_date == (2025, 4)


def test_blank_signature_date_is_undated():
    text = HEADER + "01/2020 to 03/2022 | Acme | Engineer\nPlace: ______ Date: ______\n"
    assert parse_cv_rules(text, today=TODAY).cv_date is None


def test_impossible_date_is_kept_as_a_date_problem():
    claims = parse_cv_rules(HEADER + "01/10/1995 to 09/12/2097 | Acme | Engineer\n", today=TODAY)
    assert claims.rows[0].date_problem == "after today" and claims.rows[0].end is None


# When the rules must step aside


@pytest.mark.parametrize(
    "record",
    [
        "",
        "Worked at several firms.\n",
        "01/2020 to 03/2022 | Acme\n",
        "01/2020 to 03/2022 |  | Engineer\n",
        "01/2020 to 03/2022 | Acme | \n",
        "01/2020 to 03/2022 | Acme | Engineer | Extra\n",
        "somewhere to nowhere | Acme | Engineer\n",
        "Mar 2013 to August | Contractor: Delta; Authority: NHAI; Consultant: Egret | Engineer\n",
        "01/2020 to 03/2022 | Acme | Engineer\nApr 2015 to Dec 2019 at Beta Infra as Site Engineer\n",
        "01/2020 to 03/2022 | Acme | Engineer\nFrom: Jan 2015 to Apr 2019\nBeta\nEngineer\n",
        "Duration: 01/2020 to 03/2022\n01/2020 to 03/2022 | Acme | Engineer\n",
        "01/2020 to 03/2022 | Acme | Engineer\nDuration: 01/2019 to 03/2022\nDuration: 01/2018 to 03/2022\n",
        "From: May 2019 to Till Date\nName of employer: Alpha\n",
        "From: May 2019 to Till Date\nAlpha Roads\n",
    ],
    ids=[
        "no_rows", "no_dates", "missing_title_cell", "blank_employer", "blank_title", "extra_cell", "not_dates",
        "no_clear_employer", "unparsed_job_line", "mixed_layouts", "duration_before_row", "two_durations",
        "employer_label_only", "block_missing_title",
    ],
)
def test_unclear_employment_record_returns_none(record):
    assert parse_cv_rules(HEADER + record, today=TODAY) is None


GOOD_ROW = "01/2020 to 03/2022 | Acme | Engineer\n"


@pytest.mark.parametrize(
    "text",
    [
        "Name of Staff: T\nover 20 years of experience\nEmployment Record:\n" + GOOD_ROW,
        "Proposed Position: Team Leader\nover 20 years of experience\nEmployment Record:\n" + GOOD_ROW,
        "Proposed Position: Team Leader\nName of Staff: T\nEmployment Record:\n" + GOOD_ROW,
        "Proposed Position: Team Leader\nName of Staff: T\nHas 20 years of experience.\nEmployment Record:\n" + GOOD_ROW,
        "Proposed Position: Team Leader\nName of Staff: T\nover 20 years of experience, about 6 years of site experience\nEmployment Record:\n" + GOOD_ROW,
        "Proposed Position: Team Leader\nName of Staff: T\nover 20 years of experience, including 6 years as Resident Engineer\nEmployment Record:\n" + GOOD_ROW,
        "Proposed Position: Team Leader\nName of Staff: T\nover 20 years of experience\nExperience in similar capacity: 7 years\nEmployment Record:\n" + GOOD_ROW,
        HEADER + GOOD_ROW + "Employment Record (continued):\n" + GOOD_ROW,
        HEADER + GOOD_ROW + "Place: Pune Date: 15 April 2025\n",
        HEADER + GOOD_ROW + "Place: Pune Date: 15/04/2025\nDate: 16/05/2025\n",
    ],
    ids=[
        "no_position", "no_name", "no_claim", "unqualified_claim", "two_claims", "role_years_beside_total",
        "similar_figure", "two_records", "signature_date_other_format", "two_signature_dates",
    ],
)
def test_unclear_header_returns_none(text):
    assert parse_cv_rules(text, today=TODAY) is None


# LLM_ENABLED


@pytest.mark.parametrize(
    "value, expected",
    [("", True), ("true", True), ("1", True), ("false", False), ("0", False), ("no", False), ("OFF", False)],
)
def test_llm_enabled_flag(monkeypatch, value, expected):
    monkeypatch.setenv("LLM_ENABLED", value)
    assert config.read_llm_enabled() is expected


def test_audit_works_without_the_ai_reader(world, monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    world.llm = no_ai
    world.add_expert("X3", cv=cv_text("X3"), position="team_leader")
    world.add_expert("X4", cv=cv_text("X4"), position="resident_engineer")

    assert world.audit() == 0

    for expert_id in ("X3", "X4"):
        assert "**Read by:** rules" in world.section(expert_id)
    assert "CV_DATE_INVALID" in world.report() and "09/12/2097" in world.section("X3")
    assert "CV readings this run: 0 by the LLM, 0 from cache, 2 by the rules reader." in world.report()


def test_unknown_layout_without_ai_is_reported_and_run_continues(world, monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    world.llm = no_ai
    world.add_expert("E1", url(1), cv=UNKNOWN_LAYOUT_CV)
    world.add_expert("X3", cv=cv_text("X3"), position="team_leader")

    assert world.audit() == 0

    section = world.section("E1")
    assert "CV layout not recognised without the AI reader" in section and "**Status:** CANNOT_CHECK" in section
    assert world.urls_called() == []
    assert "**Read by:** rules" in world.section("X3")


def test_cached_ai_reading_is_not_used_when_the_ai_is_off(world, monkeypatch):
    parse_cv.parse_cv(UNKNOWN_LAYOUT_CV, "E1", None, today=TODAY)
    monkeypatch.setattr(config, "LLM_ENABLED", False)

    with pytest.raises(parse_cv.CvParseError, match="not recognised"):
        parse_cv.parse_cv(UNKNOWN_LAYOUT_CV, "E1", None, today=TODAY)


def test_cli_without_ai_gives_a_clear_message(world, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    path = tmp_path / "cv.txt"
    path.write_text(UNKNOWN_LAYOUT_CV, encoding="utf-8")

    assert cli.main(["check-cv", str(path)]) == 1

    assert "CV layout not recognised without the AI reader" in capsys.readouterr().err


def test_tender_reading_without_ai_is_a_clear_error(world, monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    with pytest.raises(parse_tender.TenderParseError, match="LLM_ENABLED"):
        parse_tender.parse_tender("Team Leader needs 12 years.")


# The web app


def test_rules_reading_shows_no_claude_call(world, client):
    world.llm = no_ai

    events = read_events(client.post("/api/check-cv", json={"cv_text": cv_text("X3"), "position": "team_leader", "fresh": True}))

    assert events[-1][1]["experts"][0]["read_by"] == "rules"
    assert not [c for c in of_kind(events, "call") if c["service"].startswith("Claude")]
    assert [s["stage"] for s in of_kind(events, "stage")][:2] == ["reading_cv", "reading_dates"]


def test_claude_reading_is_labelled(world, client):
    events = read_events(client.post("/api/check-cv", json={"cv_text": UNKNOWN_LAYOUT_CV}))

    assert events[-1][1]["experts"][0]["read_by"] == "Claude"
    assert [c["service"] for c in of_kind(events, "call")] == ["Claude CV reading"]


def test_page_shows_the_reader(client):
    assert "Read by: ${esc(expert.read_by)}" in client.get("/").text
