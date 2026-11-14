# 010 FU3: tier.py hardening

Tier T2 (it steers every tier decision and fails closed; `tier.py` is under `.agents/`, so
`tier.py` itself says `human` and the human merges). Branch `agent/fu3-tier-hardening`, from
`main` `4fdb1d4`. Follow-up 3 of the WP2 review. Changes `docs/specs/002-wp2-tier-step` on the
points below. Lead writes this spec. MUST as in RFC 2119.

## 1. Changes

1. **Patch and minor dependency upgrades stay T2.** CONCEPT (section 4, rules at lines 136 and
   146) says they stay T1; `tier.py` rates every manifest 2 (policy), so they are T2. Decision:
   keep T2 (conservative; the gate sends every dependency change to the review lane anyway) and
   change CONCEPT and `maintain/references/checks.md` to say so. No code change.
2. **A path starting with `-`.** `tier.py` already accepts `--` (argparse). Every documented call
   puts `--` before the paths (`SKILL.md` step 1, `references/risk-tiers.md`, `maintain`), and
   `tier.py -- -x.py` is tested. No code change.
3. **Non-ASCII paths are tiered, not refused.** Today `canonical` refuses them (exit 2), which
   forces a hand tier. New behaviour in `tier.classify` (the gate's `canonical` stays as is; it
   still keeps non-ASCII names out of the `no_rating` exemption):
   - A path that is canonical except for non-ASCII characters is accepted. Every other rule of
     `canonical` still applies: absolute, `./`, `..`, `//`, backslash, control or unprintable
     characters (`isprintable`), trailing `/`, empty, all exit 2.
   - Guardrail matching also runs on the NFKC-then-`casefold()` form of the path, so a lookalike
     such as `ſtaging/x.md` (U+017F folds to `s`) or `.agеnts/x` is judged as the name it can
     become on a case-folding checkout: if that form matches `GUARDRAIL`, the tier is `human`.
     (Cyrillic `е` does not fold to Latin `e`; only the folds Unicode defines are covered.)
   - A non-ASCII path that is not `human` is at least `T2`, reason `non-ASCII path: <path>`; it is
     never `T0`. The size and floor rules still apply (T2 stays T2).

## 2. Files

| File | Author |
|---|---|
| `.agents/skills/t3-build/scripts/test_tier.py` | test-author-t2 (first, red) |
| `.agents/skills/t3-build/scripts/tier.py` | implementer |
| `CONCEPT.md`, `.agents/skills/maintain/references/checks.md`, `.agents/skills/t3-build/references/risk-tiers.md`, `.agents/skills/t3-build/SKILL.md`, `docs/specs/002-wp2-tier-step/spec.md` (amendment note) | lead (prose) |

Not touched: `gate/` (FU5 does it), `log_event.py`.

## 3. Acceptance criteria

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | `docs/user/café.md` and `docs/ü/x.py` are tiered `T2` (CLI exit 0, line 1 `T2`, a reason names the path); never `T0` or `T1`. This replaces the `café.md` row of WP2 AC5. | tests |
| AC2 | `ſtaging/x.md` is `human` (folds to `staging/`); `staging/ü.md` and `.agents/skills/ü/SKILL.md` are `human` (guardrail wins over the non-ASCII floor); `.ǎgents/x` (no Unicode fold to `.agents`) is `T2`, not `human`. | tests |
| AC3 | Still exit 2, stdout empty: `docs/../ü.md`, `/ü.md`, `ü\x00.md`, `docs//ü.md`, `docs/ü.md/`, `a\ü.md`, `./ü.md`, a name with U+200B or U+202E (unprintable). | tests |
| AC4 | `tier.py -- -x.py` is tiered like `x.py` at the root (`T2`: root `*.py`) and exits 0; `tier.py -x.py` still exits 2 (usage). | tests |
| AC5 | Every other existing test passes unchanged except the `café.md` row of WP2 AC5. `exempt_from_rating`, `canonical` and `rate_files` in `gate/` are not edited. | existing tests, `git diff --stat` |
| AC6 | Docs: CONCEPT lines 136 and 146 say patch and minor upgrades are T2 in `tier.py` and the review lane in the gate; `maintain/references/checks.md` and `references/risk-tiers.md` agree; the calls in `SKILL.md`, `risk-tiers.md` and `maintain` show `--` before the paths; the non-ASCII bullet of `risk-tiers.md` says tiered T2. | review |

## 4. Security

Boundary: path strings come from the lead's own list, but a path in a task can be attacker-chosen
(a PR with a lookalike name). Fail closed: never lower a tier, never turn a refusal into a lower
tier than `T2` for a name that might be a guardrail. Residual: a lookalike that no Unicode fold
maps to a guardrail name (Cyrillic) is an ordinary non-ASCII path, `T2`, which keeps the full
process; it does not reach `human`. Say so in `risk-tiers.md`.

## 5. Non-goals

Implementing a T1 patch and minor exception; changing the gate's `canonical`; confusable-script
detection.
