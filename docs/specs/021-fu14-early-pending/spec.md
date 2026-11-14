# 021 FU14: pending before any fallible read, and on any failure

Tier T2 (gate script and workflow; `.agents/` makes it `human`, the human merges). Branch
`agent/fu14-early-pending`, from `main` `5c064e8`. Implements options 2 and 3 of the answer to the
escalated item of specs 017 and 020. Lead writes this spec. MUST as in RFC 2119.

**Problem.** `risk_gate.py` writes the `pending` status only after its first `gh pr view` call, because
that call supplies the head SHA. If that call fails (HTTP 403/429, outage), or the job fails at any
later point before a status was written, no status changes: on a retargeted PR (same head SHA) the old
`success` stands and an armed auto-merge can use it.

## Change

1. **Option 2 (script).** The workflow passes the head SHA of the event (`HEAD_SHA`, empty for
   `issue_comment` events). In `main`, before the first `gh` call and only when not in dry-run: if
   `HEAD_SHA` matches `^[0-9a-f]{40}$`, write `pending` (`description` `rating`) for that SHA. Any other
   value (empty, wrong length, upper case, other characters) is ignored and never used in a path. The
   existing `pending` write for the real head after `gh pr view` stays (the event SHA can be stale).
2. **Option 3 (workflow).** The gate step gets `HEAD_SHA: ${{ github.event.pull_request.head.sha }}` in
   its `env:`. A new step after it, `if: failure()`, writes `pending` for `HEAD_SHA` when `HEAD_SHA` is
   exactly 40 lower-case hex characters (shell test in the step), with the same token and context:
   `gh api "repos/$REPO/statuses/$HEAD_SHA" -f state=pending -f context=risk-gate -f description=...`.
   Values reach the step only through `env:` (no `${{` in `run:`). It runs for `pull_request_target`
   events (the retarget case); for `issue_comment` events `HEAD_SHA` is empty and the step does nothing.
3. `gate/README.md` documents both and the residual limits.

## Files

| File | Author |
|---|---|
| `.agents/skills/t3-build/gate/test_risk_gate.py`, `test_risk_gate_workflow.py` | test-author-t2 (first, red); the checks of spec 017 AC3 that say "the only `run:` is the gate command" are superseded as below: edit only those, list every edit |
| `.agents/skills/t3-build/gate/risk_gate.py`, `risk-gate.yml` | implementer |
| `.agents/skills/t3-build/gate/README.md` | lead |

## Acceptance criteria

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | With `HEAD_SHA` = 40 lower-case hex and `gh pr view` failing, the run ends with SystemExit and exactly one status was written: `pending` for that SHA; no merge, arm or disarm call, no card. Without `HEAD_SHA`, behaviour is as before (no status written). | tests |
| AC2 | `HEAD_SHA` values that are ignored (empty, 39 or 41 characters, upper case, `g` characters, path characters such as `../x`, whitespace, a newline): no status write before `gh pr view`, and the value never appears in any `gh` argument. | tests |
| AC3 | Normal run with `HEAD_SHA` equal to the real head: statuses are `pending` (early), `pending`, then the final state; all existing tests (which leave `HEAD_SHA` unset) pass unchanged. With `HEAD_SHA` different from the real head both SHAs get `pending` and the final state goes to the real head only. | tests |
| AC4 | Dry run (`RISK_GATE_DRY_RUN=1`) with `HEAD_SHA` set writes nothing. | test |
| AC5 | Workflow, static: the gate step has `HEAD_SHA` with exactly `${{ github.event.pull_request.head.sha }}` in `env:`; there is exactly one more step, after it, with `if: failure()`, whose `env:` has `GH_TOKEN` (`secrets.RISK_GATE_TOKEN`), `REPO`, `HEAD_SHA` and whose `run:` has no `${{`; the 017 AC3 checks are updated to: exactly two `run:` steps, the gate command first; `${{` only in `env:`, `concurrency` and `if:`; secrets only `RISK_GATE_TOKEN`; every other 017 property unchanged. Mutations are caught (`if: failure()` removed or changed to `always()`/`success()`, token missing, `${{` moved into `run:`, `HEAD_SHA` from another expression). | static tests, mutations |
| AC6 | Workflow, executable: the `run:` text of the failure step is run with bash and a fake `gh` first on `PATH` that records its arguments: valid 40-hex `HEAD_SHA` gives exactly one call with arguments `api repos/<REPO>/statuses/<sha> -f state=pending -f context=risk-gate -f description=<text>`; empty, 39 or 41 characters, upper case, `../x`, a newline: no call, exit 0. | tests |
| AC7 | `gate/README.md` documents the early pending, the failure step, and the limits: `issue_comment` runs have no payload head SHA; if the GitHub API itself is unavailable no status can be written by either mechanism; the early pending can precede a run that later stops for another reason (that is intended: the status stays pending). | test (text) |

## Security

`HEAD_SHA` comes from GitHub's event payload (`pull_request_target`, base-repo context), not from PR
content; it is still validated by shape before use, never placed in a shell command except through
quoted env references, and never printed unescaped. The workflow still never checks out or runs PR code.
The change only adds `pending` writes: it cannot turn a lane weaker or write `success`.

## Non-goals

Binding the required result to base and head, a merge queue or required workflows (option 4); changing
ratings, lanes or the policy; `issue_comment` events beyond doing nothing in the new step.

## Review log

Sol security round 1: no code finding (shape checks in Python and bash across locales, no injection,
cancellation does not fire `failure()`, a failure after the final status writes `pending`, which fails
closed and is repaired by the next run). One P3 documentation finding, fixed: the README still said a
failed first `gh pr view` leaves the old status and said "first `gh` call"; both corrected (the remaining
exception is a run without a usable event head SHA, i.e. `issue_comment`, or a failed status write).
