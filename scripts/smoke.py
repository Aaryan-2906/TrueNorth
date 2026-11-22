"""Smoke test: uv run python scripts/smoke.py <profile-url> <domain-or-name>"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import crustdata  # noqa: E402


def summarise_person(reply: list) -> None:
    for item in reply:
        matches = item.get("matches") or []
        print(f"match_status: {item.get('match_status')}  matches: {len(matches)}")
        if not matches:
            continue
        person = matches[0].get("person_data") or {}
        jobs = (person.get("experience") or {}).get("employment_details") or {}
        current = jobs.get("current") or []
        past = jobs.get("past") or []
        print(f"current jobs: {len(current)}  past jobs: {len(past)}")
        first = (current or past or [None])[0]
        print(f"first job keys: {sorted(first) if first else 'no jobs'}")


def summarise_company(reply: list) -> None:
    matches = [match for item in reply for match in item.get("matches") or []]
    print(f"company matches: {len(matches)}")
    for match in matches:
        company = match.get("company_data") or {}
        info = company.get("basic_info") or {}
        line = f"  {info.get('name')} | employee_count_range: {info.get('employee_count_range')}"
        headcount = company.get("headcount")
        if isinstance(headcount, dict) and "total" in headcount:
            line += f" | headcount.total: {headcount['total']}"
        print(line)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    sys.stdout.reconfigure(encoding="utf-8")
    url, company = sys.argv[1], sys.argv[2]

    print(f"account credits: {crustdata.account_credits()}")
    summarise_person(crustdata.enrich_people([url]))
    if "." in company:
        summarise_company(crustdata.identify_company(domain=company))
    else:
        summarise_company(crustdata.identify_company(name=company))

    stats = crustdata.run_stats()
    print(f"lookups: {stats['live']} live, {stats['from_cache']} from cache")
    for item in stats["lookups"]:
        print(f"  {item['path']}: {item['source']}, fetched {item['fetched_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
