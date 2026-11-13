# 002 WP2: tier step, T0 path, role table

Tier T2 (skills are guardrail files: the human merges). Base `main` at `638f113` (WP1 merged).
Source: PLAN.md WP2, CONCEPT.md section 4. Branch `agent/wp2-tier-step`.

**Intent.** The lead sets a tier before scope, from facts a script checks. `tier.py` maps the
paths a task will touch (plus trigger flags) to `human`, `T2`, `T0` or `T1`, using the gate's
`risk-policy.toml` and `risk_gate.py`. `SKILL.md` gets the tier step and the T0 path, names
agents by role, and moves tiers, budgets and briefs into `references/`.

Keywords MUST, MUST NOT, SHOULD as in RFC 2119.

## 1. Files

| File | Change | Author |
|---|---|---|
| `.agents/skills/t3-build/scripts/test_tier.py` | new, failing tests first | test-author |
| `.agents/skills/t3-build/scripts/tier.py` | new | implementer |
| `.agents/skills/t3-build/SKILL.md` | tier step, T0 path, roles by agent name, trailers | lead |
| `.agents/skills/t3-build/references/{risk-tiers,budgets,brief}.md` | new | lead |

Not touched: `gate/` (no policy or gate change), `staging/`, `models.md`, `commit-trailers.md`.

## 2. `tier.py`

CLI: `python3 tier.py [--policy FILE] [--trigger NAME]... PATH...`
Module API (the tests use it): `classify(paths, policy, triggers=()) -> (tier, reasons)`;
`tier` is one of `"human"`, `"T2"`, `"T0"`, `"T1"`; `reasons` is a list of strings.
`classify` raises `ValueError` on bad input (2.4). Constant `GUARDRAIL`: tuple of path patterns.

### 2.1 Decision, in this order

1. `human`: any path matches a `GUARDRAIL` pattern.
2. `T2`: any path has a per-file floor of 2 or more on any dimension, or any trigger is given.
3. `T0`: every path is on the `no_rating` list under the gate's rules.
4. `T1`: everything else.

The first rule that fires decides. The reasons name the paths or triggers that decided it.

### 2.2 Reuse of the gate

- Matching MUST use `risk_gate.matches`; the floor MUST use `risk_gate.rate_files`; T0 MUST use
  `risk_gate.exempt_from_rating`. No re-implementation of pattern matching, canonical names,
  the zero floor or the list check.
- `tier.py` imports `risk_gate` from `../gate/` next to the script, never from the policy's
  directory (no code is loaded from a path the caller chose).
- Per-file floor: `rate_files([{"path": p, "additions": 0, "deletions": 0}], policy)`, once with
  `fold=False` and once with `fold=True`; the larger score counts. The aggregate size rule
  (`small_diff_lines`, `MAX_FILES`) MUST NOT affect the tier: size is unknown before work.
- T0: `exempt_from_rating` is called per file with one added line, and all files must pass.
  It MUST run for every file, whatever rule 1 or 2 decided, so a malformed `no_rating` fails
  closed (`SystemExit`, nothing on stdout).
- `GUARDRAIL` holds the patterns of the gate rule "CI, deploy, gate or agent config" except the
  deploy ones (`*/Dockerfile*`, `*/docker-compose*`, `*/deploy/*`, `*/infra/*`, `*.tf`; those
  are T2), plus `*/risk-policy.toml` and `staging/*` (unanchored, so it crosses directories; agent
  definitions waiting for the human).
  Matching is case-folded: both path and pattern lowercased before `risk_gate.matches`.

### 2.3 Triggers

`--trigger NAME`, repeatable. Allowed: `dependency`, `interface`, `schema` (deterministic),
`concurrency`, `untrusted-input` (judgment, set by the lead). Any trigger makes the tier T2
unless rule 1 fires. Unknown name: error (2.4).

### 2.4 Output and errors

- Success: exit 0. Stdout line 1 is the tier word. Each further line is `  <reason>`.
- Bad input exits 2 with a message on stderr and nothing on stdout: no paths; unknown trigger;
  a path that is not canonical (`risk_gate.canonical`: absolute, `./`, `..`, `//`, backslash,
  control characters, non-ASCII, trailing `/`, empty); unreadable policy file.
- Malformed policy (`no_rating`): `SystemExit` from `risk_gate`, non-zero exit, nothing on stdout.
- Policy: `--policy`, else `.github/risk-gate/risk-policy.toml` under the current directory if it
  exists, else `../gate/risk-policy.toml` next to the script.
- A rename is given as both names. Paths are repo-relative.

### 2.5 Non-goals

- The patch and minor dependency exception (CONCEPT section 4, after the diagram): every
  dependency manifest rates 2 in the policy, so it is T2 here. Conservative; the gate sends
  every dependency change to the review lane anyway. Follow-up if it costs too much process.
- Spec review rounds, budgets, event log (WP3), `models.md`, `commit-trailers.md`.
- Non-ASCII file names: refused (exit 2); the lead tiers them by hand.

## 3. Acceptance criteria

All tests in `scripts/test_tier.py`, run `cd .agents/skills/t3-build/scripts && python3 -m unittest`.
Fixtures use the shipped `gate/risk-policy.toml`.

