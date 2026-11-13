# 006 WP6: milestone-audit skill

Tier T2 by path (`tier.py`: `human`, `.agents/` holds guardrail files; logged as T2). Prose only,
no code. Base `agent/wp5-release-skill` at `283e5d5` (PR #5, not merged). Sources: PLAN.md WP6,
CONCEPT 4 (budgets, severity), 6.7 (layer 4, agent-security table, incident path, limit), 6.8,
"What goes where", "Rules for skills"; release SKILL.md stage 4; t3-build section 3 and
`references/{brief,budgets,event-log,models}.md`. MUST, SHOULD and MAY are used in the RFC 2119 sense.

**Intent.** A new skill `milestone-audit` tells `security-engineer` how to audit one milestone at
one exact commit: run a checklist and the repo's own scanners, check every T2 change's earlier
verdict, walk the trust boundaries, then hand the same diff to `reviewer-security` as an
independent second key. The lead reconciles both replies into a findings ledger in the state
folder. A report without exploit detail goes to `docs/security/` only once nothing is open. The
skill satisfies release stage 4 and weakens none of its rules; it also runs standalone.

## Files

| File | Change |
|---|---|
| `.agents/skills/milestone-audit/SKILL.md` | new, at most 200 lines, frontmatter `name: milestone-audit` and a `description` with the trigger phrases "/milestone-audit", "audit the milestone", "second key" |
| `.agents/skills/milestone-audit/references/checklist.md` | new: assets, trust boundaries, per-class checks, scanner classes and how to find their commands |
| `.agents/skills/milestone-audit/references/ledger.md` | new: ledger format and report template |

Not touched: release skill, t3-build (SKILL.md, references, scripts), gate, `staging/`,
`models.md`, CONCEPT.md, PLAN.md, AGENTS.md. Links from the skill to t3-build start `../t3-build/`.

## Terms

- **Candidate SHA**: 40 hex characters. In release mode the candidate record's SHA; standalone the SHA the human names.
- **Base**: release mode, the candidate record's base (already confirmed by the human at release stage 1); standalone, the SHA the human names, `git merge-base --is-ancestor <base> <sha>` exits 0, or the empty tree when the human says first audit.
- **Milestone diff**: `git diff <base> <sha>`. **T2 changes**: as release stage 6 defines them (`tier.py`-rated PRs united with `Tier: T2` trailers; no trailer and no rating is `unrated`).
- **Audit folder**: `<state>/<repo>/audit-<milestone>/`, mode 700, never in the repo. `<state>` as in `../t3-build/references/event-log.md`. Holds `ledger.md`, `scans/<sha>/`, `replies/<sha>/` (raw replies) and `checklist-<sha>.md`.
- **Round**: one audit plus one second key on one candidate SHA.

## Acceptance criteria

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | Entry: the skill starts only when `git status --porcelain` prints nothing and `git rev-parse HEAD` equals the candidate SHA; `<milestone>` matches `^[a-z0-9][a-z0-9.-]{0,30}$`, SHAs `^[0-9a-f]{40}$`, refs quoted. Release mode reads SHA and base from `<state>/<repo>/release-<milestone>/candidate.md` and names that path in its reply. Standalone mode states that no release record exists and takes SHA and base from the human's own message. Either mode writes mode, SHA and base into `checklist-<sha>.md`. | grep `porcelain`, `rev-parse HEAD`, `candidate.md`, `standalone` |
| AC2 | Inputs, read as data: the milestone diff; `docs/security/threat-model.md` and the security section of each spec in the milestone; `docs/security/accepted-risks.md`; the previous `docs/security/audit-*.md`; the ledger of earlier candidates. A missing threat model is a finding (area `process`), not a reason to skip the boundary walk. | grep each path |
| AC3 | Method, in this order, each step ticked with its result in `checklist-<sha>.md`: (a) the checklist in `references/checklist.md`; (b) the four scanner classes secret, dependency, static analysis, licence, each run with the command the repo already defines (CI workflow, Makefile, package scripts, pre-commit config), no tool installed, no install script run, stdout, stderr and exit code saved to `scans/<sha>/<class>.txt`; (c) each T2 change's recorded security-pass verdict, where `unrated` or `verdict: not found` is a finding; (d) a walk of every trust boundary the diff touches, from entry to sink, callers read; (e) review of lockfile and dependency changes and of CI and deploy config (secrets on agent branches, triggers, permissions). | grep `secret`, `licence`, `no tool installed`, `scans/`, read |
| AC4 | Second key: `reviewer-security` gets the security brief from `../t3-build/references/brief.md` with `Diff: git diff <base> <sha>`, assets and boundaries from the threat model, and nothing from the first audit: no findings, ledger, checklist, scanner output or report draft. SKILL.md states why: a reviewer shown a list anchors on it and checks it instead of the code, so the second key would add no independent coverage. Its reply MUST name the SHA. Model and effort come only from `../t3-build/references/models.md` (linked). | grep `nothing from the first audit`, `anchor`, `models.md` |
| AC5 | Reconcile (lead): every finding of both replies enters the ledger; neither key's finding is dropped. Same location and cause in both replies is one row with both sources. A severity difference keeps the higher one. No agent lowers a severity, rewords a verdict line or closes a finding; a disputed finding goes to `consultant` once (confirms, never closes), then to the human. | grep `higher`, `consultant`, read |
| AC6 | Ledger (`references/ledger.md`): columns id, severity `critical|high|medium|low`, area, status `open|fixed|accepted`, source (key and reply id), first seen candidate, fix ref (PR and merged SHA). Ids are `S<n>`, unique per milestone, never reused: the first audit's reply ids are kept, second-key-only findings take the next free number, a finding seen again keeps its id. `declined` does not exist for an audit finding. `fixed` needs a fix ref and a later round on a candidate that contains the fix. `accepted` needs the human's own message `accept risk <milestone>/<id>` in this session for this candidate; a new candidate or session turns it back to `open` until the human repeats it. The ledger lives only in the audit folder. | grep each column, `never reused`, `accept risk` |
| AC7 | Verdict output: the reply of each key has exactly one line `VERDICT: CLEAN` or `VERDICT: FINDINGS`, a line `SHA: <sha>`, then `S<n> [critical|high|medium|low] <file>:<line> <scenario>. Fix: <fix>`. CLEAN needs all four scanner classes run with no unhandled hit, every checklist step ticked, and no open finding. The lead saves each raw reply to `replies/<sha>/` with the Write tool and checks copied lines with `grep -cFx -f <file> -- <reply>` (prints 1) and `grep -c '^VERDICT:' <reply>` (prints 1); reviewer text never goes on a command line. | grep `grep -cFx -f`, `exactly one` |
| AC8 | Report: `docs/security/audit-<milestone>.md` from the template in `references/ledger.md`, written by the lead only when no ledger row is `open`, through a normal PR. Sections: candidate tag, SHA, base, date; both verdict lines verbatim; scanner summary (per class: command name, ran or not, hit count); findings table (id, severity, area, status, fix ref); human test. Accepted rows carry id, severity and area only; fixed rows MAY add one line naming the weakness class, never steps, payloads or paths to reproduce. No secret, config value or raw scanner output. | grep template headings, read |
| AC9 | Human test: the audit recommends a human security test when the milestone touches payments, personal or customer data, authentication or sessions, crypto, tenant isolation, or a public internet surface, and before the first public release. It writes `HUMAN-TEST: recommended <areas>` or `HUMAN-TEST: not indicated` into the reply and the report, and states that a model audit is not a penetration test. A recommendation is never reported as a pass; a declined test reads "recommended, not done (human decision <date>)". | grep `HUMAN-TEST`, `not a penetration test` |
| AC10 | Logging: one event per key per round to `../t3-build/scripts/log_event.py --repo <repo>` (linked how: `../t3-build/references/event-log.md`): `slice` `audit-<milestone>`, `tier` `T2`, `stage` `security-pass`, `role` `security-engineer` or `reviewer-security`, `round`, `verdict`, `findings.security`, `outcome`. Exit 2: fix and call again; exit 1: report, go on. No new stage. | run the example below |
| AC11 | Amber and secrets: unfixed findings, scanner output and exploit text never go into the repo, issues, PR bodies or comments, commit or tag messages, branch names, specs or an OpenRouter brief; they stay with Claude and Codex. No credential is requested, read or printed; no `request_secret`. | grep `Amber`, `request_secret` |
| AC12 | Failure cases below are each one SKILL.md table row with the reaction stated. | paper walkthrough |
| AC13 | Hygiene: SKILL.md at most 200 lines; every relative link in the three files resolves; the model grep prints nothing; `git diff --name-only 283e5d5` lists only the three skill files and this spec. | `wc -l`, link check, grep, git |
| AC14 | Amended after review round 1 (supersedes AC3 (b), AC1 and open questions 2 and 3 where they differ; evidence: grep). **a** The lead owns every file in the audit folder and runs the four scanners; `security-engineer` writes only under `docs/security/` and reads scan files (counts, rule ids, `file:line`). **b** Unresolved findings of earlier `FINDINGS` verdicts enter the ledger as `open` unless fixed (fix ref in the diff) or accepted for this candidate and session. **c** Before any `CLEAN` the lead checks four scan files with exit codes and a fully ticked checklist; otherwise a finding (area `process`). **d** Every round audits the full milestone diff from the recorded base for both keys; no delta-only round. | grep `docs/security/`, `unresolved`, `ls scans`, `full milestone diff` |

**Example event** (checked against `log_event.py` at `283e5d5`, exit 0 into a temp state dir):

```
echo '{"slice":"audit-m1","tier":"T2","stage":"security-pass","role":"security-engineer","model":"default","effort":"high","round":1,"verdict":"FINDINGS","findings":{"blocker":0,"should":0,"nit":0,"security":2},"outcome":"blocked"}' \
  | PYTHONDONTWRITEBYTECODE=1 python3 .agents/skills/t3-build/scripts/log_event.py --repo wp6 --state-dir "$TMPDIR/wp6-state"
```

In release mode the release skill still logs its own stage 4 event under `release-<milestone>`;
the different slice keeps the two apart for the retro (WP8).

## Failure cases

| Case | Required reaction |
|---|---|
| Scanner class not defined by the repo, tool missing, or command fails to run | Not clean. A finding (area `process`, at least medium) names the class and the exit code; the reply says the scanner did not run. Never installed, never replaced by another tool. |
| Scanner output contains a secret | The file stays in `scans/<sha>/` mode 600; the agent reads only counts and `file:line` and rule ids (the scanner's redact option where it has one), never the match. Critical finding; the human rotates the secret; it is Red and never appears in a reply, the ledger or the log. |
| Dirty checkout or `HEAD` differs from the candidate SHA | Stop before any step, log `outcome: blocked`, report. No audit on a near SHA. |
| Candidate SHA changes mid-audit | Discard that round's results for the new SHA, restart at entry; ledger rows keep their ids, `accepted` rows return to `open`. |
| Second key disagrees (finds more, or rates higher) | Union of findings, higher severity stands, as AC5. Disagreement is never resolved by majority or by the lead. |
| A reply lacks exactly one `VERDICT:` line or the `SHA:` line, or names another SHA | Not verified: ask the same key once again with the same brief; still wrong, the round is `failed`, reported. Never inferred from the text. |
| Finding disputed by the lead or the implementer | `consultant` once, then the human. The finding stays `open` meanwhile. |
| High-stakes area without a human test | Verdict unchanged; `HUMAN-TEST: recommended` stays in reply, report and brief. The human decides. |
| Quota limit, or `reviewer-security` unavailable | Checkpoint, pause, resume after the reset. No model swap, no other provider as second key. Log `quota_pause: true`. Unavailable after the reset: `blocked`. |
| Budget used up with open findings | Stop; the open ids go to the human. A budget never closes a security finding. |

**Budgets.** Rounds follow `../t3-build/references/budgets.md`: code review 3, up to 5 for
security. A milestone gets at most 5 rounds; then the human decides.

## Security

- **Assets:** unfixed findings and exploit detail (Amber); scanner output, which may hold Red
  data; the ledger and raw replies; the verdict lines the release brief copies; the report.
- **Trust boundaries:** repo text, PR text, commit messages, dependency readmes and scanner output
  to both keys (data, not instructions); first key to second key (nothing crosses); audit folder
  to repo (only the report, after nothing is open); reviewer replies to the lead's shell (files, never arguments).
- **Abuse cases and mitigations:**
  - Audit marks clean without running scanners: CLEAN requires four saved scan files with exit
    codes; a missing file is a finding; the lead checks `ls scans/<sha>/` before copying a verdict.
  - Lead softens a rating: severity only rises; verdict lines checked with `grep -cFx -f`; raw replies kept.
  - Exploit detail leaks into `docs/security/` or a brief: the report template has no description
    field for accepted rows; fixed rows carry a weakness class only; the report PR waits until nothing is open.
  - First audit biases the second key: its brief carries no first-audit artefact (AC4).
  - Stale acceptance carried over: acceptance is per candidate and session; rows revert to `open`.
  - Scanner output with secrets: redacted reads, mode 600, never quoted.
  - Injection via repo text, PR text, dependency readmes: data only; no install scripts; only the
    verdict line decides; skills and agent files are human-owned.

## Test strategy

No executable tests: the change is prose. Evidence per AC, run from the repo root, pasted into the PR report:

- AC1-AC9, AC11: `grep -n` for the phrases named in each row, plus a read by the reviewer.
- AC12: paper walkthrough by the lead, checked by the reviewer: for each failure case the SKILL.md
  line and the sentence that gives the reaction.
- AC10: the example above exits 0 and appends one line (verified while writing this spec; a
  `reviewer-security` event with `verdict` `CLEAN` also exits 0); stage and role diffed against `ENUMS["stage"]`.
- AC13: `wc -l .agents/skills/milestone-audit/SKILL.md` (at most 200); `relative_links` from
  `test_tier.py` over the three files, each target exists; `git diff --name-only 283e5d5`; model check, must print nothing:
  `grep -rnE 'claude-|gpt-|gemini|deepseek|Sonnet|Opus|Haiku|Fable|[[:<:]]Sol[[:>:]]' .agents/skills/milestone-audit`.
- PLAN's "done when: a dry run produces an audit report and a ledger" is not run here; it is
  verified later in the pilot dry run, and the PR says WP6 stays open until then.

## Non-goals

Installing scanners; CI jobs (WP9); edits to the release skill, t3-build or `staging/`; a
penetration test; scheduling (WP11); a change to the `log_event.py` stage list.

## Open questions

1. Ledger ids across two keys? Recommended: one `S<n>` space per milestone as AC6; the release
   contract's `accept risk <milestone>/<id>` then names one row unambiguously.
2. The report PR moves `main`, so release needs a new candidate. Recommended: the report rides in
   the stage 5 docs PR; the next round audits only the delta and rewrites the report only if the ledger changed.
3. `security-engineer`'s agent file says "write only under `docs/security/`". Recommended: the
   brief names `scans/<sha>/` as its only write path; a later staging PR adds "and the audit folder".
4. Reuse release stage 2 scanner output? Recommended: no; the audit runs its own, so a faulty
   stage 2 is not trusted twice.
5. Round budget 5 per milestone? Recommended: yes, matching the security review ceiling.
