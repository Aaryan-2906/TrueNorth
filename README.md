# Tender Bid Audit — The Career Check (theme 1.2) for government tenders

## The service
TODO: which service this replaces, who does it today, who pays, and 1–2 sourced lines on the pain.

## What it does now
TODO: describe the end-to-end flow and where it still needs a person.

## What it could do in the future
TODO: what comes next with more time, and why it is not built now.

## The future of this service
TODO: where the service heads once agents do the whole job.

## How to run it
Needs [uv](https://docs.astral.sh/uv/). uv installs Python 3.11 if it is missing.

```powershell
git clone https://github.com/Aaryan-2906/TrueNorth.git
cd TrueNorth
uv sync
copy .env.example .env
```

Add `CRUSTDATA_API_KEY` and `ANTHROPIC_API_KEY` to `.env`. Then:

```powershell
# Full demo bid. Writes out/report.md and out/report.html. Reuses cached responses when it can.
uv run python -m app.cli audit

# The same audit with fresh Crustdata lookups and CV readings (about 1.2 Crustdata credits).
uv run python -m app.cli audit --fresh

# One expert from the demo bid (X1, X2, X3 or X4), printed to the terminal.
uv run python -m app.cli expert X2

# One unseen CV (.md or .txt). With no position, it runs in Career Check mode.
uv run python -m app.cli check-cv path\to\any_cv.md
uv run python -m app.cli check-cv path\to\any_cv.md --position team_leader

# Also compare that CV with a profile (1 Crustdata credit).
uv run python -m app.cli check-cv path\to\any_cv.md --profile-url https://www.linkedin.com/in/<handle>

# Read a tender's text, review each number with its source sentence, and approve it.
uv run python -m app.cli read-tender path\to\tender.txt

# Audit the demo bid against the tender you approved.
uv run python -m app.cli audit --tender out/tender_approved.json

# Tests. Live CV and tender reading tests are skipped when there is no API key and no cache.
uv run pytest -q
```

To use the web app, start the server and open http://localhost:8000:

```powershell
uv run uvicorn app.server:app --port 8000
```

The web app can check one pasted CV, audit a bid, and read a pasted tender for approval. It needs internet for its Tailwind and font CDNs.

Position keys are in `data/tender.json`: `team_leader`, `resident_engineer`, `bridge_engineer`, `pavement_specialist`, `quality_material_expert`, `road_safety_expert`.

## How it's built
Diagram: TODO.

```text
data/bid/bid.json + data/tender.json + data/bid/cvs/<id>.md
  -> app/parse_cv.py    the LLM copies CV fields into JSON; values not written in the CV are rejected
  -> app/dates.py       deterministic date conversion
  -> app/crustdata.py   /person/enrich and /company/identify, with cache and credit ledger
  -> app/normalise.py   raw profile to jobs; company-name matching; duplicate jobs collapsed
  -> app/checker.py     deterministic rules for each expert
  -> app/firm.py        firm record, headcount and duplicate-expert checks
  -> app/questions.py   fixed templates turn findings into committee questions
  -> app/report.py      out/report.md and out/report.html

app/parse_tender.py     tender text to proposed positions; a person approves them before use
app/cli.py              the commands above
app/server.py           FastAPI server that streams each real stage and external call
static/index.html       the web page and Pip, an owl driven only by those stages
```

- **Model:** `claude-sonnet-5-5` (set by `ANTHROPIC_MODEL`) is used for two reading jobs: CV text and a tender's key-personnel text into JSON, copied verbatim. It makes no decisions. Values not found in the source text are rejected. Dates are converted by `app/dates.py`, every check is made by `app/checker.py` and `app/firm.py`, and questions come from fixed templates.
- **Crustdata calls:** `/person/enrich` (expert history) and `/company/identify` (firm), both in `app/crustdata.py`. `/account/credits` is used only by `scripts/smoke.py`.
- **State:** inputs are in `data/`. Raw API and LLM responses are cached in `cache/` so a repeat run does not pay twice. `credits.csv` records every paid Crustdata call. Reports are written to `out/`. It runs on a laptop with Python 3.11.
- **Tender rules:** `data/tender.json` holds the NHAI RFP's six key positions, each checked against the PDF with its page and exact sentence (excerpt in `data/tender_source_excerpt.txt`). For another tender, `read-tender` or the web app proposes positions, and nothing is used until a person approves them. Tender text must be pasted or supplied as a text file.

## Its sources and checks
TODO: sources, where a person approves, ledger total for the demo run, and the test evidence.

## The approaches you tried
TODO: what was tried, what worked, what did not, and the hardest case.

## Data and privacy
TODO: data handling, and who the demo experts are.

## Open-source and tools
No open-source code was copied into this repository. It uses these libraries as dependencies:

| Library | Used for | Licence |
|---|---|---|
| FastAPI | Web server | MIT |
| Uvicorn | Running the server | BSD-3-Clause |
| HTTPX | Crustdata calls | BSD-3-Clause |
| Pydantic | Data validation | MIT |
| python-dotenv | Loading `.env` | BSD-3-Clause |
| Anthropic Python SDK | Claude API calls | Apache-2.0 |
| pytest | Tests | MIT |
| Tailwind CSS (CDN) | Page styling | MIT |
| Inter (Google Fonts) | Page font | SIL OFL 1.1 |
| JetBrains Mono (Google Fonts) | Monospace font | SIL OFL 1.1 |

Claude Code was the AI coding tool. Pip the owl is original pixel art made for this project.

## Written before 10:15 AM (also declared on the form)
Specs, step prompts and test data, no code: CLAUDE.md, docs/SPEC.md, docs/CRUSTDATA.md, prompts/,
data/golden/ (synthetic cases), data/tender.json, data/bid/ templates and sample CVs (anonymised).
