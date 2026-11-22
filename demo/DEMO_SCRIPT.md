# Demo: 2 minutes (record a LIVE run, Fresh ON, browser at http://localhost:8000)

Show initials only. One-sentence intro, then the request going in and finished work coming out.

**0:00–0:10 Intro (one sentence).**
"TrueNorth checks every line of an expert's CV against the record, for tender committees: the World Bank debarred PwC
units this year partly for misrepresenting key experts."

**0:10–0:55 Unseen request: "Check one CV".**
Paste unseen_cv.txt (never used while building), position Team Leader, Fresh on, click Check.
While Pip works: "Claude only reads and copies the CV; plain code checks it."
Point at the result:
- "Claims 18 years, but the jobs it lists add up to 10.4."
- "Job 2's header says 2019, its own Duration line says 2017."
- "Jobs 2 and 3 are full-time at different firms and overlap a year."
- "One date has no year, one job names no employer."
- "No profile, so it says 'cannot check', not 'fake'. And here's the clarification letter, ready to send."

**0:55–1:35 Against the real record: "Audit a bid".**
Fresh on, run. While Pip flies to Crustdata: "Now it pulls the employment record live from Crustdata."
- "X1 is the founder's real career: every CV row matches the record. The only questions are about his five other
  current roles: an availability question, exactly the World Bank issue."
- "X2 is the same CV with three lines altered on purpose: a title upgraded to CTO, a start date moved two years earlier,
  and five extra years claimed. All three caught, each with the profile entry as evidence."
- "And it noticed X1 and X2 are the same person listed twice in one bid."
- Point at Activity: "1.2 credits for the whole bid; every call is logged."

**1:35–2:00 Trust + who pays.**
"437 tests, including 17 cases written before the build and never edited. The tender rules were checked against the
NHAI PDF, and the tool caught a mistake in our own data. First customer: the bidder, checking its own bid before
submitting. Same engine checks hiring CVs."

## Q&A prep
- Just a LinkedIn lookup? No: month-level overlap maths, the CV checked against itself, rename handling, tender rules,
  and questions with evidence. Half the checks work with no profile at all.
- Out-of-date profile? Then it's a question or "cannot check", never a verdict.
- Why not let the AI decide? Not repeatable, can't be tested, and it invents dates.
- Privacy? Read-only, nobody contacted, ambiguous matches show only a count.
- Hardest case? [your pick from the README]
- Credits? 1.2 per full live bid audit; 0 on repeat.
