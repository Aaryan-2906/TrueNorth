import json
from pathlib import Path

import pytest

from app.models import Claims, Job
from app.normalise import (
    months,
    normalise_company,
    profile_from_enrich,
    same_employer,
    union_months,
)

ROOT = Path(__file__).resolve().parent.parent
CLAIMS_DIR = ROOT / "data" / "bid" / "claims"
ENRICH_CACHE = ROOT / "cache" / "person_enrich"
PROFILE_URL = "https://www.linkedin.com/in/danielahmadizadeh"
CLAIM_FILES = sorted(p for p in CLAIMS_DIR.glob("*.json") if p.stem != "TEMPLATE")


def test_legal_suffix_does_not_change_core():
    assert normalise_company("U Projects Pvt. Ltd.")[0] == normalise_company("U Projects")[0]


def test_former_name_is_same_employer():
    assert same_employer("W Infra", "W Infrastructure Private Limited (formerly W Infra)") is True


def test_bracketed_label_is_same_employer():
    assert same_employer("Crustdata (YC F24)", "Crustdata") is True


def test_different_firms_are_not_same_employer():
    assert same_employer("V Consult", "Bidder Consult Pvt Ltd") is False


def test_prefix_compares_whole_words():
    assert same_employer("Meta", "Metalworks") is False


def test_word_prefix_is_same_employer():
    assert (
        same_employer(
            "Alpha Consultants International",
            "Alpha Consultants International in association with Beta Civil Solutions",
        )
        is True
    )


def test_months_is_inclusive():
    assert months((2009, 7), (2013, 5)) == 47
    assert months((2022, 3), (2026, 11)) == 57


def test_union_counts_overlapping_months_once():
    jobs = [
        Job(employer="A", title="T", start=(2020, 1), end=(2020, 12), is_current=False, source="a"),
        Job(employer="B", title="T", start=(2020, 7), end=(2021, 6), is_current=False, source="b"),
    ]
    assert union_months(jobs, (2026, 11)) == 18


def test_profile_from_real_cached_response():
    if not ENRICH_CACHE.exists():
        pytest.skip("cache/ is not committed; run scripts/smoke.py first")
    item = None
    for file in ENRICH_CACHE.glob("*.json"):
        for candidate in json.loads(file.read_text(encoding="utf-8")):
            if candidate.get("matched_on") == PROFILE_URL:
                item = candidate
    if item is None:
        pytest.skip("real profile is not in cache/; run scripts/smoke.py first")

    lookup = profile_from_enrich(item)

    assert lookup.status == "found"
    profile = lookup.profile
    sources = [job.source for job in profile.jobs] + [d for _, d in profile.duplicates_collapsed]
    assert sum(source.startswith("profile.current[") for source in sources) == 6
    assert sum(source.startswith("profile.past[") for source in sources) == 12
    assert len(set(sources)) == 18


@pytest.mark.parametrize("path", CLAIM_FILES, ids=lambda p: p.stem)
def test_claims_file_loads(path):
    Claims.model_validate(json.loads(path.read_text(encoding="utf-8")))
