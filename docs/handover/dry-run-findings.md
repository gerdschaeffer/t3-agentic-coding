# Dry-run findings

Dry runs of the retro report and the `maintain` checks on this repository, and a paper run of
`milestone-audit`, on `main` `16b948f` (2026-11-14). They need no product repository and no scanner.
Reports were written to the session scratchpad, not to the repo; this file keeps the findings.

## Retro report (`retro_report.py`)

Log: 40 events written with `log_event.py` during the unattended run (4 slices: housekeeping,
FU15, FU16, FU17), `--git` this repository.

| Finding | Evidence | Route |
|---|---|---|
| RR-1. M1 attributes a slice's first-pass result to its **latest** `implement` event, not the last one before the review. When fix attempts follow the review (often by another agent), the producer is misattributed: FU15's code-review round 1 (an implementer's code, approved) is counted for the lead, whose attempt-5 `implement` event is the latest. | Spec 008 section 4.2 says "latest `implement` event" and test `test_m1_earliest_round1_review_judges_the_latest_spec` pins a judged event that comes after the review; report row `lead T2 claude-sonnet-5-5 n=2 approved 1` | Needs a human decision: it is a deliberate rule of spec 008. A change is small (judge the latest event before the round 1 review) but edits the spec and a frozen test, so it is not done here |
| RR-2. Security-pass rounds are not in M2 (code-review only) and not in M1. FU15 needed five security rounds after a code-review round 1 APPROVE and counts as one clean round. | M2 row `T2 1 slice mean rounds 1.00` | Observation. Decide with the human whether M2 should count security rounds (spec 008 change); not fixed here |
| RR-3. The cross-check flags `git has 30 merged pull requests, the log 4 shipped slices`. Expected on the first run (the log starts today), and the flag text is the intended one. | table "Cross-check with git" | None |
| RR-4. M6 shows 26 pull requests in lane `unknown`: only PRs logged with `pr` and `lane` have a lane. Expected until the log covers every PR. | M6 table | None |
| RR-5. This repo has no running gate, so the `lane` on every `ship` event is the lead's reading of the policy (`.agents/` paths rate 3: block), not the gate's routing. The M6 block row counts that reading. | `ship` events with `lane: block` | Follow-up PR: one sentence in `event-log.md` |

No scanner and no model was needed. Thresholds (20 points per cell) were not reached, so the report
proposed nothing ("keep collecting").

## `maintain` checks

| Check | Result |
|---|---|
| dependencies | `not run: no manifest` (no `package.json`, `pyproject.toml`, lock file or Makefile in this repo) |
| advisories | `not run: no manifest` |
| flaky tests | Three runs of each suite at `main` `16b948f`, detached: scripts 637 tests OK (3 skipped) three times, gate 189 OK three times, CI templates 73 OK three times. No test differed between runs. The three skips are in the scripts suite and were skipped in every run (not investigated here). Other work ran on the machine during the runs. |
| doc drift | 30 backticked paths in `README.md`, `AGENTS.md`, `CLAUDE.md`, `PLAN.md`, `docs/HANDOVER.md` do not exist. All 30 are expected: `.claude/`, `.codex/`, `.opencode/` agents and `.claude/skills` (installed by the human, WP0), `.github/risk-gate/`, `.github/workflows/` and `docs/security/` (product repo paths), `/t3-build` (a command), `origin/main` (a ref), `security-engineer.md`, `reviewer-security.toml` and `third-voice.md` (file names inside installed folders), `actions/checkout` (an action). No real drift found. Relative Markdown links under `.agents/`, `docs/` and the root: none broken. `check_agents.py` on `staging/`: 20 files match `models.md`. |
| metrics | the retro report above |

A doc-drift check that runs on this repo reports the same 30 lines every week. `checks.md` section 4
does not say how to treat paths that a doc names but a later step creates (a folder the human
installs, a product-repo path). Follow-up PR: `checks.md` section 4 says such a path is reported
as `expected: not installed`, not as a mismatch.

## `SKILL.md` size (found by the same pass)

| Finding | Evidence |
|---|---|
| SD-1. `t3-build/SKILL.md` has 224 lines; CONCEPT "Rules for skills" says each skill is under about 200 lines and WP4's done-when says "fits the 200-line skill limit". The other skills are 199 lines or fewer. | `wc -l .agents/skills/*/SKILL.md` |

Route: move one section into `references/` (docs, T1; a guardrail file, so the human merges).
Not done in this run: the file is outside the files this run was allowed to change.

## `milestone-audit` on paper

The skill needs, for CLEAN, all four scanner classes (secret, dependency, static analysis, licence)
as files in `scans/<sha>/` with their exit codes (stage 5). This repository defines no command for
any of them (no Makefile). A scanner-free path does not exist: an audit of this repo would end
`FINDINGS` (area `process`: missing evidence) by design, and the dry run was not started. A real audit needs
repo-defined commands for all four scanner classes and their recorded results.

## What was not done

No product repository, no scanner, no real PR comment from GitHub for the rating audit, no
GitHub Actions run. The `release` skill's dry run (WP5) and the audit's dry run (WP6) stay on the
human list.
