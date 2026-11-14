# 019 FU12: per-lane outcomes in the retro report

Tier T1 as logged (`tier.py`: `human`, `.agents/` holds guardrail files; the human merges). Branch
`agent/fu12-lane-metrics`, from `main` `0e4cee5`. Extends `docs/specs/003-wp3-event-log` (two optional
fields) and `docs/specs/008-wp8-retro-report` (a sixth metric). Source: the Opus 5.5 advisory on
automation risk: before the auto lane is widened, the escape rate of the auto lane must be known
and compared with the review lane. Lead writes this spec (short, T1). MUST as in RFC 2119.

**Problem.** The report has no lane dimension. The gate's lane (`auto`, `review`, `block`) lives
only on GitHub (status and risk card), so neither the log nor git can tell which merged PR went
through which lane, and reverts cannot be attributed to a lane.

## Change

1. **Event log.** `log_event.py` accepts two optional fields on any event: `pr` (integer, 1 to
   10^9, the pull request number) and `lane` (`auto`, `review` or `block`). Both are validated like
   the other fields (exit 2 on a bad value). The lead logs them on the `ship` event once the gate
   has routed the PR (from the gate's card or dry run), see `references/event-log.md` and
   `references/ship.md` (lead edits the prose).
2. **retro_report.py.**
   - `read_git` also returns the PR number of each counted PR commit (`prn`): from
     `Merge pull request #<n>` or from the ` (#<n>)` suffix (spec 018); `None` for other commits.
   - A new section **M6 lane outcomes** after M5. The lane of a PR is the `lane` of the latest
     `ship` event in file order that has both that `pr` number and a `lane` (a later ship event without
     `lane` does not override it; `--since` does not limit this lookup); a PR commit without such an
     event is lane `unknown`. Only `ship` events count. Table `| lane | pull requests | reverts | revert rate | result |`,
     rows `auto`, `review`, `block`, `unknown` in that order. `pull requests` counts git PR commits
     (after `--since`) per lane. `reverts` counts each `This reverts commit <sha>` line (after
     `--since`) under the lane of the reverted commit's own PR number (the reverted commit's subject
     must be a PR commit with a number found in the log; otherwise `unknown`). `revert rate` is
     reverts/pull requests with two decimals (`-` when there are no PRs). `result`: for `auto` and
     `review` with fewer than `LANE_MIN = 20` pull requests `keep collecting (n=<n>)`; otherwise
     `keep`, except `auto` with a revert rate above the `review` rate: `auto lane escapes more than
     review`. `block` and `unknown` rows have result `-`.
   - Git not available: the section says `git: not available`.
   - Proposed changes: rule 8, when the `auto` row has 20 or more pull requests and its revert rate
     is above the `review` revert rate (review PRs 0 counts as rate 0): `tighten the auto lane` with
     the evidence `auto: <r>/<n>, review: <r>/<n>`.
3. The golden output of spec 008 AC8 gains the M6 section (`auto`, `review`, `block` at 0 with `keep
   collecting (n=0)` for `auto` and `review`, and `unknown` at 1: the fixture's one PR has no `pr` in its
   ship event); no other line of any existing test output changes.

## Files

| File | Author |
|---|---|
| `.agents/skills/t3-build/scripts/test_log_event.py`, `test_retro_report.py` | test-author (first, red): append classes; the golden fixture of 008 AC8 is updated as 3 says; list every edited test |
| `.agents/skills/t3-build/scripts/log_event.py`, `retro_report.py` | implementer |
| `.agents/skills/t3-build/references/event-log.md`, `references/ship.md` | lead |

## Acceptance criteria

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | `log_event.py`: `pr` 1 and 10^9 are valid; 0, -1, 10^9+1, `"7"`, `7.0`, `true`, null are exit 2 naming `pr`; `lane` `auto`, `review`, `block` valid; `Auto`, `human`, `""`, 1 exit 2 naming `lane`; the 4096-byte limit still applies; existing tests pass. | tests |
| AC2 | `retro_report.py` accepts log lines with `pr` and `lane` (not malformed); a line with a bad `lane` or `pr` is malformed as before. | tests |
| AC3 | PR numbers: `Merge pull request #12 from o/x` gives 12; `Add x (#7)` (squash rule of 018) gives 7; a merge commit that matches both gives its `Merge pull request #<n>` number; non-PR commits have none. | tests |
| AC4 | M6 table counts: a fixture with 3 `auto`, 2 `review`, 1 `block` PRs by log events and 1 PR without an event gives rows 3, 2, 1, 1 (`unknown`); the latest `ship` event of a PR wins when a lane is logged twice; an event without `lane` or a `pr` with no git commit adds nothing; `--since` limits both PRs and reverts. | tests |
| AC5 | Reverts by lane: a revert of a squash commit `X (#5)` whose event says `auto` counts under `auto`; a revert of a commit with no PR number counts under `unknown`; a revert whose target is not in the history counts under `unknown`; rate `0.33` for 1 of 3; `-` for a lane with no PRs. | tests |
| AC6 | Results: 19 PRs in `auto` gives `keep collecting (n=19)`, 20 gives `keep` when its rate is not above `review`'s and `auto lane escapes more than review` when it is; `review` with fewer than 20 keeps collecting; `block` and `unknown` show `-`. Rule 8 fires in exactly that case (and not below 20 `auto` PRs) with the evidence text; review PRs 0 with one auto revert fires. | tests |
| AC7 | Git not available: `git: not available` in the M6 section. No commit message text is printed (counts, lane names and the numbers only). Output is deterministic. | tests |
| AC8 | Golden test (008 AC8) updated for the new section only; all other existing tests pass unchanged. `event-log.md` lists `pr` and `lane` and `ship.md` tells the lead to log them on the `ship` event. | tests, review |

## Non-goals

Reading the lane from GitHub (no network); `caused-failure` labels; attributing a revert of a commit
inside a merge-commit PR to that PR (the reverted commit's own subject decides); changing the gate;
thresholds other than `LANE_MIN`.
