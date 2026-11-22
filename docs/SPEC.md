# SPEC — Tender Bid Audit

## 1. Input → output
**Inputs**
- `data/tender.json`: positions with minimum total years, minimum similar-role years, title
  keywords that count as "similar", and whether a postgraduate degree is required.
- `data/bid/bid.json`: bidding firm (name, domain if any, number of key experts it claims as
  full-time staff) and the list of experts (id, name, proposed position, profile URL if known,
  employment with bidder: `full_time` | `associate` | `intermittent`, path to CV).
- `data/bid/cvs/<id>.md`: CV text. `data/bid/claims/<id>.json`: structured claims (hand-written
  first; later produced by `parse_cv.py`).

**Output:** `out/report.md` and `out/report.html`, containing:
1. Summary table: one row per expert → status (`CONSISTENT` / `QUESTIONS` / `CANNOT_CHECK`) and codes.
2. Firm section: records found, headcount range, codes.
3. Per expert: documented vs claimed years, a CV-row table (each row: MATCHED / differs / not on
   profile / missing dates), and numbered **questions for the committee**, each citing its evidence.
4. Footer: what this tool cannot see, credits spent, data timestamp, "questions, not findings".

## 2. Pipeline
```
bid.json + tender.json
  -> for each expert: claims (claims/<id>.json, or parse_cv.py from cvs/<id>.md)
  -> lookup: profile URL -> POST /person/enrich      (1 credit)
             no URL      -> NO_PROFILE_FOUND for MVP (name search = stretch, SPEC §8)
  -> normalise.py: raw JSON -> Profile (jobs, education), dedupe
  -> checker.py: Profile + Claims + Position + bidder name -> ExpertResult
  -> firm.py: POST /company/identify (domain, else names+exact_match) -> FirmResult
  -> questions.py: codes -> templated questions with citations
  -> report.py: Markdown + HTML
```

## 3. Data models (pydantic)
Dates are month precision. Represent as `YearMonth` (year, month) or `date` set to day 1.
```python
class Job(BaseModel):
    employer: str
    title: str
    start: YearMonth | None
    end: YearMonth | None          # None + is_current=True => ongoing
    is_current: bool
    source: str                    # e.g. "profile.past[2]" — used for citations

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
    duplicates_collapsed: list[tuple[str, str]]   # (kept source, dropped source)

class CvRow(BaseModel):
    employer: str | None                  # None if the CV doesn't make the employer clear
    title: str
    start_raw: str | None = None          # copied verbatim from the CV by the parser, e.g. "Sep'14"
    end_raw: str | None = None            # e.g. "Till now"
    start: YearMonth | None               # filled by app/dates.py from start_raw (never by the LLM)
    end: YearMonth | Literal["present"] | None
    date_problem: str | None = None       # set by dates.py: "no year", "after today", "end before start", "unreadable"
    duration_start: YearMonth | None = None   # from a "Duration: ..." line in the row's description, if any
    duration_end: YearMonth | Literal["present"] | None = None

class Claims(BaseModel):
    expert_id: str
    name: str
    proposed_position: str                # key in tender.json positions
    claimed_total_years: float
    claimed_similar_years: float | None   # None if the CV doesn't state it
    cv_date: YearMonth | None = None      # date written on the CV, if any; "present" means this date
    profile_url: str | None = None        # if the CV includes one (bid.json may override)
    rows: list[CvRow]

class Lookup(BaseModel):
    status: Literal["found", "not_found", "ambiguous"]
    candidate_count: int = 0
    profile: Profile | None = None

class Finding(BaseModel):
    code: str
    severity: Literal["question", "cannot_check", "info"]
    message: str                           # factual, e.g. "documented 10.9 years vs claimed 15"
    evidence: list[str]                    # sources, e.g. ["profile.past[1]", "cv.rows[2]"]

class ExpertResult(BaseModel):
    expert_id: str
    status: Literal["CONSISTENT", "QUESTIONS", "CANNOT_CHECK"]
    documented_total_months: int | None
    documented_similar_months: int | None
    findings: list[Finding]
    row_results: list[RowResult]           # one per CV row: row index, matched job source, outcome
```

## 4. Month arithmetic and normalisation
- `TODAY` = 2026-11 (config; overridable). An ongoing job ends at TODAY's month.
- `months(start, end)` is inclusive: `(end.y - start.y)*12 + (end.m - start.m) + 1`.
- **Documented total months** = size of the union of all job intervals that have a start date
  (overlaps counted once). Jobs without a start date are excluded. A past job with a start but no
  end is excluded (cannot place it).
- **Documented similar months** = same union, restricted to jobs whose lowercased title contains
  any of the position's `similar_title_keywords`.
