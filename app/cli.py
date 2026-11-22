"""Command line: uv run python -m app.cli expert <ID> [--fresh] | audit [--fresh]"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import checker, config, crustdata, firm, normalise, report
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


def _load_claims(expert_id: str) -> Claims:
    return Claims.model_validate(_load_json(BID_DIR / "claims" / f"{expert_id}.json"))


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
        f"Documented: {_years(result.documented_total_months)} vs claimed: {claims.claimed_total_years:.1f} years",
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

    claims = _load_claims(expert_id)
    tender = _load_json(DATA_DIR / "tender.json")
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
    bid: dict[str, Any], tender: dict[str, Any], fresh: bool | None, errors: list[str]
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
        claims = _load_claims(expert["id"])
        result = _check(expert, claims, lookup, tender, bid)
        entries.append(report.ExpertEntry(expert, claims, lookup, result, origin))
    return entries


def _audit_firm(bid: dict[str, Any], fresh: bool | None, errors: list[str]) -> tuple[Any, int]:
    full_time = sum(e.get("employment_with_bidder") == "full_time" for e in bid["experts"])
    try:
        records, note = firm.lookup_firm(bid, fresh=fresh)
    except crustdata.CrustdataError as exc:
        records, note = [], "The Crustdata firm lookup failed, so the firm was not compared."
        errors.append(f"firm: {exc}")
    result = firm.check_firm(records, full_time)
    result.lookup_note = note
    return result, full_time


def run_audit(fresh: bool | None) -> int:
    bid = _load_json(BID_DIR / "bid.json")
    tender = _load_json(DATA_DIR / "tender.json")
    ledger_start = report.ledger_row_count()
    errors: list[str] = []

    entries = _audit_experts(bid, tender, fresh, errors)
    firm_result, full_time = _audit_firm(bid, fresh, errors)

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    text = report.render_report(bid, tender, entries, firm_result, full_time, ledger_start, errors, timestamp)
    path = report.write_report(text)
    print(report.headline(entries))
    print(f"Report written to {path}")
    for error in errors:
        print(f"Lookup failed: {error}", file=sys.stderr)
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
    args = parser.parse_args(argv)
    fresh = True if args.fresh else None
    if args.command == "expert":
        return run_expert(args.expert_id, fresh)
    return run_audit(fresh)


if __name__ == "__main__":
    raise SystemExit(main())
