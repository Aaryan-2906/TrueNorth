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

## Step 02: models + normaliser
- **Built:** the SPEC §3 pydantic models plus `RowResult`, with `proposed_position` optional, and company-name normalisation, month arithmetic and raw profile parsing.
- **Files:** `app/models.py`, `app/normalise.py`, `tests/test_normalise.py`.
- **Test results:** `uv run pytest -q` gives 14 passed. The real cached profile parses to 18 jobs (6 current, 12 past), no duplicates, 196 documented months.
- **Known gaps:** no checker rules yet, by design. The real-profile test skips on a clean clone because `cache/` is not committed. `DUPLICATE_ENTRY` is only recorded as `duplicates_collapsed` until the checker exists.
- **How it works:**
  1. Company names are lowercased and stripped of brackets, punctuation and legal words, so "U Projects Pvt. Ltd." and "U Projects" compare equal, and "(formerly X)" is remembered as an alias.
  2. Experience is the union of job months, so overlapping jobs count once, ongoing jobs end at TODAY, and jobs that cannot be placed are left out.
  3. Each raw Crustdata job becomes a `Job` that keeps its source, such as `profile.past[2]`. Missing or unreadable dates stay empty, so they are reported as "cannot check".

## Step 03: checker + golden tests
- **Built:** `check_expert` with one small pure function per rule in SPEC §5, §5b, §6 and §7b. The employer "starts with" rule now compares whole words, and SPEC §4 says so.
- **Files:** `app/checker.py`, `app/normalise.py`, `docs/SPEC.md`, `tests/test_golden.py`, `tests/test_normalise.py`.
- **Test results:** golden test failed first (no checker), then 17 of 17 golden cases passed. `uv run pytest -q` gives 33 passed. No golden file or expected value was edited, and no case contradicted the spec.
- **Demo data:** X1 `QUESTIONS` (all 5 rows match; 5 current roles outside the bidder). X2 `QUESTIONS` (seeded total, title and date all caught). X3 and X4 `CANNOT_CHECK`, with CV-only questions.
- **Rules I found ambiguous, and what I did:**
  1. §7b does not list `EXPERT_NOT_AT_FIRM` or `PROFILE_THIN` for no-position runs. Both still run because neither needs a tender.
  2. §7b's as-of rule sits under "Without a tender". It applies whenever `cv_date` is given, with or without a position.
  3. `CV_ROWS_OVERLAP` skips rows with no employer, because they cannot be called "different employers".
  4. `RowResult.outcome` holds one code. A row with several shows the first (dates before title), and all of them stay in `findings`.
  5. `CV_HEADER_BODY_MISMATCH` skips rows with a `date_problem`, following §5b's "left out of every row comparison".
  6. An empty title on either side raises no `ROW_TITLE_DIFFERS`, since a missing title is not a difference.
  7. Golden profiles contain uncollapsed duplicates, so `check_expert` collapses duplicates itself as well.
- **Known gaps:** §5b's "CV is undated" note and §7b's "report that this was done" belong to the report step, because `ExpertResult` has no notes field.
- **How it works:**
  1. CV-only rules always run first, so a CV gets questions even when no profile exists.
  2. Each valid CV row is matched to the closest profile job at the same employer, then its dates and title are compared.
  3. Expert rules compare documented months with the claims and tender, and the status comes only from which findings fired.

## Step 04: one expert end to end
- **Built:** `uv run python -m app.cli expert <ID> [--fresh]`, which loads the bid, claims and position, looks up the profile, runs the checker and prints a plain-text block. `Lookup` gained a `reason` field, so an expert with no URL reads "No profile URL was supplied, so no lookup was made" and keeps the code `NO_PROFILE_FOUND`.
- **Files:** `app/cli.py`, `app/models.py`, `app/checker.py`.
- **Test results:** runs for X1 (`QUESTIONS`), X2 (`QUESTIONS`), X3 (`CANNOT_CHECK`) and X4 (`CANNOT_CHECK`). A second X1 run came from cache and `credits.csv` did not change. `uv run pytest -q` gives 33 passed, with the golden helper and golden files unchanged.
- **Known gaps:** `--fresh` was tested offline with a fake API reply, not with a real paid call. No report file, firm check or CV parser yet, by design.
- **How it works:**
  1. The CLI finds the expert in `bid.json` and loads the hand-written claims and the tender position, or runs Career Check mode when the position is null.
  2. If there is a profile URL it calls Crustdata through the cache; if not, it records that no lookup was made instead of implying a search failed.
  3. It prints the status, documented vs claimed years, each CV row's outcome, each finding with its evidence, and whether the lookup was live or from cache.

