# STEP 00 — Repo setup (10:45–11:00)

**You do first (2 min):** clone the **organizer repository** you get at kickoff (all code must go there), copy in the kit's
`CLAUDE.md`, `docs/`, `data/`, `prompts/`, and an empty `PROGRESS.md`. Create `.env` with
`CRUSTDATA_API_KEY=...` (and `ANTHROPIC_API_KEY=...` if you have one). Make the first commit:
"Pre-event materials: specs, prompts, test data (declared)". Open Claude Code in the folder.

**Paste into Claude Code:**
```
Read CLAUDE.md and PROGRESS.md. This is STEP 00 only.

Set up the Python project:
- uv project, Python 3.11, dependencies: httpx, pydantic>=2, python-dotenv, pytest.
- Create app/__init__.py, app/config.py and tests/__init__.py. Nothing else in app/.
- config.py: load .env; expose CRUSTDATA_API_KEY, ANTHROPIC_API_KEY, ANTHROPIC_MODEL (default "claude-sonnet-5-5"), FRESH (bool from env, default false),
  TODAY = (2026, 11) overridable by env TODAY=YYYY-MM, DATE_TOLERANCE_MONTHS=3, CLAIM_GAP_MONTHS=24.
- .gitignore: .env, cache/, out/, .venv/, __pycache__/.
- .env.example with the variable names and empty values.
- tests/test_config.py with one test that TODAY parses from env.

Do NOT create the API client, models or any other module yet.

Done when: `uv run pytest -q` passes. Append to PROGRESS.md, commit "step 00: project setup", print the pytest output, and stop.
```
**You check:** pytest passes; `.env` is NOT in `git status`.
