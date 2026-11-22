# Crustdata notes for Tender Bid Audit (API version 2025-11-01)

Shapes below come from Crustdata's own sample agent (`candidate-verification-agent`) and from test
calls made on 19–20 Nov 2026. **Step 01 (smoke test) confirms them with your event key** and saves
real responses in `cache/`. If anything differs, the cached response wins: update this file.

## Base
```
POST https://api.crustdata.com/<path>
authorization: Bearer $CRUSTDATA_API_KEY
x-api-version: 2025-11-01
content-type: application/json
```
Free checks: `GET /account/credits` (balance), `GET /account/endpoints` (what this key may use).
Each response may carry an `x-credits-used` header: log it to `credits.csv`.

## 1. Expert profile — `POST /person/enrich` (1 credit per person found; free on a miss)
Up to 25 profile URLs per call; 15 calls/minute.
```json
{
  "professional_network_profile_urls": ["https://www.linkedin.com/in/<handle>"],
  "fields": ["basic_profile", "experience", "education", "social_handles"]
}
```
Response: a list, one item per URL:
```
[{ "matched_on": "<url>",
   "match_status": "matched" | "not_found" | ...,
   "matches": [{ "confidence_score": 0.9,
                 "person_data": {
                    "crustdata_person_id": 123,
                    "basic_profile": {"name": "...", "current_title": "...", "headline": "..."},
                    "experience": {"employment_details": {
                        "current": [{"title": "...", "name": "<employer>", "start_date": "2019-01-01", "end_date": null}],
                        "past":    [{"title": "...", "name": "<employer>", "start_date": "...", "end_date": "..."}]}},
                    "education": {"schools": [{"school": "...", "degree": "...", "field_of_study": "...", "start_year": 2005, "end_year": 2007}]},
                    "social_handles": {"professional_network_identifier": {"profile_url": "..."}}
                 }}]}]
```
- Employer name is `name` (not `company_name`) in responses. Dates are ISO strings or null.
- **Confirmed 22 Nov (Step 01):** real job dates are full timestamps like `2019-01-01T00:00:00+00:00`, not
  `2019-01-01`. Current jobs had `end_date: null`. School `start_year`/`end_year` can be null.
- `match_status != "matched"` or empty `matches` → `NO_PROFILE_FOUND`.
- More than one match for one URL → `AMBIGUOUS_IDENTITY` (should be rare for a URL).
- Do not request emails/phones: not needed, extra cost, and against our no-contact rule.

## 2. Firm — `POST /company/identify` (0.2 credits per matched identifier; unmatched free)
The Builder Handbook (p22) says identify is **free** for event keys and its example reply includes
`headcount.total`; the public rate card says 0.2 credits/match. Log `x-credits-used` and trust the ledger.
**Confirmed 22 Nov (Step 01):** a matched domain lookup with the event key cost **0.2 credits**
(`x-credits-used: 0.2`), and its reply had **no `headcount` field**. Use `basic_info.employee_count_range`.
```json
{"domains": ["example.com"]}
```
or, with no domain:
```json
{"names": ["Exact Firm Name Pvt Ltd"], "exact_match": true}
```
One identifier type per call, up to 25 values.
Response: `[{matched_on, match_type, matches: [{confidence_score, company_data: {crustdata_company_id, basic_info: {name, primary_domain, employee_count_range, industries, year_founded, ...}}}]}]`

**Seen on 19 Nov:** a name lookup **without** `exact_match` for an Indian highway consultancy
returned 25 matches. All had confidence 0.6, most were unrelated firms in other countries, and two
records were plausibly the same Indian firm (one with range `"myself only"`, one with no range).
So:
- never take the first fuzzy hit; use a domain or `exact_match: true`;
- several records → `FIRM_MULTIPLE_RECORDS`, list all;
- ranges look like `"2-10"`, `"51-200"`, `"1001-5000"`, `"10001+"`, `"myself only"`, or null.
Exact headcount needs `/company/enrich` (2 credits). The MVP uses the range; that's enough for a
"headcount plausible?" question.

## 3. Stretch only — `POST /person/search`
Not needed for the MVP. If used: **always pass `fields`**. Filter paths use `company_name`;
response paths use `name`. Operators: `=`, `!=`, `<`, `=<`, `>`, `=>` (not `>=`/`<=`), `in`,
`[.]` exact phrase, `(.)` all words. Confirm the name-filter field with the autocomplete endpoint
before using it; do not guess.

## Gotchas already seen with this data
1. Omitting `fields` on person search → reply truncated at ~75k chars but all records billed.
2. "Current" roles can be stale; one person can show 3–4 concurrent current roles.
3. Some jobs have no dates at all → `ROW_MISSING_DATES` / `PROFILE_THIN`.
4. Retroactive titles exist (a company's later name or title shown on old jobs).
5. Unlinked employers (`crustdata_company_id` 0, free text) → name normalisation needed.
6. Junk profiles exist (simultaneous CEO/COO/Board at big firms) → `IMPLAUSIBLE_PROFILE`.
7. A network drop can lose a billed reply. Never auto-retry a paid call after a timeout/5xx.

## Rate limits
person enrich 15/min · person search 30/min · company identify 30/min · 200/min per key.

## Budget
Event grant 15,000 credits. Full demo run (4 experts + 1 firm) ≈ 4–5 credits. With caching, re-runs are free.
