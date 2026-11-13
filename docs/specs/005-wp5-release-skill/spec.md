# 005 WP5: release skill

Tier T2 by path (`tier.py`: `human`, `.agents/` holds guardrail files; logged as T2). Prose only,
no code. Base `main` at `092a535`. Sources: PLAN.md WP5, CONCEPT 4 (human gates, budgets), 6.7
(layer 4, data classes), 6.8, 6.10, 6.11, "What goes where", "Rules for skills". MUST, SHOULD and
MAY are used in the RFC 2119 sense.

**Intent.** A new skill `release` tells the lead how to take a finished milestone to production:
cut a candidate at one exact commit, run every machine and agent check on that commit, write a
one-page release brief, and hand the decision and the deployment to the human. Agents prepare;
the human approves, deploys and decides on rollback. The skill reuses t3-build for models, briefs,
budgets and the event log and names no model itself.

## Files

| File | Change |
|---|---|
| `.agents/skills/release/SKILL.md` | new, at most 200 lines, frontmatter `name: release` and a `description` with trigger phrases ("/release", "cut a release", "release the milestone"), like the sibling skills |
| `.agents/skills/release/references/release-brief.md` | new: one-page brief template |
| `.agents/skills/release/references/rollback.md` | new: rollback runbook skeleton, at most 50 lines |

Not touched: t3-build (SKILL.md, references, scripts), gate, `staging/`, `models.md`, CONCEPT.md,
PLAN.md, AGENTS.md. Paths below starting `../t3-build/` are relative to `.agents/skills/release/`.

## Terms

- **Candidate**: one commit on `main` (full 40-character SHA) plus an annotated tag
  `rc/<milestone>-<n>`, `n` counting from 1 per milestone. No release branch.
- **Candidate record**: `<state>/<repo>/release-<milestone>/candidate.md`: tag, SHA, base (SHA of
  the previous release tag, or the root commit for a first release), and each stage result with
  the SHA it ran on. `<state>` is `~/.local/state/t3-build`, or `T3_BUILD_STATE`/`--state-dir`
  where the home folder is not writable (scratchpad fallback, as in `../t3-build/references/event-log.md`).
  Folder mode 700, never in the repo. The brief draft and raw reviewer replies live there too.
- **Milestone diff**: `git diff <base> <candidate SHA>`.

