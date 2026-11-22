"""Command line: expert <ID> [--fresh] | audit [--fresh] | check-cv <path> [--position KEY] [--profile-url URL]"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from app import checker, config, crustdata, cv_files, firm, normalise, parse_cv, parse_tender, report
from app.models import Claims, CvRow, ExpertResult, Lookup

DATA_DIR = config.ROOT_DIR / "data"
BID_DIR = DATA_DIR / "bid"
REQUIRED_EXPERT_FIELDS = ("id", "name", "proposed_position", "employment_with_bidder", "cv")
EMPLOYMENT_TYPES = {"full_time", "associate", "intermittent"}
REQUIRED_POSITION_FIELDS = ("min_total_years", "min_similar_years", "similar_title_keywords", "postgraduate")
POSTGRADUATE_LEVELS = {"required", "preferential", "none"}
CV_READ_ERRORS = (parse_cv.CvParseError, OSError, UnicodeDecodeError)

# progress(stage, expert id or None, detail, external=False when no outside service is contacted).
Progress = Callable[..., None]


def _silent(_stage: str, _expert: str | None, _detail: str, external: bool = True) -> None:
    return None


def _rows_text(count: int) -> str:
    return f"{count} CV row" if count == 1 else f"{count} CV rows"


@dataclass
class AuditRun:
    bid: dict[str, Any]
    entries: list[report.ExpertEntry]
    firm_result: Any
    full_time: int
    ledger_start: int
    report_path: Path
    errors: list[str] = field(default_factory=list)
    cv_problems: dict[str, str] = field(default_factory=dict)
    tender_id: str | None = None


@dataclass
class CvRun:
    entry: report.ExpertEntry
    ledger_start: int
    lookup_error: str | None = None


@dataclass
class UploadedCv:
    """One uploaded CV held in memory. problem is set when its text could not be extracted."""

    text: str | None
    problem: str | None
    profile_url: str | None
    position: str | None
    employment: str
    inline_position: dict[str, Any] | None = None


class InputError(RuntimeError):
    """An input file is malformed. The message names the file and the problem."""


def _display(path: Path) -> str:
    try:
        return path.resolve().relative_to(config.ROOT_DIR).as_posix()
    except ValueError:
        return str(path)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InputError(f"{_display(path)}: file not found.") from exc
    except json.JSONDecodeError as exc:
        raise InputError(f"{_display(path)}: not valid JSON (line {exc.lineno}, column {exc.colno}).") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(f"{_display(path)}: could not be read as text.") from exc


def _load_tender(path: Path | None = None) -> dict[str, Any]:
    path = path or DATA_DIR / "tender.json"
    tender = _load_json(path)
    positions = tender.get("positions") if isinstance(tender, dict) else None
    if not isinstance(positions, dict) or not positions:
        raise InputError(f"{_display(path)}: missing field 'positions'.")
    for key, position in positions.items():
        for field in REQUIRED_POSITION_FIELDS:
            if not isinstance(position, dict) or field not in position:
                raise InputError(f"{_display(path)}: position '{key}' is missing field '{field}'.")
        if position["postgraduate"] not in POSTGRADUATE_LEVELS:
            known = ", ".join(sorted(POSTGRADUATE_LEVELS))
            raise InputError(f"{_display(path)}: position '{key}' has unknown postgraduate '{position['postgraduate']}'. Known: {known}.")
    return tender


def _load_approved_tender(path: Path) -> dict[str, Any]:
    """Only a tender a person has approved may replace data/tender.json."""
    tender = _load_tender(path)
    if tender.get("_approved") is not True:
        raise InputError(f"{_display(path)}: this is not an approved tender. Run read-tender and approve it first.")
    return tender


def _check_expert_entry(expert: Any, index: int, tender: dict[str, Any], file: str) -> None:
    label = f"experts[{index}]"
    if not isinstance(expert, dict):
        raise InputError(f"{file}: {label} is not an object.")
    if "id" in expert:
        label += f" ({expert['id']})"
    for field in REQUIRED_EXPERT_FIELDS:
        if field not in expert:
            raise InputError(f"{file}: {label} is missing field '{field}'.")
    key = expert["proposed_position"]
    if key is not None and key not in tender["positions"]:
        known = ", ".join(tender["positions"])
        raise InputError(f"{file}: {label} has unknown proposed_position '{key}'. Known positions: {known}.")
    if expert["employment_with_bidder"] not in EMPLOYMENT_TYPES:
        known = ", ".join(sorted(EMPLOYMENT_TYPES))
        raise InputError(
            f"{file}: {label} has unknown employment_with_bidder '{expert['employment_with_bidder']}'. Known: {known}."
        )
    if not isinstance(expert["cv"], str):
        raise InputError(f"{file}: {label} field 'cv' must be a file path.")
    try:
        (BID_DIR / expert["cv"]).resolve().relative_to(BID_DIR.resolve())
    except ValueError as exc:
        raise InputError(f"{file}: {label} field 'cv' points outside the bid folder.") from exc


def _load_bid(tender: dict[str, Any], bid_path: Path | None = None) -> dict[str, Any]:
    path = bid_path or BID_DIR / "bid.json"
    file = _display(path)
    bid = _load_json(path)
    if not isinstance(bid, dict):
        raise InputError(f"{file}: must be a JSON object.")
    for field in ("firm", "experts"):
        if field not in bid:
            raise InputError(f"{file}: missing field '{field}'.")
    if not isinstance(bid["firm"], dict) or not bid["firm"].get("name"):
        raise InputError(f"{file}: firm is missing field 'name'.")
    if not isinstance(bid["experts"], list):
        raise InputError(f"{file}: field 'experts' must be a list.")
    for index, expert in enumerate(bid["experts"]):
        _check_expert_entry(expert, index, tender, file)
    ids = [expert["id"] for expert in bid["experts"]]
    repeated = sorted({str(expert_id) for expert_id in ids if ids.count(expert_id) > 1})
    if repeated:
        raise InputError(f"{file}: expert id used more than once: {', '.join(repeated)}.")
    return bid


def load_claims(path: Path) -> Claims:
    """Load a hand-written claims file, naming the file and field on any problem."""
    file = _display(path)
    data = _load_json(path)
    try:
        claims = Claims.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"])
        raise InputError(f"{file}: field '{field}': {first['msg']}.") from exc
    positions = _load_tender()["positions"]
    if claims.proposed_position is not None and claims.proposed_position not in positions:
        known = ", ".join(positions)
        raise InputError(
            f"{file}: unknown proposed_position '{claims.proposed_position}'. Known positions: {known}."
        )
    return claims


def _lookup_url(url: str, fresh: bool | None) -> tuple[Lookup, str]:
    """Look up one profile URL and say where its data came from."""
    reply = crustdata.enrich_people([url], fresh=fresh)
    latest = crustdata.run_stats()["lookups"][-1]
    source = "live" if latest["source"] == "live" else "from cache"
    origin = f"{source} (fetched {latest['fetched_at']})"
    unexpected = (
        "/person/enrich: lookup did not complete: it may have been billed, so it was not retried "
        "(the reply had an unexpected shape)."
    )
    if not isinstance(reply, list) or not all(isinstance(item, dict) for item in reply):
        raise crustdata.CrustdataError(unexpected)
    if len(reply) > 1:
        return Lookup(status="ambiguous", candidate_count=len(reply)), origin
    try:
        return normalise.profile_from_enrich(reply[0] if reply else {}), origin
    except (ValidationError, AttributeError, TypeError) as exc:
        raise crustdata.CrustdataError(unexpected) from exc


def _no_url_lookup() -> tuple[Lookup, str]:
    return Lookup(status="not_found", reason="no_profile_url"), "none (no profile URL supplied)"


def _position(expert: dict[str, Any], tender: dict[str, Any]) -> dict[str, Any] | None:
    key = expert.get("proposed_position")
    return tender["positions"][key] if key is not None else None


def _cv_label(expert: dict[str, Any]) -> str:
    return expert.get("cv_label") or _display(BID_DIR / expert["cv"])


def _read_cv(
    expert: dict[str, Any], fresh: bool | None, progress: Progress = _silent, text: str | None = None
) -> Claims:
    """Read the expert's CV through the parser. Uploads pass their text; the bid's name is the one displayed."""
    expert_id = expert["id"]
    progress("reading_cv", expert_id, f"Reading {expert_id}'s CV ({_cv_label(expert)})")
    if text is None:
        text = (BID_DIR / expert["cv"]).read_text(encoding="utf-8")
    claims = parse_cv.parse_cv(
        text,
        expert_id,
        expert.get("proposed_position"),
        fresh=fresh,
        on_dates=lambda count: progress("reading_dates", expert_id, f"Converting the dates on {_rows_text(count)}"),
    )
    return claims.model_copy(update={"name": expert.get("name") or claims.name})


def _cv_problem(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return "file not found"
    if isinstance(exc, UnicodeDecodeError):
        return "the file is not readable text"
    if isinstance(exc, OSError):
        return "the file could not be opened"
    return str(exc)


def _unread_entry(
    expert: dict[str, Any], problem: str, position: dict[str, Any] | None = None
) -> report.ExpertEntry:
    """An expert whose CV could not be read: nothing is checked and no paid lookup is made."""
    claims = Claims(
        expert_id=expert["id"],
        name=expert["name"],
        proposed_position=expert["proposed_position"],
        claimed_total_years=0,
        claimed_total_stated=False,
        claimed_similar_years=None,
        rows=[],
    )
    result = checker.cv_not_read_result(expert["id"], _cv_label(expert), problem)
    return report.ExpertEntry(
        expert, claims, Lookup(status="not_found"), result, "not looked up (CV could not be read)", position
    )


def _check(
    expert: dict[str, Any], claims: Claims, lookup: Lookup, tender: dict[str, Any], bid: dict[str, Any]
) -> ExpertResult:
    return checker.check_expert(
        lookup,
        claims,
        _position(expert, tender),
        bid["firm"]["name"],
        expert.get("employment_with_bidder"),
        today=config.TODAY,
    )


def _years(months: int | None) -> str:
    return "not available" if months is None else f"{months / 12:.1f} years"


def _row_line(index: int, row: CvRow, result: ExpertResult) -> str:
    employer = row.employer or "(employer unclear)"
    matched = result.row_results[index].matched_source
    suffix = f" [{matched}]" if matched else ""
    outcome = report.row_outcome(index, result)
    return f"  {index + 1}. {employer} | {row.title} | {report.row_dates(row)} -> {outcome}{suffix}"


def format_expert(
    expert: dict[str, Any], claims: Claims, result: ExpertResult, lookup_origin: str
) -> str:
    position = claims.proposed_position or "none (Career Check mode)"
    lines = [
        f"Expert: {expert['id']} - {claims.name}",
        f"Proposed position: {position}",
        f"Status: {result.status}",
        f"Documented: {_years(result.documented_total_months)} vs claimed: {report.claimed_text(claims)}",
    ]
    if result.documented_similar_months is not None or claims.claimed_similar_years is not None:
        claimed = claims.claimed_similar_years
        claimed_text = "not stated" if claimed is None else f"{claimed:.1f} years"
        lines.append(f"Similar role: documented {_years(result.documented_similar_months)} vs claimed: {claimed_text}")
    lines.append(f"lookup: {lookup_origin}")
    if report.reader_label(claims):
        lines.append(f"Read by: {report.reader_label(claims)}")
    lines.append("CV rows:")
    lines += [_row_line(index, row, result) for index, row in enumerate(claims.rows)]
    lines.append("Findings (questions for the committee, not conclusions):" if result.findings else "Findings: none")
    for finding in result.findings:
        lines.append(f"  [{finding.severity}] {finding.code}: {finding.message}")
        lines.append(f"      evidence: {', '.join(finding.evidence)}")
    return "\n".join(lines)


def run_expert(expert_id: str, fresh: bool | None) -> int:
    tender = _load_tender()
    bid = _load_bid(tender)
    expert = next((e for e in bid["experts"] if e["id"] == expert_id), None)
    if expert is None:
        known = ", ".join(str(e["id"]) for e in bid["experts"]) or "none"
        print(f"Unknown expert {expert_id!r}. Known experts: {known}", file=sys.stderr)
        return 2

    try:
        claims = _read_cv(expert, fresh)
    except CV_READ_ERRORS as exc:
        print(f"CV could not be read ({_display(BID_DIR / expert['cv'])}): {_cv_problem(exc)}.", file=sys.stderr)
        return 1
    try:
        url = expert.get("profile_url")
        lookup, origin = _lookup_url(url, fresh) if url else _no_url_lookup()
    except crustdata.CrustdataError as exc:
        print(f"Crustdata lookup failed: {exc}", file=sys.stderr)
        return 1

    result = _check(expert, claims, lookup, tender, bid)
    print(format_expert(expert, claims, result, origin))
    return 0


def _audit_experts(
    bid: dict[str, Any],
    tender: dict[str, Any],
    claims_by_id: dict[str, Claims],
    cv_problems: dict[str, str],
    fresh: bool | None,
    errors: list[str],
    progress: Progress = _silent,
) -> list[report.ExpertEntry]:
    entries = []
    seen: dict[str, tuple[Lookup, str, str]] = {}
    for expert in bid["experts"]:
        expert_id = expert["id"]
        if expert_id in cv_problems:
            progress("checking", expert_id, f"{expert_id}: the CV could not be read, so it was not checked")
            entries.append(_unread_entry(expert, cv_problems[expert_id], _position(expert, tender)))
            continue
        url = expert.get("profile_url")
        if not url:
            progress(
                "looking_up", expert_id, f"No profile URL supplied for {expert_id}, so no lookup is made", external=False
            )
            lookup, origin = _no_url_lookup()
        elif url in seen:
            # Same URL in one run: reuse the reply so --fresh never pays for it twice.
            lookup, first_origin, first_id = seen[url]
            progress(
                "looking_up",
                expert_id,
                f"{expert_id} has the same profile URL as {first_id}, so that lookup is reused",
                external=False,
            )
            origin = f"{first_origin}, same lookup as {first_id}"
        else:
            progress("looking_up", expert_id, f"Looking up {expert_id} in Crustdata")
            try:
                lookup, origin = _lookup_url(url, fresh)
            except crustdata.CrustdataError as exc:
                lookup = Lookup(status="not_found", reason="lookup_failed")
                origin = str(exc)
                errors.append(f"{expert['id']}: {exc}")
            seen[url] = (lookup, origin, expert["id"])
        claims = claims_by_id[expert_id]
        progress("checking", expert_id, _checking_detail(expert_id, claims, lookup))
        result = _check(expert, claims, lookup, tender, bid)
        entries.append(report.ExpertEntry(expert, claims, lookup, result, origin, _position(expert, tender)))
    return entries


def _checking_detail(expert_id: str, claims: Claims, lookup: Lookup) -> str:
    target = "the record" if lookup.profile is not None else "itself"
    return f"Checking {expert_id}'s {_rows_text(len(claims.rows))} against {target}"


def _audit_firm(
    bid: dict[str, Any], fresh: bool | None, errors: list[str], progress: Progress = _silent
) -> tuple[Any, int]:
    full_time = firm.distinct_full_time_count(bid["experts"])
    progress("looking_up", None, f"Looking up the company record for {bid['firm']['name']}")
    try:
        records, note = firm.lookup_firm(bid, fresh=fresh)
    except crustdata.CrustdataError as exc:
        records, note = [], f"The firm was not compared. {exc}"
        errors.append(f"firm: {exc}")
    people = "1 person" if full_time == 1 else f"{full_time} people"
    progress("checking", None, f"Checking the firm record against the {people} listed as full-time")
    result = firm.check_firm(records, full_time)
    result.lookup_note = note
    result.bid_findings = firm.rule_duplicate_experts(bid["experts"])
    return result, full_time


def audit_bid(
    fresh: bool | None,
    progress: Progress = _silent,
    bid_path: Path | None = None,
    tender_path: Path | None = None,
) -> AuditRun:
    """Run every expert and the firm, and write the report. The CLI and the web UI both call this."""
    tender = _load_approved_tender(tender_path) if tender_path else _load_tender()
    bid = _load_bid(tender, bid_path)
    ledger_start = report.ledger_row_count()

    # Read every CV first, so no paid lookup is made for an expert whose CV cannot be read.
    claims_by_id: dict[str, Claims] = {}
    cv_problems: dict[str, str] = {}
    for expert in bid["experts"]:
        try:
            claims_by_id[expert["id"]] = _read_cv(expert, fresh, progress)
        except CV_READ_ERRORS as exc:
            cv_problems[expert["id"]] = _cv_problem(exc)

    return _finish_audit(bid, tender, claims_by_id, cv_problems, ledger_start, fresh, progress)


def _finish_audit(
    bid: dict[str, Any],
    tender: dict[str, Any],
    claims_by_id: dict[str, Claims],
    cv_problems: dict[str, str],
    ledger_start: int,
    fresh: bool | None,
    progress: Progress,
) -> AuditRun:
    """Everything after the CVs are read: lookups, checks and the report."""
    errors: list[str] = []
    entries = _audit_experts(bid, tender, claims_by_id, cv_problems, fresh, errors, progress)
    firm_result, full_time = _audit_firm(bid, fresh, errors, progress)

    progress("writing", None, "Writing out/report.md and out/report.html")
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    text = report.render_report(bid, tender, entries, firm_result, full_time, ledger_start, errors, timestamp)
    path = report.write_report(text)
    return AuditRun(bid, entries, firm_result, full_time, ledger_start, path, errors, cv_problems, tender.get("tender_id"))


def audit_uploaded(
    firm_details: dict[str, str | None],
    uploads: list[UploadedCv],
    fresh: bool | None,
    progress: Progress = _silent,
) -> AuditRun:
    """Audit a bid built in memory from uploaded CVs. Nothing is written to data/."""
    tender = _load_tender()
    inline = {upload.position: upload.inline_position for upload in uploads if upload.inline_position is not None}
    if inline:
        tender = {
            **tender,
            "positions": {**tender["positions"], **inline},
            "tender_id": f"{tender.get('tender_id')} with custom or approved requirements",
        }
    experts = [
        {
            "id": f"C{number}",
            "name": "",
            "proposed_position": upload.position,
            "profile_url": upload.profile_url,
            "employment_with_bidder": upload.employment,
            "cv_label": f"uploaded CV {number}",
        }
        for number, upload in enumerate(uploads, 1)
    ]
    bid = {"firm": firm_details, "experts": experts, "_note": "Built from uploaded CVs for this run."}
    ledger_start = report.ledger_row_count()

    claims_by_id: dict[str, Claims] = {}
    cv_problems: dict[str, str] = {}
    for number, (expert, upload) in enumerate(zip(experts, uploads), 1):
        try:
            if upload.problem is not None:
                raise parse_cv.CvParseError(upload.problem)
            claims = _read_cv(expert, fresh, progress, upload.text)
            expert["name"] = claims.name  # The duplicate-person check compares these names.
            claims_by_id[expert["id"]] = claims
        except CV_READ_ERRORS as exc:
            expert["name"] = f"Uploaded CV {number}"
            cv_problems[expert["id"]] = _cv_problem(exc).rstrip(".")
    return _finish_audit(bid, tender, claims_by_id, cv_problems, ledger_start, fresh, progress)


def run_audit(fresh: bool | None, tender_path: Path | None = None) -> int:
    run = audit_bid(fresh, tender_path=tender_path)
    for expert_id, problem in run.cv_problems.items():
        expert = next(e for e in run.bid["experts"] if e["id"] == expert_id)
        print(f"CV could not be read for {expert_id} ({_display(BID_DIR / expert['cv'])}): {problem}.", file=sys.stderr)
    print(report.headline(run.entries, run.firm_result))
    print(f"Report written to {run.report_path} and {run.report_path.with_suffix('.html')}")
    for error in run.errors:
        print(f"Lookup failed: {error}", file=sys.stderr)
    return 0


def check_one_cv(
    text: str,
    cv_id: str,
    position_key: str | None,
    profile_url: str | None,
    fresh: bool | None,
    progress: Progress = _silent,
    inline_position: dict[str, Any] | None = None,
) -> CvRun:
    """Check one CV's text. With no position this is Career Check mode (SPEC 7b)."""
    tender = _load_tender()
    if inline_position is None and position_key is not None and position_key not in tender["positions"]:
        known = ", ".join(tender["positions"])
        raise InputError(f"Unknown position '{position_key}'. Known positions: {known}.")
    ledger_start = report.ledger_row_count()
    progress("reading_cv", cv_id, f"Reading the CV text ({len(text)} characters)")
    claims = parse_cv.parse_cv(
        text,
        cv_id,
        position_key,
        fresh=fresh,
        on_dates=lambda count: progress("reading_dates", cv_id, f"Converting the dates on {_rows_text(count)}"),
    )

    lookup_error = None
    if profile_url:
        progress("looking_up", cv_id, "Looking up the profile in Crustdata")
        try:
            lookup, origin = _lookup_url(profile_url, fresh)
        except crustdata.CrustdataError as exc:
            lookup, origin = Lookup(status="not_found", reason="lookup_failed"), str(exc)
            lookup_error = str(exc)
    else:
        progress("looking_up", cv_id, "No profile URL supplied, so no lookup is made", external=False)
        lookup, origin = _no_url_lookup()

    target = "the record" if lookup.profile is not None else "itself"
    progress("checking", cv_id, f"Checking {_rows_text(len(claims.rows))} against {target}")
    position = inline_position or (tender["positions"][position_key] if position_key else None)
    result = checker.check_expert(lookup, claims, position, None, None, today=config.TODAY)
    entry = report.ExpertEntry({"id": cv_id}, claims, lookup, result, origin, position)
    return CvRun(entry, ledger_start, lookup_error)


