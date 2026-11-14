# 017 FU10: the risk gate re-runs when a pull request is retargeted

Tier T2 (gate workflow; `.agents/` makes it `human`, the human merges). Branch
`agent/fu10-gate-retarget`, from `main` `1396374`. Follow-up of WP9 (spec 015 section 23, "outside this
diff"; spec 016). Lead writes this spec. MUST as in RFC 2119.

**Problem.** `gate/risk-gate.yml` runs on `pull_request_target` with the types `opened`,
`synchronize`, `reopened`, `ready_for_review`, `converted_to_draft`. Changing the base branch of a PR
is an `edited` event, so the gate does not re-run: the rating, the lane and the `risk-gate` status
stay those of the old base, and an armed auto-merge is not re-evaluated. The CI templates already
re-run on retarget (`edited`, spec 015 AC39 and AC45).

## Change

1. `risk-gate.yml`: `pull_request_target.types` also lists `edited`.
2. The job condition lets an `edited` event through only when the base branch changed: the
   `pull_request_target` branch of the existing `if:` becomes
   `(github.event_name == 'pull_request_target' && (github.event.action != 'edited' || github.event.changes.base.ref.from != ''))`
   (a title or body edit then starts no gate run). The comparison is with the empty string, not `null`: in the Actions expression language `null` and `0` coerce to the number 0, so `!= null` would be false for a retarget away from a branch named `0`; a string compared with `''` has no coercion and a missing property equals `''`. The `issue_comment` branch, the concurrency
   group, the permissions, the token env and every other line are unchanged.
3. `gate/README.md` says the gate re-runs on a retarget and why `edited` is filtered.

## Files

| File | Author |
|---|---|
| `.agents/skills/t3-build/gate/test_risk_gate_workflow.py` | test-author-t2 (new, first, red); static checks of `risk-gate.yml`; skipped when the workflow file is not next to the gate folder (it is not copied to a product repo) |
| `.agents/skills/t3-build/gate/risk-gate.yml` | implementer |
| `.agents/skills/t3-build/gate/README.md` | lead |

## Acceptance criteria

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | `on.pull_request_target.types` is exactly the six types (the five above plus `edited`); `on.issue_comment.types` is unchanged (`created`, `edited`, `deleted`). Mutations are caught: `edited` dropped, a seventh type (`labeled`) added. | static tests, mutations |
| AC2 | The job `if:` contains the retarget condition of change 2 exactly (a plain `pull_request_target` match without the `edited` guard is caught as a mutation), and still contains the `issue_comment` owner-and-marker clause unchanged. | static tests, mutations |
| AC3 | Unchanged properties, each with a mutation: top-level `permissions` exactly `contents: read`, `pull-requests: write`, `statuses: write`; no job-level `permissions`; `concurrency` per PR number with `cancel-in-progress: true`; `actions/checkout` with `persist-credentials: false` and no `ref:`/`repository:` input (base branch only: never the PR's code); the only `run:` is `python3 .github/risk-gate/risk_gate.py`; `${{` appears only in `env:`, `concurrency` and `if:`, never in a `run:`; no `secrets.` other than `secrets.RISK_GATE_TOKEN`. | static tests, mutations |
| AC4 | The condition evaluates correctly (the evaluator follows the Actions coercion rules for `==`/`!=`: strings compare as strings, `null` and `''` are equal, and `null` against a number-like string coerces as numbers, so the `!= null` form fails the `"0"` fixture): a small evaluator in the test (the expression subset used: `==`, `!=`, `&&`, `\|\|`, parentheses, dotted context lookups, string literals, `null`) is run on payload fixtures: `opened`, `synchronize`, `reopened`, `ready_for_review`, `converted_to_draft` start the job; `edited` with `changes.base.ref.from` set starts it, including `"0"` and `"main"`; `edited` with both a base change and a title change starts it; `edited` with only `changes.title` does not; `issue_comment` by the owner with the rating marker starts it, by another user does not. | test |
| AC5 | `gate/README.md` states the retarget re-run and that title or body edits do not start the gate, and warns that a retarget by someone with write access can race an armed auto-merge (the old head-only success stands until the new gate run writes `pending`): disable auto-merge before retargeting. | test (text) |

## Security

The workflow runs from the base branch under `pull_request_target` with write permissions and a
token: it must never check out or run PR code. The change adds one event type and narrows when
`edited` runs; no new input reaches a shell. A PR author can trigger `edited` runs by changing the
base, which only re-runs the gate (concurrency cancels in-progress runs per PR).

| AC6 | The workflow tests skip only in the product layout: when the test file lives in a folder named `risk-gate` (copied to `.github/risk-gate/`). In this repository's `gate/` folder a missing `risk-gate.yml` is a failure, not a skip. | test |

## Non-goals

A branch filter on the gate workflow (a repo with several protected branches needs all of them);
changing `risk_gate.py` or the policy; the CI templates.

## Review log

Sol security round 1: (1) `!= null` coerces: a retarget away from a branch named `0` would not start the
gate: fixed (comparison with `''`, AC4 fixture); (2) P1, existing gap that this change leaves open: a
retarget keeps the head SHA, so the old success stands until the new run writes `pending`; an armed
auto-merge could consume it; GitHub disarms auto-merge on a retarget only for actors without write
access. Mitigation documented (AC5: disable auto-merge before retargeting). The real fix (bind the
required gate result to the current base and head, or a new required status context that legacy
head-only successes cannot satisfy) is **escalated to the human**: it changes `risk_gate.py` and branch
protection. (3) The tests no longer skip silently in this repository (AC6).
