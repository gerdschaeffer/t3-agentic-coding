# Checks

Each check: command, output, tier rule. Use the command the repo already defines (CI workflow,
Makefile, package scripts, pre-commit config). The examples show the usual tool per ecosystem; run
one only if the repo already uses it. Never install a tool. Every command is repo code: run it
only as `SKILL.md` Rules say (disposable checkout, scrubbed environment, sandbox). Registry and
advisory lookups send package names and versions only. No manifest for an ecosystem:
`not run: no manifest`.

## 1. Dependencies (weekly)

- Command: the repo's outdated-dependency command. Examples: `npm outdated --json`,
  `pip list --outdated --format=json`, `cargo outdated`, `go list -u -m -json all`.
- Output: per package: name, current version, latest version, kind (patch, minor, major).
- Tier rule: new package or major upgrade: T2. Patch and minor upgrades: run `tier.py -- <manifest and lockfile
  paths>` and report its result (T2: every manifest rates 2 in the policy).

## 2. Advisories (daily)

- Command: the repo's audit command. Examples: `npm audit --json`, `pip-audit -f json`,
  `cargo audit --json`, `govulncheck ./...`. A web lookup sends package name and version only.
- Output: per advisory: id, package, installed version, fixed version, severity, whether the
  vulnerable code is reachable (unknown if not checked).
- Tier rule: any advisory on a dependency in use: T2, lead plus a human notice the same day.
  Critical or high severity: say so first. A fix that only bumps a patch version is still routed
  by this rule.

## 3. Flaky tests (weekly)

- Command: the repo's test command, three times at the `main` SHA, detached
  ([long checks](../../t3-build/references/long-checks.md)). Optionally, the CI history of `main`:
  runs that failed then passed on the same SHA.
- Output: per test: name, result per run, failure message first line. A test is flaky when its
  result differs between runs of the same SHA.
- Tier rule: run `tier.py` on the paths of the proposed fix first; a concurrency or shared-state
  cause raises the tier (production race, timing: T2), it never lowers it. A test that always
  fails is not flaky: report it as a failing check.

## 4. Doc drift (weekly)

- Command: for each backticked path, command, script or Makefile target and each relative link in
  `README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/`: `test -e <path>`, the target or script exists in
  the Makefile or package scripts, the link target exists. Read only; run no documented command.
- Output: per mismatch: file, line, what the doc says, what the code has.
- Tier rule: a docs-only fix is T0 on the gate's no-rating list, else T1. A doc that disagrees
  with a behaviour (not a name) goes to the lead as a possible bug.

## 5. Metrics (monthly)

- Command: `python3 ../t3-build/scripts/retro_report.py` (WP8; path from the skill folder) with the
  state folder of the repo.
- Output: the script's output, unchanged. No score is invented.
- Tier rule: none. Pass the proposed-changes table to the lead; the human decides. Missing script:
  `blocked`, reported.