## Step 05: firm check, committee questions, Markdown report
- **Built:** firm lookup and check, one question template per reason code, `out/report.md`, and `uv run python -m app.cli audit [--fresh]`. CV rows with no profile now read `NOT_COMPARED (no profile)`, and `NO_PROFILE_FOUND` stays at expert level.
- **Files:** `app/firm.py`, `app/questions.py`, `app/report.py`, `app/cli.py`, `app/checker.py`, `app/models.py`, `tests/test_firm_golden.py`, `tests/test_questions.py`.
- **Test results:** firm golden test failed first (no module), then 5 of 5 passed. All 17 expert golden cases still pass unchanged. `uv run pytest -q` gives 63 passed. `audit` made no paid call and `credits.csv` did not change.
- **Headline:** 4 experts: 0 consistent, 2 need clarification (5 questions), 2 cannot be checked.
- **Decisions:**
  1. The headline's question count covers only experts with status `QUESTIONS`. X3 and X4 still list their CV-only questions in their own sections.
  2. `ROW_TITLE_DIFFERS` says "for the same period" only when the dates agree. Otherwise it says "in its closest entry (dates)", so X2's question stays accurate.
  3. If a domain lookup returns 0, the firm is reported as not found. The one retry without a legal suffix applies to name lookups only.
  4. `audit --fresh` looks up a repeated URL once per run, so X1 and X2 cost 1 credit, not 2.
  5. A failed lookup is reported as failed and not compared, is not retried, and does not stop the rest of the audit.
- **Known gaps:** `audit --fresh` and the failed-lookup path were tested offline with fake API replies, not with real paid calls. X1 and X2 are the same person, so the firm check counts 2 full-time experts for one person. No HTML report or CV parser yet, by design.
- **How it works:**
  1. The firm check finds company records by domain or exact name and compares the largest headcount range with the experts the bid lists as full-time.
  2. Each finding carries its values, and a fixed template turns it into a numbered committee question. No LLM writes this wording.
  3. The report combines the headline, firm, per-expert tables, cited questions, a draft letter that is never sent, and a footer with credits and data freshness.

## Step 06a: date parser + bid duplicate check
- **Built:** `parse_cv_date` and `fill_row_dates`, a bid-level `DUPLICATE_EXPERT` question, a full-time count of distinct people, and a headline that totals questions across the whole bid.
- **Files:** `app/dates.py`, `app/firm.py`, `app/report.py`, `app/questions.py`, `app/models.py`, `app/cli.py`, `app/checker.py`, `tests/test_dates.py`, `tests/test_bid.py`, `tests/test_questions.py`.
- **Test results:** date tests failed first (no module), then all 29 golden date strings passed without editing the golden file. `fill_row_dates` also reproduces the dates in all 6 hand-written claims files. `uv run pytest -q` gives 108 passed. `audit` made no paid call.
- **Headline:** 4 experts: 0 consistent, 2 need clarification, 2 cannot be checked; 11 questions in total (including CV-only questions).
- **Decisions:**
  1. The total counts every question needing a bidder answer: 10 across experts and 1 bid-level. "No profile was available" statements are not counted.
  2. `DUPLICATE_EXPERT` sits in the "Firm and bid" section and does not change the firm's own status, because the firm's record is not what is in question.
  3. The same URL reads "point to the same person". The same name with no URL reads "may be the same person", because two people can share a name.
  4. `tests/test_questions.py` lists every reason code, so `DUPLICATE_EXPERT` was added to that list. No expected value changed.
  5. A blank date cell now reads `(blank)` in `CV_DATE_INVALID`, and golden results are unchanged.
- **Known gaps:** the parser accepts only the formats in SPEC §5c. Anything else is reported as unreadable rather than guessed. The CV parser (Part B) is not built yet.
- **How it works:**
  1. Each raw CV date is matched against a fixed list of formats, with day-first numeric dates and two-digit years resolved by the SPEC rule.
  2. A date that cannot be read, has no year, or falls after today becomes a named problem, so the row leaves all totals and raises a question.
  3. The bid check groups experts by profile URL, or by name when there is no URL, and counts each distinct person once.

