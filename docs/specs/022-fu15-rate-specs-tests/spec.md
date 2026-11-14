# 022 FU15: specs, runbooks and changed tests rate 2

Tier T2 (gate policy and code; `.agents/` makes it `human`, the owner permitted the lead to merge
this run). Branch `agent/rate-specs-tests`, from `main` `097347f`. Source: the Opus 5.5 advisory,
risk 3 ("specs and tests can launder a weakening"). Lead writes this spec. MUST as in RFC 2119.

**Problem.** The gate rates `docs/specs/**`, runbooks and every test file at regression 0 (the `docs`
and `tests` rules). `diff_guard` lets a removed test or a shrinking assertion pass as a notice when
the same diff changes a spec file. So a PR can weaken a test, add or touch a spec to silence the
guard, and reach the auto lane with ratings of 0. A rating comment would still be needed, but the
floor itself says nothing is wrong.

## Change

1. **Policy.** New rule in `risk-policy.toml`, reason `specs and runbooks`, regression 2, paths
   `*/docs/specs/*`, `*/docs/*/spec.md`, `*/docs/*.spec.md` (the three patterns of `diff_guard`'s
   `SPEC_PATTERNS`), `*/runbooks/*`, `*/runbook*.md`. The `docs` rule stays; the highest level of
   all matching rules counts.
2. **Policy key `existing_changed`.** A rule may carry `existing_changed = N`: a file of the PR whose
   `status` is present and is not `added` (`modified`, `removed`, `renamed`, `copied`, `changed`,
   `unknown`, anything else) raises regression to N for that rule, reason `<path> (changes an
   existing file: <why>)`. A file without a `status` key, or with `status == "added"`, gets nothing.
   The `tests` rule gets `existing_changed = 2`. `deletions_only` stays.
3. **`risk_gate.py` `main`.** The files request also returns `status`; a missing or null `status`
   becomes `"unknown"` (fails closed: a changed existing test). Nothing else changes: `no_rating`,
   `exempt_from_rating`, `decide`, the card and the dry run only see higher floors.
4. **`tier.py`** passes no status (the tier is set before work), so a test path stays as it is; a
   path under `docs/specs/` or a runbook is now T2 by floor. `risk-tiers.md` says: do not list the
   run's own spec file when tiering (it would make every run T2); list a spec or runbook only when
   changing it is the task.
5. **Docs.** `gate/README.md` (scale table rows for regression 0 and 2, the rule and the key, the
   consequence below), `references/risk-tiers.md`, `ci/README.md` and the `diff_guard.py` docstring
   (limits: a spec change, new or changed, now also puts the PR in the review lane).

