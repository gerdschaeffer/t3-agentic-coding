# CI jobs for a product repository

Workflow templates and guard scripts that the human copies into a product repo (spec
`docs/specs/015-wp9-ci/spec.md`). Same idea as `gate/`: the sources live in this skill, the repo gets
a copy under `.github/`. `.github/` is a guardrail folder, so the copy and every later change to it
is a pull request the human merges.

## Copy

| Source (in this skill) | Destination |
|---|---|
| `ci/ci.yml`, `ci/diff-guard.yml`, `ci/commit-trailers.yml`, `ci/agents-drift.yml` | `.github/workflows/` |
| `gate/risk-gate.yml` | `.github/workflows/` (shadow mode first) |
| `scripts/check_agents.py`, `scripts/check_trailers.py`, `scripts/diff_guard.py` and `scripts/test_check_agents.py`, `scripts/test_check_trailers.py`, `scripts/test_diff_guard.py` | `.github/ci/` (copy the three test files together: two import helpers from the others) |
| `gate/risk_gate.py`, `gate/risk-policy.toml`, `gate/test_risk_gate.py` | `.github/risk-gate/` |

Not copied: `ci/test_ci_templates.py` (it checks the templates in this skill). The gate's own setup
steps (token, branch protection) are in [gate/README.md](../gate/README.md).

## Prerequisites

- The skill is installed in the repo: `agents-drift` reads
  `.agents/skills/t3-build/references/models.md`.
- The agent folders `.claude/agents`, `.codex/agents`, `.opencode/agents` are installed (WP0).
  Without them `agents-drift` exits 2 and fails.
- Python 3.11 or newer on the runner (`tomllib`). The templates use the runner's own `python3`.
- A `Makefile` with the eight targets `format`, `lint`, `types`, `test`, `secrets`, `deps`, `sast`,
  `licence`. A missing target fails its job (fail closed).

## Pin the actions

The templates use `actions/checkout@v4` as the only allowlisted entry, so their tests can run.
Replace it with `actions/checkout@<full 40-character commit SHA>` in every workflow before the first
run. No other action is used and none may be added without a SHA.

## Settings the human does

- Required checks on `main`: `ci`, `diff-guard`, `commit-trailers`, `agents-drift`, `risk-gate`.
  A notice `guards not installed on the base commit` means the guard is not on the target branch.
  Keep the check required anyway, so a guard that the human removes is noticed.
- Code owners for `.github/` and approval for workflows from fork pull requests.
- `RISK_GATE_TOKEN` for the gate only (see the gate README).

## Secrets

No secrets in these four workflows: they run on `pull_request` with a read-only token and
`persist-credentials: false`. The only workflow with a secret is `risk-gate.yml`. Deployment
secrets live only in the release job, which the human triggers. `ci.yml` runs the PR's Makefile
and tests: that is untrusted code on a read-only token without secrets, and network egress is not
limited.

## What the guards do and do not do

- `diff-guard` (`diff_guard.py`): fails when a test file is deleted, replaced by a symlink or
  submodule, becomes binary, or when assertion lines shrink, unless the same diff changes a spec
  file. It is a heuristic: a moved assertion or a renamed test can give a false alarm, an equal-count
  rewrite or an assertion style it does not list passes, and any spec change, however small,
  authorizes every finding. The human reads the diff.
- `commit-trailers` (`check_trailers.py`): on a branch named `agent/*`, every non-merge commit needs
  an `Agent: <role> / <model> / <effort>` line and a `Tier: T0|T1|T2` line, read from the whole
  message (git's trailer parser skips the footer paragraph). The lines are self-declared:
  a consistency check, not authentication. Merge commits are skipped.
- `agents-drift` (`check_agents.py`): model and effort in the agent files equal
  `references/models.md`.
- The three guards run the script from the **base commit**, so a PR cannot weaken the script that
  judges it. The PR that installs them passes with a notice and is merged by hand.

## Suggested commands per stack

Suggestions for the Makefile targets, not run in this repo.

| Stack | format, lint, types, test | deps, licence |
|---|---|---|
| Python | `ruff format --check`, `ruff check`, `mypy`, `pytest` | `pip-audit`, `pip-licenses` |
| Node | `prettier --check`, `eslint`, `tsc --noEmit`, `npm test` | `npm audit`, `license-checker` |
| Go | `gofmt -l`, `go vet`, `go test ./...` (types come with the compiler) | `govulncheck`, `go-licenses` |

Any stack: `gitleaks` for `secrets`, `semgrep` for `sast`.

## First run in the product repo (done when)

GitHub Actions does not run in this repo, so the human or the lead records this once per product
repo. For each of the five jobs, one run on a clean branch (passes) and one on a deliberate
violation (fails): an `agent/x` commit without trailers, a deleted test file, an agent file with a
different effort, a failing make target, a gate-rated change. Until then WP9 stays open in the PLAN
sense.