def run_check_cv(path_text: str, position_key: str | None, profile_url: str | None, fresh: bool | None) -> int:
    """Check one unseen CV file from the command line."""
    path = Path(path_text)
    try:
        if path.suffix.lower() in {".pdf", ".docx"}:
            text = cv_files.extract_text(path.name, path.read_bytes())
        else:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        run = check_one_cv(text, path.stem, position_key, profile_url, fresh)
    except CV_READ_ERRORS as exc:
        print(f"CV could not be read ({path}): {_cv_problem(exc).rstrip('.')}.", file=sys.stderr)
        return 1
    if run.lookup_error:
        print(f"Crustdata lookup failed: {run.lookup_error}", file=sys.stderr)

    entry = run.entry
    claims = entry.claims
    print(format_expert({"id": path.stem}, claims, entry.result, entry.origin))
    if claims.profile_url and not profile_url:
        print("Note: the CV lists a profile URL. It was not looked up; pass --profile-url to compare against it.")
    return 0


def _shown(value: Any) -> str:
    return "not stated" if value is None else f"{value:g}"


def format_proposal(positions: dict[str, dict[str, Any]]) -> str:
    lines = [f"Proposed positions ({len(positions)}). Nothing is used until you approve."]
    for key, position in positions.items():
        sources = position["_source_sentences"]
        lines += ["", f"{key}: {position['name']}"]
        for label, field in (("Minimum total years", "min_total_years"), ("Minimum similar years", "min_similar_years")):
            lines.append(f"  {label}: {_shown(position[field])}")
            if sources.get(field):
                lines.append(f"    source: {sources[field]['text']}")
        lines.append(f"  Postgraduate: {position['postgraduate']} (marks: {_shown(position['postgraduate_marks'])})")
        for field in ("postgraduate", "postgraduate_marks"):
            if sources.get(field):
                lines.append(f"    source: {sources[field]['text']}")
        lines.append(f"  Similar-role titles (proposed): {', '.join(position['similar_title_keywords'])}")
        if position["similar_capacity_wording"]:
            lines.append(f"    source: {position['similar_capacity_wording']}")
    return "\n".join(lines)


