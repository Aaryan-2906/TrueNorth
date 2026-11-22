# PROGRESS (Claude Code appends one entry per step)

## Pre-event
- Kit prepared before the event: CLAUDE.md, docs/, prompts/, data/ (no code).

## Step 00: project setup
- **Built:** uv project on Python 3.11 with httpx, pydantic v2, python-dotenv and pytest, plus the settings module.
- **Files:** `pyproject.toml`, `uv.lock`, `.python-version`, `.env.example`, `app/__init__.py`, `app/config.py`, `tests/__init__.py`, `tests/test_config.py`. `.gitignore` already had the required entries.
- **Tests:** `uv run pytest -q` gives 1 passed.
- **Known gaps:** API keys in `.env` are still empty. No API client, models or other modules yet, by design.
- **How it works:**
  1. `app/config.py` loads `.env` from the repo root, and real environment variables take priority over it.
  2. It exposes the API keys, model name, `FRESH` flag and the date thresholds as plain constants.
  3. `TODAY` defaults to (2026, 11), and an env value must be `YYYY-MM` or startup fails with a clear error.

## Step 01: Crustdata client + smoke test
- **Built:** Crustdata client with response cache, per-run cache/live counter, paid-call ledger and safe retries, plus a smoke script.
- **Files:** `app/crustdata.py`, `scripts/smoke.py`, `credits.csv`, `docs/CRUSTDATA.md`.
- **Test results:** smoke test ran twice on one profile and `crustdata.com`. Run 1 made 2 live calls costing 1.2 credits. Run 2 served both from cache and `credits.csv` did not change. `uv run pytest -q` gives 1 passed.
- **Docs changes:** no field names differed. Job dates are full timestamps, and identify costs 0.2 credits and returns no `headcount`.
- **Known gaps:** retry, timeout and 5xx handling was checked offline with fake responses, not against real API failures. `account_credits()` is always live and is not counted as a lookup.
- **How it works:**
  1. Each lookup is hashed from its path and canonical JSON body, and a saved reply for that hash is reused, so we never pay twice unless `fresh` is on.
  2. Every live paid call is logged to `credits.csv`, and its raw reply is saved to `cache/` before anything reads it.
  3. Only 429s and connections that never opened are retried. A timeout or 5xx stops with a warning that the call may have been billed.

## Step 02: models + normaliser
- **Built:** the SPEC §3 pydantic models plus `RowResult`, with `proposed_position` optional, and company-name normalisation, month arithmetic and raw profile parsing.
- **Files:** `app/models.py`, `app/normalise.py`, `tests/test_normalise.py`.
- **Test results:** `uv run pytest -q` gives 14 passed. The real cached profile parses to 18 jobs (6 current, 12 past), no duplicates, 196 documented months.
- **Known gaps:** no checker rules yet, by design. The real-profile test skips on a clean clone because `cache/` is not committed. `DUPLICATE_ENTRY` is only recorded as `duplicates_collapsed` until the checker exists.
- **How it works:**
  1. Company names are lowercased and stripped of brackets, punctuation and legal words, so "U Projects Pvt. Ltd." and "U Projects" compare equal, and "(formerly X)" is remembered as an alias.
  2. Experience is the union of job months, so overlapping jobs count once, ongoing jobs end at TODAY, and jobs that cannot be placed are left out.
  3. Each raw Crustdata job becomes a `Job` that keeps its source, such as `profile.past[2]`. Missing or unreadable dates stay empty, so they are reported as "cannot check".

## Step 03: checker + golden tests
- **Built:** `check_expert` with one small pure function per rule in SPEC §5, §5b, §6 and §7b. The employer "starts with" rule now compares whole words, and SPEC §4 says so.
- **Files:** `app/checker.py`, `app/normalise.py`, `docs/SPEC.md`, `tests/test_golden.py`, `tests/test_normalise.py`.
- **Test results:** golden test failed first (no checker), then 17 of 17 golden cases passed. `uv run pytest -q` gives 33 passed. No golden file or expected value was edited, and no case contradicted the spec.
- **Demo data:** X1 `QUESTIONS` (all 5 rows match; 5 current roles outside the bidder). X2 `QUESTIONS` (seeded total, title and date all caught). X3 and X4 `CANNOT_CHECK`, with CV-only questions.
- **Rules I found ambiguous, and what I did:**
  1. §7b does not list `EXPERT_NOT_AT_FIRM` or `PROFILE_THIN` for no-position runs. Both still run because neither needs a tender.
  2. §7b's as-of rule sits under "Without a tender". It applies whenever `cv_date` is given, with or without a position.
  3. `CV_ROWS_OVERLAP` skips rows with no employer, because they cannot be called "different employers".
  4. `RowResult.outcome` holds one code. A row with several shows the first (dates before title), and all of them stay in `findings`.
  5. `CV_HEADER_BODY_MISMATCH` skips rows with a `date_problem`, following §5b's "left out of every row comparison".
  6. An empty title on either side raises no `ROW_TITLE_DIFFERS`, since a missing title is not a difference.
  7. Golden profiles contain uncollapsed duplicates, so `check_expert` collapses duplicates itself as well.
- **Known gaps:** §5b's "CV is undated" note and §7b's "report that this was done" belong to the report step, because `ExpertResult` has no notes field.
- **How it works:**
  1. CV-only rules always run first, so a CV gets questions even when no profile exists.
  2. Each valid CV row is matched to the closest profile job at the same employer, then its dates and title are compared.
  3. Expert rules compare documented months with the claims and tender, and the status comes only from which findings fired.

