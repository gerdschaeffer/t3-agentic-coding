# 016 FU9: branch filter on the CI templates (shared check runs)

Tier T2 (CI templates; `.agents/` makes it `human`, the human merges). Branch
`agent/fu9-branch-filter`, from `main` `5c87af6`. Follow-up of WP9 (spec 015 sections 21 to 23,
finding N2). Lead writes this spec. MUST as in RFC 2119.

**Problem.** Check runs are keyed by head SHA and name. A head opened against an old branch that
predates the guards gets green runs (`guards not installed on the base commit`), and the same SHA
against `main` then shows those runs. Without a branch filter anyone who can open a PR can do this
against any existing pre-guard branch. It reaches `ci` as well as the three guards.

## Change

The four templates (`ci.yml`, `diff-guard.yml`, `commit-trailers.yml`, `agents-drift.yml`) get
`branches: [main]` under `pull_request:` (together with the existing `types`). A PR against another
base branch starts no run of these workflows, so a required check stays pending and the PR cannot
merge: fail closed. `ci/README.md` tells the human to replace `main` by the repo's default branch in
all four files (a repo whose default branch has another name gets no CI until they do, which is
visible) and to add other protected branches to the list deliberately. This closes the
any-author variant; the variant by someone who can push a base branch stays, as the README says (a
ruleset with required workflows pinned to a ref is the full fix).

## Files

| File | Author |
|---|---|
| `.agents/skills/t3-build/ci/test_ci_templates.py` | test-author-t2 (first, red; the trigger checks that forbid a filter change as this spec says) |
| `.agents/skills/t3-build/ci/{ci,diff-guard,commit-trailers,agents-drift}.yml` | implementer |
| `.agents/skills/t3-build/ci/README.md` | lead |

## Acceptance criteria

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | Each of the four templates has `on: pull_request:` with exactly the keys `types` (the four types of spec 015 AC39/AC45) and `branches`, and `branches` is exactly the one-element list `[main]`. | static test |
| AC2 | Mutations are caught: `branches` missing; `branches: [main, release]`; `branches: ['**']`; `branches-ignore`; `paths` or `paths-ignore` added; `branches: [develop]`; `types` altered. The existing checks (`on` exactly `pull_request`, no `pull_request_target`, no inputs) keep working. | mutation tests |
| AC3 | Everything else of spec 015 sections 7, 16, 21 and 22 holds unchanged (all existing template tests pass except the ones this spec supersedes: the checks that said "no filter other than types" and the mutation that inserted a `branches` filter and expected a failure). | existing tests |
| AC4 | `ci/README.md` says: replace `main` by the default branch in all four files; a PR against a branch not in the list gets no run and a required check stays pending (fail closed); the filter closes the any-author variant of the shared-check-runs limit and the base-branch-pusher variant stays (ruleset with required workflows pinned to a ref is the full fix). The README test for the shared-check-runs section is added (the section heading exists and names `branches`, `main`, `ruleset`). | test |

## Non-goals

A ruleset or gate check; `gate/risk-gate.yml` (`edited` type, branch filter); templating the
default branch name.

## Review log

Sol security round 1: the filter itself is correct (base-branch semantics, retarget via `edited`, no
other line needs it, the ancestor check stays useful). Two README overclaims, both fixed (prose only):
S1 (medium) an older unfiltered installation on an old branch still supplies unfiltered workflow
definitions (GitHub runs them from the PR merge commit), so the filter is complete only for
branches created after it was installed; S2 (low) "cannot merge" holds for a missing required result,
while an existing result for the same head SHA can still count.
