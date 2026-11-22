# Demo — 2 minutes + 1 minute Q&A

The dashboard asks: show the product taking a request it has NEVER seen all the way to finished work, start
with the request going in, end with the finished work, show where each fact came from, one-sentence intro.
So: use a CV you never used while building (`check-cv new_cv.md`), then the full bid. Upload to Loom /
unlisted YouTube / Drive (anyone with link) and test in a private window.
Record at ~5:30 as a **live** `audit --fresh` run, terminal visible. No pre-recorded output or prepared results
(the rulebook calls that faking a demo). Show initials, not full names. In the finals, expect a live fix and
a design question: know checker.py and dates.py well.

**0:00–0:15 — The service and the pain.**
"Government consultancy tenders score key-expert CVs: years of experience, past roles, availability.
Committees check them by hand. NHAI's own RFP allows debarment for false CV information, and
the World Bank debarred firms in 2026 for misrepresenting their key experts. I built the checker."

**0:15–0:30 — Input.**
Show tender.json (real NHAI requirements: Team Leader 12 years, 5 as Team Leader) and the bid:
4 experts, real volunteers who agreed, with 2 changes I seeded on purpose.

**0:30–1:15 — Run it and read the report.**
- X1: CONSISTENT. Every CV row matches the profile.
- X2: the seeded date change → "CV start Jan 2010, profile Jul 2012: please ask for evidence."
- X3: current role at another firm → availability question (the exact issue in the World Bank cases).
- X4: no profile → CANNOT_CHECK, "not evidence against the expert".
- Firm: several records found, listed, not merged blindly.

**1:15–1:40 — Why you can trust it.**
"The checker is deterministic, no LLM, with 17 golden cases and 29 real date formats: duplicate records under two company names,
missing dates, junk profiles. The LLM only reads CV text. Every call is cached and logged. Here's a
timeout mid-run: it resumes and never bills twice." (show test or log)

**1:40–2:00 — Who pays, what's next.**
"First customer: the bidder, checking its own bid before submission, because a rejected bid costs them
the tender. Next: the same engine for employment checks in hiring and loan applications."

## Q&A prep
- *Isn't this just a LinkedIn lookup?* No. The value is the comparison: month-level overlap maths,
  tender thresholds, rename/duplicate handling, and turning gaps into committee questions with evidence.
- *What if the profile is out of date?* Then it's "cannot check" or a question, never a verdict. The
  committee asks for certificates.
- *What if someone has no LinkedIn?* NO_PROFILE_FOUND, which is explicitly not evidence. Many senior
  engineers don't keep profiles. That's the main coverage limit and it's in the README.
- *Privacy?* Read-only professional data, no contact, no emails, ambiguous matches show only a count.
- *Why not let the LLM decide?* Not repeatable, can't be tested, and it invents dates.
- *Hardest case?* <your real one from the day>
- *Credits used?* <from credits.csv>
