# STEP 08 — Robustness: the cases judges try (2:30–3:30)

**Paste into Claude Code:**
```
Read CLAUDE.md, PROGRESS.md, docs/SPEC.md. This is STEP 08 only. Add tests for each item, then fix code.

1. Failed call resumes without double charge: simulate (monkeypatch httpx) a timeout on the 3rd expert of 4.
   The run must keep results for experts 1-2 (already cached), report expert 3 as
   "lookup failed - may have been billed, not retried", finish expert 4, and still write the report.
   Re-running afterwards must not re-bill experts 1-2.
2. `audit --fresh` works end to end with live calls, and the report shows 0 lookups from cache. A normal
   run shows which lookups were reused and when they were fetched.
3. Malformed input: a claims file with a bad date or an unknown position -> a clear error naming the file
   and field, not a stack trace. A bid with zero experts -> report says so.
4. Firm with several identify records -> FIRM_MULTIPLE_RECORDS shown with all ids and names.
5. A person-enrich response with 2 matches for one URL -> AMBIGUOUS_IDENTITY, report shows only the count.

Do NOT add new product features.

Done when: all tests pass; show me the list of new tests. Append PROGRESS.md, commit, stop.
```
**You do (around 3:30):** ask a mentor or another team: "Try to break it. Give me a CV or profile URL."
Note what broke for the README's "what didn't work".
