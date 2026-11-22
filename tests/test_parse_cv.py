import json
from pathlib import Path

import pytest

from app import config, dates, parse_cv
from app.models import Claims

ROOT = Path(__file__).resolve().parent.parent
BID_DIR = ROOT / "data" / "bid"
CASES = ["SAMPLE_real_format", "SAMPLE_modern_resume", "X1", "X2", "X3", "X4"]
ROW_FIELDS = (
    "employer", "title", "start_raw", "end_raw", "start", "end",
    "date_problem", "duration_start", "duration_end",
)
TODAY = (2026, 11)


def row_diffs(parsed: Claims, written: Claims) -> list[str]:
    diffs = []
    for field in ("claimed_total_years", "claimed_similar_years", "cv_date"):
        if getattr(parsed, field) != getattr(written, field):
            diffs.append(f"{field}: parsed {getattr(parsed, field)!r}, hand-written {getattr(written, field)!r}")
    if len(parsed.rows) != len(written.rows):
        diffs.append(f"row count: parsed {len(parsed.rows)}, hand-written {len(written.rows)}")
    for number, (got, want) in enumerate(zip(parsed.rows, written.rows), 1):
        for field in ROW_FIELDS:
            if getattr(got, field) != getattr(want, field):
                diffs.append(f"row {number} {field}: parsed {getattr(got, field)!r}, hand-written {getattr(want, field)!r}")
    return diffs


@pytest.mark.parametrize("case", CASES)
def test_parser_matches_hand_written_claims(case):
    text = (BID_DIR / "cvs" / f"{case}.md").read_text(encoding="utf-8")
    written = Claims.model_validate(json.loads((BID_DIR / "claims" / f"{case}.json").read_text(encoding="utf-8")))
    if not config.ANTHROPIC_API_KEY and not parse_cv.cache_path(text).exists():
        pytest.skip("no API key and no cached reading")

    parsed = parse_cv.parse_cv(text, written.expert_id, written.proposed_position, today=TODAY)

    assert row_diffs(parsed, written) == []
    assert parsed.profile_url == written.profile_url


def extract_json(**row_changes):
    row = {"employer": "Acme", "title": "Engineer", "start_raw": "May 2020", "end_raw": "Present", "duration_raw": None}
    row.update(row_changes)
    return json.dumps(
        {"name": "A", "claimed_total_years": 5, "claimed_similar_years": None, "cv_date": None, "profile_url": None, "rows": [row]}
    )


CV = "Name: A. About 5 years.\nFrom May 2020 to Present: Acme, Engineer"


def test_validation_accepts_values_written_in_the_cv():
    assert parse_cv.validate_extract(extract_json(), CV).rows[0].employer == "Acme"


@pytest.mark.parametrize("field, value", [("employer", "Invented Ltd"), ("title", "CEO"), ("start_raw", "2020-05")])
def test_validation_rejects_values_not_in_the_cv(field, value):
    with pytest.raises(ValueError, match="not written in the CV"):
        parse_cv.validate_extract(extract_json(**{field: value}), CV)


def test_validation_rejects_wrong_shape():
    with pytest.raises(ValueError, match="schema"):
        parse_cv.validate_extract('{"name": "A"}', CV)


def test_no_stated_total_is_marked_not_stated():
    extract = parse_cv.CvExtract.model_validate(
        {**json.loads(extract_json()), "claimed_total_years": None}
    )
    claims = parse_cv._to_claims(extract, "T", None, TODAY)
    assert (claims.claimed_total_years, claims.claimed_total_stated) == (0, False)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("13/01/2022 to 03/11/2023", ((2022, 1), (2023, 11))),
        ("Duration: 06/2003 to 06/2004", ((2003, 6), (2004, 6))),
        ("Jan 2020 to date", ((2020, 1), "present")),
        ("Jan 2020 till now", ((2020, 1), "present")),
        ("Jan 2020 - April", ((2020, 1), None)),
        ("no dates here", (None, None)),
        (None, (None, None)),
    ],
)
def test_parse_duration(raw, expected):
    assert dates.parse_duration(raw, TODAY) == expected
