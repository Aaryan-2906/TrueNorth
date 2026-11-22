"""Deterministic CV date parsing (SPEC section 5c). No LLM. Unclear dates are never guessed."""

import calendar
import re
from dataclasses import dataclass
from typing import Any, Literal

from app.models import CvRow, YearMonth

_MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)
_MONTHS = {name: number for number, name in enumerate(_MONTH_NAMES, 1)}
_MONTHS.update({name[:3]: number for number, name in enumerate(_MONTH_NAMES, 1)})
_MONTHS["sept"] = 9

_PRESENT_WORDS = {"till date", "till now", "to date", "present", "current", "ongoing"}
_MONTH_YEAR = re.compile(r"([a-z]+)[\s.,'‘’`\"“”-]*(\d{4}|\d{2})")
_MONTH_ONLY = re.compile(r"([a-z]+)[\s.,]*")
_DAY_MONTH_YEAR = re.compile(r"(\d{1,2})([/.-])(\d{1,2})\2(\d{4})")
_DAY_MONTH_NAME_YEAR = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?[\s-]+([a-z]+)[\s.,-]*(\d{4})")
_MONTH_SLASH_YEAR = re.compile(r"(\d{1,2})/(\d{4})")
_NO_MONTH = re.compile(r"(?:\d{1,2}-)?\d{4}")
_DURATION = re.compile(r"(.+?)\s+(?:to|till|until|-|–|—)\s+(.+)", re.IGNORECASE)
_EARLIEST_YEAR = 1900


@dataclass(frozen=True)
class Problem:
    kind: str  # "no year", "no month", "after today", "end before start", "after cv_date", "unreadable"


ParsedDate = YearMonth | Literal["present"] | Problem


def _full_year(two_digits: str, today: tuple[int, int]) -> int:
    value = int(two_digits)
    return 2000 + value if value <= today[0] % 100 else 1900 + value


def _checked(year: int, month: int, today: tuple[int, int]) -> YearMonth | Problem:
    if not 1 <= month <= 12 or year < _EARLIEST_YEAR:
        return Problem("unreadable")
    value = YearMonth(year, month)
    return Problem("after today") if value > tuple(today) else value


def _checked_day(day: int, month: int, year: int, today: tuple[int, int]) -> YearMonth | Problem:
    """Day-first date: the day is only checked for validity, then dropped."""
    if not 1 <= month <= 12 or year < _EARLIEST_YEAR:
        return Problem("unreadable")
    if not 1 <= day <= calendar.monthrange(year, month)[1]:
        return Problem("unreadable")
    return _checked(year, month, today)


def parse_cv_date(raw: Any, today: tuple[int, int]) -> ParsedDate:
    """Parse one date string from a CV into a month, "present", or a Problem."""
    if not isinstance(raw, str) or not raw.strip():
        return Problem("unreadable")
    text = " ".join(raw.strip().lower().split())

    if re.sub(r"[\s.-]+", " ", text).strip() in _PRESENT_WORDS:
        return "present"

    match = _DAY_MONTH_YEAR.fullmatch(text)
    if match:
        return _checked_day(int(match.group(1)), int(match.group(3)), int(match.group(4)), today)

    match = _DAY_MONTH_NAME_YEAR.fullmatch(text)
    if match:
        if match.group(2) not in _MONTHS:
            return Problem("unreadable")
        return _checked_day(int(match.group(1)), _MONTHS[match.group(2)], int(match.group(3)), today)

    match = _MONTH_SLASH_YEAR.fullmatch(text)
    if match:
        return _checked(int(match.group(2)), int(match.group(1)), today)

    if _NO_MONTH.fullmatch(text):
        return Problem("no month")

    match = _MONTH_YEAR.fullmatch(text)
    if match and match.group(1) in _MONTHS:
        digits = match.group(2)
        year = int(digits) if len(digits) == 4 else _full_year(digits, today)
        return _checked(year, _MONTHS[match.group(1)], today)

    match = _MONTH_ONLY.fullmatch(text)
    if match and match.group(1) in _MONTHS:
        return Problem("no year")
    return Problem("unreadable")


def parse_duration(
    raw: str | None, today: tuple[int, int]
) -> tuple[YearMonth | None, YearMonth | Literal["present"] | None]:
    """Parse "<start> to <end>" from a Duration line. An unclear side stays None."""
    if not raw:
        return None, None
    text = re.sub(r"^\s*duration\s*:?\s*", "", raw, flags=re.IGNORECASE).strip()
    match = _DURATION.fullmatch(text)
    if match is None:
        return None, None
    end_text = match.group(2).strip()
    if end_text.lower().rstrip(".") in {"date", "now"}:
        end_text = "present"
    start, end = parse_cv_date(match.group(1), today), parse_cv_date(end_text, today)
    return (
        start if isinstance(start, YearMonth) else None,
        None if isinstance(end, Problem) else end,
    )


def fill_row_dates(
    row: CvRow, today: tuple[int, int], cv_date: tuple[int, int] | None = None
) -> CvRow:
    """Return a copy of the row with start, end and date_problem set from its raw text."""
    start = parse_cv_date(row.start_raw, today)
    end = parse_cv_date(row.end_raw, today)
    if start == "present":
        start = Problem("unreadable")

    problem = next((value.kind for value in (start, end) if isinstance(value, Problem)), None)
    start_value = start if isinstance(start, YearMonth) else None
    end_value = None if isinstance(end, Problem) else end

    if cv_date is not None:
        limit = tuple(cv_date)
        if start_value is not None and start_value > limit:
            start_value, problem = None, problem or "after cv_date"
        if isinstance(end_value, YearMonth) and end_value > limit:
            end_value, problem = None, problem or "after cv_date"

    if problem is None and isinstance(end_value, YearMonth) and start_value is not None and end_value < start_value:
        problem = "end before start"
    return row.model_copy(update={"start": start_value, "end": end_value, "date_problem": problem})