def run_read_tender(path_text: str, fresh: bool | None) -> int:
    """Propose positions from a tender's text. Only an explicit y writes the approved file."""
    path = Path(path_text)
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        positions = parse_tender.parse_tender(text, fresh)
    except (parse_tender.TenderParseError, OSError) as exc:
        problem = "could not be opened" if isinstance(exc, OSError) else str(exc)
        print(f"Tender could not be read ({path}): {problem}.", file=sys.stderr)
        return 1
    print(format_proposal(positions))
    try:
        answer = input("Approve? [y/N] ")
    except EOFError:
        answer = ""
    if answer.replace("﻿", "").strip().lower() != "y":  # PowerShell pipes can prepend a byte-order mark.
        print("Not approved. Nothing was written.")
        return 0
    approved = parse_tender.approve(positions, path.name)
    print(f"Approved. Written to {approved}. Use it with: uv run python -m app.cli audit --tender {_display(approved)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    expert = commands.add_parser("expert", help="check one expert from data/bid/bid.json")
    expert.add_argument("expert_id", metavar="ID")
    expert.add_argument("--fresh", action="store_true", help="skip reading the cache (paid call)")
    audit = commands.add_parser("audit", help="check every expert and the firm, and write out/report.md")
    audit.add_argument("--fresh", action="store_true", help="skip reading the cache (paid calls)")
    audit.add_argument("--tender", help="an approved tender file written by read-tender")
    read_tender = commands.add_parser("read-tender", help="propose positions from a tender's text, for approval")
    read_tender.add_argument("path")
    read_tender.add_argument("--fresh", action="store_true", help="skip reading the cache")
    check_cv = commands.add_parser("check-cv", help="check one CV file (.pdf, .docx, .txt or .md)")
    check_cv.add_argument("path", help="a .pdf, .docx, .txt or .md file")
    check_cv.add_argument("--position", help="key in data/tender.json; omit for Career Check mode")
    check_cv.add_argument("--profile-url", help="profile to compare against (1 paid credit)")
    check_cv.add_argument("--fresh", action="store_true", help="skip reading the cache")
    args = parser.parse_args(argv)
    fresh = True if args.fresh else None
    try:
        if args.command == "expert":
            return run_expert(args.expert_id, fresh)
        if args.command == "check-cv":
            return run_check_cv(args.path, args.position, args.profile_url, fresh)
        if args.command == "read-tender":
            return run_read_tender(args.path, fresh)
        return run_audit(fresh, Path(args.tender) if args.tender else None)
    except InputError as exc:
        print(f"Input problem: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