| AC | Requirement |
|---|---|
| AC1 | Fixture table below: every single-path row classifies as listed. |
| AC2 | Mixed lists: the highest tier wins in the order human, T2, T1, T0. `[CHANGELOG.md, app/views.py]` T1; `[CHANGELOG.md, package.json]` T2; `[CHANGELOG.md, .claude/x.md]` human; `[app/views.py, app/auth/login.py]` T2. |
| AC3 | Triggers: `CHANGELOG.md` with `--trigger schema` is T2; `app/views.py` with `concurrency` is T2; `AGENTS.md` with `schema` stays human; unknown trigger exits 2. |
| AC4 | Case and lookalikes: `docs/user/TOKEN.md`, `docs/user/Session.md` are T2 (folded floor); `docs/user/claude.md`, `.Claude/x.md`, `Docs/user/AGENTS.OVERRIDE.md` are human; `docs/user/Install.MD`, `changelog.md` are T1, never T0. |
| AC5 | Bad input exits 2, stdout empty: no paths, `./CHANGELOG.md`, `/CHANGELOG.md`, `docs/../AGENTS.md`, `a\b.md`, `docs/user/a\nb.md`, `docs/user/café.md`, empty string, `--trigger nope`, missing `--policy` file. |
| AC6 | Malformed `no_rating` in `--policy` (`paths = ["CHANGELOG.md"]`, `paths = "x"`) exits non-zero with stdout empty, also for `AGENTS.md` and `app/auth/x.py`. Absent `no_rating` means no T0: `CHANGELOG.md` is T1. |
| AC7 | Size is ignored: 3000 distinct `docs/user/f<N>.md` files are T0 (CLI and `classify`). |
| AC8 | A custom policy with `no_rating = {paths = ["/NOTES.md"]}` makes `NOTES.md` T0; a custom no_rating entry on a file the policy rates 2 (e.g. `/session.md`) stays T2. |
| AC9 | Drift: every pattern of the gate rule with reason "CI, deploy, gate or agent config", except the five deploy patterns listed in 2.2, is in `tier.GUARDRAIL`, and no deploy pattern is. |
| AC10 | CLI output: line 1 is exactly the tier word, reasons are indented two spaces and name the deciding path; `python3 tier.py` default policy works from an empty temp directory. |
| AC11 | Skill docs: every relative Markdown link in `SKILL.md` and `references/*.md` resolves to an existing file; `SKILL.md` links `references/risk-tiers.md`, `budgets.md`, `brief.md`, `models.md`. |

Fixture table (single path, no triggers):

| Tier | Paths |
|---|---|
| human | `AGENTS.md`, `CLAUDE.md`, `src/CLAUDE.md`, `.claude/settings.json`, `.codex/agents/x.toml`, `.opencode/agent/a.md`, `.agents/skills/t3-build/SKILL.md`, `.agents/skills/t3-build/gate/risk-policy.toml`, `.github/workflows/ci.yml`, `.mcp.json`, `staging/claude/agents/lead.md`, `docs/user/AGENTS.override.md`, `docs/user/GEMINI.md` |
| T2 | `app/auth/login.py`, `src/session.py`, `src/crypto/aes.py`, `package.json`, `requirements.txt`, `app/migrations/0002.py`, `schema.sql`, `Dockerfile`, `deploy/prod.yml`, `main.tf`, `pytest.ini`, `conftest.py`, `docs/user/session-timeout.md` |
| T1 | `app/views.py`, `README.md`, `LICENSE`, `docs/specs/x/spec.md`, `docs/runbooks/x.md`, `tests/test_x.py`, `docs/user/sub/x.md`, `docs/user/Install.MD` |
| T0 | `CHANGELOG.md`, `docs/user/install.md`, `docs/user/a.md` |

## 4. Skill changes (lead, no tests beyond AC11)

- Section 1 roles table names agents (the `staging/` file names) and points to
  `references/models.md` for models and efforts. No model names in `SKILL.md`.
- New first step of the feature run, **Tier**: run `scripts/tier.py`, pass judgment triggers,
  record the tier. `human` = the human reviews and merges; work stays on a branch.
  Reviewer may raise the tier, never lower it.
- T0: `implementer-t0` (Haiku), CI only, no model review, no test author. T1: short spec, no
  spec review unless the design is unclear. T2: Sol reviews the spec. `implementer` stays Opus;
  Sonnet implements T1 only when `sonnet_t1_implementer` is on in `models.md`.
- `references/risk-tiers.md` (tiers, triggers, tier vs lane), `budgets.md` (CONCEPT loop
  budgets), `brief.md` (the three briefs moved out of section 9).
- Section 8 footer: `Agent:` and `Tier:` trailers per `references/commit-trailers.md`
  replace `Models:`.

## 5. Security

Asset: the tier decision. Under-classification lets an agent run a guardrail or T2 change with
less process (the gate still rates and the human still merges, so the damage is wasted trust,
not an auto-merge). Trust boundary: path strings and flags from the lead; the policy file.
Abuse cases: lookalike and case variants (AC4), non-canonical names (AC5), malformed policy
(AC6), size hiding a doc file in a big list (AC7), a policy chosen by the caller loading code
(2.2: import only from `../gate/`). `tier.py` does no network, no subprocess, no writes.

## 6. Roles

Spec: lead (this file). Tests: test-author (Sonnet, medium). `tier.py`: implementer (Opus,
medium). Code review: reviewer (Sol, medium), security-weighted brief. `SKILL.md` and references:
lead (documentation chore). No separate spec review and no second-vendor security pass: the
PLAN stage table lists neither, and the change has no I/O beyond reading one policy file.
