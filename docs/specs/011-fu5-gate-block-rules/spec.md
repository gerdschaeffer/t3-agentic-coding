# 011 FU5: gate block rules for opencode.json and .cursor/, case-insensitive floor

Tier T2 (gate and policy). Branch `agent/fu5-gate-block-rules`, from `main` `4fdb1d4`. Follow-up
of WP1 open questions OQ3 and OQ4 (`docs/specs/001-wp1-gate-changes/spec.md` section 10). The
policy and gate are guardrail files: this PR is a proposal, the human reviews and merges it, and
the gate never rates its own change as anything but block (rule "CI, deploy, gate or agent
config"). Lead writes this spec. MUST as in RFC 2119.

## 1. Changes

1. **Block rule** "CI, deploy, gate or agent config" in `risk-policy.toml` gets
   `"*/opencode.json"`, `"*/opencode.jsonc"` and `"*/.cursor/*"` (they steer agents like
   `.opencode/` and `.claude/`). `tier.GUARDRAIL` gets the same three (AC9 drift test of WP2).
2. **Case-insensitive floor.** The per-file floor the gate computes in `main` MUST be the larger,
   per dimension, of `rate_files(..., fold=False)` and `rate_files(..., fold=True)`. A new
   function `floor(files, policy)` in `risk_gate.py` does this and returns `(scores, reasons)`,
   where each reason line belongs to the combined score (a reason of a lower level from one
   pass is not shown). `main` calls it instead of `rate_files(files, policy)`. `rate_files`,
   `exempt_from_rating` and `canonical` keep their behaviour and signatures. The fold can only
   add rule matches, and the max never lowers a score, so no PR gets a lower lane than before.
   Effect: `.OpenCode/x`, `.Claude/x`, `.AGENTS/x`, `Opencode.JSON`, `.CURSOR/rules` are block.

## 2. Files

| File | Author |
|---|---|
| `.agents/skills/t3-build/gate/test_risk_gate.py` | test-author-t2 (first, red) |
| `.agents/skills/t3-build/gate/risk_gate.py` | implementer |
| `.agents/skills/t3-build/gate/risk-policy.toml`, `.agents/skills/t3-build/scripts/tier.py` (GUARDRAIL) | implementer |
| `.agents/skills/t3-build/gate/README.md` | lead (prose) |

Not touched: `risk-gate.yml`, the `no_rating` list, CI, branch protection.

## 3. Acceptance criteria

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | `opencode.json`, `opencode.jsonc`, `sub/opencode.json`, `.cursor/rules/x.mdc` and `.cursor/mcp.json` rate regression 3 (lane block) through `main`'s floor; before the change they rate 1. | tests |
| AC2 | Case variants rate like the original through `floor`: `.OpenCode/x`, `.Claude/settings.json`, `.AGENTS/skills/x/SKILL.md`, `.Codex/x`, `Opencode.JSON`, `.CURSOR/rules/x.mdc`, `CLAUDE.MD`, `docs/Agents.md` (3), `.GitHub/workflows/ci.yml` (3). | tests |
| AC3 | `floor` never lowers a score: for every path in a table (ordinary code, docs, tests, `README.MD`, `Docs/x.py`, `Tests/x.py`, `app/Auth/x.py`, `package.json`, `PACKAGE.JSON`) each dimension of `floor` is at least that of `rate_files(..., fold=False)`. `README.MD` stays 1 (not lowered to 0 by the docs rule). | tests |
| AC4 | The reasons of `floor` name only the deciding level per dimension and are deduplicated; renamed files are rated under both names as before. | tests |
| AC5 | `main` uses `floor`: a dry-run PR touching `.Claude/x` shows lane block and the reason. Existing tests pass unchanged except two assertions in `Amendment.test_exemption_survives_case_variants_of_instruction_files` that record the behaviour this change ends: `.OpenCode/opencode.json` rates 3 (was 1), and `main` rates `docs/user/Claude.md` block (was auto). | tests |
| AC6 | `tier.GUARDRAIL` contains the three new patterns; the WP2 drift test (AC9) passes. | existing test |
| AC7 | `gate/README.md` describes the three patterns and that the floor is case-folded for every path. | review |

## 4. Security

Fail closed: the change only adds blocks. The fold does not cover non-ASCII lookalikes (`canonical`
refuses them for the exemption; the rule patterns match them as written). `risk-policy.toml`
changes only inside the block rule's `paths`.

## 5. Non-goals

Unicode-aware folding in the floor (WP1 `canonical` covers the exemption); changing the lane
logic; sanitising reasons (OQ5).
