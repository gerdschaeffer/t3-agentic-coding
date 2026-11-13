# Risk gate

Every PR is rated 0-3 on three dimensions. The lane comes from the **highest**
rating, never an average:

| Lane | Rule | What happens |
|---|---|---|
| auto | all ratings 0-1 and every condition below holds | auto-merge (`[merge] method`, default squash) on the exact head commit once CI passes |
| review | any rating 2, or a condition fails | waits; the owner reads the risk card, brings the branch up to date, and merges by hand once checks are green (not via auto-merge: the gate disarms it on the next push, it cannot tell the owner's click from an agent's) |
| block | any rating 3 | `risk-gate` status fails; split, flag, plan, or the owner overrides |

Auto-merge conditions: a trusted risk rating for the current head commit,
verdict APPROVE, no disputed findings, a clean security pass whenever security
is 1 or more, not a draft, inside merge hours.

## Shadow-mode repos: the leader's own check

Where the gate cannot enforce (no branch protection) and the leader merges the
auto lane itself, it decides from a read-only dry run of **main's** gate and
policy, never from the `risk-gate` status, which any workflow can write:

```
RISK_GATE_DRY_RUN=1 PR=<n> REPO=<owner/repo> \
  RISK_GATE_POLICY=<(git show origin/main:.github/risk-gate/risk-policy.toml) \
  python3 <(git show origin/main:.github/risk-gate/risk_gate.py)
```

It prints the lane, ratings and reasons and writes nothing. Run it from a
recipe the branch cannot change (e.g. `make -f <(git show origin/main:Makefile)`).

## Scale (reviewers rate with this)

| Level | Regression | Security | User impact (only when `live = true`) |
|---|---|---|---|
| 0 | docs, comments, tests only | no security trigger touched | not reachable by users (internal, tests, flag off) |
| 1 | small change, changed lines covered by tests, no interface change | trigger touched only by additive hardening; both security reviews clean | user-visible but behind a flag or progressive rollout, reversible |
| 2 | public interface change, shared module with many callers, large diff, weak coverage, removed tests, dependency or config change | authn/authz, permissions, tenant or customer data, secrets, crypto, input parsing at a trust boundary, new dependency | user-visible without a flag, critical journey (login, payment, export), performance-sensitive path |
| 3 | schema or data migration, CI/deploy/gate config | open security finding, exposed secret, permission model change | irreversible effects (emails, deletions, billing) or the error budget is burning |

## How the rating gets in

The policy file gives the deterministic floor. The reviewer's rating comes as a
PR comment from a trusted login (default: repo owner), first line exactly:

```
<!-- risk-rating sha=<full 40-char head SHA> -->
verdict: APPROVE
regression: 1
security: 0
user: 0
security_pass: n/a
disputed: none
reasons: <one line>
```

`security_pass` is `clean` or `n/a`; `disputed` is `none` or finding ids. Only
the repo owner can rate (personal repos). A rating for another commit, with
anything before the marker, or with a bad value is ignored. A new push needs a
new rating.

Ratings are **append-only**: the newest rating for the head commit wins, so to
withdraw an approval post a new rating with `verdict: CHANGES`. Never edit or
delete a rating: the gate re-evaluates, and an older approval for the same
commit can count again. Only the owner identity can do that, which is inside the
accepted risk below.

**Accepted risk (owner decision, 2026-11-07):** the leader posts the rating under
the owner's identity, so the gate cannot tell an honest relay of Sol's verdict
from a faked one. A buggy or prompt-injected leader could auto-merge ordinary
code. Path rules still hold: auth, secrets, dependencies, migrations and CI never
reach the auto lane. Upgrade path: run the rating inside CI and trust only
`github-actions[bot]`, or restrict the auto lane to a path floor of 0.

## Setup per repository

1. Copy `risk_gate.py`, `risk-policy.toml`, `test_risk_gate.py` to
   `.github/risk-gate/`, and `risk-gate.yml` to `.github/workflows/`.
   Run the tests in CI too: `python3 -m unittest discover -s .github/risk-gate`.
2. Create a fine-grained token for this repo only (Contents, Pull requests,
   Commit statuses: read and write) and store it as secret `RISK_GATE_TOKEN`.
   The default `GITHUB_TOKEN` would merge without starting the staging deploy.
   Check the permissions really saved: with read-only access the gate fails at
   its first status write (`gh api statuses failed: ... 403`) and nothing merges.
3. Repo settings: allow auto-merge and the merge method the repo uses (set `[merge] method` to match; a repo that needs merge commits must not squash); delete branches on merge.
4. Branch protection (or a ruleset) on `main`: require a PR, require status
   checks `ci` and `risk-gate`, require branches to be up to date. Do not
   require approvals: you cannot approve your own PR. GitHub Free has no branch
   protection for private repos; use a public repo or a paid plan.
5. Staging deploy runs on every push to `main`, followed by a smoke test.
6. Start with `mode = "shadow"`. After about 10 PRs compare the gate's lanes
   with your own decisions, adjust the rules, then switch to `enforce`.
   In shadow mode `risk-gate` is always green ("shadow: would be ..."). The gate
   uses the policy on `main`, so the PR that switches to enforce is still judged
   in shadow mode; merge it by hand.
7. Label PRs that caused a revert or a red staging `caused-failure`; once a
   month compare failures per lane and tighten rules that let failures through.

Later, when production CD exists: `live = true`, feature flags off by default,
tested rollback, merge hours. Canary analysis and error-budget freezes only once
there is enough traffic to measure.
