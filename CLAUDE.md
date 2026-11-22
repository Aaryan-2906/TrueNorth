# CLAUDE.md — Tender Bid Audit (ContextCon 05)

You are my build partner for a 7-hour solo hackathon. I give you ONE step at a time from `prompts/`.
Do only that step. When it is done, stop and wait for me.

## The product, in one paragraph
A procuring agency (or a bidder checking its own bid) supplies a tender's key-expert requirements
(`data/tender.json`) and the bid: the firm (`data/bid/bid.json`) and each expert's CV. For every
expert we fetch their employment history from Crustdata. A deterministic checker compares the CV's
dated claims and the tender's minimums against that history. The output is a report of
**questions for the tender committee**, never accusations. We also check the bidding firm exists
and that its headcount is plausible for the team it claims.

## Working rules (follow every time)
1. **One step at a time.** Build only what the current prompt asks. Do not create files, features,
   UI, or abstractions the step does not name. If you think something else is needed, write it
   under "Suggestions" in `PROGRESS.md` and leave it.
2. **Read before writing.** At the start of every step read `PROGRESS.md`, then the spec sections
   the prompt names. Do not rely on memory of earlier steps.
3. **End every step** by: running the step's "Done when" commands, appending a short entry to
   `PROGRESS.md` (what was built, files changed, test results, known gaps, and a 3-line plain-English
   explanation of how the new code works, so I can explain it to judges), and making a git commit.
   Then print the "Done when" output and stop.
4. **If unsure about an API field or response shape, do not guess.** Look in `cache/` for a real
   response, or ask me. Never invent Crustdata field names.
5. **Tests are the source of truth.** Never edit a test or a golden file to make it pass. If a test
   seems wrong, stop and tell me why.

## Event rules (from the participant rulebook) — never break these
- All code lives in the organizer repository given at kickoff. Commit often. Never rewrite git
  history, amend timestamps, or edit `credits.csv` or logs after the fact.
- No hard-coded answers, no returning stored results, no mock presented as live. The service must
  work on inputs it has never seen (judges may bring their own tender and CVs).
- The service never contacts a real person. No email/SMS/messaging code at all.
- Any open-source code copied in must be credited in the README (name + licence).

## Truth rules (the product's core; judges score these)
- Never invent data. A missing date or job is reported as **"cannot check"**, never as "false".
- No record in Crustdata is **not** evidence a person or firm is fake. Say "not found in Crustdata".
- Wording: "documented 10.9 years vs claimed 15", "consistency-checked", "question for the
  committee". Never "verified", "fake", "lied", "fraud".
- Every report line cites the profile entry (employer, title, dates) or CV row it came from.
  A line with no citation is dropped.
- One entity under two names stays one record (renames, "Pvt. Ltd." variants, duplicate company ids).
- Never pick silently between several matching people or companies. Report ambiguity.
- When identity is ambiguous, show only the count of candidates, never strangers' profile data.
- Never contact anyone. No emails, no messages, no outreach features.

## Crustdata rules
- See `docs/CRUSTDATA.md`. Base `https://api.crustdata.com`, headers
  `authorization: Bearer $CRUSTDATA_API_KEY`, `x-api-version: 2025-11-01`.
- **Every person search must pass a `fields` list** (omitting it bills all records but truncates the reply).
- Company lookup by name must use `exact_match: true` or a domain. Never take the top fuzzy hit.
- Cache every raw response under `cache/<endpoint>/<sha1-of-request>.json` before parsing.
  The cache exists only so we never pay twice for the same call and can resume after a failure.
  `--fresh` (or FRESH=1) skips reading the cache (still writes it): the live demo uses it.
  Every report states how many lookups came from cache and when they were fetched. There is no
  offline/replay mode: the event rules forbid a service that returns stored results.
- Append every paid call to `credits.csv` (time, endpoint, request hash, credits from the
  `x-credits-used` header if present, else blank). Never retry a paid call after a timeout or 5xx:
  it may have gone through. Retry only on 429 or a connection that never opened.

## Architecture rule
The checker is **pure Python, deterministic, unit-tested**, with no network and no LLM.
The LLM is used in exactly one place: turning CV text into the structured claims JSON
(`app/parse_cv.py`), and its output is validated by pydantic. Committee questions are written from
**templates** per reason code (`app/questions.py`), not by an LLM.

## Stack
Python 3.11, uv, httpx, pydantic v2, pytest, python-dotenv. Report: Markdown + a single static HTML
file. Optional later: Streamlit. Keep dependencies to these unless a step says otherwise.

## Layout (create files only when a step asks)
```
app/config.py      settings, TODAY, thresholds
app/crustdata.py   HTTP client, cache, credit ledger
app/models.py      pydantic models (SPEC §3)
app/normalise.py   raw Crustdata JSON -> Profile; company-name normalisation; dedupe
app/checker.py     pure rules (SPEC §5, §6)
app/firm.py        firm check (SPEC §7)
app/questions.py   reason code -> committee question templates
app/report.py      Markdown + HTML report
app/parse_cv.py    CV text -> Claims (LLM), validated
app/cli.py         `uv run python -m app.cli ...`
tests/             pytest; golden tests read data/golden/*.json
```
