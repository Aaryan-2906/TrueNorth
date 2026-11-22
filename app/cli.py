"""Command line: uv run python -m app.cli expert <ID> [--fresh]"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app import checker, config, crustdata, normalise
from app.models import Claims, CvRow, ExpertResult, Lookup

DATA_DIR = config.ROOT_DIR / "data"
BID_DIR = DATA_DIR / "bid"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _lookup_expert(expert: dict[str, Any], fresh: bool | None) -> tuple[Lookup, str]:
    """Return the Lookup and a line saying where its data came from."""
    url = expert.get("profile_url")
    if not url:
        return Lookup(status="not_found", reason="no_profile_url"), "none (no profile URL supplied)"

    reply = crustdata.enrich_people([url], fresh=fresh)
    latest = crustdata.run_stats()["lookups"][-1]
    source = "live" if latest["source"] == "live" else "from cache"
    origin = f"{source} (fetched {latest['fetched_at']})"
    if len(reply) > 1:
        return Lookup(status="ambiguous", candidate_count=len(reply)), origin
    return normalise.profile_from_enrich(reply[0] if reply else {}), origin


def _years(months: int | None) -> str:
    return "not available" if months is None else f"{months / 12:.1f} years"


def _row_line(index: int, row: CvRow, result: ExpertResult) -> str:
    cite = f"cv.rows[{index}]"
    codes = [f.code for f in result.findings if f.code.startswith("ROW_") and cite in f.evidence]
    outcome = ", ".join(codes) or result.row_results[index].outcome
    employer = row.employer or "(employer unclear)"
    start = row.start_raw or row.start or "no start date"
    end = row.end_raw or row.end or "no end date"
    matched = result.row_results[index].matched_source
    suffix = f" [{matched}]" if matched else ""
    return f"  {index + 1}. {employer} | {row.title} | {start} to {end} -> {outcome}{suffix}"


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

    claims = Claims.model_validate(_load_json(BID_DIR / "claims" / f"{expert_id}.json"))
    tender = _load_json(DATA_DIR / "tender.json")
    key = expert.get("proposed_position")
    position = tender["positions"][key] if key is not None else None

    try:
        lookup, origin = _lookup_expert(expert, fresh)
    except crustdata.CrustdataError as exc:
        print(f"Crustdata lookup failed: {exc}", file=sys.stderr)
        return 1

    result = checker.check_expert(
        lookup,
        claims,
        position,
        bid["firm"]["name"],
        expert.get("employment_with_bidder"),
        today=config.TODAY,
    )
    print(format_expert(expert, claims, result, origin))
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    expert = commands.add_parser("expert", help="check one expert from data/bid/bid.json")
    expert.add_argument("expert_id", metavar="ID")
    expert.add_argument("--fresh", action="store_true", help="skip reading the cache (paid call)")
    args = parser.parse_args(argv)
    return run_expert(args.expert_id, True if args.fresh else None)


if __name__ == "__main__":
    raise SystemExit(main())
