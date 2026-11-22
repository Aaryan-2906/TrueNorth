"""Deterministic consistency rules (SPEC sections 5, 5b, 6, 7b). No network, no LLM, no printing."""

import re
from itertools import combinations
from typing import Any

from app.config import CLAIM_GAP_MONTHS, DATE_TOLERANCE_MONTHS
from app.models import (
    Claims,
    CvRow,
    ExpertResult,
    Finding,
    Job,
    Lookup,
    Profile,
    RowResult,
    YearMonth,
)
from app.normalise import collapse_duplicates, months, same_employer, union_months

CV_OVERLAP_MONTHS = 3
MIN_DATED_JOBS = 2
EXPERT_LEVEL_CANNOT_CHECK = {"NO_PROFILE_FOUND", "AMBIGUOUS_IDENTITY", "PROFILE_THIN"}

_TITLE_STOPWORDS = {"cum", "and", "of", "the", "sr", "senior", "jr", "junior"}
_TOP_ROLE = re.compile(r"\b(?:ceo|coo|cfo|cto|chief|president|board)\b", re.IGNORECASE)
_FOUNDER_OR_OWNER = re.compile(r"founder|owner", re.IGNORECASE)
_POSTGRADUATE = re.compile(
    r"(?<![a-z0-9.])(?:m\.tech|mtech|m\.e\.?|masters?|m\.sc|msc|post[\s-]?graduate|pg)(?![a-z0-9])",
    re.IGNORECASE,
)


def _finding(code: str, severity: str, message: str, evidence: list[str]) -> Finding:
    return Finding(code=code, severity=severity, message=message, evidence=evidence)


def _years(month_count: int) -> str:
    return f"{month_count / 12:.1f}"


