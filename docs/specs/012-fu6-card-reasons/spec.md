# 012 FU6: the risk card keeps the deciding reason of every dimension

Tier T2 (gate code; `.agents/` makes it `human`, the human merges). Branch
`agent/fu6-card-reasons`, from `main` `d49efff`. Follow-up found by Sol in the FU5 review
(`risk_gate.py` `render_card`, `reasons[:12]`). Lead writes this spec. MUST as in RFC 2119.

**Problem.** The card prints only the first 12 reasons. A PR with 12 or more ordinary files that
each add a `regression 1` reason, followed by `app/AUTH/x.py` (security 2, lane review), shows
a card whose scores say security 2 but whose "Why" list omits the security reason. The lane is
right; the explanation the owner reads is not.

## Change

`render_card` (only) chooses what to print when there are more than 12 reasons; with 12 or fewer
the output stays byte-for-byte as before. With more:

1. Keep, for each dimension (`regression`, `security`, `user`) that has a reason line, the
   reason with the highest level (a line starts `<dimension> <level>: `); the first one on a tie.
2. Fill the remaining places up to 12 with the other reasons in their original order; the kept
   lines stay in their original order too.
3. Add a last bullet `- ... and <n> more (run the gate's dry run for the full list)` where `n`
   is the number not shown.

Reason lines that do not start with a dimension and level (for example `no rating needed: ...`)
are ordinary fillers. The lane, scores, blockers and `decide` are not touched.

## Files

| File | Author |
|---|---|
| `.agents/skills/t3-build/gate/test_risk_gate.py` | test-author-t2 (first, red) |
| `.agents/skills/t3-build/gate/risk_gate.py` | implementer |

## Acceptance criteria

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | 12 or fewer reasons: the card equals the old output exactly (existing card tests unchanged). | existing tests |
| AC2 | 13 `regression 1` file reasons then one `security 2` reason: the card shows the security reason, 11 more reasons, and `... and 2 more` ... counted correctly; scores unchanged. | test |
| AC3 | Three dimensions each with many reasons: the highest-level reason of each dimension is shown; a reviewer-raised reason (`regression 3: reviewer: ...`) beats the floor `regression 1` lines. | test |
| AC4 | Order of shown lines equals their order in `reasons`; no duplicate; the marker line, table, head SHA and "Not eligible" section are unchanged. | test |
| AC5 | The count in the `more` line is `len(reasons) - shown`; exactly 12 reason bullets plus the `more` bullet appear when truncating. | test |

## Non-goals

Changing the 12 limit, `decide`, the dry-run output (it prints all reasons), sanitising reasons
(OQ5).
