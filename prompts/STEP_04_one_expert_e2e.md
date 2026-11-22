# STEP 04 — One expert end to end (12:40–1:00)  ← 1 PM milestone: "one request runs"

**Paste into Claude Code:**
```
Read CLAUDE.md, PROGRESS.md, docs/SPEC.md §2. This is STEP 04 only.

Create app/cli.py with: `uv run python -m app.cli expert X1`
- loads data/bid/bid.json, finds expert X1, loads data/bid/claims/X1.json (hand-written; no LLM yet)
  and the position from data/tender.json;
- if profile_url: crustdata.enrich_people([url]) -> normalise.profile_from_enrich -> Lookup;
  else Lookup(status="not_found");
- checker.check_expert(...);
- prints a plain-text block: expert, status, documented vs claimed years (1 decimal), each CV row
  with its outcome, each finding message with its evidence sources.

Do NOT build the report file, the firm check or the CV parser yet.

Done when: I run it for X1 and X4 (X4 has no URL -> CANNOT_CHECK / NO_PROFILE_FOUND), and a second run
of X1 makes no paid call. Append PROGRESS.md, commit "step 04: one expert end to end", stop.
```
**You check:** read X1's output against X1's real profile yourself. Is every date right? This is your
"facts and verification" proof; screenshot it for the README.
