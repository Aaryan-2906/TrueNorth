"""Find experts (preview): people whose public record appears to meet a tender position's minimums.

It only reads names, employers, titles and dates. It never asks for contact details and never contacts anyone.
"""

import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app import checker, cli, config, crustdata, normalise
from app.models import Job, Profile

SEARCH_PATH = "/person/search"
DEFAULT_LIMIT = 10
CREDITS_PER_RESULT = 0.23  # Measured: a one-result search with these filters and fields cost 0.23 credits.
GROUPS = ("current", "past")
JOB_FIELDS = ("name", "title", "start_date", "end_date")
SEARCH_FIELDS = [
    "basic_profile.name",
    "social_handles.professional_network_identifier.profile_url",
    *(f"experience.employment_details.{group}.{name}" for group in GROUPS for name in JOB_FIELDS),
]
LABEL = "Candidates whose public record appears to meet the tender's rules. This is not a recommendation; the committee decides."
TITLE_NOTE = (
    "Titles and sector words are matched as text, so a role in another sector (for example IT infrastructure) can "
    "still appear. Public records can be incomplete or out of date."
)
_HONORIFICS = {"dr", "mr", "mrs", "ms", "er", "prof", "shri", "smt"}
_UNEXPECTED_REPLY = f"{SEARCH_PATH}: the reply had an unexpected shape."


@dataclass
class Candidate:
    profile: Profile
    total_months: int
    similar_months: int
    similar_jobs: list[Job]
    sector_job: Job | None = None
    sector_keyword: str | None = None


@dataclass
class FinderRun:
    position_key: str
    position: dict[str, Any]
    region: str
    searched: int
    total_matching: int | None
    candidates: list[Candidate]


def search_body(position: dict[str, Any], region: str, limit: int) -> dict[str, Any]:
    titles = [
        {"field": f"experience.employment_details.{group}.title", "type": "[.]", "value": keyword}
        for group in GROUPS
        for keyword in position["similar_title_keywords"]
    ]
    return {
        "filters": {
            "op": "and",
            "conditions": [
                {"field": "basic_profile.location.country", "type": "=", "value": region},
                {"op": "or", "conditions": titles},
            ],
        },
        "fields": SEARCH_FIELDS,
        "limit": limit,
    }


def initials(name: str | None) -> str:
    """'Daniel Ahmadizadeh' becomes 'D.A.'. Honorifics and bracketed text are left out."""
    text = re.sub(r"[(\[{].*?[)\]}]", " ", name or "")
    words = [word for word in re.findall(r"[^\W\d_]+", text) if word.casefold() not in _HONORIFICS]
    return "".join(f"{word[0].upper()}." for word in words[:3]) or "Name not listed"


def _profile(person: dict[str, Any]) -> Profile | None:
    lookup = normalise.profile_from_enrich({"match_status": "matched", "matches": [{"person_data": person}]})
    return lookup.profile


def _is_dated(job: Job) -> bool:
    return job.start is not None and (job.end is not None or job.is_current)


def _sector_word(text: str, keywords: list[str]) -> str | None:
    """The first keyword found at the start of a word. Two-letter keywords such as NH must be a whole word."""
    for keyword in keywords:
        ending = r"\b" if len(keyword) <= 2 else ""
        if re.search(rf"\b{re.escape(keyword)}{ending}", text, re.IGNORECASE):
            return keyword
    return None


def sector_evidence(jobs: list[Job], similar_jobs: list[Job], keywords: list[str]) -> tuple[Job, str] | None:
    """One job whose title or employer has a sector keyword, preferring a similar-capacity role."""
    others = [job for job in jobs if job not in similar_jobs]
    for job in similar_jobs + others:
        keyword = _sector_word(job.title, keywords) or _sector_word(job.employer, keywords)
        if keyword:
            return job, keyword
    return None


