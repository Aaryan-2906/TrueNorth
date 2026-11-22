import json
import re
from pathlib import Path

import pytest

from app.checker import check_expert
from app.firm import check_firm
from app.questions import DATE_PROBLEM_TEXT, TEMPLATES, build_questions
from tests.test_golden import GOLDEN, TENDER, TODAY, build_claims, build_lookup

ROOT = Path(__file__).resolve().parent.parent
FIRM_GOLDEN = json.loads((ROOT / "data" / "golden" / "firm_cases.json").read_text(encoding="utf-8"))
BANNED = re.compile(r"\b(?:fake|false|lied|fraud|verified)\b", re.IGNORECASE)
ALL_CODES = {
    "CV_DATE_INVALID", "CV_HEADER_BODY_MISMATCH", "CV_ROWS_OVERLAP", "CV_EMPLOYER_UNCLEAR",
    "CLAIM_EXCEEDS_CV_ROWS", "ROW_NOT_ON_PROFILE", "ROW_MISSING_DATES", "ROW_DATES_DIFFER",
    "ROW_TITLE_DIFFERS", "NO_PROFILE_FOUND", "AMBIGUOUS_IDENTITY", "PROFILE_THIN", "YEARS_SHORTFALL",
    "CLAIM_EXCEEDS_RECORD", "SIMILAR_ROLE_UNPROVEN", "CURRENT_ROLE_ELSEWHERE",
    "OVERLAPPING_CURRENT_ROLES", "EXPERT_NOT_AT_FIRM", "EDUCATION_NOT_SHOWN", "DUPLICATE_ENTRY",
    "IMPLAUSIBLE_PROFILE", "FIRM_NOT_FOUND", "FIRM_MULTIPLE_RECORDS", "FIRM_HEADCOUNT_UNKNOWN",
    "FIRM_HEADCOUNT_LOW",
}


def expert_findings(case):
    return check_expert(
        build_lookup(case["lookup"]),
        build_claims(case),
        TENDER["positions"][case["position"]],
        case["bidder_name"],
        case["employment_with_bidder"],
        today=TODAY,
    ).findings


def test_every_code_has_a_template():
    assert set(TEMPLATES) == ALL_CODES


def test_templates_avoid_banned_words():
    for text in [*TEMPLATES.values(), *DATE_PROBLEM_TEXT.values()]:
        assert BANNED.search(text) is None, text


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda case: case["id"])
def test_expert_questions_render_with_evidence(case):
    questions, notes = build_questions(expert_findings(case))
    for item in questions + notes:
        assert item.evidence
        assert BANNED.search(item.text) is None


@pytest.mark.parametrize("case", FIRM_GOLDEN["cases"], ids=lambda case: case["id"])
def test_firm_questions_render_with_evidence(case):
    result = check_firm(case["records"], case["full_time_count"])
    questions, notes = build_questions(result.findings)
    for item in questions + notes:
        assert item.evidence
        assert BANNED.search(item.text) is None


def test_current_role_questions_merge_into_one():
    case = next(case for case in GOLDEN["cases"] if case["id"] == "E04")
    questions, _ = build_questions(expert_findings(case))
    merged = [q for q in questions if "CURRENT_ROLE_ELSEWHERE" in q.codes]
    assert len(questions) == 1
    assert merged[0].codes == ["CURRENT_ROLE_ELSEWHERE", "OVERLAPPING_CURRENT_ROLES"]
