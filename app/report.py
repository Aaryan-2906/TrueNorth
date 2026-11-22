"""Builds out/report.md and out/report.html. Files only: nothing here sends anything to anyone."""

import csv
import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import config, crustdata, firm, parse_cv
from app.checker import cite_job
from app.models import Claims, CvRow, ExpertResult, FirmResult, Job, Lookup
from app.questions import Question, build_questions

OUT_PATH = config.ROOT_DIR / "out" / "report.md"
UNDATED_NOTE = "CV is undated; 'present' taken as today"
EQUIVALENT_GRADES_NOTE = "Equivalent grades are for the committee to judge."
CLOSING_LINE = "These are questions for the committee, not findings."
MAX_DETAILED_CV_ROWS = 2

_FIXED_SOURCES = {
    "cv.claimed_total_years": "total years claimed on the CV",
    "tender.min_total_years": "tender minimum total years",
    "tender.min_similar_years": "tender minimum similar-role years",
    "tender.postgraduate": "tender postgraduate rule",
    "profile.education": "education section of the profile",
    "profile.jobs": "job list of the profile",
    "bid.profile_url": "profile URL field in the bid (empty)",
    "bid.employment_with_bidder": "employment status stated in the bid",
    "crustdata.lookup": "Crustdata lookup",
    "cv.file": "the CV file named in the bid",
}
_REJECTED_STATUSES = {"401", "402", "403", "429"}
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
    position: dict[str, Any] | None = None


def row_dates(row: CvRow) -> str:
    start = row.start_raw or row.start or "no start date"
    end = row.end_raw or row.end or "no end date"
    return f"{start} to {end}"


def claimed_text(claims: Claims) -> str:
    return f"{claims.claimed_total_years:.1f} years" if claims.claimed_total_stated else "not stated"


def reader_label(claims: Claims) -> str | None:
    return {"rules": "rules", "claude": "Claude"}.get(claims.read_by or "")


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


def ledger_credits(path: str, sha1: str) -> float | None:
    """Credits on the newest credits.csv line for this request, or None if it has no figure."""
    for row in reversed(_ledger_rows()):
        if row["path"] == path and row["sha1"] == sha1:
            return float(row["credits_used"]) if row["credits_used"] else None
    return None


def credits_since(ledger_start: int) -> float:
    """Total credits recorded in credits.csv since the given line count."""
    known = [float(row["credits_used"]) for row in _ledger_rows()[ledger_start:] if row["credits_used"]]
    return round(sum(known), 4)


def _credits_line(ledger_start: int) -> str:
    all_rows = _ledger_rows()[ledger_start:]
    rows = [row for row in all_rows if row["status"] not in _REJECTED_STATUSES or row["credits_used"]]
    known = [float(row["credits_used"]) for row in rows if row["credits_used"]]
    line = f"Credits this run: {round(sum(known), 4):g} ({len(rows)} paid calls logged in credits.csv)"
    rejected = len(all_rows) - len(rows)
    if rejected:
        line += f"; {rejected} call(s) rejected by Crustdata"
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
    if not entries:
        return f"0 experts: this bid lists no experts; {questions} {noun} in total"
    people = "expert" if len(entries) == 1 else "experts"
    return (
        f"{len(entries)} {people}: {counts['CONSISTENT']} consistent, "
        f"{counts['QUESTIONS']} need clarification, "
        f"{counts['CANNOT_CHECK']} cannot be checked; "
        f"{questions} {noun} in total (including CV-only questions)"
    )


def _jobs_by_source(lookup: Lookup) -> dict[str, Job]:
    return {job.source: job for job in lookup.profile.jobs} if lookup.profile else {}


def _evidence_text(evidence: list[str], entry: ExpertEntry) -> str:
    return "; ".join(_evidence_parts(evidence, entry))


def _evidence_parts(evidence: list[str], entry: ExpertEntry) -> list[str]:
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
    return parts


def _firm_evidence_text(evidence: list[str], result: FirmResult) -> str:
    return "; ".join(_firm_evidence_parts(evidence, result))


