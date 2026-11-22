# STEP 05 — All experts + firm check + Markdown report (1:00–1:40)

**Paste into Claude Code:**
```
Read CLAUDE.md, PROGRESS.md, docs/SPEC.md §1, §7 and data/golden/firm_cases.json. This is STEP 05 only.

1. tests/test_firm_golden.py FIRST (all 5 firm cases), then app/firm.py: parse_range(), check_firm(records,
   full_time_count) per SPEC §7, and lookup_firm(bid) using crustdata.identify_company. Make the firm
   golden tests pass without editing them.
2. app/questions.py: one template per reason code turning a Finding into a numbered committee question.
   Examples of tone (write the rest in the same style):
   - YEARS_SHORTFALL: "The tender asks for at least {min} years. The expert's public profile documents
     {doc} years ({first_job} to today). Please ask the bidder for evidence of the remaining experience."
   - CURRENT_ROLE_ELSEWHERE: "The profile shows a current role as {title} at {employer} since {start}.
     Please confirm the expert's availability for the full assignment and their employment with the bidder."
   - ROW_MISSING_DATES: "The profile lists {employer} without dates, so this CV row cannot be checked.
     Please ask for an employment certificate."
   - NO_PROFILE_FOUND: "No public profile was found, so nothing could be checked. This is not evidence
     against the expert."
   - CV_DATE_INVALID: "Row {n} of the CV gives the date '{raw}', which {problem}. It was left out of all totals.
     Please ask the bidder to correct it." (likely a typing error; say so neutrally, never imply intent)
   Never use the words: fake, false, lied, fraud, verified. Add a test that scans every template for them.
3. app/report.py: build out/report.md per SPEC §1 (a one-line headline with counts, e.g. "4 experts: 1 consistent,
   2 need clarification (5 questions), 1 cannot be checked" — counts only, no percentage score; summary table, firm section, per-expert section with
   CV-row table and questions with evidence, the note "CV is undated; 'present' taken as today" where SPEC §5b says, footer with limits + credits from credits.csv + timestamp +
   "These are questions for the committee, not findings.").
   At the end of report.md, a plain "Draft clarification letter to the bidder" listing the questions by expert,
   for the committee to copy if they choose. It is text in the report only; nothing is ever sent.
4. cli: `uv run python -m app.cli audit [--fresh]` runs every expert + firm and writes out/report.md.
   The report footer states: lookups live vs reused from cache (with fetch times).

Do NOT build HTML or the CV parser yet.

Done when: tests pass; `audit` writes out/report.md; show me the summary table. Append PROGRESS.md, commit, stop.
```
**You check:** open out/report.md. Every question must point to a profile entry or CV row.