## Dates: more formats
- **Built:** day-first "12 March 2019", "12 Mar 2019", "12th March, 2019", "12-03-2019" and "12.03.2019" (month and year kept). "03-2019" and "2019" alone give the problem "no month". Commit `3ee8064`.

## Step 06b: CV parser
- **Built:** `parse_cv` reads CV text into `Claims` through the Anthropic API, `audit` and `expert` now always read `cvs/<id>.md` through it, and `uv run python -m app.cli check-cv <path> [--position KEY] [--profile-url URL]` checks a single unseen CV.
- **Files:** `app/parse_cv.py`, `app/dates.py`, `app/models.py`, `app/report.py`, `app/cli.py`, `tests/test_parse_cv.py`, `pyproject.toml`, `uv.lock`. `app/checker.py` was not changed.
- **Test results:** the parser matched all 6 hand-written claims files with no diffs. `uv run pytest -q` gives 142 passed. `audit` matches the previous report except for one new footer line about CV readings, and `credits.csv` did not change. `check-cv` worked on a new synthetic CV in a temp folder and ignored an instruction planted in it.
- **Decisions:**
  1. `temperature=0` is not sent, because `claude-sonnet-5-5` rejects non-default values with a 400. A strict JSON schema, the verbatim check and the response cache keep results stable, but a `--fresh` reading is not guaranteed to be identical.
  2. Every employer, title and raw date must appear verbatim in the CV. If not, the model is retried once with the error, then the run stops.
  3. Server-side refusal fallback is on, so if the model declines a CV the API can retry on another model.
  4. `audit` reads every CV before any paid lookup, so an unreadable CV stops the run with no report and no Crustdata credits spent.
  5. `check-cv` looks up a profile only when `--profile-url` is passed. A URL written inside a CV never triggers a paid call by itself.
  6. A CV with no stated total shows "not stated" rather than a made-up number.
- **Known gaps:** `audit --fresh` has not been run live with the parser. LLM token cost is not recorded in `credits.csv`, which covers Crustdata only. No HTML report yet.
- **How it works:**
  1. The LLM copies the name, claims and each job's employer, title and raw dates out of the CV as JSON, and treats the CV as data, not instructions.
  2. Python rejects anything not written in the CV, then `dates.py` converts every date, so the LLM never produces a date value.
  3. The same deterministic checker then runs on those claims, and each reading is cached so a repeat run costs nothing.

## Step 07: HTML report, README, live run
- **Built:** `out/report.html` (one static file, inline CSS, no scripts), `README.md` from the template, and clearer date-problem messages in `checker.py` (wording only, no rule changes).
- **Files:** `app/report.py`, `app/checker.py`, `app/cli.py`, `README.md`, `credits.csv` (2 new ledger lines from the live run).
- **Live run:** `audit --fresh` made real Crustdata calls and 4 real CV readings. Headline: 4 experts: 0 consistent, 2 need clarification, 2 cannot be checked; 11 questions in total. It cost 1.2 credits (1 enrich + 0.2 identify), and the balance went from 15168.28 to 15167.08. All 180 report lines outside run metadata matched the cached run.
- **Test results:** `uv run pytest -q` gives 142 passed. The HTML was checked for well-formed tags, no scripts, escaped CV text and the same content as the Markdown, and was viewed at desktop and phone widths in headless Edge.
- **Known gaps:** README sections other than "How to run it" and "How it's built" are marked TODO. The diagram link is TODO. `read-tender` from the template does not exist, so it is not in the README. LLM token cost is not in `credits.csv`.
- **How it works:**
  1. The HTML is generated from the finished Markdown report, so the two files cannot disagree.
  2. All text is escaped before it becomes HTML, so anything written in a CV stays plain text.
  3. Statuses become coloured pills and evidence becomes grey chips, with nothing loaded from outside the file.

