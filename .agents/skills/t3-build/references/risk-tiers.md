# Risk tiers

The tier is set once, before work starts, and fixes the process depth. The lane (auto, review,
block) is set later by the [risk gate](../gate/README.md) on the finished pull request and decides
who merges. Tier and lane are independent.

## Setting the tier

Run, from the repo root, with every path the task will touch (both names of a rename):

```
python3 <skill>/scripts/tier.py [--policy FILE] [--trigger NAME]... -- PATH...
```

Line 1 of the output is the tier; the lines below name the paths or triggers that decided it.
The decision order is fixed, the first rule that fires wins:

| Result | Rule | Process |
|---|---|---|
| `human` | any path is a guardrail file: CI, `AGENTS.md`, `CLAUDE.md` and other agent instruction files, `.claude/`, `.codex/`, `.opencode/`, `.agents/` (skills), `.mcp.json`, `staging/`, `risk-policy.toml` | Work on a branch, open the pull request, the human reviews and merges. Never merge, never change the policy to fit the work. Process depth: T1 or T2 as the plan or the user names it, else T2, never T0 (log that tier). |
| `T2` | a path the policy rates 2 or 3 on any dimension (auth, permissions, sessions, secrets, crypto, dependencies, migrations, deploy, infrastructure, root tool config), or any `--trigger` | Full spec reviewed by `reviewer` (Sol), failure cases listed. Separate test author `test-author-t2`. Implementer `implementer`. Code review up to 3 rounds, 5 for security, data loss, concurrency. Security pass: `reviewer-security` and `security-engineer`, both clean. End-to-end proof. |
| `T0` | every path is on the policy's `no_rating` list (user documentation only) | One line in the task record. `implementer-t0` (Haiku) writes it. No new tests, no model review, CI is the only gate. The gate merges without a rating if the rater opened the PR. |
| `T1` | everything else | Short spec by `spec-writer`; `spec-reviewer` (Fable) reviews it only when the design is unclear. Separate test author `test-author`. Implementer `implementer` (Opus); Sonnet only when `sonnet_t1_implementer` is on in [models.md](models.md). `reviewer` (Sol) reviews code, 3 rounds. CI green plus the behaviour observed once. |

In every tier the lead implements a change of about 20 lines or less itself. Everything else in
the table stays.

## Triggers

Pass `--trigger NAME` for facts a path list cannot show:

- Deterministic: `dependency` (new package or major upgrade), `interface` (public interface or
  contract), `schema` (data schema).
- Judgment, assessed by the lead: `concurrency` (shared state), `untrusted-input` (code that
  parses untrusted input).

Any trigger makes a task T2 (unless a guardrail file makes it `human`). The reviewer may raise the
tier when it finds new evidence of risk; nobody lowers it. A raised tier restarts the run at the
Tier step with the new tier.

## What `tier.py` does not do

- A path the policy rates 2 makes the task T2, so a spec or a runbook (`docs/specs/`, `runbooks/`) is
  T2. Do not list the run's own spec file when you tier (every run would be T2); list a spec or a
  runbook only when changing it is the task. A test path stays as before: `tier.py` passes no file
  status, and the gate rates a changed existing test 2 later, on the finished PR.
- It ignores size: the policy's size rule (over 200 changed lines) changes the lane only.
- Every dependency manifest rates 2 in the policy, so a patch or minor upgrade is also T2 (a
  decision, CONCEPT agrees); the gate sends every dependency change to the review lane anyway.
- It refuses non-canonical names (exit 2): absolute, `./`, `..`, `//`, backslash, control or
  unprintable characters. Put `--` before the paths so a name starting with `-` is a path.
- A non-ASCII name is tiered at least T2, never T0 or T1, and a name that Unicode folds to a
  guardrail path (for example `ſtaging/x`) is `human`. A lookalike that no fold maps (Cyrillic
  letters) is only T2, not `human`: look at such names yourself.
- It trusts the policy file it reads. A `--policy` other than the repo's is for tests and
  projects that keep the gate elsewhere.
- Guardrail patterns live in `tier.py` (`GUARDRAIL`); a test compares them with the gate's
  agent-config rule, so a new agent-config pattern in the policy fails that test until added.
