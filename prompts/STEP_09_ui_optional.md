# STEP 09 — Optional simple UI (3:30–4:15). Skip if behind schedule.

**Paste into Claude Code:**
```
Read CLAUDE.md and PROGRESS.md. This is STEP 09 only.

Add app/ui.py, a Streamlit page (add streamlit dependency): choose tender.json and bid.json from data/,
a "Run audit" button that calls the same function as `cli audit`, then shows the summary table and each
expert's section (expand/collapse), plus a download button for report.html. A toggle "Fresh run (ignore cache)".
No new logic in the UI: it only calls existing functions.

Done when: `uv run streamlit run app/ui.py` runs the audit live on the demo bid. Append PROGRESS.md, commit, stop.
```
