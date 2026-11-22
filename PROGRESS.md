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

## Suggestions (ideas noted but not built)