## Step 08: robustness
- **Built:** no new product features. A failed lookup or unreadable CV now affects only that expert, malformed inputs give a clear message naming the file and problem, and Crustdata 401/402/403/429 replies have clear messages.
- **Files:** `app/cli.py`, `app/crustdata.py`, `app/parse_cv.py`, `app/firm.py`, `app/checker.py`, `app/questions.py`, `app/report.py`, `docs/CRUSTDATA.md`, `tests/test_robustness.py`, `tests/test_questions.py`.
- **Test results:** 28 of the 38 new test cases failed before the fixes. `uv run pytest -q` now gives 180 passed. The normal `audit` matches the live run on all 180 report lines. A real recipe sent through the real model was reported as "does not look like a CV". No Crustdata credits were spent in this step.
- **Decisions:**
  1. An unreadable CV no longer stops the audit. That expert gets the new code `CV_NOT_READ`, status `CANNOT_CHECK`, and no paid lookup. `CV_NOT_READ` was added to the code list in `tests/test_questions.py`.
  2. A malformed `bid.json` or `tender.json` stops the run before any paid call, because no result would be reliable.
  3. A damaged Crustdata cache file is reported and never re-fetched automatically, since that would cost credits. A damaged LLM cache file is simply read again.
  4. Rejected calls (401/402/403/429) are counted separately from paid calls in the report's credit line.
  5. A bid's `cv` path may not point outside the bid folder, so a bad bid cannot send other local files to the LLM.
- **Known gaps:** re-running after a timeout calls that one failed lookup again, so it could be charged twice if the first call went through. Crustdata error handling was tested with fake replies only. The meaning of 402 is the standard HTTP one. `cli.load_claims` is only used by tests, since runs read CVs rather than hand-written claims.
- **How it works:**
  1. Inputs are validated first, so a broken bid gives one clear message before anything is spent.
  2. Each CV and lookup failure is recorded against that expert as "could not be read" or "lookup failed", and the run carries on.
  3. Every paid result is cached as soon as it arrives, so a re-run pays only for what is still missing.

## UI: web app and Pip
- **Built:** a FastAPI server that streams real run events, a one-file dark results page, and Pip, an original 16x16 pixel owl driven only by those events. Run it with `uv run uvicorn app.server:app --port 8000`.
- **Files:** `app/server.py`, `static/index.html`, `tests/test_server.py`, `app/cli.py`, `app/crustdata.py`, `app/parse_cv.py` (hooks and a tighter prompt), `app/report.py`, `docs/CRUSTDATA.md`, `credits.csv`, `pyproject.toml`, `uv.lock`.
- **Test results:** `uv run pytest -q` gives 197 passed. In headless Edge, a live "Check one CV" took 16.2s (1 real Claude reading, 0 credits). A live "Audit a bid" took 30.9s (4 real Claude readings, 2 Crustdata calls, 1.2 credits; balance 15167.08 to 15165.88). The audit matched the earlier live report on all 180 lines. The recipe error state, phone width, report link, letter copy and reduced motion were also checked.
- **Decisions:**
  1. The CLI and server call the same `audit_bid` and `check_one_cv`, with small progress and call hooks. No checking logic was added.
  2. Only one run happens at a time, and a second gets a clear "in progress" message, because run counters and the report file are shared.
  3. Pip glides to the Crustdata node only when a real call is made. No-URL experts and reused lookups show the caption but Pip does not move there.
  4. "Check one CV" does not overwrite `out/report.html`, so its report button appears only after a bid audit.
  5. A cached Crustdata lookup shows 0 credits. A Claude reading shows no Crustdata credits, since its cost is not in `credits.csv`.
  6. HTTP 402 is now documented as credits run out, confirmed by the event handbook.
  7. The live run exposed a parser variation: the model once read X3's "5.10 years as Superintending Engineer" as similar-role experience. The prompt now counts only figures labelled as similar capacity or the proposed position. After the fix, 20 of 20 fresh readings of the 5 distinct test CVs matched the hand-written answers. The test was not changed.
- **Known gaps:** LLM readings can still vary between `--fresh` runs, since `claude-sonnet-5-5` does not accept `temperature=0`. The full UI audit ran live before the prompt fix; afterwards the CV readings were re-run live and the audit used cached Crustdata replies. The page needs internet for the Tailwind and font CDNs. On cached runs the stages finish within milliseconds, so Pip jumps almost directly to the result rather than being slowed down artificially. The app's built-in browser pane could not reach the local server, so the page was tested in headless Edge.
- **How it works:**
  1. The server runs the normal audit in a background thread and streams each real stage and external call to the browser as it happens.
  2. The page only displays those events and the final result, escaping all CV text so it can never become markup.
  3. Pip's position, pose and caption come directly from the stage events, so it cannot show progress that did not happen.

