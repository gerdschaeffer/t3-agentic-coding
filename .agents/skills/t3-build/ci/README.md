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

## The Makefile is the trust root of `ci.yml`

The Makefile is the trust root of `ci.yml`: it runs `make <target>` from the PR, so the Makefile
and everything it calls decide what the eight checks do (a PR that turns `test:` into `@true`
makes `ci` green). The gate policy rates a Makefile in any folder (`Makefile`, `GNUmakefile`,
`makefile`, `Makefile.inc`) and `*.mk` as root tool config (regression 2, review lane), so such a
change is never auto-merged. A script that the Makefile runs is rated by the ordinary rules of the
policy, so read those changes too.

## Known design limit: shared check runs

Check runs are keyed by head SHA and name. Two pull requests with the same head (one against a base
branch that predates the guards, one against `main`) share them, and the newest run counts, so a
person who can push a base branch can leave green runs on the head. Fork authors cannot: add
`branches: [main]` under `pull_request:` in the four workflows to limit this. The full fix is a
gate or ruleset that checks that each required run belongs to this PR number; it is not part of
these templates.

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
- The three guards run the script from the **base commit** (the first parent of the merge commit
  that `pull_request` checks out; the step fails unless the second parent is the PR head), so a PR
  cannot weaken the script that judges it. The workflow file itself is read from the merge commit:
  a PR that edits `.github/workflows/*` is held back only by the gate's block lane and code owners
  (a design limit; running the guards on `pull_request_target` would close it, and the spec chose
  `pull_request`). The PR that installs the guards passes with a notice and is merged by hand. The
  workflows also re-run when the base branch of a PR is edited.
- A spec change in the diff authorizes every removed test or shrinking assertion `diff_guard`
  finds, and it passes with a plain `notice:` line in a green job: read that line.
- `diff_guard` knows these test paths only: `*/tests/*`, `*/test/*`, `*/test_*`, `*_test.*`,
  `*.test.*`, `*.spec.*` (the gate's own list). `__tests__/`, `spec/` and `*_spec.*` are not
  covered.
- A commit with two parents counts as a merge and is skipped by `commit-trailers`, so a redundant
  second parent skips the check.

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
