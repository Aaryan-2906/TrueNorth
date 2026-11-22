# STEP 10 — README + diagram (4:15–5:00)

**Paste into Claude Code:**
```
Read CLAUDE.md, PROGRESS.md and README.md. This is STEP 10 only.

1. Fill every section of README.md from docs/README_TEMPLATE.md using PROGRESS.md as the source of truth.
   Do not claim anything that PROGRESS.md and the tests don't support. Include real numbers: tests
   passing, credits spent (from credits.csv), number of golden cases.
2. docs/diagram.md: a Mermaid flowchart of the pipeline (SPEC §2): inputs -> Crustdata enrich / identify
   (cached, ledger) -> normalise -> checker (pure) -> questions (templates) -> report. Mark the single LLM
   step (CV parsing) clearly.
3. Add a "Pre-event materials" section listing exactly what was written before 10:45 (specs, prompts,
   synthetic golden cases, tender data, volunteer CVs/claims, consent) - no code.

Done when: README renders on GitHub (push and check). Append PROGRESS.md, commit, stop.
```
**You do:** the form wants a diagram LINK that opens for anyone: in mermaid.live use Share → copy link (or link to
docs/diagram.md in your public repo, which GitHub renders). Also export docs/diagram.png and commit it.
The video is a LINK too (Loom, unlisted YouTube, or Google Drive set to anyone-with-the-link). Test both links in a private window.
