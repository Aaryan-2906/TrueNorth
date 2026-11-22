import json
from pathlib import Path

import pytest

from app.checker import check_expert
from app.models import Claims, Lookup

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = json.loads((ROOT / "data" / "golden" / "expert_cases.json").read_text(encoding="utf-8"))
# A frozen copy of the positions these expected answers were written against. The real tender is data/tender.json.
TENDER = json.loads((ROOT / "data" / "golden" / "tender_golden.json").read_text(encoding="utf-8"))
TODAY = (2026, 11)


def build_lookup(raw: dict) -> Lookup:
    data = json.loads(json.dumps(raw))
    profile = data.get("profile")
    if profile is not None:
        profile.setdefault("duplicates_collapsed", [])
        for index, job in enumerate(profile["jobs"]):
            job.setdefault("source", f"profile.jobs[{index}]")
    return Lookup.model_validate(data)


def build_claims(case: dict) -> Claims:
    return Claims.model_validate({"expert_id": case["id"], "name": "(synthetic)", **case["claims"]})


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda case: case["id"])
def test_golden_case(case):
    expected = case["expected"]

    result = check_expert(
        build_lookup(case["lookup"]),
        build_claims(case),
        TENDER["positions"][case["position"]],
        case["bidder_name"],
        case["employment_with_bidder"],
        today=TODAY,
    )

    assert result.status == expected["status"]
    assert {finding.code for finding in result.findings} == set(expected["codes"])
    if "documented_total_months" in expected:
        assert result.documented_total_months == expected["documented_total_months"]
    if "documented_similar_months" in expected:
        assert result.documented_similar_months == expected["documented_similar_months"]
