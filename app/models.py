"""Pydantic data models (SPEC section 3). Dates have month precision."""

import re
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel
from pydantic_core import core_schema

_DATE_PATTERN = re.compile(r"(\d{4})-(\d{2})(?:-\d{2}(?:T.*)?)?")


class YearMonth(NamedTuple):
    """A (year, month) pair, parsed from "YYYY-MM", "YYYY-MM-DD" or an ISO timestamp."""

    year: int
    month: int

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @classmethod
    def parse(cls, value: Any) -> "YearMonth":
        if isinstance(value, str):
            match = _DATE_PATTERN.fullmatch(value.strip())
            if match is None:
                raise ValueError(f"not a YYYY-MM date: {value!r}")
            year, month = int(match.group(1)), int(match.group(2))
        elif (
            isinstance(value, (tuple, list))
            and len(value) == 2
            and all(isinstance(part, int) and not isinstance(part, bool) for part in value)
        ):
            year, month = value
        else:
            raise ValueError(f"not a year-month: {value!r}")
        if not 1 <= month <= 12:
            raise ValueError(f"month out of range: {value!r}")
        return cls(year, month)

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            cls.parse, serialization=core_schema.plain_serializer_function_ser_schema(str)
        )


class Job(BaseModel):
    employer: str
    title: str
    start: YearMonth | None
    end: YearMonth | None  # None + is_current=True means ongoing
    is_current: bool
    source: str  # e.g. "profile.past[2]", used for citations


class Education(BaseModel):
    school: str | None
    degree: str | None
    field_of_study: str | None


class Profile(BaseModel):
    person_id: int | None
    name: str | None
    profile_url: str | None
    jobs: list[Job]
    education: list[Education]
    duplicates_collapsed: list[tuple[str, str]]  # (kept source, dropped source)


class CvRow(BaseModel):
    employer: str | None  # None if the CV doesn't make the employer clear
    title: str
    start_raw: str | None = None  # copied verbatim from the CV, e.g. "Sep'14"
    end_raw: str | None = None  # e.g. "Till now"
    start: YearMonth | None
    end: YearMonth | Literal["present"] | None
    date_problem: str | None = None  # "no year", "after today", "end before start", "unreadable"
    duration_start: YearMonth | None = None  # from a "Duration: ..." line, if any
    duration_end: YearMonth | Literal["present"] | None = None


class Claims(BaseModel):
    expert_id: str
    name: str
    proposed_position: str | None = None  # key in tender.json positions; None in Career Check mode
    claimed_total_years: float
    claimed_similar_years: float | None  # None if the CV doesn't state it
    cv_date: YearMonth | None = None  # date written on the CV; "present" means this date
    profile_url: str | None = None  # if the CV includes one (bid.json may override)
    rows: list[CvRow]


class Lookup(BaseModel):
    status: Literal["found", "not_found", "ambiguous"]
    candidate_count: int = 0
    profile: Profile | None = None


class Finding(BaseModel):
    code: str
    severity: Literal["question", "cannot_check", "info"]
    message: str  # factual, e.g. "documented 10.9 years vs claimed 15"
    evidence: list[str]  # sources, e.g. ["profile.past[1]", "cv.rows[2]"]


class RowResult(BaseModel):
    row_index: int
    matched_source: str | None
    outcome: str  # "MATCHED" or a finding code


class ExpertResult(BaseModel):
    expert_id: str
    status: Literal["CONSISTENT", "QUESTIONS", "CANNOT_CHECK"]
    documented_total_months: int | None
    documented_similar_months: int | None
    findings: list[Finding]
    row_results: list[RowResult]  # one per CV row
