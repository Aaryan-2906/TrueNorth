# STEP 02 — Models + normaliser (11:15–11:50)

**Paste into Claude Code:**
```
Read CLAUDE.md, PROGRESS.md, docs/SPEC.md §3 and §4. This is STEP 02 only.

1. app/models.py: the pydantic models in SPEC §3 exactly (YearMonth can be a small model or a
   tuple-backed type with parsing from "YYYY-MM" and "YYYY-MM-DD"). Add RowResult (row_index,
   matched_source: str|None, outcome: "MATCHED"|code).
2. app/normalise.py:
   - normalise_company(name) -> (core, bracket_aliases) per SPEC §4.
   - same_employer(a, b) -> bool per SPEC §4.
   - months(start, end) inclusive; union_months(jobs, today) per SPEC §4.
   - profile_from_enrich(raw_item) -> Lookup: builds Profile from ONE item of the /person/enrich
     response (shape in docs/CRUSTDATA.md §1 and the real file in cache/). Each Job gets
     source like "profile.current[0]" / "profile.past[2]". Collapse duplicate jobs per SPEC §4.
3. tests/test_normalise.py:
   - normalise_company("U Projects Pvt. Ltd.") == normalise_company("U Projects")[core]
   - same_employer("W Infra", "W Infrastructure Private Limited (formerly W Infra)") is True
   - same_employer("V Consult", "Bidder Consult Pvt Ltd") is False
   - months((2009,7),(2013,5)) == 47 ; months((2022,3),(2026,11)) == 57
   - union of overlapping jobs counts months once
   - profile_from_enrich on the REAL cached response from step 01 returns a Profile whose job count
     equals current+past in the raw file (minus collapsed duplicates).

Do NOT write checker rules yet.

Done when: `uv run pytest -q` passes. Append PROGRESS.md, commit, print test output, stop.
```
**You check:** the test on the real cached file exists (not only synthetic tests).
