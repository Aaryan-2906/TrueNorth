# STEP 01 — Crustdata client + smoke test (11:00–11:15)

**Paste into Claude Code:**
```
Read CLAUDE.md, PROGRESS.md and docs/CRUSTDATA.md. This is STEP 01 only.

Create app/crustdata.py:
- post(path, body, billed: bool) and get(path) using httpx, base URL and headers from docs/CRUSTDATA.md.
- Cache: before calling, compute sha1 of path + canonical JSON body (json.dumps(body, sort_keys=True, separators=(",",":"))); if cache/<path-slug>/<sha1>.json
  exists, return it without calling. After a successful call, write the raw JSON there first.
- fresh=True (or FRESH=1): skip reading the cache, always call the API, still write the cache.
- Keep a per-run counter of lookups served from cache vs live (with the cache file's timestamp), for the report.
- Ledger: append every non-cached billed call to credits.csv: iso time, path, sha1, x-credits-used header (blank if absent), status code.
- Retries: retry only on 429 (wait 5s, max 3) or a connection that never opened. Never retry billed
  calls after a timeout or 5xx; raise an error saying the call may have been billed.
- Functions: account_credits(), enrich_people(urls: list[str]) (fields exactly as in docs/CRUSTDATA.md §1),
  identify_company(domain: str|None, name: str|None) (domain if given, else names + exact_match true).

Then create scripts/smoke.py that:
1. prints account credits,
2. enriches ONE profile URL that I will give you as a command-line argument,
3. identifies ONE company domain or name I give as an argument,
and saves nothing except via the cache. Print a short summary: match_status, number of current and past
jobs, first job's keys, number of company matches and each match's name + employee_count_range.

Do NOT parse into models yet. Do NOT write the checker.

Done when: I run `uv run python scripts/smoke.py <url> <domain-or-name>` and it prints the summary;
running it twice makes no second paid call (credits.csv has no new line). Update docs/CRUSTDATA.md if any
real field name differs from what it says (show me the diff). Append PROGRESS.md, commit, stop.
```
**You run:** `uv run python scripts/smoke.py https://www.linkedin.com/in/<volunteer-X1> <firm domain or exact name>`
then run it again and check `credits.csv` didn't grow.
**If you get 403 on an endpoint:** stop and tell an organiser; ask which endpoints the event key allows (`GET /account/endpoints`).
