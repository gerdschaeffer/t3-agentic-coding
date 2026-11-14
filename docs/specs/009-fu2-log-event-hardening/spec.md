# 009 FU2: log_event.py exits 2 on overflow and deep nesting

Tier T1 (`tier.py`: `human`, `.agents/` holds guardrail files; logged as T1; the human merges).
Branch `agent/fu2-log-event-hardening`, from `main` `092a535`. Follow-up from the WP3 review
(spec `003-wp3-event-log`). Lead writes this spec (short, T1). MUST as in RFC 2119.

**Problem.** Two inputs make `log_event.py` exit 1 with a traceback where the contract says exit 2
(invalid input, nothing written):

1. `{"duration": 10**400}`: `math.isfinite` converts the int to float and raises `OverflowError`.
2. A JSON document nested about 10,000 levels (for example `[[[...]]]`): `json.loads` raises
   `RecursionError`, which `main` does not catch.

## Files

| File | Author |
|---|---|
| `.agents/skills/t3-build/scripts/test_log_event.py` | test-author (first, red); add tests only, never weaken one |
| `.agents/skills/t3-build/scripts/log_event.py` | implementer |

Not touched: `SKILL.md`, references, `tier.py`, `retro_report.py` (it reuses `validate`).

## Acceptance criteria

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | `validate` returns a `duration` problem (never raises) for any int too large for a float, for example `10**400`, `-10**400` and `10**309`; `main` exits 2, stderr names `duration`, nothing is written. | tests |
| AC2 | Stdin JSON nested deeper than the parser allows (10,000 levels of arrays and of objects) makes `main` exit 2 with a message on stderr that names the cause, no traceback, nothing written. | tests |
| AC3 | Existing behaviour is unchanged: every existing test passes unmodified; `duration` 0, 12.5 and a normal int are still valid; `NaN`, `Infinity`, negative and `True` still fail. | existing tests |
| AC4 | A nested value inside an otherwise valid field (for example `findings` with 10,000 nested levels) cannot reach `validate` as a crash: it is AC2 at parse time. | test |

## Non-goals

A maximum for `duration` below the float limit; changes to the 4096-byte line limit; the
`retro_report.py` integer bound.
