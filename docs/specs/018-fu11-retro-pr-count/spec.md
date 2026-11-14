# 018 FU11: retro_report counts squash-merged pull requests

Tier T1 as logged (`tier.py`: `human`, `.agents/` holds guardrail files; the human merges). Branch
`agent/fu11-retro-pr-count`, from `main` `11c4617`. Amends `docs/specs/008-wp8-retro-report` section 4.1.
Found by the Opus 5.5 advisory on automation risk. Lead writes this spec (short, T1). MUST as in RFC 2119.

**Problem.** `retro_report.py` counts `pull requests merged` in git as commits whose subject starts
`Merge pull request #`. That is the subject of GitHub's *merge commit* method. The gate's default merge
method is `squash` (`risk-policy.toml` `[merge]`), whose subject is `<title> (#<n>)`. In a squash repo
the git count is 0, so the cross-check against the log's `ship` slices (the only audit of the lead's
own log) never fires and the flag `git has more merged pull requests than the log` is blind.

## Change

In `read_git` a commit counts as a merged pull request when either:
1. its subject starts `Merge pull request #<digits>` (unchanged), or
2. its subject ends with ` (#<digits>)` (a space, `(#`, one or more digits, `)`, end of subject) and the
   commit is on the first-parent history of `HEAD` and is not a merge (at most one parent: a squash or a
   rebase merge that kept the title suffix; a root commit has no parent, is not a merge, and counts if
   its subject has the suffix: accepted, and the frozen fixture relies on it).

A commit counts at most once. Everything else of 4.1 is unchanged: the flags, the other rows, the
output format, `--since`. No README or SKILL text describes the row; the docstring is the documentation. Limits (stated in the docstring): a repo that merges by rebase without the `(#n)` suffix
is not counted; a direct commit to the main line whose subject happens to end in `(#n)` is counted
(GitHub's own convention; accepted); a side commit of a merge-commit PR whose subject ends `(#n)` is
not on the first-parent history (or has the merge as child) and is not counted.

## Files

| File | Author |
|---|---|
| `.agents/skills/t3-build/scripts/test_retro_report.py` | test-author (first, red): append a new class; do not touch existing tests |
| `.agents/skills/t3-build/scripts/retro_report.py` | implementer |

## Acceptance criteria

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | A fixture history of squash merges (`Add x (#1)`, `Fix y (#2)`, each one parent, on the first-parent line) gives git count 2 in the `pull requests merged` row; with the log having 2 `ship` slices there is no flag, with 0 `ship` slices the flag `FLAG: git has 2 merged pull requests ...` is printed. | test |
| AC2 | Merge-commit PRs (`Merge pull request #3 from ...`) still count exactly as before; a mixed history counts both kinds once each. | test, existing tests |
| AC3 | Not counted: a subject without the suffix; `(#12)` in the middle of a subject; `(#)` or `(#a)`; ` (#5) ` followed by text; the side commits of a `--no-ff` merge-commit PR even when their subjects end `(#n)` (not on the first-parent line); a merge commit (two parents) whose subject ends `(#n)` but does not start `Merge pull request #`. | test |
| AC4 | A commit that matches both forms counts once (a merge commit titled `Merge pull request #7 ... (#7)`). `--since` filters squash PRs by commit date as before. | test |
| AC5 | All existing tests pass unchanged. The docstring states the two forms and the limits. | existing tests |

## Review log

Sol round 1: the code said 'at most one parent' (root counts) while the spec said 'exactly one'; the frozen
fixture's first commit is a root commit with a `(#1)` subject and expects it to count, so the spec was
corrected, not the code. The claimed README/SKILL text did not exist; removed. Docstring now names the
side-commit exclusion.

## Non-goals

Per-lane or escaped-defect metrics, reading `caused-failure` labels, a GitHub API call (the script has
no network), counting rebase merges without a suffix.
