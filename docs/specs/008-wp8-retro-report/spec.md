# 008 WP8: retro report script

Tier T1 as PLAN names it (`tier.py`: `human`, `.agents/` holds guardrail files; logged as T1; the
human merges). Branch `agent/wp8-retro-report`, stacked on WP7 `1bcbbcf` (PRs #5 to #7 unmerged).
Sources: PLAN.md WP8, CONCEPT 6.12 and section 8 ("Calibration"), `scripts/log_event.py`,
`docs/specs/003-wp3-event-log/spec.md`. Lead writes this spec (short, T1). MUST as in RFC 2119.

**Intent.** `retro_report.py` reads the event log and the git history of the repository, prints the
five calibration metrics and the proposed-changes table of CONCEPT 6.12 as Markdown on stdout, and
changes nothing. No model call, no network. Fewer than 20 data points for a role and tier:
"keep collecting", no proposal.

## 1. Files

| File | Author |
|---|---|
| `.agents/skills/t3-build/scripts/test_retro_report.py` | test-author (first, red) |
| `.agents/skills/t3-build/scripts/retro_report.py` | implementer |

Not touched: `log_event.py`, `SKILL.md` and references, `tier.py`, gate, `maintain` (it calls the
script by path). Python 3 standard library only; `from log_event import validate` is allowed.

## 2. Interface

`python3 retro_report.py --repo NAME [--state-dir DIR] [--git PATH] [--since YYYY-MM-DD]
[--ci-runs FILE] [--today YYYY-MM-DD]`

- Log: `<state>/<repo>/events.jsonl`; `<state>` is `--state-dir`, else `T3_BUILD_STATE`, else
  `~/.local/state/t3-build`. `--repo` must match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`.
- `--git` default: the current directory. If it is not a git work tree and was not given, the git
  sections print `git: not available` and the run continues; an explicit bad path exits 2.
- `--since`: only events whose `ts` date, and commits whose commit date, are on or after it.
- `--today` (default: today UTC) only fills the report header; it makes output reproducible.
- Exit 0: report printed. Exit 2: bad argument or unreadable `--ci-runs` (message on stderr,
  nothing on stdout). Exit 1: I/O or permission problem on the log. A missing log file is not an
  error: the report says `Events: 0` and every metric `keep collecting`.
- The log must be a regular file: a symlink or other file type is an I/O error (exit 1). Lines are
  read with a bound; a line over 4096 bytes is drained, never kept.
- `--since` also limits the failed checks counted from `--ci-runs`. `git log` runs with
  `--no-show-signature` so a hostile repository config cannot run a program.
- Writes nothing: no file, no directory. Runs `git` only with read commands and an argument list
  (never a shell).

## 3. Reading the log

Each line is JSON. A line is **malformed** when it is not a JSON object, is longer than 4096
bytes, or fails `log_event.validate` after its `ts` key is removed, or has no valid `ts`, or holds an
integer above 10^9, or makes the parser raise (deeply nested JSON, huge numbers). Malformed lines are counted and
skipped. A slice is the set of events with the same `slice`. A **group key** is
(producer role, tier, producer model): events of another model never mix, so a model change restarts
the count (CONCEPT 6.12 rule 6).

## 4. Output

Header `# Retro report <repo> <today>`, then the line
`Events: <n> (malformed skipped: <m>). Slices: <s>.`, then the sections in this order.

### 4.1 Cross-check with git

An `Agent` or `Tier` trailer is a message line starting `Agent: ` or `Tier: ` in any paragraph of
the commit message: the repo footer (`references/commit-trailers.md`) puts them in a paragraph
before `Co-Authored-By:`, which git's own trailer parser ignores (amended after the first
real run on this repo: 0 counted instead of 29).

Table `| item | log | git |` with rows, in this order, `pull requests merged` (log: slices with a
`ship` event; git: commits on `HEAD` whose subject starts `Merge pull request #`), `commits with
Agent trailer` (log `-`), `agent commits without Tier trailer` (log `-`), `reverts` (log `-`; git:
commits whose message has a line `This reverts commit <40 hex>`), `failed checks after merge` (log
`-`; git: from `--ci-runs`, a JSON list of `{"sha": ..., "conclusion": ...}`: entries with
`conclusion` `failure` whose `sha` is on the first-parent history of `HEAD`; without the option the
cell reads `n/a (no --ci-runs)`). Then one line per flag, each starting `FLAG:`:
- git has more merged pull requests than the log has `ship` slices: the log may be incomplete;
- agent commits exist and the log has no events;
- agent commits without a `Tier:` trailer exist.