**Consequence (stated, accepted by the owner's request).** A PR that adds a spec under `docs/specs/`
goes to the review lane. A PR that adds only new test files stays at regression 0. If the owner
wants new specs to stay auto, add `existing_changed = 2` to the spec rule instead of `regression = 2`
(one policy edit, supported by item 2).

## Files

| File | Author |
|---|---|
| `gate/test_risk_gate.py`, `scripts/test_tier.py`, `ci/test_ci_templates.py` (only if a README text pin needs it) | test-author-t2 (first, red). Edits to existing tests only where the new behaviour changes their expectation (`docs/specs/x/spec.md` and `docs/runbooks/x.md` move from the T1 list to the T2 list in `test_tier.py`); list every edit; `test_rename_needs_both_names_on_the_list` (gate) uses `docs/specs/x/spec.md` as a zero-rated name: its second fixture pair moves to `README.md`, which still rates 0 and is not on the list, so the check stays about the exemption list; no other assertion of an existing test changes |
| `gate/risk_gate.py`, `gate/risk-policy.toml` | implementer |
| `gate/README.md`, `references/risk-tiers.md`, `ci/README.md`, `scripts/diff_guard.py` docstring, `scripts/tier.py` docstring | lead |

## Acceptance criteria

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | `rate_files` (exact) gives regression 2 for `docs/specs/x/spec.md`, `docs/specs/a/b/c.md`, `docs/x/spec.md`, `docs/y.spec.md`, `docs/runbooks/x.md`, `ops/runbooks/x.txt`, `docs/runbook-db.md`; `floor` (the higher of the exact and the case-folded pass, as the gate uses it) also gives 2 for `DOCS/SPECS/X.MD` and `Docs/Runbooks/X.md` (the exact pass alone keeps its case-sensitive meaning and is not asserted for them); `docs/user/x.md`, `docs/guide.md`, `README.md` stay 0. Every path `diff_guard` treats as a spec rates 2 (pattern lists compared). | tests |
| AC2 | `existing_changed`: `tests/test_x.py` with `status` in `modified`, `removed`, `renamed`, `copied`, `changed`, `unknown`, `xyz` rates regression 2 with reason text `changes an existing file`; with `added` or no `status` key it rates 0. Zero-line renames (additions 0, deletions 0, `status: renamed`) from `tests/test_x.py` to `archive/x.txt` and from `archive/x.txt` to `tests/test_x.py` both rate 2 (the `previous` name is rated with the status), as does a modified `TESTS/TEST_X.PY` through `floor` (the folded pass applies `existing_changed` too). `deletions_only` still gives 2 without a status. | tests |
| AC3 | A PR of only added test files plus README with an APPROVE rating of 0 is `auto`; with one modified test file it is `review` (reasons name the file). A PR with `docs/specs/x/spec.md` and an APPROVE rating of 0 is `review`. | tests |
| AC4 | `[no_rating]` is unchanged and `exempt_from_rating` is unchanged: `docs/user/x.md` and `CHANGELOG.md` still need no rating; a spec, runbook or test file still never does. `test_shipped_no_rating_list_is_minimal` passes unchanged. | existing and new tests |
| AC5 | `main` end to end (fake `gh`): the files call asks for `status`; a PR whose files carry `status: added` for a test file is `auto` in the dry run; `status: modified` is `review`; a file object with no `status` or `null` is `review` (unknown); a renamed test (`previous_filename` and `status: renamed` in the same fake payload) is `review`, so both reach scoring together. All other existing end-to-end tests pass unchanged. | tests |
| AC6 | `tier.py`: `docs/specs/x/spec.md` and `docs/runbooks/x.md` are `T2` (a reason names floor 2); `tests/test_x.py` stays `T1`; no other row of the existing fixture changes. | tests |
| AC7 | Policy as shipped: the `tests` rule has `existing_changed = 2`, the new rule exists with regression 2 and exactly the five patterns, and no other rule changed (the test pins the list of rule reasons and their levels). | test |
| AC8 | Docs: `gate/README.md` names the rule, the key, the status semantics (missing means not changed, `main` maps a missing status to unknown) and the consequence; the scale table row 0 says new test files, row 2 names specs, runbooks and changed existing tests; `risk-tiers.md` has the "own spec" sentence; `ci/README.md` and the `diff_guard.py` docstring say a spec change rates 2 in the gate. | text tests |

## Failure cases

- Unknown status string from the API: rated as changed (fail closed). A missing `status` key in a
  caller that does not know it (`tier.py`, tests) is not changed: that caller is not the gate's main path.
- Rename of a test to a non-test path: both names are rated (existing rename rule), the old name hits
  `tests` with `status: renamed` and rates 2.
- A malformed `existing_changed` (not an integer): not validated, as for `deletions_only`; the
  comparison fails and the run stops with a traceback (pending). No silent pass.

## Security

No new trust boundary: `status` comes from GitHub's files API for the PR, not from PR content. The
change only raises floors. Residuals: a PR that changes an existing test and adds nothing else is
now `review`, which is the point; an equal-count assertion rewrite in a modified test is rated 2 but
the human must still read it; the diff guard stays the check for removed assertions.

## Non-goals

Changing `diff_guard` behaviour; changing `no_rating`; rating by content; new dimensions.
