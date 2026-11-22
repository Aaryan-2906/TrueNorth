"""Find experts (preview): people whose public record appears to meet a tender position's minimums.

It only reads names, employers, titles and dates. It never asks for contact details and never contacts anyone.
"""

import math
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
CUSTOM_KEY = "custom"
DEFAULT_REGION = "India"
MAX_CUSTOM_WORDS = 10
MAX_CUSTOM_WORD_LENGTH = 60
MAX_YEARS = 50
_REGION = re.compile(r"[^\W\d_][\w .,'()&-]{0,59}")


class FinderInputError(ValueError):
    """A custom role's inputs are not usable. The message is safe to show."""


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


def _word_list(raw: Any, label: str) -> list[str]:
    """Comma-separated words or phrases: lowercase, single-spaced, without repeats."""
    if raw is None:
        return []
    if not isinstance(raw, str):
        raise FinderInputError(f"{label} must be text.")
    words = list(dict.fromkeys(" ".join(part.split()).lower() for part in raw.split(",") if part.strip()))
    if len(words) > MAX_CUSTOM_WORDS:
        raise FinderInputError(f"{label}: please list at most {MAX_CUSTOM_WORDS}.")
    for word in words:
        if len(word) > MAX_CUSTOM_WORD_LENGTH:
            raise FinderInputError(f"{label}: each entry must be at most {MAX_CUSTOM_WORD_LENGTH} characters.")
        if not any(character.isalnum() for character in word) or not word.isprintable():
            raise FinderInputError(f"{label}: each entry needs readable words.")
    return words


def _years_input(raw: Any, label: str) -> float:
    if raw is None or raw == "":
        return 0.0
    try:
        if isinstance(raw, bool):
            raise ValueError
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise FinderInputError(f"{label} must be a number from 0 to {MAX_YEARS}.") from exc
    if not math.isfinite(value) or not 0 <= value <= MAX_YEARS:
        raise FinderInputError(f"{label} must be a number from 0 to {MAX_YEARS}.")
    return value


def build_custom_position(custom: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """A position with the same shape as a tender position, and the region to search."""
    titles = _word_list(custom.get("titles"), "Role titles")
    if not titles:
        raise FinderInputError("Please enter at least one role title.")
    sectors = _word_list(custom.get("sectors"), "Sector words")
    minimum_total = _years_input(custom.get("min_total_years"), "Minimum total years")
    minimum_similar = _years_input(custom.get("min_similar_years"), "Minimum years in those titles")
    raw_region = custom.get("region")
    if raw_region is not None and not isinstance(raw_region, str):
        raise FinderInputError("Region must be text.")
    region = " ".join((raw_region or "").split()) or DEFAULT_REGION
    if not _REGION.fullmatch(region):
        raise FinderInputError("Region must be a place name, for example India.")
    position = {
        "name": f"Custom role: {', '.join(titles)}",
        "min_total_years": minimum_total,
        "min_similar_years": minimum_similar,
        "similar_title_keywords": titles,
        "sector_keywords": sectors,
    }
    return position, region


def search_body(
    position: dict[str, Any], region: str, limit: int, include_sector: bool = False
) -> dict[str, Any]:
    titles = [
        {"field": f"experience.employment_details.{group}.title", "type": "[.]", "value": keyword}
        for group in GROUPS
        for keyword in position["similar_title_keywords"]
    ]
    conditions: list[dict[str, Any]] = [
        {"field": "basic_profile.location.country", "type": "=", "value": region},
        {"op": "or", "conditions": titles},
    ]
    fields = list(SEARCH_FIELDS)
    sectors = position.get("sector_keywords") or []
    if include_sector and sectors:
        # Industries are a fixed list ("fintech" is not one), so a sector word may be in any of these.
        conditions.append(
            {
                "op": "or",
                "conditions": [
                    {"field": f"experience.employment_details.{group}.{name}", "type": "[.]", "value": word}
                    for group in GROUPS
                    for name in ("title", "name", "company_industries")
                    for word in sectors
                ],
            }
        )
        fields += [f"experience.employment_details.{group}.company_industries" for group in GROUPS]
    return {"filters": {"op": "and", "conditions": conditions}, "fields": fields, "limit": limit}


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
        ending = r"(?!\w)" if len(keyword) <= 2 else ""
        phrase = r"\s+".join(re.escape(word) for word in keyword.split())
        if re.search(rf"(?<!\w){phrase}{ending}", text, re.IGNORECASE):
            return keyword
    return None


def _job_industries(person: dict[str, Any]) -> dict[str, list[str]]:
    """Each job's company industries, by the same source label the Profile's jobs carry."""
    details = (person.get("experience") or {}).get("employment_details") or {}
    found: dict[str, list[str]] = {}
    for group in GROUPS:
        for index, raw in enumerate(details.get(group) or []):
            value = raw.get("company_industries")
            values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
            found[f"profile.{group}[{index}]"] = [item for item in values if isinstance(item, str)]
    return found


def sector_evidence(
    jobs: list[Job],
    similar_jobs: list[Job],
    keywords: list[str],
    industries: dict[str, list[str]] | None = None,
) -> tuple[Job, str] | None:
    """One job whose title, employer or company industry has a sector keyword, preferring a similar-capacity role."""
    others = [job for job in jobs if job not in similar_jobs]
    for job in similar_jobs + others:
        texts = [job.title, job.employer, *(industries or {}).get(job.source, [])]
        keyword = next((word for word in (_sector_word(text, keywords) for text in texts) if word), None)
        if keyword:
            return job, keyword
    return None


def assess(
    profile: Profile,
    position: dict[str, Any],
    today: tuple[int, int],
    industries: dict[str, list[str]] | None = None,
    same_job: bool = False,
) -> Candidate | None:
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
    # same_job: the sector must be on a job that also has one of the role titles.
    found = sector_evidence(similar_jobs if same_job else profile.jobs, similar_jobs, keywords, industries)
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
    custom_position: dict[str, Any] | None = None,
) -> FinderRun:
    """Search, then apply the same year and sector checks to a tender position or a custom one."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if custom_position is not None:
        position = custom_position
    else:
        positions = cli._load_tender()["positions"]
        if position_key not in positions:
            raise cli.InputError(f"Unknown position '{position_key}'. Known positions: {', '.join(positions)}.")
        position = positions[position_key]

    progress("looking_up", None, f"Searching public records in {region} for similar roles")
    body = search_body(position, region, limit, include_sector=custom_position is not None)
    reply = crustdata.post(SEARCH_PATH, body, billed=True, fresh=fresh)
    people = reply.get("profiles") if isinstance(reply, dict) else None
    if not isinstance(people, list) or not all(isinstance(person, dict) for person in people):
        raise crustdata.CrustdataError(_UNEXPECTED_REPLY)

    count = "1 record" if len(people) == 1 else f"{len(people)} records"
    progress("checking", None, f"Checking {count} against the minimums")
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
            same_job = custom_position is not None
            candidate = assess(profile, position, as_of, _job_industries(person), same_job) if profile else None
        except (ValidationError, AttributeError, TypeError) as exc:
            raise crustdata.CrustdataError(_UNEXPECTED_REPLY) from exc
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
    source = "the role's minimums" if run.position_key == CUSTOM_KEY else "the tender's minimums"
    rules = f"{source} and sector check" if run.position.get("sector_keywords") else source
    return {
        "ok": True,
        "kind": "finder",
        "headline": f"{kept} of {searched} public {noun} searched appear to meet {rules}",
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
