# Post-mortem

Trigger: a bug that was severe (data loss, security, outage, wrong money) or that came back
after a fix. Not for every bug. The lead writes it after the fix is merged, in `decisions.md`
and, when it changes a rule, as a proposal.

Answer in a few lines each:

1. **What escaped?** The bug in one sentence and the slice that caused it.
2. **Which gate should have caught it, and why did it not?** Go in order: the tier (set too
   low?), the spec (missing AC?), the tests (no test for the case?), CI and scanners (no check
   for it?), code review (did the brief miss it, did a finding get declined?), the risk gate (a
   path list or rating too low?).
3. **What is the fix to the gate?** One change: a test or lint rule first; else a line in a
   skill, a path on the T2 list, a sharper review brief. Name the file.
4. **Who decides?** Skills, path lists, policy and agent files are guardrail files: the lead
   proposes the change as a pull request; the human merges. The lead never edits them to
   close its own post-mortem.

Keep one change per post-mortem, then watch one retro period before the next (CONCEPT 6.12,
rule 4).