def assess(profile: Profile, position: dict[str, Any], today: tuple[int, int]) -> Candidate | None:
    """Documented months for one profile, or None if it falls short of a tender minimum."""
    total = normalise.union_months(profile.jobs, today)
    similar_jobs = [job for job in checker._similar_jobs(profile.jobs, position) if _is_dated(job)]
    similar = normalise.union_months(similar_jobs, today)
    minimum_total, minimum_similar = position["min_total_years"], position["min_similar_years"]
    if minimum_total is not None and total < minimum_total * 12:
        return None
    if minimum_similar is not None and similar < minimum_similar * 12:
        return None
    if not similar_jobs:  # With no minimum stated, a match still needs a dated similar role to show.
        return None
    keywords = position.get("sector_keywords") or []
    if not keywords:
        return Candidate(profile, total, similar, similar_jobs)
    found = sector_evidence(profile.jobs, similar_jobs, keywords)
    if found is None:
        return None
    return Candidate(profile, total, similar, similar_jobs, found[0], found[1])


def find_candidates(
    position_key: str,
    region: str = "India",
    limit: int = DEFAULT_LIMIT,
    fresh: bool | None = None,
    today: tuple[int, int] | None = None,
    progress: cli.Progress = cli._silent,
) -> FinderRun:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    positions = cli._load_tender()["positions"]
    if position_key not in positions:
        raise cli.InputError(f"Unknown position '{position_key}'. Known positions: {', '.join(positions)}.")
    position = positions[position_key]

    progress("looking_up", None, f"Searching public records in {region} for similar roles")
    reply = crustdata.post(SEARCH_PATH, search_body(position, region, limit), billed=True, fresh=fresh)
    people = reply.get("profiles") if isinstance(reply, dict) else None
    if not isinstance(people, list) or not all(isinstance(person, dict) for person in people):
        raise crustdata.CrustdataError(_UNEXPECTED_REPLY)

    count = "1 record" if len(people) == 1 else f"{len(people)} records"
    progress("checking", None, f"Checking {count} against the tender's minimums")
    as_of = today or config.TODAY
    candidates: list[Candidate] = []
    seen: set[Any] = set()
    for person in people:
        person_id = person.get("crustdata_person_id")
        if person_id is not None and person_id in seen:
            continue
        seen.add(person_id)
        try:
            profile = _profile(person)
        except (ValidationError, AttributeError, TypeError) as exc:
            raise crustdata.CrustdataError(_UNEXPECTED_REPLY) from exc
        candidate = assess(profile, position, as_of) if profile else None
        if candidate:
            candidates.append(candidate)
    candidates.sort(key=lambda item: (item.similar_months, item.total_months), reverse=True)
    total = reply.get("total_count")
    return FinderRun(position_key, position, region, len(people), total if isinstance(total, int) else None, candidates)


def _sector_view(item: Candidate) -> dict[str, str] | None:
    job = item.sector_job
    if job is None:
        return None
    return {
        "title": job.title,
        "employer": job.employer,
        "period": checker.job_period(job),
        "keyword": item.sector_keyword,
    }


def result_view(run: FinderRun) -> dict[str, Any]:
    """What the page shows. No profile URLs; an employer appears only on the sector evidence line."""
    kept, searched = len(run.candidates), run.searched
    noun = "record" if searched == 1 else "records"
    return {
        "ok": True,
        "kind": "finder",
        "headline": f"{kept} of {searched} public {noun} searched appear to meet the tender's minimums and sector check",
        "overall": None,
        "label": LABEL,
        "note": TITLE_NOTE,
        "position": {
            "key": run.position_key,
            "name": run.position.get("name", run.position_key),
            "min_total_years": run.position["min_total_years"],
            "min_similar_years": run.position["min_similar_years"],
        },
        "region": run.region,
        "searched": searched,
        "total_matching": run.total_matching,
        "candidates": [
            {
                "initials": initials(item.profile.name),
                "name": item.profile.name,
                "total_years": round(item.total_months / 12, 1),
                "similar_years": round(item.similar_months / 12, 1),
                "evidence": [{"title": job.title, "period": checker.job_period(job)} for job in item.similar_jobs],
                "sector_evidence": _sector_view(item),
            }
            for item in run.candidates
        ],
    }