### 4.2 Metrics

**M1 first-pass approval** table `| role | tier | model | n | approved | rate | result |`. For each
slice, the earliest `spec-review` event with `round` 1 judges the slice's latest `spec` event; the
earliest `code-review` event with `round` 1 judges its latest `implement` event. Approved means
verdict `APPROVE`. Producer role and model come from the judged event; a slice without one counts
as role `unknown`, model `unknown`. Tier is the reviewer event's tier. `rate` is approved/n with
two decimals. `result`: n below 20 `keep collecting (n=<n>)`; rate above 0.90 `try one tier down`;
below 0.60 `one tier up`; else `keep`.

**M2 review rounds per slice** table `| tier | slices | mean rounds | at budget | result |`: rounds
of a slice are the highest `round` of its `code-review` events; `mean rounds` two decimals; `at
budget` counts slices with 3 or more rounds. `result`: slices below 20 `keep collecting`;
`at budget` share of 0.30 or more `specs too vague`; else `keep`.

**M3 escaped defects per tier** table `| tier | reverts |`: each git revert is counted under the
`Tier:` trailer of the commit it reverts (`T0`, `T1`, `T2`), or `unknown` when it has none. Rows
for all four keys, in that order.

**M4 limit hits per ISO week** table `| week | hits |`: events with `quota_pause` true, per ISO
week of `ts` (`YYYY-Www`), weeks ascending, only weeks with hits.

**M5 OpenRouter spend per task**: one line `not recorded in the event log`.

### 4.3 Proposed changes

Table `| signal | evidence | proposal |`, one row per fired rule, else the line `none: keep
collecting`. Rules (constants at the top of the script):

1. M1 row with n of 20 or more and rate above 0.90: `try one tier down` for that role and tier;
   below 0.60: `one tier up`.
2. M2 row with slices of 20 or more and `at budget` share of 0.30 or more: `specs too vague`.
3. M3: any revert under `T0` or `T1`: `extend the T2 trigger list`.
4. M4: any week with 3 or more hits: `shift heavy jobs, reduce parallel workers`.
5. `human-gate` events: 20 or more and 0.30 or more with `outcome` `blocked`: `improve the decision
   brief`.
6. `implement` and `tests` events: 20 or more and 0.30 or more with `attempt` 3 or more: `slices too
   large or specs too vague`.
7. `code-review` events with `round` 3 or more: 20 or more and 0.90 or more with `blocker` 0:
   `lower the round budget for that tier`, one row per tier meeting it.

Signals that need finding text (same finding comes back, reviewer findings rejected) are not in the
log; the report says so in one line under the table.

## 5. Acceptance criteria

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | Interface and exit codes of section 2, including a missing log, bad `--repo`, bad `--since`, bad `--git`, bad `--ci-runs`. | tests |
| AC2 | Malformed lines are counted, skipped, never crash; `Events` and `malformed skipped` correct. | tests |
| AC3 | Cross-check rows and the three flags of 4.1 against a fixture git history (merge commits, trailers, a revert, `--ci-runs`). | tests |
| AC4 | M1 to M5 of 4.2 exactly as specified, including `unknown`, model separation and the 0.90 and 0.60 boundaries (0.90 and 0.60 themselves are `keep`). | tests |
| AC5 | Proposed changes: each of the seven rules fires at its threshold and not below; under 20 points no proposal. | tests |
| AC6 | `--since` filters both sources; `--today` only the header. | tests |
| AC7 | Read only: no file or directory created; no network; `git` called with an argument list and read commands only; commit messages are never printed, only counts and the validated fields (`role`, `model`, `tier` strings come from validated events). | tests (directory listing before and after; scan of source for `shell=True`, `socket`, `urllib`) |
| AC8 | Fixture run: a fixture log and a fixture git history give the expected report (golden output). PLAN's "done when". | test |

## 6. Security

Assets: the event log and git history (Amber, local). Boundary: log lines and commit messages are
data from agents: the report prints only validated enum or token fields and numbers, never free
text; `git` receives no value taken from the log. No credential, no network.

## 7. Non-goals

Cost data (not in the log); finding-text signals; writing the report into the repository (a normal
PR does that, by hand); scheduling (WP11); changes to `log_event.py`, SKILL.md or references.
