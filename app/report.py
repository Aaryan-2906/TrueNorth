"""Builds out/report.md. Text only: nothing here sends anything to anyone."""

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import config, crustdata, firm
from app.checker import cite_job
from app.models import Claims, CvRow, ExpertResult, FirmResult, Job, Lookup
from app.questions import Question, build_questions

OUT_PATH = config.ROOT_DIR / "out" / "report.md"
UNDATED_NOTE = "CV is undated; 'present' taken as today"
CLOSING_LINE = "These are questions for the committee, not findings."
MAX_DETAILED_CV_ROWS = 2

_FIXED_SOURCES = {
    "cv.claimed_total_years": "total years claimed on the CV",
    "tender.min_total_years": "tender minimum total years",
    "tender.min_similar_years": "tender minimum similar-role years",
    "tender.requires_postgraduate": "tender postgraduate requirement",
    "profile.education": "education section of the profile",
    "profile.jobs": "job list of the profile",
    "bid.profile_url": "profile URL field in the bid (empty)",
    "bid.employment_with_bidder": "employment status stated in the bid",
    "crustdata.lookup": "Crustdata lookup",
}
_CV_ROW = re.compile(r"cv\.rows\[(\d+)\]")
_FIRM_RECORD = re.compile(r"firm\.records\[(\d+)\]")
_BID_EXPERT = re.compile(r"bid\.experts\[(.+)\]")


@dataclass
class ExpertEntry:
    expert: dict[str, Any]
    claims: Claims
    lookup: Lookup
    result: ExpertResult
    origin: str


def row_dates(row: CvRow) -> str:
    start = row.start_raw or row.start or "no start date"
    end = row.end_raw or row.end or "no end date"
    return f"{start} to {end}"


def row_outcome(index: int, result: ExpertResult) -> str:
    """All row-level codes for one row, or its single outcome label."""
    cite = f"cv.rows[{index}]"
    codes = [f.code for f in result.findings if f.code.startswith("ROW_") and cite in f.evidence]
    return ", ".join(codes) or result.row_results[index].outcome


def ledger_row_count() -> int:
    return len(_ledger_rows())


def _ledger_rows() -> list[dict[str, str]]:
    if not crustdata.LEDGER_PATH.exists():
        return []
    with crustdata.LEDGER_PATH.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _credits_line(ledger_start: int) -> str:
    rows = _ledger_rows()[ledger_start:]
    known = [float(row["credits_used"]) for row in rows if row["credits_used"]]
    line = f"Credits this run: {round(sum(known), 4):g} ({len(rows)} paid calls logged in credits.csv)"
    blank = len(rows) - len(known)
    if blank:
        line += f"; {blank} call(s) had no credit figure and may also have been billed"
    return line + "."


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _firm_questions(result: FirmResult) -> tuple[list[Question], list[Question]]:
    return build_questions(result.findings + result.bid_findings)


def total_questions(entries: list[ExpertEntry], firm_result: FirmResult) -> int:
    """Every question that needs an answer from the bidder, across experts, firm and bid."""
    groups = [build_questions(entry.result.findings)[0] for entry in entries]
    groups.append(_firm_questions(firm_result)[0])
    return sum(question.action_needed for group in groups for question in group)


def headline(entries: list[ExpertEntry], firm_result: FirmResult) -> str:
    counts = {"CONSISTENT": 0, "QUESTIONS": 0, "CANNOT_CHECK": 0}
    for entry in entries:
        counts[entry.result.status] += 1
    questions = total_questions(entries, firm_result)
    noun = "question" if questions == 1 else "questions"
    return (
        f"{len(entries)} experts: {counts['CONSISTENT']} consistent, "
        f"{counts['QUESTIONS']} need clarification, "
        f"{counts['CANNOT_CHECK']} cannot be checked; "
        f"{questions} {noun} in total (including CV-only questions)"
    )


def _jobs_by_source(lookup: Lookup) -> dict[str, Job]:
    return {job.source: job for job in lookup.profile.jobs} if lookup.profile else {}


def _evidence_text(evidence: list[str], entry: ExpertEntry) -> str:
    jobs = _jobs_by_source(entry.lookup)
    cv_numbers = [int(m.group(1)) for m in map(_CV_ROW.fullmatch, evidence) if m]
    parts = []
    if len(cv_numbers) > MAX_DETAILED_CV_ROWS:
        parts.append("CV rows " + ", ".join(str(n + 1) for n in cv_numbers))
    else:
        for number in cv_numbers:
            row = entry.claims.rows[number]
            employer = row.employer or "employer unclear"
            parts.append(f"CV row {number + 1} ({employer}, {row.title}, {row_dates(row)})")
    for source in evidence:
        if _CV_ROW.fullmatch(source):
            continue
        if source in jobs:
            parts.append(f"profile entry {source} ({cite_job(jobs[source])})")
        else:
            parts.append(_FIXED_SOURCES.get(source, source))
    return "; ".join(parts)


