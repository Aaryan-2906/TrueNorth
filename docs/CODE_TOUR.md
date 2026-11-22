# TrueNorth: code tour for the live fix and design questions

## 1. The path of one request (say this in 20 seconds)
CV text → `parse_cv.py` (Claude copies fields word for word; anything not in the CV is rejected) → `dates.py`
(plain code turns "Sep'14" into 2014-09) → `crustdata.py` (`/person/enrich`, cached, logged in credits.csv) →
`normalise.py` (profile → jobs, company-name matching, duplicates) → `checker.py` (all rules, pure Python) →
`questions.py` (fixed wording) → `report.py` / `server.py` (report, web page, Pip).
**The AI only reads. Every decision is in checker.py and firm.py, and the same input always gives the same answer.**

## 2. Which file does what
| File | Job | Key names inside |
|---|---|---|
| `app/config.py` | Settings and thresholds | `DATE_TOLERANCE_MONTHS = 3`, `CLAIM_GAP_MONTHS = 24`, `TODAY`, `ANTHROPIC_MODEL` |
| `app/crustdata.py` | Crustdata calls, cache, credit ledger, retries | `post()`, `enrich_people()`, `identify_company()`, `PERSON_FIELDS`, `HTTP_ERROR_MESSAGES`, `MAX_RETRIES` |
| `app/parse_cv.py` | Claude reads a CV into JSON; rejects anything not written in the CV | `SYSTEM_PROMPT`, `OUTPUT_SCHEMA`, `validate_extract()`, `parse_cv()` |
| `app/dates.py` | Date strings → year-month, or a "problem" | `parse_cv_date()`, `_PRESENT_WORDS`, the regexes at the top, `fill_row_dates()` |
| `app/normalise.py` | Company names, month maths, profile → jobs | `normalise_company()`, `same_employer()`, `_LEGAL_TOKENS`, `union_months()`, `collapse_duplicates()`, `profile_from_enrich()` |
| `app/checker.py` | **All expert rules** (one small function per rule) | `check_expert()` (start here), `_cv_rules()`, `_profile_rules()`, `_status()`, every `rule_...()` |
| `app/firm.py` | Firm and bid checks | `check_firm()`, `parse_range()`, `rule_duplicate_experts()`, `lookup_firm()` |
| `app/questions.py` | Code → committee question wording | `TEMPLATES` (one entry per code), `build_questions()` |
| `app/report.py` | report.md / report.html / letter | |
| `app/parse_tender.py` | Tender text → proposed rules (person approves) | |
| `app/cli.py` | Commands: audit, expert, check-cv, read-tender | |
| `app/server.py` | FastAPI: `/api/check-cv`, `/api/audit` (streams stages), report | `SERVICE_NAMES` |
| `static/index.html` | The page and Pip | |

## 3. How `check_expert()` works (checker.py, near the end)
1. `as_of` = the CV's own date if it has one, else today ("present" means that date).
2. `_cv_rules()` always runs first: date invalid → header vs Duration → rows overlap → employer unclear → claim vs listed years.
3. No profile? Add `NO_PROFILE_FOUND` (or `AMBIGUOUS_IDENTITY`) and stop. Rows show "NOT_COMPARED".
4. Otherwise: collapse duplicate jobs, keep jobs as of the CV date, count documented months (overlaps counted once),
   compare each CV row (`check_row()`), then `_profile_rules()`: thin profile, years shortfall, similar role,
   claim vs record, current role elsewhere, several current roles, not at firm, education, duplicates, junk profile.
5. `_status()`: any of `EXPERT_LEVEL_CANNOT_CHECK` → CANNOT_CHECK; any question → QUESTIONS; else CONSISTENT.

## 4. Likely live-fix requests and exactly where to change them
| Judge asks | Change | Then run |
|---|---|---|
| "Allow 6 months' date tolerance" | `config.py`: `DATE_TOLERANCE_MONTHS = 6` | `uv run pytest -q` (some golden cases may change: that's expected, explain why) |
| "Flag claims 1 year over, not 2" | `config.py`: `CLAIM_GAP_MONTHS = 12` | tests |
| "Overlaps of 1 month should count" | `checker.py`: `CV_OVERLAP_MONTHS = 1` | tests |
| "Support the date '2019/03'" (or another format) | `dates.py`: add a regex near the top and a branch in `parse_cv_date()`; add a case to `tests/test_dates.py` | `uv run pytest tests/test_dates.py -v` |
| "Treat 'Pvt. Ltd.' / 'LLP' / 'Inc' as the same company" | `normalise.py`: add the word to `_LEGAL_TOKENS` | `uv run pytest tests/test_normalise.py -v` |
| "Count 'Principal' or 'Lead' as the same title" | `checker.py`: `_TITLE_STOPWORDS` (words ignored when comparing titles) | tests |
| "Recognise 'MBA' / 'Ph.D' as postgraduate" | `checker.py`: the `_POSTGRADUATE` regex | tests |
| "Change a question's wording" | `questions.py`: the matching entry in `TEMPLATES` (never use fake/false/fraud/verified; a test checks this) | `uv run pytest tests/test_questions.py -v` |
| "Add a new rule" | `checker.py`: write `rule_new(...) -> list[Finding]` (copy a similar rule), call it in `_cv_rules()` or `_profile_rules()`; add its wording to `TEMPLATES` in questions.py (a test requires every code to have a template); add a test | tests |
| "Make a missing profile count as QUESTIONS, not CANNOT_CHECK" | `checker.py`: remove the code from `EXPERT_LEVEL_CANNOT_CHECK` | tests (golden cases will change; explain) |
| "Use another model" | `.env`: `ANTHROPIC_MODEL=...` | restart the server |
| "Fetch another Crustdata field" | `crustdata.py`: `PERSON_FIELDS`; read it in `normalise.py` `profile_from_enrich()` | tests |
| "Change UI text/colour" | `static/index.html` | refresh the page |

**The live-fix routine:** say what you'll change and why → edit one place → run the matching test file → run the full
`uv run pytest -q` → re-run the example in the UI → commit. If a golden case changes, say so out loud: "this case
was written to the old rule, so its expected answer changes, and here's why". Never silently edit golden files.

## 5. Design questions: short answers
- **Why not let the AI decide?** Not repeatable (our model doesn't even accept temperature 0), can't be unit-tested, and
  it invents dates. Reading is the AI's strength; judging is code's.
- **How do you stop the AI inventing data?** Strict JSON schema, and `validate_extract()` rejects any employer, title or
  date not found word for word in the CV; one retry, then the CV is reported as "could not be read".
- **How do you avoid paying twice?** Every request is hashed (sorted JSON) and cached; paid calls are never retried after a
  timeout or 5xx because they may have gone through; every paid call goes to credits.csv.
- **Same person, two names?** `same_employer()`: legal words stripped, whole-word prefixes, and "(formerly X)" aliases;
  duplicate jobs with the same dates are collapsed so months aren't double counted; the same person listed twice in a
  bid is flagged.
- **Missing data?** Always "cannot check", never "false". No profile isn't evidence of anything.
- **Prompt injection in a CV?** The CV is wrapped in tags and treated as data; tested with a planted "ignore previous
  instructions" line; and the verbatim check means injected text can't create facts.
- **What breaks it?** Sector isn't checked (an IT "Project Manager" counts toward a highway Team Leader); scanned PDFs;
  senior engineers often have thin profiles.
- **How would it scale?** Batch enrich (25 URLs per call), cache per request hash, and the checker is pure and fast.