def _firm_evidence_parts(evidence: list[str], result: FirmResult) -> list[str]:
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
    return parts


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
    if not entry.claims.rows:
        return ["No CV rows could be read."]
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


def _expert_notes(entry: ExpertEntry) -> list[str]:
    claims = entry.claims
    notes = []
    if claims.cv_date is None and any(row.end == "present" for row in claims.rows):
        notes.append(UNDATED_NOTE)
    if claims.cv_date is not None and entry.lookup.profile is not None:
        notes.append(f"the profile was read as it stood at the CV date, {claims.cv_date}")
    if entry.position and entry.position.get("equivalent_grades"):
        notes.append(EQUIVALENT_GRADES_NOTE)
    return notes


def _expert_lines(entry: ExpertEntry) -> list[str]:
    claims, result = entry.claims, entry.result
    position = claims.proposed_position or "none (Career Check mode)"
    lines = [
        f"### {entry.expert['id']}: {claims.name}",
        "",
        f"- **Status:** {result.status}",
        f"- **Proposed position:** {position}",
        f"- **Years:** documented {_years_text(result.documented_total_months)} vs claimed {claimed_text(claims)}",
    ]
    if result.documented_similar_months is not None:
        claimed = claims.claimed_similar_years
        similar_claimed = "not stated" if claimed is None else f"{claimed:.1f} years"
        lines.append(
            f"- **Similar role:** documented {_years_text(result.documented_similar_months)} vs claimed {similar_claimed}"
        )
    lines.append(f"- **Lookup:** {entry.origin}")
    if reader_label(claims):
        lines.append(f"- **Read by:** {reader_label(claims)}")
    lines += [f"- **Note:** {note}" for note in _expert_notes(entry)]
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
    readings = parse_cv.run_stats()
    lines.append(
        f"- CV readings this run: {readings['live']} by the LLM, {readings['from_cache']} from cache"
        + (f", {readings['rules']} by the rules reader" if readings["rules"] else "")
        + ". The LLM only copies text from the CV. Dates are converted and all checks are run by deterministic code."
    )
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
    if not entries:
        lines += ["This bid lists no experts, so no CV was checked.", ""]
    for entry in entries:
        lines += _expert_lines(entry)
    lines += _letter_lines(bid, entries, firm_result)
    lines += _footer_lines(ledger_start, errors, timestamp)
    return "\n".join(lines)


# --- JSON views for the web UI: the same questions, evidence and wording as the report ---


def _question_view(question: Question, parts: list[str]) -> dict[str, Any]:
    return {
        "number": question.number,
        "text": question.text,
        "codes": question.codes,
        "evidence": parts,
        "action_needed": question.action_needed,
    }


def _months_to_years(months: int | None) -> float | None:
    return None if months is None else round(months / 12, 1)


def expert_view(entry: ExpertEntry) -> dict[str, Any]:
    claims, result = entry.claims, entry.result
    jobs = _jobs_by_source(entry.lookup)
    rows = []
    for index, row in enumerate(claims.rows):
        source = result.row_results[index].matched_source
        rows.append(
            {
                "number": index + 1,
                "employer": row.employer or "(employer unclear)",
                "title": row.title,
                "dates": row_dates(row),
                "outcome": row_outcome(index, result),
                "profile_entry": f"{cite_job(jobs[source])} ({source})" if source in jobs else None,
            }
        )
    questions, notes = build_questions(result.findings)
    return {
        "id": entry.expert["id"],
        "name": claims.name,
        "status": result.status,
        "position": claims.proposed_position,
        "documented_years": _months_to_years(result.documented_total_months),
        "claimed_years": claims.claimed_total_years if claims.claimed_total_stated else None,
        "documented_similar_years": _months_to_years(result.documented_similar_months),
        "claimed_similar_years": claims.claimed_similar_years,
        "lookup": entry.origin,
        "read_by": reader_label(claims),
        "notes": _expert_notes(entry),
        "rows": rows,
        "questions": [_question_view(q, _evidence_parts(q.evidence, entry)) for q in questions],
        "info": [_question_view(n, _evidence_parts(n.evidence, entry)) for n in notes],
        "why": [
            {"code": f.code, "severity": f.severity, "message": f.message, "evidence": f.evidence}
            for f in result.findings
        ],
    }


