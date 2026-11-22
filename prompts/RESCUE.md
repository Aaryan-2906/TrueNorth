# When Claude Code goes off the rails

**It built things the step didn't ask for / is wandering:**
```
Stop. Run `git status` and `git diff --stat`. List every file you changed that the current step did not
ask for. Revert those with git. Re-read CLAUDE.md rule 1 and the current step prompt. Then continue only
with that step.
```

**It invented an API field / response shape:**
```
Stop. Show me the exact cached response file in cache/ that contains this field. If none does, remove the
code that uses it and tell me which field you need; we'll check the real response.
```

**Tests failing and it wants to change the golden file:**
```
Do not edit data/golden/ or expected values. Print the failing case id, expected vs actual codes, and the
spec rule each code comes from. Then fix the code, or tell me exactly which spec sentence conflicts.
```

**Context got long / new session:**
```
Read CLAUDE.md, PROGRESS.md and `git log --oneline`. Summarise in 5 lines where we are and what the next
step is. Do not write code until I paste the next step.
```

**Everything is broken at 5 PM:** `git log --oneline`, find the last good commit, `git checkout <sha> -- .`,
commit "revert to working state" (a new commit — never rewrite history), run `audit --fresh` live, record the video from that.
