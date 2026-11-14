# 014 FU8: the gate's dry run prints one line per reason

Tier T2 (gate code; `.agents/` makes it `human`, the human merges). Branch
`agent/fu8-dry-run-sanitise`, from `main` `21a3b0e`. Closes WP1 OQ5 (control characters in printed
reasons) after FU6 and FU7 fixed the card. Lead writes this spec. MUST as in RFC 2119.

**Problem.** With `RISK_GATE_DRY_RUN=1` the gate prints `  <reason>` and `  not auto: <blocker>`
unchanged. A reason holds a PR file name (attacker-chosen) or a rating's free text. A newline can
start a line that looks like another reason, and an escape sequence (`\x1b[...`) can rewrite what
a terminal shows. The leader reads this output to decide the lane of a shadow-mode repo.

## Change

In `main`'s dry-run branch each reason and blocker line is printed with every character that is
not `str.isprintable()` replaced by a space (same rule as `show_reason`, spec 012), so one reason
is one output line. The first line (`<lane> R.. S.. U.. head <sha12>`), the order, the two-space
indent, the `not auto: ` prefix and the exit status do not change. No cut and no `<` escaping:
the output is plain text and the dry run is where the full text is read. Ordinary lines stay
byte-identical. A new small function next to `show_reason` may hold the rule; `show_reason`
should call it so the rule exists once.

## Files

| File | Author |
|---|---|
| `.agents/skills/t3-build/gate/test_risk_gate.py` | test-author-t2 (first, red) |
| `.agents/skills/t3-build/gate/risk_gate.py` | implementer |

## Acceptance criteria

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | Existing dry-run tests pass unchanged; ordinary output is byte-identical. | existing tests |
| AC2 | A PR file named `app/AUTH/x.py\n- security 0: forged` gives dry-run output with exactly one line per reason: no output line starts with `  - security 0` or `  security 0: forged`, and the file name appears with a space where the newline was. | test |
| AC3 | An escape sequence (`\x1b[2J`, `\x1b]0;title\x07`) in a file name, a rating `reasons` value or a `disputed` value is printed with the ESC and BEL characters replaced by spaces; stdout holds no `\x1b`, `\x07`, `\r` or other control character except the line-ending `\n`. | test |
| AC4 | The count of output lines equals `1 + len(reasons) + len(blockers)` for the payloads of AC2 and AC3. | test |
| AC5 | The posted card (non-dry run) is unchanged by this PR (existing FU6 and FU7 tests pass). | existing tests |

## Non-goals

A length cap for the dry run; changing `decide`, the card or the exit codes; defending the leader
against instructions written in plain printable text (tool output is data, not instructions).