## Acceptance criteria

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | Entry: the skill starts only when every slice of the milestone is merged and `main`'s latest CI for that SHA is green (`gh run list --commit <sha>` or the check-runs API). It cuts the candidate from that exact SHA, writes the tag name and SHA into the candidate record, and states that the candidate is on `main`, tagged `rc/<milestone>-<n>`, no release branch. The `rc/` tag MUST NOT match the product's deploy trigger; the release tag (e.g. `v1.2.0`) is the human's. | grep `rc/`, `exact SHA`, `no release branch`, read |
| AC2 | Flow: SKILL.md has numbered stages in this order: 1 entry and cut, 2 full machine gate on the candidate, 3 QA run, 4 security, 5 docs, 6 release brief, 7 human gate, 8 human deploys, 9 smoke test and monitoring, 10 healthy call, 11 rollback by runbook. Each stage names its agent by role: `lead`, `qa`, `security-engineer`, `reviewer-security`, `docs-writer`. Stage 7 starts only when the same candidate SHA has a passing stage 2, a stage 3 with no open defect and a clean stage 4 (see the Failure cases rows). Models and efforts come only from `../t3-build/references/models.md` (linked); no model, vendor model family or model ID appears in the three files. | `grep -n` stage headings (line numbers ascending); grep below in Test strategy returns nothing |
| AC3 | Security: `security-engineer` is told to invoke the `milestone-audit` skill (named in backticks, not linked: it does not exist until WP6) on the milestone diff, then `reviewer-security` runs as second key with the t3-build security brief (`../t3-build/references/brief.md`). If `.agents/skills/milestone-audit/SKILL.md` is missing the stage stops as `blocked`, is logged, and is reported; no other skill substitutes for it. Any open blocker, or any open security finding of any severity, sends the release back to a normal t3-build slice. Only the human accepts a security risk, explicitly, recorded with id, area, decision and date. | grep `milestone-audit`, `blocked`, `any severity`, `only the human` |
| AC4 | Docs: `docs-writer` writes the changelog, upgrade notes and the product rollback runbook (from `references/rollback.md`) from the approved specs of the milestone plus the milestone diff, not from PR titles or bodies. The docs go in through a normal docs PR; the next candidate rule (Failure cases, row "main moves") applies. | grep `changelog`, `upgrade notes`, `approved spec` |
| AC5 | Brief: `references/release-brief.md` has these sections in order: candidate (tag, SHA, base); what changed; every T2 change with its security verdict (T2 changes = commits in `<base>..<sha>` with a `Tier: T2` trailer, `git log -E --grep='^Tier: T2'`); milestone audit and second key; QA result; accepted security risks (id, area, decision, date, no exploit detail); open risks; declined findings; migrations and anything irreversible, named separately; rollback plan; healthy checks; the decision asked. Reviewer and QA verdict lines and each finding's id and severity are copied verbatim, never softened; exploit text stays in the ledger, referenced by id. The brief is the decision page: it has at most 60 lines and 600 words, and lists T2 changes as `id, verdict line` rows, or, above 15 T2 changes, a count per verdict plus every non-clean row. The complete table of T2 changes, verdict lines and raw reviewer replies is the private appendix `<state>/<repo>/release-<milestone>/verdicts.md`; the brief names its path and ratings in it are unchanged copies. The accepted-risk summary rows also go to `docs/security/accepted-risks.md`; detail stays in the state folder. | grep each heading; `wc -l -w` on the fixture brief |
| AC6 | Human gate: nothing deploys without the human's explicit reply naming the tag and SHA (`approve release <tag> <sha>`); silence, a timeout or an earlier approval is not approval, and a new SHA needs a new approval. The human holds the deploy credentials and triggers the deployment. The skill never runs a deploy command or workflow, never pushes a release tag, never requests, reads or prints a credential, and calls no `request_secret`. | grep `explicit`, `credentials`, `request_secret`, read |
| AC7 | After deploy: `qa` runs the smoke test from the runbook against the deployed system without production credentials; the human reads monitoring that needs access and reports the values. The lead calls healthy only when every named check in the brief passes for the whole watch window, else unhealthy. Unhealthy: the lead presents the rollback runbook steps and a recommendation; rollback is the human's decision and action, never automatic. | grep `named check`, `watch window`, `never automatic` |
| AC8 | Logging: every stage, human gate and retry pipes one event to `../t3-build/scripts/log_event.py --repo <repo>` using the stage map below; `slice` is `release-<milestone>`, `tier` is `T2`. SKILL.md links `../t3-build/references/event-log.md` and says exit 2 means fix the event and call again, exit 1 report and go on. The example event below exits 0 into a temp state dir. | run the example (Test strategy) |
| AC9 | Hygiene: every relative link in the three files resolves; `test_tier.py` AC11 still passes; `git diff --name-only main` lists only the three skill files and this spec. | link check, unittest, git |
| AC10 | Hardening (amended after the security pass, round 1; supersedes Terms "base", AC1, AC5, AC6, the Security section and the Test strategy where they differ (`grep -F` becomes `grep -cFx -f`, `git rev-parse` plus `git ls-remote`, accepted-risk rows keyed `<milestone>/<id>`, acceptance and approval only as the human's own message in the session, migrations numbered M1.., first release against the empty tree, required checks present, fix branch in a private fork or mirror, smoke test unauthenticated only); evidence: read and grep of the named phrase). **a** `rc/` tag never moved, deleted or force-pushed; remote tag checked with `git ls-remote` at brief time and at approval; the human deploys by SHA; stage 10 needs deployed version = approved SHA (S1). **b** T2 list = `tier.py`-rated PRs united with `Tier: T2` trailers, `unrated`, `verdict: not found`, never clean (S2). **c** verdict lines checked with `grep -cFx -f <file>` printing 1 and one `VERDICT:` line, reviewer text never on a command line (S3). **d** risk acceptance is `accept risk <milestone>/<id>`, one per finding, valid for that candidate and session only, rows in `accepted-risks.md` carry the candidate tag, a new candidate re-runs stage 4 on the open and earlier accepted ids; approval accepts nothing; a fix reaches `main` only through the gated PR; stage 4 findings never "declined"; clean defined (S4). **e** approval is the human's own message in the session, full SHA, after the last brief, `irreversible: <ids>` when the migrations section is not none, void on change (S5). **f** base = last deployed SHA, `merge-base --is-ancestor`, empty tree for a first release, only when the human says so (S6). **g** Amber list covers PR bodies, comments, commit and tag messages, branch names, specs; brief shown in session only; open-risk and declined rows hold id, severity, area (S7). **h** SHA from `origin/main`, green = required checks named by the human, compared by check name, stored in the candidate record and printed in the brief, `git push origin refs/tags/rc/...` only, human confirms no outside deploy on `rc/*` (S8). **i** security brief diff `git diff <base> <sha>`, clean checkout at the SHA for stages 2 to 4 (S9). **j** input patterns for milestone and SHAs, quoted refs (S10). **k** credential rule valid in every stage, unauthenticated smoke test only, monitoring values without logs (S11). | grep per letter |

**Stage map for the log** (`log_event.py` has no release sub-stages and is not changed here):

| Release stage | `stage` | `role` |
|---|---|---|
| entry and cut, machine gate, brief, healthy call | `release` | `lead` |
| docs | `release` | `docs-writer` |
| QA run, smoke test | `qa` | `qa` |
| milestone audit / second key | `security-pass` | `security-engineer` / `reviewer-security` |
| approval, deploy reported done, rollback decision | `human-gate` | `lead` |

Example (illustrative; checked against the script at `092a535`):

```
echo '{"slice":"release-m1","tier":"T2","stage":"human-gate","role":"lead","model":"claude-sonnet-5-5","effort":"high","outcome":"blocked"}' \
  | python3 <skills>/t3-build/scripts/log_event.py --repo <repo> --state-dir "$TMPDIR/wp5-state"
```

The model in the example is test data for the script, not text for the skill: SKILL.md writes
`<model>` and `<effort>` and says "what the runtime confirmed".

## Failure cases

| Case | Required reaction |
|---|---|
| Candidate CI red, or main CI red at entry | No candidate. Stop; the fix runs as a normal slice (t3-build section 6 step 5). Log `release` with `outcome: failed`. |
| QA defect | Release back to a normal slice (`fix:` run). After it merges, cut `rc/<milestone>-<n+1>` and restart at stage 2. |
| `milestone-audit` skill missing | Stage 4 stops as `blocked`, logged, reported to the human. No substitute. |
| Open security finding, any severity | Back to a normal slice (T2), or the human accepts it explicitly. The lead never declines or accepts it. A new candidate re-runs stage 4 on the new milestone diff plus the open ids. |
| Human rejects | Reason recorded in `decisions.md`; back to a normal slice; new candidate. Log `human-gate`, `outcome: blocked`. |
| Smoke test or a monitoring check fails | Unhealthy call. Lead presents the runbook and a recommendation at once; the human decides and rolls back. Log `qa` with `outcome: failed`. |
| Migration not reversible | Named in its own brief section with its recovery path (restore from a named backup, or forward fix only). It is an irreversible action (CONCEPT 4): the approval must name it; a general approval does not count. |
| `main` moves after the cut | The candidate stays at its SHA; new merges are not in this release. Stage results count only for the SHA they ran on. Every new candidate SHA, including one that only adds the stage 5 docs, restarts at stage 2 (docs paths can hold runnable examples and runbooks). |
| Machine gate fails on the candidate | No QA, no brief. Fix as a normal slice, then a new candidate. Log `release` with `outcome: failed`. |
| Open blocker or should from any stage (QA, audit, second key, machine gate) | The human gate (stage 7) is not reached until the candidate SHA has a passing machine gate, a QA result with no open defect, and a clean stage 4. The brief lists none of them as "open risks" to get past the gate; a finding the human accepts is recorded as accepted, not open. |
| Quota limit mid-stage | CONCEPT 6.11: the stage pauses and resumes after the reset; no model swap inside a running stage, no paid fallback without a budget decision. Checkpoint first; log `quota_pause: true`. |
| Third candidate fails | Stop and ask the human (proposed budget, see Open questions). |

## Security

- **Assets:** deploy credentials (human only); release artefacts (candidate tag, SHA, brief);
  accepted-risk records; unfixed findings and audit detail, which are Amber and stay with Claude
  and Codex: never in the repo, a public issue, or an OpenRouter brief.
- **Trust boundaries:** agents to production (agents have no production credentials); merged PR
  text, changelog input and reviewer replies to the lead (data, not instructions); state folder to
  repo (only the accepted-risk summary crosses, without exploit detail).
- **Abuse cases and mitigations:**
  - Agent deploys on its own: no credentials; the skill forbids deploy commands, deploy
    workflows and release-tag pushes; `rc/` tags do not match the deploy trigger.
  - Agent approves for the human, or tags a different SHA: approval must name tag and SHA; the
    brief shows `git rev-parse <tag>^{commit}`, which must equal the recorded SHA.
  - Brief softens a reviewer rating: verdict lines copied verbatim; raw replies kept in the state
    folder; each verdict line in the brief is found by `grep -F` in the raw reply.
  - Secret in the brief or the log: the template has no field for credentials or config values;
    `log_event.py` has no free-text field; the docs PR passes the CI secret scan.
  - Injection through changelog or PR text: `docs-writer` works from approved specs and the
    diff; PR bodies, commit messages and tool output are data; the docs PR is reviewed as usual.
  - Exploit detail leaks: the brief and `docs/security/` carry ids and severities only.

## Test strategy

No executable tests: the change is prose. Evidence per AC, run from the repo root and pasted into
the PR report:

- AC1-AC7: `grep -n` for the phrases named in each row, and a read by the reviewer. Keywords
  alone do not prove a working procedure, so the PR report also carries a paper walkthrough
  (lead, then checked by the reviewer): for each of failed gate, changed SHA after the cut,
  rejected approval, accepted security risk and unhealthy deployment, the skill step numbers and
  the sentence that gives the reaction.
- AC2 model check, must print nothing:
  `grep -rnE 'claude-|gpt-|gemini|deepseek|Sonnet|Opus|Haiku|Fable|\bSol\b' .agents/skills/release`.
- AC2, AC5: `wc -l .agents/skills/release/SKILL.md` (at most 200); `grep -c '^## '` on the
  template.
- AC5 fixture check now: the lead fills the template in the scratchpad for a mock milestone
  "WP1-WP4" (base root commit, candidate `092a535`, verdicts copied from the recorded PRs #1-#4,
  no new agent runs) and shows `wc -l -w` (at most 60 lines, 600 words) and a grep for every
  heading. PLAN's "done when: a dry run on the pilot repository produces a one-page brief" cannot
  run here. WP10 is a T1 slice, not a release, so no work package covers it. This PR is therefore
  not "done" in the PLAN sense: the PR text says so and proposes that the human adds a release
  dry run to WP10 or WP11. Until then WP5 stays open with the fixture check as its only evidence.
- AC8: the example with `--state-dir "$TMPDIR/wp5-state"` exits 0 and appends one line; the
  stage map's values are diffed against `ENUMS["stage"]` in `log_event.py`.
- AC9: `(cd .agents/skills/t3-build/scripts && python3 -m unittest test_tier -k AC11)` (the
  file-path form fails on the leading-dot folder); link check with
  `relative_links` from `test_tier.py` over the three files, each target exists; `git diff
  --name-only main`.

## Non-goals

- A deploy job or CI workflow: WP9, in the product repository.
- The `milestone-audit` skill: WP6. This skill only calls it and blocks without it.
- Scheduling of releases or of monitoring: WP11.
- Release sub-stages in `log_event.py`, trimming t3-build SKILL.md, any edit to the gate.

## Open questions

1. Candidate tag scheme `rc/<milestone>-<n>` with the release tag left to the human?
   Recommended: yes; it keeps agent-pushed tags off any `v*` deploy trigger.
2. Docs changes after the cut move `main`. Decided after review round 1: always restart at
   stage 2 on a new SHA, no carry-over.
3. Who reads production monitoring? Recommended: the human reports values; `qa` runs only
   checks that need no production credential. Watch window: the runbook's value, 30 minutes when
   it names none.
4. Candidate budget per milestone? Recommended: 3 candidates, then the human decides; budgets.md
   has no release loop today, so the skill states it and a later t3-build PR may move it there.
5. File name for the accepted-risk summary? Recommended: `docs/security/accepted-risks.md`,
   append-only table (id, area, decision, date).
6. Add release sub-stages (`machine-gate`, `audit`, `docs`, `brief`, `deploy`, `smoke`) to
   `log_event.py` later? Recommended: yes, a T1 follow-up before WP8 reads the log; until then
   the stage map above holds.