def _gap(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(months(a, b) - 1)


def _resolve(value: Any, as_of: YearMonth) -> YearMonth | None:
    return as_of if value == "present" else value


def _cite(job: Job) -> str:
    end = "present" if job.is_current else job.end or "no end date"
    return f"{job.employer}, {job.title}, {job.start or 'no start date'} to {end}"


def _cv_jobs(claims: Claims, as_of: YearMonth) -> list[Job]:
    """Valid CV rows as Job intervals, with "present" resolved to the as-of month."""
    return [
        Job(
            employer=row.employer or "",
            title=row.title,
            start=row.start,
            end=_resolve(row.end, as_of),
            is_current=False,
            source=f"cv.rows[{index}]",
        )
        for index, row in enumerate(claims.rows)
        if row.date_problem is None and row.start is not None
    ]


def _jobs_as_of(jobs: list[Job], cv_date: YearMonth | None, as_of: YearMonth) -> list[Job]:
    """SPEC 7b: with a dated CV, keep only what the record showed at that date."""
    if cv_date is None:
        return jobs
    visible = []
    for job in jobs:
        if job.start is not None and job.start > as_of:
            continue
        if job.end is not None and job.end > as_of:
            job = job.model_copy(update={"end": None, "is_current": True})
        visible.append(job)
    return visible


def _distinct_employers(jobs: list[Job]) -> list[Job]:
    """One job per employer, using the same-employer rule."""
    distinct: list[Job] = []
    for job in jobs:
        if job.employer and not any(same_employer(job.employer, d.employer) for d in distinct):
            distinct.append(job)
    return distinct


# --- SPEC 5b: CV-only rules ---


def rule_cv_date_invalid(claims: Claims) -> list[Finding]:
    findings = []
    for index, row in enumerate(claims.rows):
        if row.date_problem is None:
            continue
        if row.start is None and row.start_raw:
            label, raw = "start date", row.start_raw
        elif row.end is None and row.end_raw:
            label, raw = "end date", row.end_raw
        else:
            label, raw = "dates", f"{row.start_raw} to {row.end_raw}"
        message = f"row {index + 1} {label} '{raw}' is {row.date_problem}"
        findings.append(_finding("CV_DATE_INVALID", "question", message, [f"cv.rows[{index}]"]))
    return findings


def rule_cv_header_body_mismatch(claims: Claims, as_of: YearMonth) -> list[Finding]:
    findings = []
    for index, row in enumerate(claims.rows):
        if row.date_problem is not None:
            continue
        pairs = (("start", row.start, row.duration_start), ("end", row.end, row.duration_end))
        for label, header, body in pairs:
            header_date, body_date = _resolve(header, as_of), _resolve(body, as_of)
            if header_date is None or body_date is None:
                continue
            if _gap(header_date, body_date) > DATE_TOLERANCE_MONTHS:
                message = f"row {index + 1} {label} is {header} in the header but {body} in its Duration text"
                findings.append(
                    _finding("CV_HEADER_BODY_MISMATCH", "question", message, [f"cv.rows[{index}]"])
                )
    return findings


def rule_cv_rows_overlap(cv_jobs: list[Job]) -> list[Finding]:
    findings = []
    dated = [job for job in cv_jobs if job.employer and job.end is not None]
    for a, b in combinations(dated, 2):
        if same_employer(a.employer, b.employer):
            continue
        start, end = max(a.start, b.start), min(a.end, b.end)
        if start > end:
            continue
        overlap = months(start, end)
        if overlap > CV_OVERLAP_MONTHS:
            message = f"CV rows at {a.employer} and {b.employer} overlap by {overlap} months"
            findings.append(_finding("CV_ROWS_OVERLAP", "question", message, [a.source, b.source]))
    return findings


def rule_cv_employer_unclear(claims: Claims) -> list[Finding]:
    return [
        _finding(
            "CV_EMPLOYER_UNCLEAR",
            "question",
            f"row {index + 1} ({row.title}) does not make the employer clear",
            [f"cv.rows[{index}]"],
        )
        for index, row in enumerate(claims.rows)
        if row.employer is None
    ]


def rule_claim_exceeds_cv_rows(claims: Claims, cv_jobs: list[Job], as_of: YearMonth) -> list[Finding]:
    listed = union_months(cv_jobs, as_of)
    if claims.claimed_total_years * 12 - listed <= CLAIM_GAP_MONTHS:
        return []
    message = (
        f"CV claims about {claims.claimed_total_years:g} years; "
        f"its employment record lists {_years(listed)} years of jobs."
    )
    evidence = ["cv.claimed_total_years"] + [job.source for job in cv_jobs]
    return [_finding("CLAIM_EXCEEDS_CV_ROWS", "question", message, evidence)]


# --- SPEC 5: per-row rules ---


def rule_row_not_on_profile(index: int, row: CvRow) -> Finding:
    message = f"cannot confirm from profile: no entry at {row.employer} (CV row {index + 1})"
    return _finding("ROW_NOT_ON_PROFILE", "question", message, [f"cv.rows[{index}]"])


def rule_row_missing_dates(index: int, job: Job) -> list[Finding]:
    if job.start is not None and (job.is_current or job.end is not None):
        return []
    message = f"cannot check CV row {index + 1}: profile entry has incomplete dates ({_cite(job)})"
    return [_finding("ROW_MISSING_DATES", "cannot_check", message, [f"cv.rows[{index}]", job.source])]


def _dates_differ(row: CvRow, job: Job) -> bool:
    if row.start is not None and _gap(row.start, job.start) > DATE_TOLERANCE_MONTHS:
        return True
    if row.end == "present":
        return not job.is_current
    if row.end is None:
        return False
    return job.is_current or _gap(row.end, job.end) > DATE_TOLERANCE_MONTHS


def rule_row_dates_differ(index: int, row: CvRow, job: Job) -> list[Finding]:
    if not _dates_differ(row, job):
        return []
    message = f"CV row {index + 1} says {row.start} to {row.end}; profile shows {_cite(job)}"
    return [_finding("ROW_DATES_DIFFER", "question", message, [f"cv.rows[{index}]", job.source])]


def _title_tokens(title: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", title.lower())) - _TITLE_STOPWORDS


def rule_row_title_differs(index: int, row: CvRow, job: Job) -> list[Finding]:
    cv_tokens, profile_tokens = _title_tokens(row.title), _title_tokens(job.title)
    if not cv_tokens or not profile_tokens or cv_tokens & profile_tokens:
        return []
    message = f"CV row {index + 1} title is '{row.title}'; profile shows {_cite(job)}"
    return [_finding("ROW_TITLE_DIFFERS", "question", message, [f"cv.rows[{index}]", job.source])]


def _closest_job(row: CvRow, candidates: list[Job]) -> Job:
    def distance(job: Job) -> float:
        if row.start is None or job.start is None:
            return float("inf")
        return _gap(row.start, job.start)

    return min(candidates, key=distance)


def check_row(index: int, row: CvRow, jobs: list[Job]) -> tuple[RowResult, list[Finding]]:
    candidates = [job for job in jobs if same_employer(row.employer, job.employer)]
    if not candidates:
        finding = rule_row_not_on_profile(index, row)
        return RowResult(row_index=index, matched_source=None, outcome=finding.code), [finding]

    job = _closest_job(row, candidates)
    findings = rule_row_missing_dates(index, job)
    if not findings:
        findings = rule_row_dates_differ(index, row, job)
    findings += rule_row_title_differs(index, row, job)
    outcome = findings[0].code if findings else "MATCHED"
    return RowResult(row_index=index, matched_source=job.source, outcome=outcome), findings


def _check_rows(claims: Claims, jobs: list[Job] | None, skip: str) -> tuple[list[RowResult], list[Finding]]:
    """Check each CV row. With no usable profile, rows take the outcome named by skip."""
    results: list[RowResult] = []
    findings: list[Finding] = []
    for index, row in enumerate(claims.rows):
        if row.date_problem is not None:
            results.append(RowResult(row_index=index, matched_source=None, outcome="CV_DATE_INVALID"))
        elif row.employer is None:
            results.append(RowResult(row_index=index, matched_source=None, outcome="CV_EMPLOYER_UNCLEAR"))
        elif jobs is None:
            results.append(RowResult(row_index=index, matched_source=None, outcome=skip))
        else:
            result, row_findings = check_row(index, row, jobs)
            results.append(result)
            findings += row_findings
    return results, findings


# --- SPEC 6: per-expert rules ---


def rule_no_profile_found(reason: str | None = None) -> Finding:
    if reason == "no_profile_url":
        message = "No profile URL was supplied, so no lookup was made; this is not evidence against the expert"
        return _finding("NO_PROFILE_FOUND", "cannot_check", message, ["bid.profile_url"])
    message = "not found in Crustdata; this is not evidence against the expert"
    return _finding("NO_PROFILE_FOUND", "cannot_check", message, ["crustdata.lookup"])


def rule_ambiguous_identity(candidate_count: int) -> Finding:
    message = f"{candidate_count} candidate profiles match; none was chosen"
    return _finding("AMBIGUOUS_IDENTITY", "cannot_check", message, ["crustdata.lookup"])


def rule_profile_thin(jobs: list[Job]) -> list[Finding]:
    dated = [job for job in jobs if job.start is not None]
    if len(dated) >= MIN_DATED_JOBS:
        return []
    message = f"cannot check years: only {len(dated)} of {len(jobs)} profile jobs have a start date"
    return [_finding("PROFILE_THIN", "cannot_check", message, [job.source for job in jobs] or ["profile.jobs"])]


def rule_years_shortfall(total: int, position: dict[str, Any], jobs: list[Job]) -> list[Finding]:
    minimum = position["min_total_years"]
    if total >= minimum * 12:
        return []
    message = f"documented {_years(total)} years vs tender minimum {minimum:g}"
    return [_finding("YEARS_SHORTFALL", "question", message, ["tender.min_total_years"] + [j.source for j in jobs])]


def rule_claim_exceeds_record(claims: Claims, total: int, jobs: list[Job]) -> list[Finding]:
    if claims.claimed_total_years * 12 - total <= CLAIM_GAP_MONTHS:
        return []
    message = f"documented {_years(total)} years vs claimed {claims.claimed_total_years:g}"
    return [_finding("CLAIM_EXCEEDS_RECORD", "question", message, ["cv.claimed_total_years"] + [j.source for j in jobs])]


def _similar_jobs(jobs: list[Job], position: dict[str, Any]) -> list[Job]:
    keywords = position["similar_title_keywords"]
    return [job for job in jobs if any(keyword in job.title.lower() for keyword in keywords)]


def rule_similar_role_unproven(
    claims: Claims, similar: int, position: dict[str, Any], similar_jobs: list[Job]
) -> list[Finding]:
    minimum = position["min_similar_years"]
    claimed = claims.claimed_similar_years
    below_minimum = similar < minimum * 12
    over_claimed = claimed is not None and claimed * 12 - similar > CLAIM_GAP_MONTHS
    if not below_minimum and not over_claimed:
        return []
    message = f"documented {_years(similar)} years in a similar role vs tender minimum {minimum:g}"
    if claimed is not None:
        message += f" and claimed {claimed:g}"
    sources = [job.source for job in similar_jobs] or ["profile.jobs"]
    return [_finding("SIMILAR_ROLE_UNPROVEN", "question", message, ["tender.min_similar_years"] + sources)]


def rule_current_role_elsewhere(jobs: list[Job], bidder_name: str | None) -> list[Finding]:
    if not bidder_name:
        return []
    elsewhere = [job for job in jobs if job.is_current and not same_employer(job.employer, bidder_name)]
    if not elsewhere:
        return []
    listed = "; ".join(_cite(job) for job in elsewhere)
    message = f"profile shows a current role outside {bidder_name}: {listed}"
    return [_finding("CURRENT_ROLE_ELSEWHERE", "question", message, [job.source for job in elsewhere])]


def rule_overlapping_current_roles(jobs: list[Job]) -> list[Finding]:
    current = [job for job in jobs if job.is_current]
    employers = _distinct_employers(current)
    if len(employers) < 2:
        return []
    message = f"profile shows current roles at {len(employers)} different employers"
    return [_finding("OVERLAPPING_CURRENT_ROLES", "question", message, [job.source for job in current])]


def rule_expert_not_at_firm(
    jobs: list[Job], bidder_name: str | None, employment_with_bidder: str | None
) -> list[Finding]:
    if employment_with_bidder != "full_time" or not bidder_name:
        return []
    current = [job for job in jobs if job.is_current]
    if any(same_employer(job.employer, bidder_name) for job in current):
        return []
    message = f"bid lists the expert as full-time at {bidder_name}; profile shows no current role there"
    return [_finding("EXPERT_NOT_AT_FIRM", "question", message, ["bid.employment_with_bidder"] + [j.source for j in current])]


def rule_education_not_shown(profile: Profile, position: dict[str, Any]) -> list[Finding]:
    if not position.get("requires_postgraduate"):
        return []
    if any(_POSTGRADUATE.search(entry.degree or "") for entry in profile.education):
        return []
    if not profile.education:
        message = "no education listed on profile"
    else:
        message = "tender requires a postgraduate degree; none is shown in the profile's education"
    return [_finding("EDUCATION_NOT_SHOWN", "question", message, ["tender.requires_postgraduate", "profile.education"])]


def rule_duplicate_entries(pairs: list[tuple[str, str]]) -> list[Finding]:
    return [
        _finding(
            "DUPLICATE_ENTRY",
            "info",
            f"{dropped} repeats {kept}; counted once",
            [kept, dropped],
        )
        for kept, dropped in pairs
    ]


def rule_implausible_profile(jobs: list[Job]) -> list[Finding]:
    top_roles = [
        job
        for job in jobs
        if job.is_current and _TOP_ROLE.search(job.title) and not _FOUNDER_OR_OWNER.search(job.title)
    ]
    employers = _distinct_employers(top_roles)
    if len(employers) < 2:
        return []
    message = f"profile shows top roles at {len(employers)} different employers at the same time"
    return [_finding("IMPLAUSIBLE_PROFILE", "question", message, [job.source for job in top_roles])]


def _status(findings: list[Finding]) -> str:
    if any(finding.code in EXPERT_LEVEL_CANNOT_CHECK for finding in findings):
        return "CANNOT_CHECK"
    if any(finding.severity in {"question", "cannot_check"} for finding in findings):
        return "QUESTIONS"
    return "CONSISTENT"


def _cv_rules(claims: Claims, as_of: YearMonth) -> list[Finding]:
    cv_jobs = _cv_jobs(claims, as_of)
    return (
        rule_cv_date_invalid(claims)
        + rule_cv_header_body_mismatch(claims, as_of)
        + rule_cv_rows_overlap(cv_jobs)
        + rule_cv_employer_unclear(claims)
        + rule_claim_exceeds_cv_rows(claims, cv_jobs, as_of)
    )


def _profile_rules(
    claims: Claims,
    profile: Profile,
    jobs: list[Job],
    pairs: list[tuple[str, str]],
    position: dict[str, Any] | None,
    bidder_name: str | None,
    employment_with_bidder: str | None,
    total: int,
    similar: int | None,
    similar_jobs: list[Job],
) -> list[Finding]:
    thin = rule_profile_thin(jobs)
    findings = list(thin)
    if not thin:
        if position is not None:
            findings += rule_years_shortfall(total, position, jobs)
            findings += rule_similar_role_unproven(claims, similar, position, similar_jobs)
        findings += rule_claim_exceeds_record(claims, total, jobs)
    findings += rule_current_role_elsewhere(jobs, bidder_name)
    findings += rule_overlapping_current_roles(jobs)
    findings += rule_expert_not_at_firm(jobs, bidder_name, employment_with_bidder)
    if position is not None:
        findings += rule_education_not_shown(profile, position)
    findings += rule_duplicate_entries(pairs)
    findings += rule_implausible_profile(jobs)
    return findings


def check_expert(
    lookup: Lookup,
    claims: Claims,
    position: dict[str, Any] | None,
    bidder_name: str | None,
    employment_with_bidder: str | None,
    today: tuple[int, int],
) -> ExpertResult:
    """Run every rule for one expert and return all findings that fired."""
    current_month = YearMonth(*today)
    as_of = min(claims.cv_date, current_month) if claims.cv_date else current_month
    findings = _cv_rules(claims, as_of)

    if lookup.status != "found" or lookup.profile is None:
        if lookup.status == "ambiguous":
            stop = rule_ambiguous_identity(lookup.candidate_count)
        else:
            stop = rule_no_profile_found(lookup.reason)
        row_results, _ = _check_rows(claims, None, stop.code)
        return ExpertResult(
            expert_id=claims.expert_id,
            status=_status(findings + [stop]),
            documented_total_months=None,
            documented_similar_months=None,
            findings=findings + [stop],
            row_results=row_results,
        )

    profile = lookup.profile
    kept, new_pairs = collapse_duplicates(profile.jobs)
    pairs = list(profile.duplicates_collapsed) + new_pairs
    jobs = _jobs_as_of(kept, claims.cv_date, as_of)

    total = union_months(jobs, as_of)
    similar_jobs = _similar_jobs(jobs, position) if position is not None else []
    similar = union_months(similar_jobs, as_of) if position is not None else None

    row_results, row_findings = _check_rows(claims, jobs, "")
    findings += row_findings
    findings += _profile_rules(
        claims, profile, jobs, pairs, position, bidder_name, employment_with_bidder, total, similar, similar_jobs
    )
    return ExpertResult(
        expert_id=claims.expert_id,
        status=_status(findings),
        documented_total_months=total,
        documented_similar_months=similar,
        findings=findings,
        row_results=row_results,
    )
