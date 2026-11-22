import json
from pathlib import Path

import pytest

from app.firm import check_firm

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = json.loads((ROOT / "data" / "golden" / "firm_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda case: case["id"])
def test_firm_golden_case(case):
    result = check_firm(case["records"], case["full_time_count"])

    assert result.status == case["expected"]["status"]
    assert {finding.code for finding in result.findings} == set(case["expected"]["codes"])
