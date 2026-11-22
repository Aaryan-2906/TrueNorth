# Tender Bid Audit — The Career Check (theme 1.2) for government tenders

## The service
Which service: scrutiny of key-expert CVs in consultancy tenders (years, roles, availability, education).
Who does it today: tender evaluation committees by hand (NHAI, MoRTH, state PWDs, multilateral lenders), and
bid managers before submission. Who pays: the bidder (a rejected or debarred bid costs the contract) and the
procuring agency. <1–2 sourced lines on the pain: NHAI RFP clause on false CV information (debarment up to 2 years,
link); a published key-personnel debarment (link); World Bank 2026 debarments for misrepresenting key experts (link).>

## What it does now
End to end today: reads a tender's key-expert rules (a person approves them), reads each expert's CV text,
looks up the employment record (Crustdata), checks the CV against itself and against the record, checks the
bidding firm, and writes a report of questions for the committee with evidence for each.
Where it still needs a person: approving the tender rules it extracted; deciding what counts as "similar
capacity"; acting on the questions (asking the bidder for certificates). It never contacts anyone.

## What it could do in the future
<next with more time: summary-table vs employment-record cross-check, name search for experts with no profile
URL, conflict check (expert recently worked at the procuring agency), scanned-PDF CVs. Why not now: each needs
more data or ambiguity handling than 7 hours allows to do honestly.>

## The future of this service
<where it heads once agents do the whole job: bidders self-audit every bid automatically before submission;
committees get a clarification pack the day bids open; the same engine checks hiring and loan-application CVs.>

## How to run it
```
git clone <this repo> && cd <repo>
uv sync
copy .env.example .env    # add CRUSTDATA_API_KEY and ANTHROPIC_API_KEY
uv run python -m app.cli audit --fresh                          # demo bid -> out/report.md, out/report.html
uv run python -m app.cli check-cv any_cv.md [--position team_leader]   # one unseen CV
uv run python -m app.cli read-tender tender.txt                 # propose tender rules, you approve
uv run pytest -q
```

## How it's built
Diagram: <link that opens for anyone>.
- **Model:** <ANTHROPIC_MODEL> is used only to read CV text (and the tender's key-personnel section) into JSON,
  copying dates verbatim. Code (app/dates.py) converts dates; a deterministic checker (app/checker.py) makes every
  decision; questions come from fixed templates.
- **Crustdata calls:** `/person/enrich` (expert history), `/company/identify` (firm). Where: app/crustdata.py.
- **State:** input files in data/; raw API and LLM responses cached in cache/ (never pay twice, resume after a
  failure); credits.csv ledger; outputs in out/. Runs on a laptop with Python 3.11.

## Its sources and checks
- Sources: the CV text; Crustdata person enrich; Crustdata company identify; the tender document.
- Where a person approves: the extracted tender rules (read-tender asks Approve? y/N). Nothing irreversible happens.
- Ledger: credits.csv records every paid call (time, endpoint, request hash, credits). Total for the demo run: <n>.
- Checks: <N> golden expert cases + 5 firm cases + 29 real CV date formats, a simulated timeout (resumes, no
  double charge), fresh live runs, hand-checked results on <N> real profiles.

## The approaches you tried
- LLM judges the CV against the profile → rejected: not repeatable, untestable, invents dates.
- LLM converts dates → replaced by deterministic parsing after seeing real CVs ("Sep'14", "January2012", "09/12/2097").
- <what worked / what didn't on the day, from PROGRESS.md; hardest case>.

## Data and privacy
Read-only professional data; nobody is contacted; no emails/phones requested; ambiguous matches show only a count;
results are questions, not findings. Demo experts: <who, with consent; initials in the video>.

## Open-source and tools
<libraries + licences; Claude Code used as the AI coding tool>.

## Written before 10:15 AM (also declared on the form)
Specs, step prompts and test data, no code: CLAUDE.md, docs/SPEC.md, docs/CRUSTDATA.md, prompts/,
data/golden/ (synthetic cases), data/tender.json, data/bid/ templates and sample CVs (anonymised).
