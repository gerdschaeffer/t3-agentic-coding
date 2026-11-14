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

**Presentation of every shown reason** (added after Sol round 1: a reason carries a PR file name or
a reviewer's text, so it is attacker-influenced, and keeping a reason that used to be cut off
made two defects reachable; this applies also with 12 or fewer reasons):

- every control or non-printable character (`\n`, `\r`, `\t`, U+2028, and so on) becomes a
  space, so one reason is one bullet;
- `<` becomes `&lt;`, so a reason cannot open an HTML comment or tag;
- a reason longer than 300 characters is cut to 297 characters plus `...`;
- selection (which reasons to keep) works on the raw text; the presentation is applied to what is
  shown only.

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
| AC1 | 12 or fewer reasons without a control character, `<` or more than 300 characters: the card equals the old output exactly (existing card tests unchanged). | existing tests |
| AC2 | 13 `regression 1` file reasons then one `security 2` reason: the card shows the security reason, 11 more reasons, and `... and 2 more` ... counted correctly; scores unchanged. | test |
| AC3 | Three dimensions each with many reasons: the highest-level reason of each dimension is shown; a reviewer-raised reason (`regression 3: reviewer: ...`) beats the floor `regression 1` lines. | test |
| AC4 | Order of shown lines equals their order in `reasons`; no duplicate; the marker line, table, head SHA and "Not eligible" section are unchanged. | test |
| AC5 | The count in the `more` line is `len(reasons) - shown`; exactly 12 reason bullets plus the `more` bullet appear when truncating. | test |
| AC6 | A file name `app/AUTH/x.py\n\n- security 0: forged safe reason\n\n<!--` (13th file after 12 ordinary ones, plus a reviewer-raised `user 2` reason) gives a card in which the forged text is part of one bullet line (no line of the card starts with `- security 0`), `<!--` appears as `&lt;!--`, and the real `user 2` reason, the `more` line and the blocker section are still present. | test |
| AC7 | A trusted rating with `regression: 2` and `reasons:` of 65,000 characters gives a card under 10,000 characters; the shown reviewer reason ends with `...`. A card with 12 reasons of 300 characters stays under GitHub's 65,536-character limit. | test |

## Non-goals

Changing the 12 limit, `decide`, the dry-run output (it prints all reasons; it is read by the
leader, not rendered as Markdown). The wider OQ5 (control characters in the dry run and in
`blockers`) stays open.