def _firm_evidence_text(evidence: list[str], result: FirmResult) -> str:
    parts = []
    for source in evidence:
        match = _FIRM_RECORD.fullmatch(source)
        expert = _BID_EXPERT.fullmatch(source)
        if match:
            record = result.records[int(match.group(1))]
            parts.append(
                f"company record {record.get('crustdata_company_id')} "
                f"({record.get('name')}, {firm.describe_range(record)})"
            )
        elif expert:
            parts.append(f"bid entry {expert.group(1)}")
        else:
            parts.append(_FIXED_SOURCES.get(source, source))
    return "; ".join(parts)


def _question_lines(questions: list[Question], describe: Any) -> list[str]:
    lines = []
    for question in questions:
        lines.append(f"{question.number}. {question.text}")
        lines.append(f"   - Evidence: {describe(question.evidence)}")
    return lines


def _summary_lines(entries: list[ExpertEntry]) -> list[str]:
    lines = ["## Summary", "", "| Expert | Status | Codes |", "|---|---|---|"]
    for entry in entries:
        codes = sorted({finding.code for finding in entry.result.findings})
        lines.append(f"| {entry.expert['id']} | {entry.result.status} | {', '.join(codes) or 'none'} |")
    return lines + [""]


def _firm_lines(bid: dict[str, Any], result: FirmResult, full_time_count: int) -> list[str]:
    details = bid["firm"]
    lines = [
        "## Firm and bid",
        "",
        f"- **Bidder:** {details.get('name')} ({details.get('domain') or 'no domain given'})",
        f"- **Firm status:** {result.status}",
        f"- **Records found:** {len(result.records)}",
    ]
    if result.headcount_range is not None:
        lines.append(f"- **Headcount used:** {firm.format_range(result.headcount_range)}")
    else:
        lines.append("- **Headcount used:** not available")
    lines.append(f"- **Distinct people listed as full-time in the bid:** {full_time_count}")
    if result.lookup_note:
        lines.append(f"- **Lookup note:** {result.lookup_note}")
    if result.records:
        lines += ["", "| Company id | Name | Domain | Employee range |", "|---|---|---|---|"]
        for record in result.records:
            lines.append(
                f"| {record.get('crustdata_company_id')} | {_cell(record.get('name'))} | "
                f"{_cell(record.get('primary_domain') or 'not listed')} | {_cell(firm.describe_range(record))} |"
            )
    questions, notes = _firm_questions(result)
    describe = lambda evidence: _firm_evidence_text(evidence, result)
    lines += ["", "**Questions for the committee:**", ""]
    lines += _question_lines(questions, describe) or ["None."]
    if notes:
        lines += ["", "**Notes:**", ""]
        lines += [f"- {note.text} (Evidence: {describe(note.evidence)})" for note in notes]
    return lines + [""]


def _years_text(months: int | None) -> str:
    return "not available" if months is None else f"{months / 12:.1f} years"


def _row_table(entry: ExpertEntry) -> list[str]:
    jobs = _jobs_by_source(entry.lookup)
    lines = ["| # | Employer | Title | CV dates | Outcome | Profile entry |", "|---|---|---|---|---|---|"]
    for index, row in enumerate(entry.claims.rows):
        source = entry.result.row_results[index].matched_source
        matched = f"{cite_job(jobs[source])} ({source})" if source in jobs else "none"
        employer = row.employer or "(employer unclear)"
        lines.append(
            f"| {index + 1} | {_cell(employer)} | {_cell(row.title)} | {_cell(row_dates(row))} | "
            f"{_cell(row_outcome(index, entry.result))} | {_cell(matched)} |"
        )
    return lines


