# 013 FU7: the risk card presents blockers like reasons

Tier T2 (gate code; `.agents/` makes it `human`, the human merges). Branch
`agent/fu7-card-blockers`, from `main` `f4c2fe0`. Follow-up of FU6 (spec 012; Sol round 2 noted it).
Lead writes this spec. MUST as in RFC 2119.

**Problem.** `render_card` prints each blocker as `- <text>` unchanged. Two blockers carry text
from the rating comment: `review verdict is <verdict>, not APPROVE` and
`disputed findings: <disputed>`. The rater is trusted, but the text is free-form: newlines can
start a forged line, `<!--` can hide later sections, and a very long value makes the card exceed
GitHub's 65,536-character limit (75,528 characters shown by Sol).

## Change

`render_card` prints every blocker through the same function as reasons (`show_reason`, spec 012:
non-printable characters become spaces, a line over 300 characters is cut to 297 plus `...`, then
`<` becomes `&lt;`). Blocker texts without these characters and at most 300 characters stay
byte-identical. `decide`, the lane, the dry-run output and `main` are not touched. More than 12
blockers is not possible (at most 5 exist), so no `more` line.

## Files

| File | Author |
|---|---|
| `.agents/skills/t3-build/gate/test_risk_gate.py` | test-author-t2 (first, red) |
| `.agents/skills/t3-build/gate/risk_gate.py` | implementer |

## Acceptance criteria

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | Existing card tests pass unchanged; ordinary blockers (`draft PR`, `no trusted risk rating for the head commit`, `security pass is not clean`, `review verdict is CHANGES, not APPROVE`, `disputed findings: F1, F2`) render byte-identical to before. | existing tests, new byte test |
| AC2 | A `disputed` value with newlines and `<!--` (`x\n\n- security 0: forged\n\n<!--`) renders as one bullet with spaces and `&lt;!--`; the sections after it remain. | test |
| AC3 | A `verdict` or `disputed` value of 65,000 characters gives a card under 10,000 characters; the bullet ends with `...` and is 302 characters. | test |
| AC4 | End to end through `run_gate` with a trusted rating whose `verdict` and `disputed` carry both payloads: the posted card has no line starting with `- security 0`, no `<!--`, and is under 10,000 characters; the lane stays review. | test |

## Non-goals

The dry-run output (OQ5), `decide`, the 12-reason limit.