def firm_view(bid: dict[str, Any], result: FirmResult, full_time_count: int) -> dict[str, Any]:
    questions, notes = _firm_questions(result)
    return {
        "name": bid["firm"].get("name"),
        "domain": bid["firm"].get("domain"),
        "status": result.status,
        "headcount": firm.format_range(result.headcount_range) if result.headcount_range is not None else None,
        "full_time_people": full_time_count,
        "lookup_note": result.lookup_note,
        "records": [
            {
                "id": record.get("crustdata_company_id"),
                "name": record.get("name"),
                "domain": record.get("primary_domain"),
                "range": firm.describe_range(record),
            }
            for record in result.records
        ],
        "questions": [_question_view(q, _firm_evidence_parts(q.evidence, result)) for q in questions],
        "info": [_question_view(n, _firm_evidence_parts(n.evidence, result)) for n in notes],
    }


def status_counts(entries: list[ExpertEntry]) -> dict[str, int]:
    counts = {"CONSISTENT": 0, "QUESTIONS": 0, "CANNOT_CHECK": 0}
    for entry in entries:
        counts[entry.result.status] += 1
    return counts


def draft_letter(bid: dict[str, Any], entries: list[ExpertEntry], firm_result: FirmResult) -> str:
    """The report's draft letter as plain text. It is only ever copied by a person."""
    lines = _letter_lines(bid, entries, firm_result)[4:]  # Without the heading and the note to the committee.
    text = "\n".join(line.replace("**", "").strip("*") for line in lines).strip() + "\n"
    return _to_bidder(text)


# The questions are written for the committee ("Please ask the bidder ..."); the letter speaks to the bidder.
_BIDDER_PHRASES = (
    ("Please ask the bidder which", "Please confirm which"),
    ("Please ask the bidder whether", "Please confirm whether"),
    ("Please ask the bidder for ", "Please provide "),
    ("Please ask the bidder to ", "Please "),
    ("pasted_cv: ", "CV: "),
)


def _to_bidder(text: str) -> str:
    for old, new in _BIDDER_PHRASES:
        text = text.replace(old, new)
    return text


# --- HTML: rendered from the Markdown report, so both files always hold the same content ---

_STATUS_WORD = re.compile(r"\b(CONSISTENT|QUESTIONS|CANNOT_CHECK)\b")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ORDERED_ITEM = re.compile(r"(\d+)\. (.*)")
_NOTE_EVIDENCE = re.compile(r"(.*) \(Evidence: (.*)\)")
_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
_EVIDENCE_PREFIX = "   - Evidence: "

_CSS = """
*{box-sizing:border-box}
body{margin:0;background:#F8FAFC;color:#0F172A;font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
header{background:#1E3A8A;color:#fff;padding:36px 24px 40px}
.wrap{max-width:1112px;margin:0 auto}
header h1{margin:0 0 10px;font-size:28px;line-height:1.2;letter-spacing:-.01em}
.headline{margin:0 0 14px;font-size:18px;font-weight:600}
.meta{margin:2px 0;color:#DBEAFE;font-size:13.5px}
main{max-width:1160px;margin:0 auto;padding:24px 24px 48px}
.card{background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(15,23,42,.08),0 6px 18px rgba(15,23,42,.06);padding:24px;margin:0 0 20px}
h2{margin:0 0 14px;font-size:20px;line-height:1.3;color:#1E3A8A}
.section-title{margin:30px 0 14px}
.card-head{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px 16px;margin:0 0 14px}
h3{margin:0;font-size:17px;line-height:1.35}
p{margin:0 0 10px}
.note{color:#64748B;font-size:13.5px;font-style:italic}
.closing{margin:16px 0 0;padding:12px 16px;border-radius:8px;background:#EFF6FF;color:#1E3A8A;font-weight:600}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:700;letter-spacing:.02em;white-space:nowrap}
.pill-consistent{color:#15803D;background:#DCFCE7}
.pill-questions{color:#B45309;background:#FEF3C7}
.pill-cannot-check{color:#475569;background:#E2E8F0}
.chip{display:inline-block;margin:3px 4px 0 0;padding:2px 8px;border-radius:6px;background:#F1F5F9;color:#64748B;font-size:12px;line-height:1.5}
.chips{margin-top:3px}
ul{margin:0 0 14px;padding-left:20px}
ul.facts{list-style:none;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:4px 24px}
ul ul{margin:2px 0 4px}
ol{margin:0 0 14px;padding-left:24px}
ol li{margin:0 0 10px}
.table-wrap{overflow-x:auto;margin:0 0 16px;border:1px solid #E2E8F0;border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:13px}
th{background:#F1F5F9;color:#334155;text-align:left;font-weight:600;white-space:nowrap}
th,td{padding:8px 10px;border-bottom:1px solid #E2E8F0;vertical-align:top}
tr:last-child td{border-bottom:0}
.subhead{margin:16px 0 8px;font-weight:600}
.letter{border-left:4px solid #1E3A8A}
@media (max-width:640px){header{padding:24px 16px}main{padding:16px}.card{padding:16px}header h1{font-size:23px}}
@media print{body{background:#fff}.card{box-shadow:none;border:1px solid #E2E8F0;break-inside:avoid-page}header{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
""".strip()


