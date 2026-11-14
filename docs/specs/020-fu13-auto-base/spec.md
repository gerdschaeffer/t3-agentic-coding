# 020 FU13: auto-merge only into the default branch

Tier T2 (gate code; `.agents/` makes it `human`, the human merges). Branch `agent/fu13-auto-base`,
from `main` `1d0540a`. Source: the Opus 5.5 advisory on automation risk (risk 4) and the escalated
P1 of spec 017 (retarget race). Lead writes this spec. MUST as in RFC 2119.

**Problem.** The gate rates a PR by its files and its rating and may arm auto-merge whatever the base
branch is. A PR that is small against a stacked base (which already contains a CI change, for example)
gets the auto lane; retargeting it to `main`, where the same change is in the diff and should block,
keeps the head SHA, so the old success stands until the new gate run writes `pending`, and an armed
auto-merge can consume it. Auto-merge into a branch that is not the default branch has no use case
here: stacked PRs are merged by the human, and the stack bottom lands in the default branch.

## Change

1. `risk_gate.py` `main` also reads the PR's base branch (`baseRefName` added to the `gh pr view
   --json` list) and the repository's default branch (`gh api repos/<repo> --jq .default_branch`).
2. Allowed auto-merge bases: the policy key `[merge] auto_base_branches` (a list of branch names),
   else the default branch only. A malformed value (not a list of strings, an empty list, a name that
   does not match `^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$`) makes the run fail closed:
   `SystemExit("... risk-gate stays pending")`, as for an unknown merge method.
3. A PR whose base is not an allowed base gets the blocker `base branch <name> is not an auto-merge
   branch (<allowed>)` (printed through `show_reason`/`plain` like the other texts). The lane is then
   `review` (never `auto`), in every mode, so shadow cards and the leader's dry run show it too. The
   scores and the reasons are unchanged; `block` stays `block`.
4. Fail closed: if `baseRefName` or the default branch cannot be read, is empty, or is the literal `null` (what `--jq` prints for a missing key), the run ends with
   `SystemExit` (the status stays pending, nothing is merged or armed), like other `gh` failures.
5. Everything else is unchanged: the exemption path (T0, no rating) is subject to the same rule; a
   stale auto-merge is still disarmed whenever the lane is not auto (existing logic).
6. Docs: `gate/README.md` (what the rule is, why, the policy key), `risk-policy.toml` gets the commented
   key in `[merge]`.

## Files

| File | Author |
|---|---|
| `.agents/skills/t3-build/gate/test_risk_gate.py` | test-author-t2 (first, red); the `gh` stub used by existing tests answers the two new calls: edit it minimally and list the edit |
| `.agents/skills/t3-build/gate/risk_gate.py`, `risk-policy.toml` | implementer |
| `.agents/skills/t3-build/gate/README.md` | lead |

## Acceptance criteria

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | Base is the default branch: lane, status, card and auto-merge arming are exactly as before (all existing tests pass with the stub answering `baseRefName` and the default branch equal). | existing tests |
| AC2 | Base differs from the default branch, rating APPROVE with zero scores, mode `enforce`, merge hours ok: lane `review`, the blocker text is in the card and the dry-run output, `gh pr merge --auto` is NOT called, `--disable-auto` is called (stale arming removed), status `review`. In shadow mode the card says `shadow, would be review`. | tests |
| AC3 | `auto_base_branches = ["main", "release/1"]`: a PR into `release/1` can be auto; a PR into another branch cannot; the default branch itself is not implied by the list (only listed names count). Malformed values (string, empty list, list with a non-string, a name with a space or a leading `-`) fail closed (SystemExit, status pending, no merge call). | tests |
| AC4 | Reading `baseRefName` or the default branch fails (gh error, empty output, missing key): SystemExit, the `pending` status was written first and no `success` is written, no merge or arm call. | tests |
| AC5 | A `block` lane stays `block` for any base; a `review` lane stays `review`; the T0 exemption path with a non-default base is `review`, not `auto`. | tests |
| AC6 | The base name in the blocker is shown through the plain-text rule (a base branch named with a newline or `<` cannot forge a card line; fixture). | test |
| AC7 | Retarget sequence: a run for base `main` arms auto-merge; the next run (same head) for base `stack` disarms it (`--disable-auto` called) and writes `review`. | test |
| AC8 | `gate/README.md` documents the rule and the key; `risk-policy.toml` has the commented key. | test (text) |

## Security

The base branch name is chosen by the PR author among existing branches (or by anyone who can create
a branch): it is data, shown only through the plain-text rule and never used in a shell. The default
branch name comes from the GitHub API of the same repository. The rule only removes auto-merge cases
(it can turn `auto` into `review`, never the reverse).

## Non-goals

Fixing the stale-success window itself (binding the required result to base and head, or a new status
context: still escalated, spec 017); changing how reviews or ratings are read; the CI templates.