- Years for display = months / 12, rounded to 1 decimal. **Compare thresholds in months**
  (`min_years * 12`) to avoid rounding errors.
- **Company-name normalisation:** lowercase; remove bracketed text but remember it; remove
  punctuation; remove tokens `pvt private ltd limited llp india co company group`; collapse spaces.
- **Same employer** if normalised names are equal, OR one normalised name is a **word prefix** of the
  other, comparing whole words so "meta" is not a prefix of "metalworks" (both ≥ 4 characters), OR one's bracketed text (after "formerly"/"presently"/"now") normalises to
  the other.
- **Duplicate jobs:** same employer (rule above) AND same start AND same end → keep one, record the
  pair in `duplicates_collapsed`, add `DUPLICATE_ENTRY` (info). Never double count.

## 5. Checker rules — per CV row
Match each valid CV row (no date_problem, employer not None) to profile jobs at the same employer; if several, take the one with the closest
start date. Tolerance `DATE_TOLERANCE_MONTHS = 3`.

| Code | Severity | Fires when |
|---|---|---|
| `ROW_NOT_ON_PROFILE` | question | no profile job at that employer ("cannot confirm from profile", not "false") |
| `ROW_MISSING_DATES` | cannot_check | matched job has no start date (or past job with no end) |
| `ROW_DATES_DIFFER` | question | start differs by > tolerance, or end differs by > tolerance, or CV says "present" but profile job has ended (or vice versa) |
| `ROW_TITLE_DIFFERS` | question | title token sets share no token after lowercasing and removing stopwords `cum and of the sr senior jr junior` |
A row with none of these is `MATCHED`.

## 5b. CV-only rules (run first, always, even with no profile)
"present" in a CV row means `cv_date` if given, else TODAY. If `cv_date` is missing and any row
says present, the report adds the note "CV is undated; 'present' taken as today" (a note, not a code).
Rows with `date_problem` are left out of every year total and every overlap/row comparison.

| Code | Severity | Fires when |
|---|---|---|
| `CV_DATE_INVALID` | question | a row has `date_problem` (missing year, date after TODAY or after cv_date, end before start, unreadable). Message names the raw text, e.g. "row 6 end date '09/12/2097' is after today". |
| `CV_HEADER_BODY_MISMATCH` | question | a row has `duration_start`/`duration_end` and either differs from the row's start/end by > DATE_TOLERANCE_MONTHS |
| `CV_ROWS_OVERLAP` | question | two valid rows at different employers (§4 same-employer rule) overlap by > 3 months |
| `CV_EMPLOYER_UNCLEAR` | question | a row has employer None. The row still counts toward CV years but is skipped for profile matching. |
| `CLAIM_EXCEEDS_CV_ROWS` | question | see §6 (it is a CV-only rule too) |

## 5c. Date parsing (`app/dates.py`, deterministic; the LLM never converts dates)
- Accept: month names/abbreviations with or without dots ("Sept", "Dec.", "Apr."), glued
  ("January2012"), apostrophe years ("Sep'14", and the typo `April"11`), hyphens ("June-2019"),
  "MM/YYYY", and "DD/MM/YYYY" (Indian order: day first).
- Two-digit years: `yy <= TODAY.year % 100` → 20yy, else 19yy ("Mar.85" → 1985, "Sep'14" → 2014).
- "Till Date", "Till now", "to date", "Present", "Current", "Ongoing" → present.
- Return a problem string instead of a date for: empty text, month with no year ("April"), month
  > 12, a date after TODAY. Never guess.
- `data/golden/date_strings.json` lists real strings from CVs with expected results; tests must pass all.

## 6. Checker rules — per expert
Thresholds: `CLAIM_GAP_MONTHS = 24`.

