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

## Suggestions (ideas noted but not built)
- Experts with no profile URL (X3, X4) get `NO_PROFILE_FOUND`, whose message says "not found in Crustdata", but no lookup was made for them. The CLI or report should say "no profile URL supplied; not looked up".
- SPEC §4 collapses same-employer, same-date jobs even when titles differ, which could hide a title a CV row should match. Consider keeping both titles.
