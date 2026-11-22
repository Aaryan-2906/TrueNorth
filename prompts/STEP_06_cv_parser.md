# STEP 06 — CV text → claims (1:40–2:15)
Part A (dates) is pure Python. Part B (LLM CV reader) is **required**: the rulebook says judges may bring
unseen inputs and nobody may type results in by hand during a run, so the service must read raw CV text.
Hand-written claims files are only the test answers for the parser.

**Paste into Claude Code (Part A):**
```
Read CLAUDE.md, PROGRESS.md, docs/SPEC.md §3 (CvRow, Claims) and §5c, and data/golden/date_strings.json.
This is STEP 06 Part A only.

1. tests/test_dates.py FIRST: every case in data/golden/date_strings.json (TODAY=2026-11). Run it, show it fails.
2. app/dates.py: parse_cv_date(raw, today) -> YearMonth | "present" | Problem(kind) per SPEC §5c, and
   fill_row_dates(row, today, cv_date) that sets start/end/date_problem from start_raw/end_raw
   (also "end before start", and "after cv_date" when cv_date is given). Pure, no LLM.
3. Make all date cases pass without editing the golden file.

Done when: tests pass. Append PROGRESS.md, commit "step 06a: date parser", stop.
```

**Paste into Claude Code (Part B):**
```
Read CLAUDE.md, PROGRESS.md, docs/SPEC.md §3 and §5c. This is STEP 06 Part B only.

Create app/parse_cv.py: parse_cv(text, expert_id, proposed_position) -> Claims.
- Anthropic API (add the anthropic dependency), model from config.ANTHROPIC_MODEL, temperature 0. The prompt asks ONLY for JSON with: name, claimed_total_years,
  claimed_similar_years (null if not stated), cv_date (raw text or null; look for the signature block, e.g.
  "Place: ... Date: 14/03/2021", or a dated header/footer), profile_url (or null), and rows with
  employer, title, start_raw, end_raw, duration_raw (the "Duration: ..." text in that row's description, or null).
  Prompt rules: copy employer, title and every date EXACTLY as written; do NOT convert dates; if the employer is
  not clearly stated (e.g. only Contractor/Authority/Consultant are named) set employer to null; never invent a
  row; "about 12 years" -> 12; anything not in the CV -> null.
- Then app/dates.py converts start_raw/end_raw/duration_raw/cv_date. The LLM never produces YearMonth values.
- Validate with pydantic; on failure retry once with the error, then raise.
- Cache LLM responses under cache/llm/<sha1>.json (so tests don't pay twice); --fresh bypasses it.
- tests/test_parse_cv.py: cvs/SAMPLE_real_format.md -> claims/SAMPLE_real_format.json and
  cvs/SAMPLE_modern_resume.md -> claims/SAMPLE_modern_resume.json (read each file's _note), plus every
  volunteer with both cvs/<id>.md and claims/<id>.json. Skip if no API key and no cache.
- cli: `audit` now ALWAYS reads cvs/<id>.md through parse_cv (hand-written claims are used only in tests).
  Add `uv run python -m app.cli check-cv <path-to-any-cv.md|.txt> [--position <key>] [--profile-url <url>]` for a single
  unseen CV; with no --position it runs Career Check mode (SPEC §7b), including the cv_date "as-of" rule.

Do NOT change checker.py.

Done when: parse tests pass (show diffs if any) and check-cv works on a CV file it has never seen. Append PROGRESS.md, commit "step 06b: CV parser", stop.
```
**You check:** if the parser disagrees with your hand-written JSON, read the CV and decide who's right.