| Code | Severity | Fires when |
|---|---|---|
| `CLAIM_EXCEEDS_CV_ROWS` | question | **CV-only rule, runs first and always (even with no profile).** claimed_total_years × 12 − (union of the CV's valid employment rows, "present" = cv_date or TODAY) > CLAIM_GAP_MONTHS. Message: "CV claims about N years; its employment record lists M years of jobs." |
| `NO_PROFILE_FOUND` | cannot_check | lookup.status == not_found. Stop: no profile-based rules run (CV-only rules still apply). |
| `AMBIGUOUS_IDENTITY` | cannot_check | lookup.status == ambiguous. Stop as above. Report only candidate_count. |
| `PROFILE_THIN` | cannot_check | fewer than 2 jobs have a start date. Then **do not** run the three year rules below. |
| `YEARS_SHORTFALL` | question | documented_total_months < position.min_total_years × 12 |
| `CLAIM_EXCEEDS_RECORD` | question | claimed_total_years × 12 − documented_total_months > CLAIM_GAP_MONTHS |
| `SIMILAR_ROLE_UNPROVEN` | question | documented_similar_months < min_similar_years × 12, OR (claimed_similar_years is not None AND claimed_similar_years × 12 − documented_similar_months > CLAIM_GAP_MONTHS) |
| `CURRENT_ROLE_ELSEWHERE` | question | any current job at an employer that is not the bidder (availability question) |
| `OVERLAPPING_CURRENT_ROLES` | question | current jobs at ≥ 2 different employers |
| `EXPERT_NOT_AT_FIRM` | question | bid says `full_time` and no current job is at the bidder |
| `EDUCATION_NOT_SHOWN` | question | position has `postgraduate: "required"` and no education entry's degree matches `m.tech mtech m.e. m.e master m.sc msc post graduate postgraduate pg` (case-insensitive, word-ish match). If the education list is empty, message says "no education listed on profile". |
| `POSTGRADUATE_PREFERENTIAL` | info | position has `postgraduate: "preferential"` and no degree matches the same list. A note on scoring, never a question. |
| `DUPLICATE_ENTRY` | info | see §4 |
| `IMPLAUSIBLE_PROFILE` | question | ≥ 2 current jobs at different employers whose titles contain CEO/COO/CFO/CTO/Chief/President/Board (founder/owner excluded) |

**Status:** `CANNOT_CHECK` if any `cannot_check` finding at expert level (NO_PROFILE_FOUND,
AMBIGUOUS_IDENTITY, PROFILE_THIN); else `QUESTIONS` if any `question` finding (expert or row
level) or any row-level `cannot_check` finding (ROW_MISSING_DATES becomes a request for dates);
else `CONSISTENT`. Info findings never change status. Report **all** findings that fired.

## 7. Firm check (`firm.py`)
- Lookup: `/company/identify` with `domains` if bid.json has a domain, else `names` with
  `exact_match: true`. If that returns 0, try ONCE more with exact_match and the name's legal suffix
  removed ("Pvt Ltd", "Private Limited", "Ltd", "LLP"), and say in the report that the second form was used.
  Never fall back to fuzzy matching. 0.2 credits per matched identifier.
- If the response has `headcount.total` (the handbook's example shows it), use it as (total, total).
  Otherwise use `basic_info.employee_count_range` (a string range such as `"201-500"`,
  `"2-10"`, `"10001+"`, `"myself only"`, or null). Parse to (low, high); `"myself only"` → (1, 1);
  `"10001+"` → (10001, ∞).

| Code | Severity | Fires when |
|---|---|---|
| `FIRM_NOT_FOUND` | cannot_check | 0 records |
| `FIRM_MULTIPLE_RECORDS` | info | > 1 record: list all (id, name, range); "possibly the same firm under several records"; use the largest range for the headcount check and say so |
| `FIRM_HEADCOUNT_UNKNOWN` | info | range is null for every record |
| `FIRM_HEADCOUNT_LOW` | question | range high < number of experts the bid lists as `full_time` |

Firm status uses the same precedence as experts.

## 7b. Without a tender (Career Check mode) and the "as-of" date
- The position is optional. With no position (e.g. a hiring CV, Builder Handbook theme 1.2 "every line of a
  CV checked against the record"), run §5b and §5 and the §6 rules that need no tender
  (CLAIM_EXCEEDS_RECORD, CURRENT_ROLE_ELSEWHERE only if a bidder/employer is given, OVERLAPPING_CURRENT_ROLES,
  IMPLAUSIBLE_PROFILE, DUPLICATE_ENTRY); skip YEARS_SHORTFALL, SIMILAR_ROLE_UNPROVEN, EDUCATION_NOT_SHOWN.
- If `cv_date` is given, judge the CV only against what the record showed then: ignore profile jobs that start
  after cv_date, and treat a job that ended after cv_date as current at cv_date. (Handbook: "built only from
  what the record showed before each hire".) Report that this was done.

## 8. Stretch (only after the 2:30 checkpoint, only if asked)
- Name search for experts with no URL: `/person/search` on name + bidder; 0 → NO_PROFILE_FOUND,
  1 → found, >1 → AMBIGUOUS_IDENTITY (count only).
- Conflict flag: expert worked at the procuring agency in the last 3 years.

## 9. Golden tests
`data/golden/expert_cases.json` and `data/golden/firm_cases.json` hold hand-built cases with
expected status, expected codes (the exact set of all codes that fire, expert-level and row-level,
including info codes) and, where given,
expected documented months. `tests/test_golden.py` must pass all of them. TODAY = 2026-11.
