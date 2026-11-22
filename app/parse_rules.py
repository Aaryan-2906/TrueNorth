"""Deterministic reader for the standard NHAI key-personnel CV layout. No LLM.

It reads a CV only when it is confident. Anything unusual returns None, so the caller can
use the AI reader or report the layout as not recognised. It never guesses a value.
"""

import re
from typing import Literal

from app import config, dates
from app.dates import Problem
from app.models import Claims
from app.parse_cv import CvExtract, RowExtract, _not_in_cv, _to_claims
from app.parse_tender import position_key

_POSITION = re.compile(r"^\s*Proposed Position\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_NAME = re.compile(r"^\s*Name of (?:Staff|Expert)\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_RECORD_HEADING = re.compile(r"^\s*Employment Record\b.*$", re.IGNORECASE | re.MULTILINE)
_RECORD_END = re.compile(
    r"^\s*(?:Summary|Certification|Training|Computer|Languages|Education|Key Qualification|Declaration)\b",
    re.IGNORECASE,
)
_CLAIM = re.compile(
    r"\b(?:over|about|more than)\s+(\d{1,2}(?:\.\d+)?)\s*\+?\s*years?['’]?\s+(?:of\s+)?"
    r"(?:[\w-]+\s+){0,3}?experience\b(?!\s+as\b)",
    re.IGNORECASE,
)
_ANY_YEARS = re.compile(r"(?<![\d.])(\d{1,2}(?:\.\d+)?)\s*\+?\s*years?\b", re.IGNORECASE)
_SIMILAR_FIGURE = re.compile(
    r"similar capacity[^\n]*?\d+(?:\.\d+)?\s*\+?\s*years?|\d+(?:\.\d+)?\s*\+?\s*years?[^\n]*?similar capacity",
    re.IGNORECASE,
)
_PROFILE_URL = re.compile(r"https?://(?:[\w-]+\.)?linkedin\.com/in/[\w%-]+/?", re.IGNORECASE)
_SIGNATURE_DATE = re.compile(r"^(?:.*\bPlace\s*:.*?)?\bDate\s*:(.*)$", re.IGNORECASE | re.MULTILINE)
_NUMBERING = re.compile(r"^\s*\d{1,2}[.)]\s+")
_FROM_LINE = re.compile(r"^\s*From\s*:\s*(.+?)\s*$", re.IGNORECASE)
_EMPLOYER_LABEL = re.compile(r"^\s*(?:Name of employer|Employer)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_TITLE_LABEL = re.compile(r"^\s*(?:Position held|Designation)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_DURATION = re.compile(r"\bDuration\s*:\s*(.+?)\s*$", re.IGNORECASE)
_DESCRIPTION = re.compile(r"^\s*(?:Project|Duration|Activities|Client|Name of Work)\b[^:]*:", re.IGNORECASE)
_NO_CLEAR_EMPLOYER = re.compile(r"\b(?:Contractor|Authority|Consultant|Client)\s*:", re.IGNORECASE)
_DATE_TOKEN = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}/\d{4}|[A-Za-z]{3,9}\.?['\s-]*\d{2,4}\b")


def _split_range(text: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"(.+?)\s+to\s+(.+)", text.strip(), re.IGNORECASE)
    if match is None:
        return None
    start, end = match.group(1).strip(), match.group(2).strip()
    return (start, "to date") if end.lower() == "date" else (start, end)


def _dates_readable(start: str, end: str, today: tuple[int, int]) -> bool:
    """A real date problem is kept for the checker; text that is not a date at all is not ours to read."""
    parsed_start, parsed_end = dates.parse_cv_date(start, today), dates.parse_cv_date(end, today)
    if parsed_start == "present":
        return False
    return not any(isinstance(value, Problem) and value.kind == "unreadable" for value in (parsed_start, parsed_end))


def _row(dates_text: str, employer: str, title: str, today: tuple[int, int]) -> RowExtract | None:
    span = _split_range(dates_text)
    employer, title = employer.strip(), title.strip()
    if span is None or not employer or not title or _NO_CLEAR_EMPLOYER.search(employer):
        return None
    if not _dates_readable(span[0], span[1], today):
        return None
    return RowExtract(employer=employer, title=title, start_raw=span[0], end_raw=span[1], duration_raw=None)


def _looks_like_job_dates(line: str) -> bool:
    return bool(re.search(r"\bto\b", line, re.IGNORECASE)) and bool(_DATE_TOKEN.search(line))


def _with_duration(row: RowExtract, line: str) -> RowExtract | None:
    """Attach a Duration line to its row. A second Duration for one row is not ours to resolve."""
    match = _DURATION.search(line)
    if match is None:
        return row
    if row.duration_raw is not None:
        return None
    return row.model_copy(update={"duration_raw": match.group(1).rstrip(". ")})


def _pipe_rows(lines: list[str], today: tuple[int, int]) -> list[RowExtract] | None:
    rows: list[RowExtract] = []
    for line in lines:
        if "|" in line and not _DESCRIPTION.match(line):
            cells = _NUMBERING.sub("", line).split("|")
            row = _row(cells[0], cells[1], cells[2], today) if len(cells) == 3 else None
            if row is None:
                return None
            rows.append(row)
        elif _DESCRIPTION.match(line):
            if "duration" in line.lower():
                is_duration_line = re.match(r"\s*(?:Project|Duration)\b", line, re.IGNORECASE)
                if not rows or not is_duration_line:
                    return None
                updated = _with_duration(rows[-1], line)
                if updated is None:
                    return None
                rows[-1] = updated
        elif _looks_like_job_dates(line):
            return None
    return rows


def _block_row(dates_text: str, body: list[str], today: tuple[int, int]) -> RowExtract | None:
    employer = title = None
    plain: list[str] = []
    for line in body:
        employer_match, title_match = _EMPLOYER_LABEL.match(line), _TITLE_LABEL.match(line)
        if employer_match:
            employer = employer_match.group(1)
        elif title_match:
            title = title_match.group(1)
        elif _DESCRIPTION.match(line):
            continue
        elif ":" not in line and not line.lstrip().startswith(("-", "•", "*")):
            if _looks_like_job_dates(line):
                return None
            plain.append(line.strip())
    if (employer is None) != (title is None):
        return None
    if employer is None:
        if len(plain) < 2:
            return None
        employer, title = plain[0], plain[1]
    row = _row(dates_text, employer, title, today)
    if row is None:
        return None
    for line in body:
        if "duration" in line.lower():
            row = _with_duration(row, line)
            if row is None:
                return None
    return row


def _from_blocks(lines: list[str], today: tuple[int, int]) -> list[RowExtract] | None:
    starts = [index for index, line in enumerate(lines) if _FROM_LINE.match(line)]
    if not starts or any(_looks_like_job_dates(line) for line in lines[: starts[0]]):
        return None
    rows: list[RowExtract] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        dates_text = _FROM_LINE.match(lines[start]).group(1)
        row = _block_row(dates_text, lines[start + 1 : end], today)
        if row is None:
            return None
        rows.append(row)
    return rows


def _employment_rows(record: list[str], today: tuple[int, int]) -> list[RowExtract] | None:
    has_blocks = any(_FROM_LINE.match(line) for line in record)
    has_pipes = any("|" in line and not _DESCRIPTION.match(line) for line in record)
    if has_blocks == has_pipes:  # Neither layout, or both mixed together.
        return None
    return _from_blocks(record, today) if has_blocks else _pipe_rows(record, today)


def _claimed_years(header: str) -> float | None:
    """Only one clearly stated total is accepted. Any other figure beside it is ambiguous."""
    flat = " ".join(header.split())
    claims = {float(value) for value in _CLAIM.findall(flat)}
    others = {float(value) for value in _ANY_YEARS.findall(flat)}
    if len(claims) != 1 or others != claims:
        return None
    return claims.pop()


def _signature_date(text: str, today: tuple[int, int]) -> tuple[bool, str | None]:
    """Return (readable, raw date). A blank signature date is readable and has no value."""
    found: set[str] = set()
    for match in _SIGNATURE_DATE.finditer(text):
        value = match.group(1).strip()
        if not value.strip("_ .-"):
            continue
        if not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", value):
            return False, None
        if isinstance(dates.parse_cv_date(value, today), Problem):
            return False, None
        found.add(value)
    if len(found) > 1:
        return False, None
    return True, found.pop() if found else None


def read_extract(text: str, today: tuple[int, int] | None = None) -> CvExtract | None:
    """Read the CV's fields, or return None if the layout cannot be read with confidence."""
    today = today or config.TODAY
    if not _POSITION.search(text):
        return None
    name_match = _NAME.search(text)
    headings = list(_RECORD_HEADING.finditer(text))
    if name_match is None or len(headings) != 1 or _SIMILAR_FIGURE.search(text):
        return None
    name = name_match.group(1).split("(")[0].strip()
    if not name:
        return None

    heading = headings[0]
    claimed = _claimed_years(text[: heading.start()])
    if claimed is None:
        return None
    record: list[str] = []
    for line in text[heading.end() :].splitlines():
        if _RECORD_END.match(line):
            break
        if line.strip():
            record.append(line)
    rows = _employment_rows(record, today)
    if not rows:
        return None

    readable, cv_date = _signature_date(text, today)
    urls = {url.rstrip("/") for url in _PROFILE_URL.findall(text)}
    if not readable or len(urls) > 1:
        return None
    profile_url = _PROFILE_URL.search(text).group(0) if urls else None
    extract = CvExtract(
        name=name,
        claimed_total_years=claimed,
        claimed_similar_years=None,
        cv_date=cv_date,
        profile_url=profile_url,
        rows=rows,
    )
    return None if _not_in_cv(extract, text) else extract


def _position_from_cv(text: str) -> str | None:
    match = _POSITION.search(text)
    if match is None or match.group(1).strip().lower() in {"not specified", "none", "n/a"}:
        return None
    return position_key(match.group(1), set())


_FROM_CV: Literal["from_cv"] = "from_cv"


def parse_cv_rules(
    text: str,
    expert_id: str = "",
    proposed_position: str | None = _FROM_CV,
    today: tuple[int, int] | None = None,
) -> Claims | None:
    """Claims from the standard layout, or None. By default the position comes from the CV's own heading."""
    today = today or config.TODAY
    extract = read_extract(text, today)
    if extract is None:
        return None
    position = _position_from_cv(text) if proposed_position == _FROM_CV else proposed_position
    return _to_claims(extract, expert_id, position, today).model_copy(update={"read_by": "rules"})