def _inline(text: str) -> str:
    """Escape first: CV text is untrusted and must never become markup."""
    return _BOLD.sub(r"<strong>\1</strong>", html.escape(text))


def _pill(status: str) -> str:
    return f'<span class="pill pill-{status.lower().replace("_", "-")}">{status}</span>'


def _chips(text: str, separator: str = "; ") -> str:
    items = "".join(f'<span class="chip">{html.escape(item)}</span>' for item in text.split(separator))
    return f'<div class="chips">{items}</div>'


def _outcome_html(text: str) -> str:
    parts = []
    for code in text.split(", "):
        if code == "MATCHED":
            status = "consistent"
        elif code.startswith("NOT_COMPARED"):
            status = "cannot-check"
        else:
            status = "questions"
        parts.append(f'<span class="pill pill-{status}">{html.escape(code)}</span>')
    return " ".join(parts)


def _cell_html(header: str, value: str) -> str:
    if header == "Status" and _STATUS_WORD.fullmatch(value):
        return _pill(value)
    if header == "Outcome":
        return _outcome_html(value)
    if header == "Codes" and value != "none":
        return _chips(value, ", ")
    return _inline(value)


def _split_row(line: str) -> list[str]:
    cells = _UNESCAPED_PIPE.split(line.strip().strip("|"))
    return [cell.strip().replace("\\|", "|") for cell in cells]


def _table_html(rows: list[str]) -> str:
    headers = _split_row(rows[0])
    head = "".join(f"<th>{html.escape(name)}</th>" for name in headers)
    body = ""
    for row in rows[2:]:
        cells = "".join(f"<td>{_cell_html(h, v)}</td>" for h, v in zip(headers, _split_row(row)))
        body += f"<tr>{cells}</tr>"
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _ordered_html(lines: list[str]) -> str:
    first = _ORDERED_ITEM.fullmatch(lines[0]).group(1)
    items: list[str] = []
    for line in lines:
        if line.startswith(_EVIDENCE_PREFIX):
            items[-1] += _chips(line[len(_EVIDENCE_PREFIX):])
        else:
            items.append(_inline(_ORDERED_ITEM.fullmatch(line).group(2)))
    return f'<ol start="{first}">' + "".join(f"<li>{item}</li>" for item in items) + "</ol>"


def _bullet_text(text: str) -> str:
    note = _NOTE_EVIDENCE.fullmatch(text)
    if note:
        return _inline(note.group(1)) + _chips(note.group(2))
    rendered = _inline(text)
    if "status:</strong>" in rendered.lower():
        rendered = _STATUS_WORD.sub(lambda match: _pill(match.group(1)), rendered)
    return rendered


