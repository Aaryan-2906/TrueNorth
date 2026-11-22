import json
from pathlib import Path

import pytest

from app.dates import Problem, fill_row_dates, parse_cv_date
from app.models import Claims, CvRow, YearMonth

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = json.loads((ROOT / "data" / "golden" / "date_strings.json").read_text(encoding="utf-8"))
CLAIMS_DIR = ROOT / "data" / "bid" / "claims"
CLAIM_FILES = sorted(p for p in CLAIMS_DIR.glob("*.json") if p.stem != "TEMPLATE")
TODAY = (2026, 11)


def expected_value(text):
    if text.startswith("PROBLEM:"):
        return Problem(text.removeprefix("PROBLEM:"))
    if text == "present":
        return "present"
    return YearMonth.parse(text)


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda case: repr(case["raw"]))
def test_golden_date_string(case):
    assert parse_cv_date(case["raw"], TODAY) == expected_value(case["expected"])


def row(start_raw, end_raw):
    return CvRow(employer="A", title="T", start_raw=start_raw, end_raw=end_raw, start=None, end=None)


def test_fill_sets_dates():
    filled = fill_row_dates(row("Sep'14", "Till Date"), TODAY)
    assert (filled.start, filled.end, filled.date_problem) == ((2014, 9), "present", None)


def test_fill_flags_end_before_start():
    assert fill_row_dates(row("May 2020", "Apr 2020"), TODAY).date_problem == "end before start"


def test_fill_flags_date_after_cv_date():
    filled = fill_row_dates(row("May 2020", "Jan 2024"), TODAY, cv_date=(2023, 12))
    assert (filled.start, filled.end, filled.date_problem) == ((2020, 5), None, "after cv_date")


def test_fill_keeps_good_date_when_other_is_bad():
    filled = fill_row_dates(row("01/10/1995", "09/12/2097"), TODAY)
    assert (filled.start, filled.end, filled.date_problem) == ((1995, 10), None, "after today")


def test_fill_flags_month_without_year():
    filled = fill_row_dates(row("Sep 2021", "April"), TODAY)
    assert (filled.start, filled.end, filled.date_problem) == ((2021, 9), None, "no year")


@pytest.mark.parametrize("path", CLAIM_FILES, ids=lambda p: p.stem)
def test_fill_reproduces_hand_written_claims(path):
    claims = Claims.model_validate(json.loads(path.read_text(encoding="utf-8")))
    for written in claims.rows:
        filled = fill_row_dates(written, TODAY, claims.cv_date)
        assert (filled.start, filled.end, filled.date_problem) == (written.start, written.end, written.date_problem)
