"""Firm check (SPEC section 7): does the bidder have a record, and is its headcount plausible?"""

import math
import re
from typing import Any

from app import crustdata
from app.models import Finding, FirmResult

_OPEN_RANGE = re.compile(r"(\d[\d,]*)\s*\+")
_CLOSED_RANGE = re.compile(r"(\d[\d,]*)\s*[-–]\s*(\d[\d,]*)")
_LEGAL_SUFFIX = re.compile(r"[\s,]*\b(?:pvt\.?\s*ltd\.?|private\s+limited|ltd\.?|llp)\s*$", re.IGNORECASE)


def _number(text: str) -> int:
    return int(text.replace(",", ""))


def parse_range(value: Any) -> tuple[int, float] | None:
    """Parse "51-200", "10001+" or "myself only" to (low, high). Anything else is None."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text == "myself only":
        return (1, 1)
    match = _OPEN_RANGE.fullmatch(text)
    if match:
        return (_number(match.group(1)), math.inf)
    match = _CLOSED_RANGE.fullmatch(text)
    if match:
        low, high = _number(match.group(1)), _number(match.group(2))
        return (low, high) if low <= high else None
    return None


def record_range(record: dict[str, Any]) -> tuple[int, float] | None:
    """Use headcount.total when present, else the employee count range."""
    headcount = record.get("headcount")
    total = headcount.get("total") if isinstance(headcount, dict) else None
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        return (total, total)
    return parse_range(record.get("employee_count_range"))


def describe_range(record: dict[str, Any]) -> str:
    headcount = record.get("headcount")
    if isinstance(headcount, dict) and headcount.get("total") is not None:
        return f"headcount {headcount['total']}"
    return record.get("employee_count_range") or "no range"


def format_range(value: tuple[int, float]) -> str:
    low, high = value
    if math.isinf(high):
        return f"{low}+"
    return str(low) if low == high else f"{low}-{int(high)}"


def _unique_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One company id stays one record."""
    seen: set[Any] = set()
    unique = []
    for record in records:
        company_id = record.get("crustdata_company_id")
        if company_id is not None and company_id in seen:
            continue
        seen.add(company_id)
        unique.append(record)
    return unique


def _finding(code: str, severity: str, message: str, evidence: list[str], **params: Any) -> Finding:
    return Finding(code=code, severity=severity, message=message, evidence=evidence, params=params)


def rule_firm_not_found() -> Finding:
    message = "no company record found in Crustdata; this is not evidence against the firm"
    return _finding("FIRM_NOT_FOUND", "cannot_check", message, ["crustdata.lookup"])


def rule_firm_multiple_records(records: list[dict[str, Any]]) -> list[Finding]:
    if len(records) <= 1:
        return []
    listed = "; ".join(
        f"{record.get('name')} (id {record.get('crustdata_company_id')}, {describe_range(record)})"
        for record in records
    )
    message = f"possibly the same firm under several records: {listed}. The largest range was used."
    evidence = [f"firm.records[{index}]" for index in range(len(records))]
    return [_finding("FIRM_MULTIPLE_RECORDS", "info", message, evidence, count=len(records), records=listed)]


def rule_firm_headcount_unknown(records: list[dict[str, Any]], ranges: list[Any]) -> list[Finding]:
    if any(value is not None for value in ranges):
        return []
    message = "no headcount or employee range is recorded for this firm"
    evidence = [f"firm.records[{index}]" for index in range(len(records))]
    return [_finding("FIRM_HEADCOUNT_UNKNOWN", "info", message, evidence)]


def rule_firm_headcount_low(
    ranges: list[Any], best: tuple[int, float] | None, full_time_count: int
) -> list[Finding]:
    if best is None or best[1] >= full_time_count:
        return []
    shown = format_range(best)
    message = f"company record shows {shown} staff; bid lists {full_time_count} full-time key experts"
    index = ranges.index(best)
    evidence = [f"firm.records[{index}]", "bid.employment_with_bidder"]
    return [_finding("FIRM_HEADCOUNT_LOW", "question", message, evidence, range=shown, count=full_time_count)]


def _status(findings: list[Finding]) -> str:
    if any(finding.severity == "cannot_check" for finding in findings):
        return "CANNOT_CHECK"
    if any(finding.severity == "question" for finding in findings):
        return "QUESTIONS"
    return "CONSISTENT"


def check_firm(records: list[dict[str, Any]], full_time_count: int) -> FirmResult:
    unique = _unique_records(records)
    if not unique:
        findings = [rule_firm_not_found()]
        return FirmResult(status=_status(findings), records=[], headcount_range=None, findings=findings)

    ranges = [record_range(record) for record in unique]
    known = [value for value in ranges if value is not None]
    best = max(known, key=lambda value: (value[1], value[0])) if known else None
    findings = (
        rule_firm_multiple_records(unique)
        + rule_firm_headcount_unknown(unique, ranges)
        + rule_firm_headcount_low(ranges, best, full_time_count)
    )
    return FirmResult(status=_status(findings), records=unique, headcount_range=best, findings=findings)


def _records_from_reply(reply: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    for item in reply:
        for match in item.get("matches") or []:
            company = match.get("company_data") or {}
            info = company.get("basic_info") or {}
            records.append(
                {
                    "crustdata_company_id": company.get("crustdata_company_id"),
                    "name": info.get("name"),
                    "primary_domain": info.get("primary_domain"),
                    "employee_count_range": info.get("employee_count_range"),
                    "headcount": company.get("headcount"),
                }
            )
    return records


def lookup_firm(bid: dict[str, Any], fresh: bool | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """Return (records, note). Uses the domain first; a name is only ever matched exactly."""
    firm = bid["firm"]
    domain, name = firm.get("domain"), firm.get("name")
    if domain:
        return _records_from_reply(crustdata.identify_company(domain=domain, fresh=fresh)), None

    records = _records_from_reply(crustdata.identify_company(name=name, fresh=fresh))
    if records:
        return records, None
    shorter = _LEGAL_SUFFIX.sub("", name).strip()
    if not shorter or shorter == name:
        return [], None
    records = _records_from_reply(crustdata.identify_company(name=shorter, fresh=fresh))
    return records, f"No exact match for '{name}', so the name was tried once more as '{shorter}'."