def _expert_lines(entry: ExpertEntry) -> list[str]:
    claims, result = entry.claims, entry.result
    position = claims.proposed_position or "none (Career Check mode)"
    lines = [
        f"### {entry.expert['id']}: {claims.name}",
        "",
        f"- **Status:** {result.status}",
        f"- **Proposed position:** {position}",
        f"- **Years:** documented {_years_text(result.documented_total_months)} vs claimed {claims.claimed_total_years:.1f} years",
    ]
    if result.documented_similar_months is not None:
        claimed = claims.claimed_similar_years
        claimed_text = "not stated" if claimed is None else f"{claimed:.1f} years"
        lines.append(
            f"- **Similar role:** documented {_years_text(result.documented_similar_months)} vs claimed {claimed_text}"
        )
    lines.append(f"- **Lookup:** {entry.origin}")
    if claims.cv_date is None and any(row.end == "present" for row in claims.rows):
        lines.append(f"- **Note:** {UNDATED_NOTE}")
    if claims.cv_date is not None and entry.lookup.profile is not None:
        lines.append(f"- **Note:** the profile was read as it stood at the CV date, {claims.cv_date}")
    lines += ["", *_row_table(entry), "", "**Questions for the committee:**", ""]
    questions, notes = build_questions(result.findings)
    describe = lambda evidence: _evidence_text(evidence, entry)
    lines += _question_lines(questions, describe) or ["None."]
    if notes:
        lines += ["", "**Notes:**", ""]
        lines += [f"- {note.text} (Evidence: {describe(note.evidence)})" for note in notes]
    return lines + [""]


def _letter_lines(bid: dict[str, Any], entries: list[ExpertEntry], firm_result: FirmResult) -> list[str]:
    lines = [
        "## Draft clarification letter to the bidder",
        "",
        "*Draft for the committee to copy if it chooses. This tool has not sent it and never sends anything.*",
        "",
        f"To: {bid['firm'].get('name')}",
        "",
        "Subject: Request for clarification on proposed key experts",
        "",
        "The committee would be grateful for your clarification on the points below. "
        "They are questions arising from a consistency check and are not conclusions about any person or firm.",
        "",
    ]
    for entry in entries:
        questions = [q for q in build_questions(entry.result.findings)[0] if q.action_needed]
        if questions:
            lines += [f"**{entry.expert['id']}: {entry.claims.name}**", ""]
            lines += [f"{index}. {q.text}" for index, q in enumerate(questions, 1)] + [""]
    firm_questions = [q for q in _firm_questions(firm_result)[0] if q.action_needed]
    if firm_questions:
        lines += ["**The firm and bid**", ""]
        lines += [f"{index}. {q.text}" for index, q in enumerate(firm_questions, 1)] + [""]
    return lines + ["Yours faithfully,", "", "[Name], Tender Committee", ""]


def _footer_lines(ledger_start: int, errors: list[str], timestamp: str) -> list[str]:
    stats = crustdata.run_stats()
    lines = [
        "## Limits and sources",
        "",
        "- This tool sees only what a public professional profile and company record show. Profiles can be "
        "incomplete or out of date, and a missing record is not evidence against anyone.",
        "- It compares dates and titles. It cannot see contracts, payroll or certificates.",
        "- Which titles count as a similar role is our reading of the tender. The committee decides.",
        f"- {_credits_line(ledger_start)}",
        f"- Lookups this run: {stats['live']} live, {stats['from_cache']} from cache.",
    ]
    for item in stats["lookups"]:
        source = "live" if item["source"] == "live" else "from cache"
        lines.append(f"  - {item['path']}: {source}, fetched {item['fetched_at']}")
    if errors:
        lines.append("- Lookups that failed in this run, shown as not compared:")
        lines += [f"  - {error}" for error in errors]
    lines += [f"- Report generated: {timestamp}", "", f"**{CLOSING_LINE}**", ""]
    return lines


def render_report(
    bid: dict[str, Any],
    tender: dict[str, Any],
    entries: list[ExpertEntry],
    firm_result: FirmResult,
    full_time_count: int,
    ledger_start: int,
    errors: list[str],
    timestamp: str,
) -> str:
    lines = ["# Tender Bid Audit", "", f"**{headline(entries, firm_result)}**", ""]
    lines.append(f"Tender: {tender.get('tender_id')}. Bidder: {bid['firm'].get('name')}.")
    if bid.get("_note"):
        lines.append(f"Bid note: {bid['_note']}")
    lines.append("")
    lines += _summary_lines(entries)
    lines += _firm_lines(bid, firm_result, full_time_count)
    lines += ["## Experts", ""]
    for entry in entries:
        lines += _expert_lines(entry)
    lines += _letter_lines(bid, entries, firm_result)
    lines += _footer_lines(ledger_start, errors, timestamp)
    return "\n".join(lines)


def write_report(text: str, path: Path | None = None) -> Path:
    target = path or OUT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target
