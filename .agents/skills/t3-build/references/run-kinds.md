# Request kinds

Moved from `SKILL.md`. A request may start with a word that sets the kind of feature run. Same run, same gates; only these steps change:

- `fix:` bug. Step 5 first writes a test that reproduces the bug and fails for the
  right reason. AC: the repro passes, nothing else changes. Fix the root cause once,
  in shared code. After a severe or repeated bug, write a post-mortem
  ([postmortem.md](postmortem.md)).
- `refactor:` behaviour stays the same. Needs a reason (measured cost, blocked
  feature, repeated bug); ask for it if missing. Step 2: `code-scout` maps the area
  and its callers. Step 5: if coverage is thin, pin
  today's behaviour with characterisation tests first. Change in small steps, tests
  green after each. Add to the review brief: "Did behaviour change anywhere?" Tier
  follows the code touched (auth is T2).
- `docs:` docs only. A chore: the leader writes it, no test author. Check every
  claim against the code. Behaviour change in the diff means it is not `docs:`.
No word: pick by the request and say which in one line.