## Step 04: one expert end to end
- **Built:** `uv run python -m app.cli expert <ID> [--fresh]`, which loads the bid, claims and position, looks up the profile, runs the checker and prints a plain-text block. `Lookup` gained a `reason` field, so an expert with no URL reads "No profile URL was supplied, so no lookup was made" and keeps the code `NO_PROFILE_FOUND`.
- **Files:** `app/cli.py`, `app/models.py`, `app/checker.py`.
- **Test results:** runs for X1 (`QUESTIONS`), X2 (`QUESTIONS`), X3 (`CANNOT_CHECK`) and X4 (`CANNOT_CHECK`). A second X1 run came from cache and `credits.csv` did not change. `uv run pytest -q` gives 33 passed, with the golden helper and golden files unchanged.
- **Known gaps:** `--fresh` was tested offline with a fake API reply, not with a real paid call. No report file, firm check or CV parser yet, by design.
- **How it works:**
  1. The CLI finds the expert in `bid.json` and loads the hand-written claims and the tender position, or runs Career Check mode when the position is null.
  2. If there is a profile URL it calls Crustdata through the cache; if not, it records that no lookup was made instead of implying a search failed.
  3. It prints the status, documented vs claimed years, each CV row's outcome, each finding with its evidence, and whether the lookup was live or from cache.

## Step 05: firm check, committee questions, Markdown report
- **Built:** firm lookup and check, one question template per reason code, `out/report.md`, and `uv run python -m app.cli audit [--fresh]`. CV rows with no profile now read `NOT_COMPARED (no profile)`, and `NO_PROFILE_FOUND` stays at expert level.
- **Files:** `app/firm.py`, `app/questions.py`, `app/report.py`, `app/cli.py`, `app/checker.py`, `app/models.py`, `tests/test_firm_golden.py`, `tests/test_questions.py`.
- **Test results:** firm golden test failed first (no module), then 5 of 5 passed. All 17 expert golden cases still pass unchanged. `uv run pytest -q` gives 63 passed. `audit` made no paid call and `credits.csv` did not change.
- **Headline:** 4 experts: 0 consistent, 2 need clarification (5 questions), 2 cannot be checked.
- **Decisions:**
  1. The headline's question count covers only experts with status `QUESTIONS`. X3 and X4 still list their CV-only questions in their own sections.
  2. `ROW_TITLE_DIFFERS` says "for the same period" only when the dates agree. Otherwise it says "in its closest entry (dates)", so X2's question stays accurate.
  3. If a domain lookup returns 0, the firm is reported as not found. The one retry without a legal suffix applies to name lookups only.
  4. `audit --fresh` looks up a repeated URL once per run, so X1 and X2 cost 1 credit, not 2.
  5. A failed lookup is reported as failed and not compared, is not retried, and does not stop the rest of the audit.
- **Known gaps:** `audit --fresh` and the failed-lookup path were tested offline with fake API replies, not with real paid calls. X1 and X2 are the same person, so the firm check counts 2 full-time experts for one person. No HTML report or CV parser yet, by design.
- **How it works:**
  1. The firm check finds company records by domain or exact name and compares the largest headcount range with the experts the bid lists as full-time.
  2. Each finding carries its values, and a fixed template turns it into a numbered committee question. No LLM writes this wording.
  3. The report combines the headline, firm, per-expert tables, cited questions, a draft letter that is never sent, and a footer with credits and data freshness.

## Step 06a: date parser + bid duplicate check
- **Built:** `parse_cv_date` and `fill_row_dates`, a bid-level `DUPLICATE_EXPERT` question, a full-time count of distinct people, and a headline that totals questions across the whole bid.
- **Files:** `app/dates.py`, `app/firm.py`, `app/report.py`, `app/questions.py`, `app/models.py`, `app/cli.py`, `app/checker.py`, `tests/test_dates.py`, `tests/test_bid.py`, `tests/test_questions.py`.
- **Test results:** date tests failed first (no module), then all 29 golden date strings passed without editing the golden file. `fill_row_dates` also reproduces the dates in all 6 hand-written claims files. `uv run pytest -q` gives 108 passed. `audit` made no paid call.
- **Headline:** 4 experts: 0 consistent, 2 need clarification, 2 cannot be checked; 11 questions in total (including CV-only questions).
- **Decisions:**
  1. The total counts every question needing a bidder answer: 10 across experts and 1 bid-level. "No profile was available" statements are not counted.
  2. `DUPLICATE_EXPERT` sits in the "Firm and bid" section and does not change the firm's own status, because the firm's record is not what is in question.
  3. The same URL reads "point to the same person". The same name with no URL reads "may be the same person", because two people can share a name.
  4. `tests/test_questions.py` lists every reason code, so `DUPLICATE_EXPERT` was added to that list. No expected value changed.
  5. A blank date cell now reads `(blank)` in `CV_DATE_INVALID`, and golden results are unchanged.
- **Known gaps:** the parser accepts only the formats in SPEC §5c. Anything else is reported as unreadable rather than guessed. The CV parser (Part B) is not built yet.
- **How it works:**
  1. Each raw CV date is matched against a fixed list of formats, with day-first numeric dates and two-digit years resolved by the SPEC rule.
  2. A date that cannot be read, has no year, or falls after today becomes a named problem, so the row leaves all totals and raises a question.
  3. The bid check groups experts by profile URL, or by name when there is no URL, and counts each distinct person once.

## Suggestions (ideas noted but not built)
- SPEC §5c does not accept "12 March 2019", "12-03-2019" or "12.03.2019", so they are reported as unreadable. Real CVs may use them. Consider adding them before Part B.
- SPEC §4 collapses same-employer, same-date jobs even when titles differ, which could hide a title a CV row should match. Consider keeping both titles.
