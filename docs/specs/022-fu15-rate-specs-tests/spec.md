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
5. **Unicode fold (Sol security pass round 1, S2).** The folded pass of `_found` (`fold=True`) keys
   paths and patterns with NFKC then `casefold()` instead of `str.lower`, so `docs/ſpecs/x.md`
   (U+017F folds to `s`, the same directory on a case-folding checkout) rates like `docs/specs/x.md`.
   For ASCII input this equals `lower()`. **Sol security round 2 (X1, S1):** NFKC alone can lower a
   floor that `lower()` gave (U+0303 composes `N` into `Ñ` and erases `session`; U+FF0F becomes a
   path separator; a one-character glob stops matching a letter that folds to two), so the folded pass
   (`rate_files(..., fold=True)`, `floor`) is the higher of two keyings: `str.lower` (as before) and
   NFKC + `casefold()`. The exact pass is unchanged. `floor` is therefore never lower than the exact
   or the `lower()` result. A lookalike that no fold maps (Cyrillic) is a different directory on
   disk and for `diff_guard`'s own patterns, so it is neither a spec nor a way to replace one.
6. **Case collisions of added files (S1).** `main` already lists the head tree
   (`git/trees/<sha>?recursive=1`); the jq now also returns `paths` (every path of the tree).
   For each file whose `status` is `added`, if its NFKC-casefold path equals that of another path
   of the tree, `main` sets the file's `status` to `case collision`, which `existing_changed`
   rates as a changed file (an added `tests/test_login.py` next to `tests/test_Login.py` is the
   replace-on-a-case-insensitive-checkout attack). A tree response without `paths` changes
   nothing (the truncated-tree rule stays: every file flagged). **Sol security round 3 (S1, X1):**
   collisions are counted under both keyings (`str.lower` and NFKC-casefold: two names can share a
   `lower()` key and differ under NFKC, as `Ĥ̱` and `ẖ̂` do), and an `added` file with a non-ASCII
   path is always marked `case collision`, because no keying can list every alias a filesystem
   applies; the tests rule thus rates a new non-ASCII test file 2 (review lane) and the exact
   residual is stated below. A rename's both names are
   not given this status (they are not `added`).
7. **Docs.** `gate/README.md` (scale table rows for regression 0 and 2, the rule and the key, the
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
| AC9 | S2: `floor` gives regression 2 for `docs/ſpecs/x/spec.md` (U+017F), `docs/runbookſ/x.md`, and for a modified `teſts/test_x.py` (status `modified`); `rate_files(..., fold=False)` is unchanged for them (the exact pass is case- and fold-sensitive); every existing fold test (FU5) passes unchanged; an ASCII upper-case path gives the same result as before. | tests |
| AC10 | S1: `main` end to end with a fake tree whose `paths` contain both `tests/test_Login.py` and `tests/test_login.py`: a PR adding `tests/test_login.py` (status `added`) is `review` and the dry run names the file; the same with `tests/teſt_x.py` and `tests/test_x.py` (a Unicode fold collision); a PR adding `tests/test_new.py` with a tree without a collision is `auto`; a tree without a `paths` key (old fake shape) behaves as before; a tree whose paths collide only among non-added files of the PR (no added test) changes no lane. | tests |
| AC11 | Sol round 2: `floor` is the maximum of the exact, `lower()` and NFKC-casefold passes. Root `A／B.PY` (U+FF0F) rates regression 2 (root `*.py` rule through `lower()`); a modified `app/SESSION\u0303.py` (`SESSION` + U+0303) rates security 2; a custom policy rule `/settings/?.json` (regression 3) still rates `settings/ß.JSON` 3 through `floor`; and over the whole earlier fold fixture set (AC9, the FU5 case tests, the shipped-policy paths) `floor` is at least `rate_files(.., fold=False)` and at least the `lower()` keying per dimension. | tests |
| AC12 | Sol round 3: with the fake tree of AC10, an `added` `tests/test_Ĥ̱.py` (`H` + U+0302 + U+0331; the tree holds `tests/test_h` + U+0302 + U+0331 + `.py`: the two share a `lower()` key and differ under NFKC-casefold, as the test checks in a precondition) is `review R2`; an `added` `tests/tést.py` (non-ASCII, no collision in the tree) is `review R2`; an `added` ASCII `tests/test_new.py` stays `auto`; an `added` non-ASCII path that matches no existing_changed rule (`docs/user/café.md` with a rating) keeps its lane (the status only matters to rules with `existing_changed`). | tests |
| AC8 | Docs: `gate/README.md` names the rule, the key, the Unicode fold and the case-collision rule, the status semantics (missing means not changed, `main` maps a missing status to unknown) and the consequence; the scale table row 0 says new test files, row 2 names specs, runbooks and changed existing tests; `risk-tiers.md` has the "own spec" sentence; `ci/README.md` and the `diff_guard.py` docstring say a spec change rates 2 in the gate. | text tests |

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

## Review log

Sol spec review: round 1 CHANGES (3 should, fixed), round 2 APPROVE. Sol code review round 1:
APPROVE, no findings. Sol security pass round 1 (high): S1 [medium] added test beside a
case-colliding existing one rates 0 (fixed by item 6, AC10); S2 [medium] `ſ` bypasses the spec
floor because the folded pass uses `lower()` (fixed by item 5, AC9). Both were fixed, not declined.
Round 2 (code and security, high): S1 [should] and X1 [medium]: the NFKC keying replaced `lower()`
and lowered some floors (composed `SESSION\u0303`, U+FF0F, `?` glob over `ß`); fixed by taking the
maximum of both keyings (item 5, AC11).
Round 3 (code and security, high): S1 [should] and X1 [medium]: collision detection used only the
NFKC keying (`Ĥ̱` and `ẖ̂` share a `lower()` key); fixed by counting both keyings and treating an added
non-ASCII test path as changed (item 6, AC12). Residual, stated: a filesystem alias that neither
keying nor the non-ASCII rule covers is possible only for an ASCII added name colliding with a
non-ASCII existing one through an alias table that is neither `lower()` nor NFKC-casefold; none known.

## Non-goals

Changing `diff_guard` behaviour; changing `no_rating`; rating by content; new dimensions.
