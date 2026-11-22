"""Command line: expert <ID> [--fresh] | audit [--fresh] | check-cv <path> [--position KEY] [--profile-url URL]"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import checker, config, crustdata, firm, normalise, parse_cv, report
from app.models import Claims, CvRow, ExpertResult, Lookup

DATA_DIR = config.ROOT_DIR / "data"
BID_DIR = DATA_DIR / "bid"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _lookup_url(url: str, fresh: bool | None) -> tuple[Lookup, str]:
    """Look up one profile URL and say where its data came from."""
    reply = crustdata.enrich_people([url], fresh=fresh)
    latest = crustdata.run_stats()["lookups"][-1]
    source = "live" if latest["source"] == "live" else "from cache"
    origin = f"{source} (fetched {latest['fetched_at']})"
    if len(reply) > 1:
        return Lookup(status="ambiguous", candidate_count=len(reply)), origin
    return normalise.profile_from_enrich(reply[0] if reply else {}), origin


def _no_url_lookup() -> tuple[Lookup, str]:
    return Lookup(status="not_found", reason="no_profile_url"), "none (no profile URL supplied)"


def _position(expert: dict[str, Any], tender: dict[str, Any]) -> dict[str, Any] | None:
    key = expert.get("proposed_position")
    return tender["positions"][key] if key is not None else None


def _read_cv(expert: dict[str, Any], fresh: bool | None) -> Claims:
    """Read the expert's CV text through the parser. The bid's name is the one displayed."""
    text = (BID_DIR / expert["cv"]).read_text(encoding="utf-8")
    claims = parse_cv.parse_cv(text, expert["id"], expert.get("proposed_position"), fresh=fresh)
    return claims.model_copy(update={"name": expert.get("name") or claims.name})


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
    lines.append("CV rows:")
    lines += [_row_line(index, row, result) for index, row in enumerate(claims.rows)]
    lines.append("Findings (questions for the committee, not conclusions):" if result.findings else "Findings: none")
    for finding in result.findings:
        lines.append(f"  [{finding.severity}] {finding.code}: {finding.message}")
        lines.append(f"      evidence: {', '.join(finding.evidence)}")
    return "\n".join(lines)


def run_expert(expert_id: str, fresh: bool | None) -> int:
    bid = _load_json(BID_DIR / "bid.json")
    expert = next((e for e in bid["experts"] if e["id"] == expert_id), None)
    if expert is None:
        known = ", ".join(e["id"] for e in bid["experts"])
        print(f"Unknown expert {expert_id!r}. Known experts: {known}", file=sys.stderr)
        return 2

    tender = _load_json(DATA_DIR / "tender.json")
    try:
        claims = _read_cv(expert, fresh)
    except (parse_cv.CvParseError, OSError) as exc:
        print(f"Could not read the CV for {expert_id}: {exc}", file=sys.stderr)
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
    fresh: bool | None,
    errors: list[str],
) -> list[report.ExpertEntry]:
    entries = []
    seen: dict[str, tuple[Lookup, str, str]] = {}
    for expert in bid["experts"]:
        url = expert.get("profile_url")
        if not url:
            lookup, origin = _no_url_lookup()
        elif url in seen:
            # Same URL in one run: reuse the reply so --fresh never pays for it twice.
            lookup, first_origin, first_id = seen[url]
            origin = f"{first_origin}, same lookup as {first_id}"
        else:
            try:
                lookup, origin = _lookup_url(url, fresh)
            except crustdata.CrustdataError as exc:
                lookup = Lookup(status="not_found", reason="lookup_failed")
                origin = "failed (see the limits section)"
                errors.append(f"{expert['id']}: {exc}")
            seen[url] = (lookup, origin, expert["id"])
        claims = claims_by_id[expert["id"]]
        result = _check(expert, claims, lookup, tender, bid)
        entries.append(report.ExpertEntry(expert, claims, lookup, result, origin))
    return entries


def _audit_firm(bid: dict[str, Any], fresh: bool | None, errors: list[str]) -> tuple[Any, int]:
    full_time = firm.distinct_full_time_count(bid["experts"])
    try:
        records, note = firm.lookup_firm(bid, fresh=fresh)
    except crustdata.CrustdataError as exc:
        records, note = [], "The Crustdata firm lookup failed, so the firm was not compared."
        errors.append(f"firm: {exc}")
    result = firm.check_firm(records, full_time)
    result.lookup_note = note
    result.bid_findings = firm.rule_duplicate_experts(bid["experts"])
    return result, full_time


def run_audit(fresh: bool | None) -> int:
    bid = _load_json(BID_DIR / "bid.json")
    tender = _load_json(DATA_DIR / "tender.json")
    ledger_start = report.ledger_row_count()
    errors: list[str] = []

    # Read every CV first, so an unreadable CV stops the run before any paid lookup.
    claims_by_id: dict[str, Claims] = {}
    for expert in bid["experts"]:
        try:
            claims_by_id[expert["id"]] = _read_cv(expert, fresh)
        except (parse_cv.CvParseError, OSError) as exc:
            print(f"Could not read the CV for {expert['id']}: {exc}. No report was written.", file=sys.stderr)
            return 1

    entries = _audit_experts(bid, tender, claims_by_id, fresh, errors)
    firm_result, full_time = _audit_firm(bid, fresh, errors)

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    text = report.render_report(bid, tender, entries, firm_result, full_time, ledger_start, errors, timestamp)
    path = report.write_report(text)
    print(report.headline(entries, firm_result))
    print(f"Report written to {path} and {path.with_suffix('.html')}")
    for error in errors:
        print(f"Lookup failed: {error}", file=sys.stderr)
    return 0


def run_check_cv(path_text: str, position_key: str | None, profile_url: str | None, fresh: bool | None) -> int:
    """Check one unseen CV. With no position this is Career Check mode (SPEC 7b)."""
    path = Path(path_text)
    tender = _load_json(DATA_DIR / "tender.json")
    if position_key is not None and position_key not in tender["positions"]:
        known = ", ".join(tender["positions"])
        print(f"Unknown position {position_key!r}. Known positions: {known}", file=sys.stderr)
        return 2
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        claims = parse_cv.parse_cv(text, path.stem, position_key, fresh=fresh)
    except (parse_cv.CvParseError, OSError) as exc:
        print(f"Could not read the CV {path}: {exc}", file=sys.stderr)
        return 1

    try:
        lookup, origin = _lookup_url(profile_url, fresh) if profile_url else _no_url_lookup()
    except crustdata.CrustdataError as exc:
        lookup, origin = Lookup(status="not_found", reason="lookup_failed"), "failed"
        print(f"Crustdata lookup failed: {exc}", file=sys.stderr)

    position = tender["positions"][position_key] if position_key else None
    result = checker.check_expert(lookup, claims, position, None, None, today=config.TODAY)
    print(format_expert({"id": path.stem}, claims, result, origin))
    if claims.profile_url and not profile_url:
        print("Note: the CV lists a profile URL. It was not looked up; pass --profile-url to compare against it.")
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    expert = commands.add_parser("expert", help="check one expert from data/bid/bid.json")
    expert.add_argument("expert_id", metavar="ID")
    expert.add_argument("--fresh", action="store_true", help="skip reading the cache (paid call)")
    audit = commands.add_parser("audit", help="check every expert and the firm, and write out/report.md")
    audit.add_argument("--fresh", action="store_true", help="skip reading the cache (paid calls)")
    check_cv = commands.add_parser("check-cv", help="check one CV file (.md or .txt)")
    check_cv.add_argument("path")
    check_cv.add_argument("--position", help="key in data/tender.json; omit for Career Check mode")
    check_cv.add_argument("--profile-url", help="profile to compare against (1 paid credit)")
    check_cv.add_argument("--fresh", action="store_true", help="skip reading the cache")
    args = parser.parse_args(argv)
    fresh = True if args.fresh else None
    if args.command == "expert":
        return run_expert(args.expert_id, fresh)
    if args.command == "check-cv":
        return run_check_cv(args.path, args.position, args.profile_url, fresh)
    return run_audit(fresh)


if __name__ == "__main__":
    raise SystemExit(main())
