# STEP 09a — Read an unseen tender (3:30–4:15, in the buffer slot). Do this before any UI.

Why: judges may bring a tender you haven't seen. Today `data/tender.json` is typed by hand. This step lets
the service read the tender's key-personnel section and propose the positions, and a person approves them
(the rulebook allows a person to approve or reject, not to type results).

**Paste into Claude Code:**
```
Read CLAUDE.md, PROGRESS.md, docs/SPEC.md §1 and data/tender.json. This is STEP 09a only.

Create app/parse_tender.py: parse_tender(text) -> Tender (pydantic, same shape as data/tender.json positions).
- LLM prompt: from the key-personnel / CV-evaluation section, extract each position's name, minimum total
  years, minimum years in a similar capacity, the exact wording that defines "similar capacity", and whether a
  postgraduate degree is required. Copy numbers exactly; null if not stated; never guess. Return the source
  sentence for each number.
- Derive similar_title_keywords from the position name and the "similar capacity" wording, and mark them
  "proposed" in the output.
- cli: `uv run python -m app.cli read-tender <tender.txt>` prints the proposed positions with source sentences
  and asks "Approve? [y/N]". Only on y does it write out/tender_approved.json. `audit --tender <file>` uses it.
- Test: feed the text of the key-personnel section of the NHAI RFP (save it as data/tender_source_excerpt.txt;
  I will paste it) and check the result matches data/tender.json for team_leader and resident_engineer.

Do NOT change checker.py.

Done when: the test passes and the approve prompt works. Append PROGRESS.md, commit, stop.
```
**You do:** copy the key-personnel text from the NHAI PDF into data/tender_source_excerpt.txt (from the PDF, by
hand, before or during the step; it's input data, not code).
