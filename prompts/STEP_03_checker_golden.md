# STEP 03 — Checker + golden tests (11:50–12:40)  ← the core; take the time

**Paste into Claude Code:**
```
Read CLAUDE.md, PROGRESS.md, docs/SPEC.md §4, §5, §5b, §6, §9, and data/golden/expert_cases.json.
This is STEP 03 only.

1. tests/test_golden.py FIRST: for every case in data/golden/expert_cases.json, build the Lookup and
   Claims from the JSON, load the position from data/tender.json (positions.<case.position>), call
   check_expert(lookup, claims, position, bidder_name, employment_with_bidder, today=(2026,11)), and assert:
   status == expected.status; set of all finding codes (expert + row level) == set(expected.codes);
   documented_total_months / documented_similar_months equal expected when given.
   Run it and show me it fails (no checker yet).
2. app/checker.py: implement check_expert as pure functions, one small function per rule in SPEC §5b
   (CV-only, run first, always), §5 and §6, each returning Finding objects with a factual message and evidence sources. No network,
   no LLM, no printing. Status precedence exactly as SPEC §6.
3. Make all 17 golden cases pass. Do NOT edit data/golden/*.json or the expected values. If you
   believe a golden case contradicts the spec, stop and quote both to me.

Done when: `uv run pytest -q` shows all golden cases passing (print the per-case results with -v).
Append PROGRESS.md (list any rule you found ambiguous), commit, stop.
```
**You check:** `uv run pytest tests/test_golden.py -v` → 17 passed. Glance at one message, e.g. E02
should read like "documented 10.9 years vs claimed 15" (not "lied").
**If stuck > 20 min on one case:** tell Claude Code to mark that one test `xfail` with the reason,
note it in PROGRESS.md, and move on. Fix it after the checkpoint.