def _bullets_html(lines: list[str]) -> str:
    items: list[tuple[str, list[str]]] = []
    for line in lines:
        if line.startswith("  - "):
            items[-1][1].append(_inline(line[4:]))
        else:
            items.append((_bullet_text(line[2:]), []))
    facts = all(line.startswith("- **") for line in lines)
    opening = '<ul class="facts">' if facts else "<ul>"
    body = ""
    for text, children in items:
        nested = "<ul>" + "".join(f"<li>{child}</li>" for child in children) + "</ul>" if children else ""
        body += f"<li>{text}{nested}</li>"
    return f"{opening}{body}</ul>"


def _paragraph_html(line: str) -> str:
    if line == f"**{CLOSING_LINE}**":
        return f'<p class="closing">{html.escape(CLOSING_LINE)}</p>'
    if line.startswith("*") and not line.startswith("**") and line.endswith("*"):
        return f'<p class="note">{html.escape(line.strip("*"))}</p>'
    if line.startswith("**") and line.endswith("**") and line.count("**") == 2:
        return f'<p class="subhead">{html.escape(line[2:-2])}</p>'
    return f"<p>{_inline(line)}</p>"


def _take_while(lines: list[str], start: int, keep: Any) -> int:
    end = start
    while end < len(lines) and keep(lines[end]):
        end += 1
    return end


def _blocks_html(lines: list[str]) -> str:
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
        elif line.startswith("|"):
            end = _take_while(lines, index, lambda item: item.startswith("|"))
            out.append(_table_html(lines[index:end]))
            index = end
        elif _ORDERED_ITEM.fullmatch(line):
            end = _take_while(
                lines, index, lambda item: bool(_ORDERED_ITEM.fullmatch(item)) or item.startswith(_EVIDENCE_PREFIX)
            )
            out.append(_ordered_html(lines[index:end]))
            index = end
        elif line.startswith("- "):
            end = _take_while(lines, index, lambda item: item.startswith(("- ", "  - ")))
            out.append(_bullets_html(lines[index:end]))
            index = end
        else:
            out.append(_paragraph_html(line))
            index += 1
    return "".join(out)


def _split_on(lines: list[str], prefix: str) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    for line in lines:
        if line.startswith(prefix):
            sections.append((line[len(prefix):], []))
        elif sections:
            sections[-1][1].append(line)
    return sections


def _expert_card(title: str, body: list[str]) -> str:
    status_prefix = "- **Status:** "
    status = next((line[len(status_prefix):] for line in body if line.startswith(status_prefix)), None)
    rest = [line for line in body if not line.startswith(status_prefix)]
    pill = _pill(status) if status else ""
    return (
        f'<section class="card"><div class="card-head"><h3>{html.escape(title)}</h3>{pill}</div>'
        f"{_blocks_html(rest)}</section>"
    )


def _section_html(title: str, body: list[str]) -> str:
    if any(line.startswith("### ") for line in body):
        cards = "".join(_expert_card(name, part) for name, part in _split_on(body, "### "))
        return f'<h2 class="section-title">{html.escape(title)}</h2>{cards}'
    kind = "card letter" if title.startswith("Draft clarification letter") else "card"
    return f'<section class="{kind}"><h2>{html.escape(title)}</h2>{_blocks_html(body)}</section>'


def render_html(markdown: str) -> str:
    """One static page with inline CSS and no scripts, holding the same content as the Markdown."""
    lines = markdown.splitlines()
    first_section = next(index for index, line in enumerate(lines) if line.startswith("## "))
    intro = [line for line in lines[:first_section] if line.strip()]
    title = intro[0].removeprefix("# ")
    lead = intro[1].strip("*")
    meta = "".join(f'<p class="meta">{html.escape(line)}</p>' for line in intro[2:])
    sections = "".join(_section_html(name, body) for name, body in _split_on(lines[first_section:], "## "))
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head><body>"
        f'<header><div class="wrap"><h1>{html.escape(title)}</h1>'
        f'<p class="headline">{html.escape(lead)}</p>{meta}</div></header>'
        f"<main>{sections}</main></body></html>\n"
    )


def write_report(text: str, path: Path | None = None) -> Path:
    """Write the Markdown report and its HTML twin. Returns the Markdown path."""
    target = path or OUT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    target.with_suffix(".html").write_text(render_html(text), encoding="utf-8")
    return target
