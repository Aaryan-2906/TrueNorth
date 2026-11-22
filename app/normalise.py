"""Company-name normalisation, month arithmetic, and raw profile parsing (SPEC section 4)."""

import re
from typing import Any, Iterable

from app.models import Education, Job, Lookup, Profile, YearMonth

_BRACKETED = re.compile(r"[(\[{]([^)\]}]*)(?:[)\]}]|$)")
_ALIAS = re.compile(r"\b(?:formerly|presently|now)\b(?:\s+known\s+as)?[\s:.\-]*(.+)", re.IGNORECASE)
_PUNCTUATION = re.compile(r"[^\w\s]|_")
_LEGAL_TOKENS = {"pvt", "private", "ltd", "limited", "llp", "india", "co", "company", "group"}
_MIN_PREFIX_LENGTH = 4


def _clean(text: str) -> str:
    text = text.lower().replace("'", "").replace("’", "")
    tokens = _PUNCTUATION.sub(" ", text).split()
    return " ".join(token for token in tokens if token not in _LEGAL_TOKENS)


def normalise_company(name: str) -> tuple[str, list[str]]:
    """Return (core name, normalised names given after formerly/presently/now in brackets)."""
    aliases = []
    for bracket in _BRACKETED.findall(name):
        match = _ALIAS.search(bracket)
        if match:
            alias = _clean(match.group(1))
            if alias:
                aliases.append(alias)
    return _clean(_BRACKETED.sub(" ", name)), aliases


def same_employer(a: str, b: str) -> bool:
    core_a, aliases_a = normalise_company(a)
    core_b, aliases_b = normalise_company(b)
    if not core_a or not core_b:
        return False
    if core_a == core_b:
        return True
    if min(len(core_a), len(core_b)) >= _MIN_PREFIX_LENGTH and _is_word_prefix(core_a, core_b):
        return True
    return core_b in aliases_a or core_a in aliases_b


def _is_word_prefix(a: str, b: str) -> bool:
    """True if the shorter name's whole words are the first words of the longer name."""
    words_a, words_b = a.split(), b.split()
    shorter, longer = sorted((words_a, words_b), key=len)
    return longer[: len(shorter)] == shorter


def months(start: tuple[int, int], end: tuple[int, int]) -> int:
    """Inclusive month count between two (year, month) pairs."""
    return (end[0] - start[0]) * 12 + (end[1] - start[1]) + 1


def _month_index(value: tuple[int, int]) -> int:
    return value[0] * 12 + value[1] - 1


def union_months(jobs: Iterable[Job], today: tuple[int, int]) -> int:
    """Months covered by any placeable job, counting overlaps once and stopping at today."""
    last = _month_index(today)
    covered: set[int] = set()
    for job in jobs:
        if job.start is None:
            continue
        if job.end is not None:
            end = _month_index(job.end)
        elif job.is_current:
            end = last
        else:
            continue
        covered.update(range(_month_index(job.start), min(end, last) + 1))
    return len(covered)


def _parse_date(value: Any) -> YearMonth | None:
    """A missing or unreadable date stays None, so it is reported as cannot check."""
    if value is None:
        return None
    try:
        return YearMonth.parse(value)
    except ValueError:
        return None


def _is_duplicate(a: Job, b: Job) -> bool:
    # Undated jobs are never treated as duplicates: nothing shows they are the same job.
    return (
        a.start is not None
        and a.start == b.start
        and a.end == b.end
        and a.is_current == b.is_current
        and same_employer(a.employer, b.employer)
    )


def collapse_duplicates(jobs: Iterable[Job]) -> tuple[list[Job], list[tuple[str, str]]]:
    """Keep one of each duplicate job; return (kept jobs, (kept source, dropped source) pairs)."""
    kept: list[Job] = []
    collapsed: list[tuple[str, str]] = []
    for job in jobs:
        original = next((item for item in kept if _is_duplicate(item, job)), None)
        if original is None:
            kept.append(job)
        else:
            collapsed.append((original.source, job.source))
    return kept, collapsed


def _build_jobs(details: dict[str, Any]) -> tuple[list[Job], list[tuple[str, str]]]:
    jobs = [
        Job(
            employer=raw.get("name") or "",
            title=raw.get("title") or "",
            start=_parse_date(raw.get("start_date")),
            end=_parse_date(raw.get("end_date")),
            is_current=is_current,
            source=f"profile.{group}[{index}]",
        )
        for group, is_current in (("current", True), ("past", False))
        for index, raw in enumerate(details.get(group) or [])
    ]
    return collapse_duplicates(jobs)


def profile_from_enrich(raw_item: dict[str, Any]) -> Lookup:
    """Build a Lookup from one item of the /person/enrich response."""
    matches = raw_item.get("matches") or []
    if raw_item.get("match_status") != "matched" or not matches:
        return Lookup(status="not_found")
    if len(matches) > 1:
        return Lookup(status="ambiguous", candidate_count=len(matches))

    person = matches[0].get("person_data") or {}
    basic = person.get("basic_profile") or {}
    handles = person.get("social_handles") or {}
    network = handles.get("professional_network_identifier") or {}
    details = (person.get("experience") or {}).get("employment_details") or {}
    schools = (person.get("education") or {}).get("schools") or []

    jobs, collapsed = _build_jobs(details)
    education = [
        Education(
            school=school.get("school") or None,
            degree=school.get("degree") or None,
            field_of_study=school.get("field_of_study") or None,
        )
        for school in schools
    ]
    profile = Profile(
        person_id=person.get("crustdata_person_id"),
        name=basic.get("name"),
        profile_url=network.get("profile_url"),
        jobs=jobs,
        education=education,
        duplicates_collapsed=collapsed,
    )
    return Lookup(status="found", candidate_count=1, profile=profile)
