from app.checker import check_expert
from app.firm import check_firm, distinct_full_time_count, rule_duplicate_experts
from app.questions import build_questions
from app.report import ExpertEntry, headline
from tests.test_golden import GOLDEN, TENDER, TODAY, build_claims, build_lookup

URL = "https://www.linkedin.com/in/example-person"


def expert(expert_id, url=None, name="Person", employment="full_time"):
    return {"id": expert_id, "name": name, "profile_url": url, "employment_with_bidder": employment}


def test_same_url_is_flagged_and_counted_once():
    experts = [expert("X1", URL, "A"), expert("X2", URL + "/", "B"), expert("X3", None, "C")]

    findings = rule_duplicate_experts(experts)

    assert [f.code for f in findings] == ["DUPLICATE_EXPERT"]
    assert findings[0].evidence == ["bid.experts[X1]", "bid.experts[X2]"]
    assert distinct_full_time_count(experts) == 2
    text = build_questions(findings)[0][0].text
    assert text == (
        "X1 and X2 point to the same person; please confirm they are different experts "
        "or that one person is proposed for one position only."
    )


def test_same_name_without_url_is_flagged_as_possible():
    experts = [expert("X1", None, "Expert R."), expert("X2", None, "expert  r (test copy)")]

    findings = rule_duplicate_experts(experts)

    assert len(findings) == 1
    assert "may be the same person" in findings[0].message
    assert distinct_full_time_count(experts) == 1


def test_different_people_are_not_flagged():
    experts = [expert("X1", URL, "Same Name"), expert("X2", URL + "-2", "Same Name"), expert("X3", None, "Other")]

    assert rule_duplicate_experts(experts) == []
    assert distinct_full_time_count(experts) == 3


def test_only_full_time_people_are_counted():
    experts = [expert("X1", URL), expert("X2", None, "Other", employment="associate")]

    assert distinct_full_time_count(experts) == 1


def _entry(case_id):
    case = next(c for c in GOLDEN["cases"] if c["id"] == case_id)
    lookup, claims = build_lookup(case["lookup"]), build_claims(case)
    result = check_expert(
        lookup,
        claims,
        TENDER["positions"][case["position"]],
        case["bidder_name"],
        case["employment_with_bidder"],
        today=TODAY,
    )
    return ExpertEntry({"id": case_id}, claims, lookup, result, "test")


def test_headline_counts_questions_across_all_experts_and_the_bid():
    firm_result = check_firm([{"crustdata_company_id": 1, "name": "A", "employee_count_range": "11-50"}], 1)
    firm_result.bid_findings = rule_duplicate_experts([expert("X1", URL), expert("X2", URL)])

    # E12 has 1 question. E13 has 1 CV-only question plus a no-profile statement, which is not a question.
    assert headline([_entry("E12"), _entry("E13")], firm_result) == (
        "2 experts: 0 consistent, 1 need clarification, 1 cannot be checked; "
        "3 questions in total (including CV-only questions)"
    )