## Step 09a: corrected tender, tender reading and approval
- **Built:** `data/tender.json` corrected from the NHAI RFP (six positions, the PDF's similar-capacity titles, source pages and exact sentences), postgraduate levels, `parse_tender`, `read-tender`, `audit --tender`, and a "Read a tender" box with an Approve button in the UI.
- **Files:** `data/tender.json`, `data/tender_source_excerpt.txt`, `data/golden/tender_golden.json`, `app/parse_tender.py`, `app/checker.py`, `app/questions.py`, `app/report.py`, `app/parse_cv.py`, `app/cli.py`, `app/server.py`, `app/crustdata.py`, `static/index.html`, `docs/SPEC.md`, `docs/CRUSTDATA.md`, `tests/test_tender.py`, `tests/test_golden.py`, `tests/test_questions.py`, `tests/test_robustness.py`.
- **What the PDF changed:** all year minimums were right. Postgraduate is preferential for every position (3 marks for Team Leader, 4 for the others), not required. Similar-role titles were too narrow, and Senior Pavement Specialist and Road Safety Expert were missing. The Quality/Quantity typo on page 74 is recorded in `_note`.
- **Test results:** `uv run pytest -q` passes. All 17 golden cases pass against the frozen `tender_golden.json`, and `expert_cases.json` was not edited. Four fresh live readings of the excerpt matched `tender.json` on all six positions. In headless Edge, reading, approving and auditing with the approved tender worked. No Crustdata credits were spent. The normal audit differs from the previous report only by two "Equivalent grades" notes, on X3 and X4.
- **Decisions:**
  1. A number is accepted only if its source sentence is verbatim in the tender and contains that exact number, so "5" does not match "5.5".
  2. Similar-role keywords are derived by code from the PDF's wording and marked "proposed". The LLM does not choose them.
  3. `audit --tender` refuses any file that was not written by an approval. The UI approves by a one-time token, so the browser cannot change the numbers.
  4. A tender with no stated minimum skips that rule instead of guessing.
  5. "Preferential" produces an info note only, so it never changes an expert's status or adds a question.
- **Known gaps:** only text can be pasted or read; a tender PDF must be converted first. Keyword matching on titles cannot judge "or equivalent and above" grades, so the report leaves those to the committee. The experience type in the tender ("in Highway Projects") is not checked against each job. LLM token cost is not in `credits.csv`.
- **How it works:**
  1. The LLM copies each position's requirements and the sentence each number came from.
  2. Python rejects anything not found verbatim, then derives the similar-role titles from the tender's own definition.
  3. A person reviews the numbers with their sources, and only an explicit approval writes the file an audit can use.

## README update
- Added the web app, `read-tender` and `audit --tender` commands, the current architecture, and the open-source table with licences. Python licences come from the installed package metadata. The remaining sections stay marked TODO, as does the diagram link.

## Check after the Sandstone redesign
- **Checked:** the new `static/index.html`, README, demo script and code tour from commit `436fc17`. In headless Edge: all three pages, Check one CV (`SAMPLE_real_format.md`), cached bid audit, Read a tender (first part of the excerpt, through PDF page 28), Approve, audit with the approved tender, report link, letter copy, reload and phone width.
- **Fixed (only real breakages):**
  1. After reading a tender, the Activity credits total showed `NaN`, because a tender result has no credits field.
  2. After reading a tender, Pip showed the "cannot check" pose, although nothing had been checked. It now returns to its calm pose.
  3. README said "30+ real formats". The tests hold 29 real date strings plus extra formats, so it now says that.
  4. README's licence table was missing Fraunces, the new heading font (SIL OFL 1.1).
- **Files:** `static/index.html`, `README.md`, `PROGRESS.md`. The design and all other text were kept.
- **Test results:** `uv run pytest -q` gives 238 passed, matching the README and the page. The browser run went from 43 of 45 checks to 45 of 45, with no console or request errors. Every README command ran, except `audit --fresh` and `--profile-url`, which spend credits and were checked through `--help` only. All 42 code names in the code tour exist. The demo script's unseen-CV claims match a real `check-cv` run. No Crustdata credits were spent.
- **Known gaps:** the World Bank and MoRTH claims on the page and in the README were not re-checked against their sources. The NHAI debarment quote was found in the PDF. When a tender excerpt has only the marking tables, a source sentence can be a bare table fragment such as "12 years -7": it is verbatim, but less readable than the Enclosure-B sentence.
- **How it works:** the page now updates the credits total only when a result carries one, and a finished run with no expert status leaves Pip calm instead of implying a result.

## CV file upload
- **Built:** `/api/check-cv` now also accepts a multipart `.pdf`, `.docx`, `.txt` or `.md` file up to 5 MB, and the JSON `cv_text` path still works. CLI `check-cv` reads `.pdf` and `.docx`. The page has a drag-and-drop / Choose file zone with file name, size and Clear.
- **Files:** `app/cv_files.py`, `app/server.py`, `app/cli.py`, `static/index.html`, `tests/test_cv_files.py`, `README.md`, `demo/DEMO_SCRIPT.md`, `pyproject.toml`, `uv.lock`.
- **Test results:** `uv run pytest -q` gives 270 passed. Generated PDF, DOCX and DOCX-with-table files parse to the same claims as the text. Real PDF and Word-table copies of CV X3, read by the live model, matched the hand-written claims on all 12 rows. In headless Edge, 22 of 22 upload checks and 45 of 45 earlier flow checks passed. No Crustdata credits were spent.
- **Decisions:**
  1. Extraction happens before the pipeline, so parsing, dates and checks are unchanged.
  2. `python-multipart` was added because FastAPI needs it to receive file uploads. It is in the README licence table.
  3. Uploads from another website's origin are refused, because any site can post a form to localhost and could otherwise spend credits.
  4. A PDF with fewer than 20 readable characters gets the scanned-image message. Password-protected, damaged, oversized and wrong-type files get their own clear messages and spend nothing.
  5. Word cells split into several paragraphs stay on one row, and a merged cell appears once in its row.
  6. Uploads are checked under the id `pasted_cv`, so a file name (often a person's name) does not appear in results.
- **Known gaps:** no OCR. Word headers, footers and text boxes are not read, so a CV date placed only in a footer is treated as undated. PDFs with multi-column layouts may extract in an awkward order. The test PDFs are simple generated files, not exports from Word or a CV builder. Bid audits still read `.md` CVs only.
- **How it works:**
  1. `cv_files.py` turns the uploaded file into plain text, with Word table rows written as cells joined by " | ".
  2. That text goes through the same `check_one_cv` function as pasted text.
  3. The page sends the chosen file instead of the textarea, and shows the server's message if the file cannot be read.

## Deterministic CV reader
- **Built:** `parse_cv_rules` reads the standard NHAI key-personnel layout with regexes and `app/dates.py`. `parse_cv` tries it first and uses Claude only if it returns None. Each expert records `read_by`, shown as "Read by: rules" or "Read by: Claude" in the report, CLI and web card. New env flag `LLM_ENABLED` (default true).
- **Files:** `app/parse_rules.py`, `app/parse_cv.py`, `app/models.py`, `app/config.py`, `app/parse_tender.py`, `app/report.py`, `app/cli.py`, `static/index.html`, `.env.example`, `tests/test_parse_rules.py`, `README.md`, `demo/DEMO_SCRIPT.md`.
- **Test results:** `uv run pytest -q` gives 330 passed. X3, X4 and SAMPLE_real_format are read by rules and match their hand-written claims exactly. SAMPLE_modern_resume, X1 and X2 fall back to Claude and match, except that the modern resume's name is compared ignoring case because the CV prints "EXPERT M" and the claims file says "Expert M". The demo audit report is unchanged apart from the new Read-by lines and footer count. In headless Edge, X3 shows "Read by: rules" with no Claude call in Activity, and the modern resume shows "Read by: Claude" with its call. No Crustdata credits were spent.
- **Decisions:**
  1. The rules are deliberately cautious. They step aside for several claimed totals, a stated similar-capacity figure, an unclear employer cell, a job line they cannot place, or an unreadable date.
  2. Impossible dates such as 09/12/2097 are still read, so the checker raises them as date questions.
  3. With `LLM_ENABLED=false`, cached Claude readings are not used either, so "off" means no AI output of any age.
  4. Reading a new tender needs the AI reader, so with the flag off it gives a clear error.
- **Known gaps:** with the flag off, X1 and X2 are reported as not recognised, so the demo audit has 4 experts that cannot be checked. The demo `unseen_cv.txt` still goes to Claude because one row's employer cell lists several parties. The rules were built from the few NHAI CVs in this project, so other real CVs may often fall back to Claude.
- **How it works:**
  1. The rules look for the NHAI headings and read each employment row, keeping only text written in the CV.
  2. If anything is unclear, they return nothing and Claude reads the CV, unless `LLM_ENABLED` is false.
  3. Dates and all checks then run through the same deterministic code, whichever reader was used.

## PDF download and question-to-row links
- **Built:** a "Download as PDF" button beside "Open report.html" calls `window.print()`. The print view hides the nav, input panel, Pip runway, Activity bar and buttons, uses a white background, opens every `<details>`, and avoids breaks inside cards. Clicking a question card scrolls to the CV rows cited in its evidence and rings them in amber for 1.5 s.
- **Files:** `static/index.html`, `PROGRESS.md`.
- **Test results:** `uv run pytest -q` gives 330 passed. In headless Edge on the real demo bid, 25 of 25 checks passed, covering the button, print styles, opened sections, a generated 9-page A4 PDF, each linked card's rows against its evidence, the 1.5 s timing, keyboard use and text selection. No Crustdata credits were spent.
- **Decisions:**
  1. Row numbers are read only from evidence chips that start "CV row N (" or are "CV rows N, M", so a number inside an employer's name or question text cannot link a row.
  2. Cards with no CV-row evidence, including firm questions, are not clickable.
  3. Dragging to select question text does not trigger the jump, and Enter or Space works from the keyboard.
- **Known gaps:** the browser's print dialog was stubbed in the automated test, so the real "Save as PDF" dialog was not clicked through. A card taller than one page still splits across pages. The PDF's look depends on the browser's "Background graphics" handling.
- **How it works:**
  1. The button opens the browser's print dialog, and print-only CSS leaves just the results.
  2. Closed sections are opened before printing and closed again afterwards.
  3. A question card stores the row numbers from its own evidence and highlights those rows in its own expert's table.

## Audit from uploaded CVs
- **Built:** `POST /api/audit-upload` takes a firm name, optional domain, and 1-6 CV files, each with an optional profile URL, position and employment. It builds the bid in memory, runs the existing audit (rules reader first, Claude fallback), and streams the same events and result shape as `/api/audit`. "Audit a bid" now has a Saved bid / Upload CVs switch with firm fields, a multi-file drop zone, and a row per file with Remove.
- **Files:** `app/server.py`, `app/cli.py`, `static/index.html`, `tests/test_audit_upload.py`, `README.md`, `demo/DEMO_SCRIPT.md`.
- **Test results:** `uv run pytest -q` gives 353 passed. New tests use generated CVs with no profile URLs and a mocked firm lookup, and cover the 6-file limit, wrong file types, size limit, cross-site refusal, bad fields and an unchanged `data/` folder. In headless Edge, 40 of 40 checks passed against the real app with the firm lookup mocked and all outside calls blocked. No Crustdata credits were spent.
- **Decisions:**
  1. Experts are labelled C1 to C6, because file names often contain a person's name. Displayed names come from the CVs.
  2. A wrong type, oversized file or bad field refuses the whole upload before anything runs. A damaged or scanned file becomes that expert's "cannot check" entry, and the others continue.
  3. Employment defaults to full-time when left out, so the firm's headcount check still considers that person.
  4. Profile URLs must look like a LinkedIn profile, so a mistyped value cannot spend a paid lookup.
  5. Upload mode uses the standard tender positions. Approved-tender controls stay in Saved bid mode.
- **Known gaps:** uploaded files are never written to `data/`, but the run's report is written to `out/` as for any audit, a Claude fallback reading is cached in `cache/`, and large uploads may briefly use a system temp file while the form is parsed. A real firm lookup through this endpoint spends Crustdata credits; the tests and Edge run mocked it. Claude fallback through this endpoint was not run live in this step.
- **How it works:**
  1. The server checks the form and turns each file into text, keeping it in memory.
  2. It builds a temporary bid and sends it through the same checks as a saved bid.
  3. The page shows the same live stages, results, report link and PDF button.

## Suggestions (ideas noted but not built)
- Check whether each job's sector matches the tender's wording ("in Highway Projects"), since total years currently count all jobs.
- SPEC §4 collapses same-employer, same-date jobs even when titles differ, which could hide a title a CV row should match. Consider keeping both titles.
