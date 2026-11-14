# 015 WP9: CI jobs

Tier T2, guardrail (`tier.py`: `human`; CI and skills are guardrail files: the human merges).
Branch `agent/wp9-ci` from `main` at `48fe79c`. Sources: PLAN.md WP9 and Rules, CONCEPT 6.2 and
section 4, `references/{commit-trailers,models,risk-tiers}.md`, `gate/`. MUST, MUST NOT, SHOULD
as in RFC 2119.

**Intent.** Four CI workflow templates (`ci`, `diff-guard`, `commit-trailers`, `agents-drift`)
and three checker scripts, kept in the skill like `gate/`. The human copies them into a product
repo's `.github/`. Each job fails on a deliberate violation and passes on a clean branch. The
checks catch agent mistakes. They are not authentication.

## 1. Decisions (fixed by the lead, not reopened)

1. This repo has no `.github/`. The sources live in `.agents/skills/t3-build/ci/` (templates and
   README) and `.agents/skills/t3-build/scripts/` (scripts). The README tells the human how to
   copy them. `risk-gate` stays `gate/risk-gate.yml` and is copied in shadow mode. Its tests run
   in CI through a step in `ci.yml`.
2. Scripts use the Python 3 standard library only (3.11+, for `tomllib`). They make no network
   calls, are read-only on the repo and never execute code from the PR. In a product repo they
   live in `.github/ci/`, a block-lane path, so a PR that edits them is merged by the human.
3. The templates trigger on `pull_request` (never `pull_request_target`; only the risk gate uses
   that). They have `permissions: contents: read` and contain no `secrets.` anywhere. Deployment
   secrets live only in the release job, which the human triggers. That meets "CI on agent
   branches runs without secrets".
4. Commit messages are read as lines from the whole body, never through git's trailer parser.
   The repo footer puts `Agent:`/`Tier:` in a paragraph before `Co-Authored-By:`, and git's
   parser ignores that paragraph (WP8 finding: `retro_report.py` counted 0 trailers instead of 29).

## 2. Files

| File | Change | Author |
|---|---|---|
| `.agents/skills/t3-build/scripts/test_check_agents.py` | new, failing first, then frozen | test-author-t2 |
| `.agents/skills/t3-build/scripts/test_check_trailers.py` | new, failing first, then frozen | test-author-t2 |
| `.agents/skills/t3-build/scripts/test_diff_guard.py` | new, failing first, then frozen | test-author-t2 |
| `.agents/skills/t3-build/ci/test_ci_templates.py` | new, failing first, then frozen (static template checks) | test-author-t2 |
| `.agents/skills/t3-build/scripts/{check_agents,check_trailers,diff_guard}.py` | new | implementer |
| `.agents/skills/t3-build/ci/{ci,diff-guard,commit-trailers,agents-drift}.yml` | new | implementer |
| `.agents/skills/t3-build/ci/README.md` | new: copy table, settings, per-stack commands | lead |
| `.agents/skills/t3-build/references/commit-trailers.md` | one pointer line (section 8) | lead |

Not touched: `gate/` (all files), `tier.py`, `log_event.py`, `retro_report.py`, `SKILL.md`,
`models.md`, `staging/`, `PLAN.md`, `CONCEPT.md`.

## 3. Shared rules for the three scripts

- Exit 0: pass. Exit 1: a violation, or input over a bound ("too large", fail closed). Exit 2:
  bad arguments, not a git repo, an unresolvable revision, unreadable required input. On exit 2,
  stdout is empty and a message goes to stderr.
- Git runs as `subprocess.run(["git", "-C", repo, "-c", "core.fsmonitor=false", "-c",
  "diff.external=", "-c", "core.pager=cat", "--no-pager", <cmd>, ...], shell=False)`. `log` gets
  `--no-show-signature`. `diff` gets `--no-ext-diff --no-textconv --no-color`. The environment
  sets `GIT_TERMINAL_PROMPT=0`. With this, a hostile repo config (`gpg.program`,
  `log.showSignature`, `diff.external`, a textconv driver, an fsmonitor hook) cannot run a
  program. Never `shell=True`, `os.system` or `eval`. No PR file is imported or run.
- Revision and branch arguments MUST match `^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$` (so nothing
  can start with `-`, which blocks option injection). Otherwise exit 2. Paths from git are read
  with `-z` and are never passed back to a shell.
- Output: one line per problem. Every value from a commit, a file name or a file field goes
  through `plain()`, which works like `risk_gate.plain`: every non-printable character becomes a
  space and the value is cut at 200 characters with `...`. Commit message text and file
  contents are never printed. Only the short sha, paths and validated field values are.
- Bounds (constants at the top of each script): `MAX_COMMITS = 500`, `MAX_FILES = 3000` (as the
  gate), `MAX_BYTES = 5_000_000` git output per call, `MAX_LINE = 4096` (longer lines are cut
  before regex matching). Going over a bound prints `too large: <what> over <limit>` and exits 1.
- All regexes are linear: no nested quantifiers and no back-references. They are applied to cut
  lines.
- Symlinks are never followed. A path that is a symlink (or whose parent folder is one, within
  the checked root) is a problem, not a target.
- Read-only: no writes to the repo, the index or a ref, and no temp files.

## 4. `check_agents.py`

CLI: `python3 check_agents.py --root DIR [--models FILE]`. `--models` defaults to
`<script dir>/../references/models.md`. A missing or unreadable file exits 2.

**Table.** The first Markdown table in `models.md` whose header is exactly
`| Agent file | Role | Provider | Model | Effort |`. No such table, or a row with the wrong
number of cells, exits 2.
- Agent file: split on `,`, strip, each name must match `^[a-z0-9][a-z0-9-]{0,63}$`
  (`code-scout, web-scout` gives two names).
- Model and Effort: the first whitespace-separated token of the cell
  (`claude-haiku-5-5 (Sonnet on escalation)` gives `claude-haiku-5-5`, and
  `medium (high: cross-cutting, security)` gives `medium`). Tokens must match
  `^[A-Za-z0-9][A-Za-z0-9._/:-]{0,127}$`, else exit 2.
- Provider maps to a folder: `Claude` to `claude`, `Codex` to `codex`, `OpenRouter` to
  `opencode` (OpenRouter models run through OpenCode). Any other provider exits 2, so a new
  provider needs a script change.

**Folders.** For each of `claude`, `codex`, `opencode`: `<root>/.<p>/agents` if it exists, else
`<root>/<p>/agents`. That covers `--root staging` here and `--root .` in a product repo. If both
exist: exit 2. If no folder exists for any provider: exit 2 (no vacuous pass). A provider without
a folder is skipped.

**Agent files** (at most 200 per folder, regular files, at most 64 KB each). The stem is the
agent name.
- Claude `*.md`: front matter between a first line `---` and the next `---` within 100 lines.
  Each line is `key: value`. A duplicate key makes the file unparsable. Keys `name` (must equal
  the stem), `model`, `effort`.
- Codex `*.toml`: `tomllib`. Keys `name` (must equal the stem), `model`,
  `model_reasoning_effort`.
- OpenCode `*.md`: front matter as for Claude, no `name` key. `model` must equal
  `openrouter/<table model>`. Effort is compared only when the file has a `reasoningEffort` key
  (the staged files have none). See open question Q1.

**Problems** (exit 1), one line each:
`<rel path>: model <file value> != <table value>`, `<rel path>: effort <file> != <table>`,
`<rel path>: no row in models.md`, `<rel path>: unparsable (<reason>)`, `<rel path>: name does
not match file name`, `<agent>: row in models.md (<Provider>) has no file in <rel folder>`, `<rel
path>: not a regular file`. A field value that does not match the token regex is reported as
`unparsable model value` without echoing it. Other files in the folder (`*.txt`, folders) are
ignored. Clean: exit 0 and one line `agents: <n> files match models.md`.

## 5. `check_trailers.py`

CLI: `python3 check_trailers.py --branch NAME --base REV --head REV [--repo DIR]` (default
`--repo` is `.`).

- If the lowercased branch name does not start with `agent/`, print `trailers: not an agent
  branch, skipped` and exit 0, without calling git. Lowercasing catches `Agent/x`.
- Commits: `git log -z --no-show-signature --no-merges --max-count=501 --format=%H%x1f%B
  BASE..HEAD`. More than 500 means "too large". Zero commits pass.
- A commit passes when its message (cut to 64 KB, `\r` removed) has, in **any** paragraph:
  - at least one line matching
    `^Agent: [a-z0-9][a-z0-9-]{0,63} / [A-Za-z0-9][A-Za-z0-9._/:-]{0,127} / [a-z]{1,16}( \([^()]{0,200}\))?\s*$`
    (the reviewer form `medium (2 rounds, APPROVE)` is allowed), and
  - at least one line matching `^Tier: T[012](\s.*)?$`. `T3` and `T20` fail. A note after a
    space is allowed (`Tier: T2 (gate code; human merges)`).
- The human-only exception of `commit-trailers.md` does not apply on `agent/*` branches. Every
  non-merge commit must carry both lines.
- Failure line: `<12-hex sha>: missing Agent line`, `missing Tier line` or `missing Agent and
  Tier lines`. Exit 1 if any commit fails. Last line: `trailers: <n> commits checked, <m>
  failing`.

## 6. `diff_guard.py`

CLI: `python3 diff_guard.py --base REV --head REV [--repo DIR] [--spec PATTERN]...`.

- Diff base: `git merge-base BASE HEAD`. Files:
  `git diff -z --name-status -M --no-ext-diff --no-textconv <mb> HEAD`. At most `MAX_FILES`.
- Patterns use the gate's `matches` semantics (fnmatch, `*` crosses `/`, `*/x` also matches at
  the root, a leading `/` anchors) on the lowercased path. The script holds its own copy as
  constants (no import across folders):
  - `TEST_PATTERNS` = the policy's `tests` rule: `*/tests/*`, `*/test/*`, `*/test_*`, `*_test.*`,
    `*.test.*`, `*.spec.*`.
  - `SPEC_PATTERNS` default: `*/docs/specs/*`, `*/docs/*/spec.md`, `*/docs/*.spec.md`.
    `--spec`, repeatable, replaces the defaults. A path that matches a spec pattern is never a
    test file.
- **(a) Test file removed:** status `D` on a test file, or `R` with a test-file old name and a
  non-test new name. Line `removed test file: <path>`.
- **(b) Assertions shrink:** for each test file with status `M`, or `R` from test to test, run
  `git diff -U0 --no-ext-diff --no-textconv <mb> HEAD -- <old> <new>` and count assertion lines
  among the `-` and `+` lines (headers `---`/`+++` excluded; a binary file has no lines). Removed
  assertion lines more than added means a violation. Line `assertions shrink: <path> (-<r>
  +<a>)`.
- **Assertion line:** the text after the marker, stripped. It does not start with a comment
  marker (`#`, `//`, `/*`, `*`, `--`, `<!--`), and it matches one of:

  | Pattern | Covers |
  |---|---|
  | `^assert\b` | Python `assert` statement |
  | `\bassert\w*\s*[(!]` | `assert(`, `self.assertEqual(`, JUnit `assertThat(`, Rust `assert_eq!(` |
  | `\b(assert\|require)\.\w+\(` | Node `assert.equal(`, Go testify `require.NoError(` |
  | `\bexpect\s*\(` | Jest, Vitest, Chai `expect(` |
  | `\.should\b\|\bshould\.` | Chai and should.js |
  | `\bt\.(Error\|Errorf\|Fatal\|Fatalf\|Fail\|FailNow)\(` | Go `testing` |
  | `\b(pytest\.raises\|self\.fail)\(` | pytest, unittest |

  A line counts once, whatever number of patterns match it.
- **(c) Spec change:** when (a) or (b) fires and the diff has a file with status `A`, `M` or `R`
  (new name) matching a spec pattern (a deleted spec does not count), the guard passes, exits 0
  and prints `notice: tests removed or assertions shrink; spec changed: <spec path>` after the
  violation lines.
- Clean: exit 0, line `diff guard: <n> test files checked`.

**Limits (stated in the README and the script docstring):** this is a heuristic, not proof.
False positives: an assertion moved to another file, a test file renamed to a non-test name, a
removed fixture under `tests/`, an assertion rewritten into a helper. False negatives: an
equal-count rewrite (`assert x == 1` to `assert True`) passes, as does an assertion in a style
the table does not list, and test logic weakened without touching assertion lines. The human
reviews the diff in every case.

## 7. Workflow templates (`ci/*.yml`)

Each template, checked statically by `test_ci_templates.py`:
- `on:` has `pull_request` only (`ci.yml` may add nothing else). No `pull_request_target`, no
  `workflow_run`.
- Top-level `permissions:` is exactly `contents: read`. There are no job-level `permissions`.
- No `secrets.` anywhere, no `continue-on-error`, no `|| true`, no `set +e`.
- No `${{` inside a `run:` block. Values reach scripts through `env:` and quoted `"$VAR"`, which
  stops injection through branch names and titles.
- `concurrency: group: <workflow name>-${{ github.event.pull_request.number }}`,
  `cancel-in-progress: true`.
- Every job has `timeout-minutes` (at most 30) and `runs-on: ubuntu-latest`.
- Every `uses:` is `owner/repo@<40 hex>` or on the allowlist, which has exactly one entry:
  `actions/checkout@v4`. The README names the allowlist and tells the human to replace it with
  a full commit SHA. Checkout has `persist-credentials: false`. There is no cache action and no
  `setup-*` action (the runner's `python3` is used).
- `fetch-depth: 0` in `diff-guard` and `commit-trailers`. The default depth elsewhere.

Jobs:
- `ci.yml`: job `check` with a matrix `target: [format, lint, types, test, secrets, deps, sast,
  licence]`, `fail-fast: false`, step `make "$TARGET"`. A missing Makefile or target fails the
  job (fail closed). Job `guard-tests` runs `python3 -m unittest discover -s .github/risk-gate`
  and `python3 -m unittest discover -s .github/ci`. Job `ci` has `needs: [check, guard-tests]`
  and `if: always()`, and fails unless both results are `success`. Branch protection requires
  `ci`.
- `diff-guard.yml`, `commit-trailers.yml` and `agents-drift.yml` run the guard script from the
  **base commit**, not from the PR (lead decision Q3): a first step writes
  `git show "$BASE_SHA:.github/ci/<script>"` to `"$RUNNER_TEMP/<script>"` and the next step runs
  that copy with `python3`. If the base commit has no such script (the PR that installs the
  guards), the step prints `::notice::guards not installed on the base commit` and the job passes;
  that PR is a block-lane change the human merges by hand. The check is `git cat-file -e`
  in an `if` / `else`, not `|| true`. The command lines below show the script arguments only.
- `diff-guard.yml`: `diff_guard.py --base "$BASE_SHA" --head "$HEAD_SHA"`.
- `commit-trailers.yml`: `check_trailers.py --branch "$BRANCH" --base
  "$BASE_SHA" --head "$HEAD_SHA"`, with `BRANCH` from `github.head_ref`.
- `agents-drift.yml`: `check_agents.py --root . --models
  .agents/skills/t3-build/references/models.md`.

To keep line-level checks sound, the templates use a restricted YAML subset: block style,
2-space indent, no tabs, no anchors or aliases, no flow mappings. A test enforces the subset.
PyYAML is not used, since it may not be installed. No full YAML parse is done (see section 11).

## 8. README (`ci/README.md`, lead)

- Copy table: source to destination. `ci/*.yml` and `gate/risk-gate.yml` go to
  `.github/workflows/`. `scripts/{check_agents,check_trailers,diff_guard}.py` and their
  `test_*.py` go to `.github/ci/`. `gate/{risk_gate.py,risk-policy.toml,test_risk_gate.py}` go
  to `.github/risk-gate/` (as in the gate README, shadow mode). `test_ci_templates.py` is not
  copied.
- Pin every action to a full commit SHA before the first run. The allowlist (`actions/checkout@v4`)
  exists only for the template tests.
- Required settings (human): required checks `ci`, `diff-guard`, `commit-trailers`,
  `agents-drift`, `risk-gate`; code owners for `.github/`; fork PR workflows need approval.
- Secrets rule: no secrets in these workflows. Deployment secrets live only in the
  human-triggered release job.
- The Makefile must define all eight targets. Suggested commands per stack (suggestions, not run
  in this repo): Python `ruff format --check`, `ruff check`, `mypy`, `pytest`, `pip-audit`,
  `pip-licenses`. Node `prettier --check`, `eslint`, `tsc --noEmit`, `npm test`, `npm audit`,
  `license-checker`. Go `gofmt -l`, `go vet`, `staticcheck`, `go test ./...`, `govulncheck`,
  `go-licenses`. Any stack: `gitleaks` (secrets), `semgrep` (sast).
- Limits of `diff_guard` (section 6), and that trailers are self-declared.

## 9. Acceptance criteria

Run: `cd .agents/skills/t3-build/scripts && python3 -m unittest test_check_agents
test_check_trailers test_diff_guard`, then `python3 -m unittest discover -s
.agents/skills/t3-build/ci`. Fixture repos are built in temp dirs with
`GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1` and a fixed author. Large histories are
built with `git fast-import`.

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | `check_agents`: exit 2 with empty stdout on a missing `--root`, a missing or unreadable `--models`, no matching table, a row with a wrong cell count, an unknown provider, both `.claude/agents` and `claude/agents` present, no provider folder at all. | tests |
| AC2 | `check_agents` parses the table formats of section 4: a comma-separated agent cell, `model (note)`, `effort (note)`, OpenRouter to `opencode` with the `openrouter/` prefix. | tests on a fixture `models.md` |
| AC3 | `check_agents` exits 1 with one line each on: a model differs, an effort differs (Claude, Codex, OpenCode with `reasoningEffort`), a file with no row, a row with no file in an existing folder, a `name` that differs from the stem, a duplicate key, no front matter, broken TOML, a symlinked file, a file over 64 KB, more than 200 files. A row whose provider has no folder is not reported. | tests |
| AC4 | Clean and real: `--root staging --models ../references/models.md` on this repo exits 0. A copy of `staging/` with one effort changed exits 1 and names that file. Skipped when `staging/` is not found, for example in `.github/ci/`. | test |
| AC5 | `check_trailers` on an `agent/` branch: a commit without `Agent:`, without `Tier:` or without both fails with its 12-hex sha and the missing part. A clean range exits 0. Merge commits are skipped. Zero commits pass. | tests, fixture repo |
| AC6 | Line rules: trailers in a paragraph followed by `Co-Authored-By:` pass (WP8 case). `Agent: x / m / medium (2 rounds, APPROVE)` and `Tier: T2 (note)` pass. These fail: `Agent: x (m, medium)`, `Agent: X / m / medium`, `Agent: x / m`, an indented ` Agent:`, `Tier: T3`, `Tier: T20`, `Tier:T1`, and `Agent:` inside a longer line. | tests |
| AC7 | Branches: `feature/x` and `main` exit 0 without running git (a non-repo `--repo` still exits 0). `Agent/x` is checked. A bad `--branch`, `--base` or `--head` (leading `-`, a space, an empty value) exits 2. An unknown revision exits 2. 501 commits exit 1 with `too large`. | tests |
| AC8 | `diff_guard` (a): deleting `tests/test_a.py`, `src/a.test.ts` or `test_x.py` at the root fails. Renaming `tests/test_a.py` to `src/a.py` fails. A rename test-to-test with unchanged assertions passes. Deleting a non-test file passes. | tests, fixture repo |
| AC9 | `diff_guard` (b): removing one `assert` line fails. Replacing `assert x` with `# assert x` fails. Removing 2 and adding 2 passes. Adding only passes. Each row of the assertion table is detected in a removed line. A comment-only change passes. A binary test file change passes. Counts appear as `(-r +a)`. | tests |
| AC10 | `diff_guard` (c): (a) or (b) together with an added or modified `docs/specs/x/spec.md` exits 0 and prints the notice. With only a deleted spec it still fails. `--spec docs/other/*` replaces the defaults. `docs/specs/a.spec.md` is a spec, not a test file. | tests |
| AC11 | `diff_guard`: bad revisions exit 2. More than `MAX_FILES` changed files exits 1 with `too large`. The diff base is the merge base: a commit that lands on base after the branch point and deletes a test does not fail the branch. | tests |
| AC12 | Hardening, all three scripts: a fixture repo with `log.showSignature=true`, `gpg.program`, `diff.external`, a textconv driver in `.gitattributes` with `diff.<x>.textconv`, and `core.fsmonitor`, each pointing to a script that writes a marker file. After every script run, no marker exists. The repo's `git status --porcelain` and refs are unchanged. | tests |
| AC13 | Output safety: a file name with `\x1b[31m`, `\n` or 300 characters, and a commit message with control bytes and an `Agent:` lookalike, appear only through `plain()` (no control characters on stdout, values cut at 200). No message text is printed. | tests |
| AC14 | Source scan of the three scripts: no `shell=True`, `os.system`, `eval(`, `exec(`, `socket`, `urllib`, `http.client`, `requests`. Every `subprocess` call passes a list. | tests |
| AC15 | Drift: `diff_guard.TEST_PATTERNS` equals the `paths` of the `tests` rule in `gate/risk-policy.toml` (skipped when the policy is not found). | test |
| AC16 | Templates (section 7): trigger, permissions, no `secrets.`, no `${{` in `run:`, concurrency, timeouts, `uses:` SHA or allowlist, `persist-credentials: false`, `fetch-depth: 0` where named, no fallbacks, YAML subset. Each check fails on a mutated copy of a template (for example with `secrets.X` inserted). | tests |
| AC17 | `ci.yml`: exactly the eight matrix targets, `fail-fast: false`, `guard-tests` runs both discover commands, `ci` needs both and checks results under `if: always()`. Every `.github/ci/*` and `.github/risk-gate/*` path named in a template is in the README copy table, and its source file exists. The README names the allowlist entry, the five required checks and the secrets rule. | tests |
| AC19 | The three guard templates run the script from the base commit through `$RUNNER_TEMP` as above, with the `git cat-file -e` if/else and the notice text; none runs `.github/ci/<script>` from the checked-out PR directly. A mutated copy that does fails the test. | test |
| AC18 | Docs: `references/commit-trailers.md` has the pointer line. Every relative Markdown link in `ci/README.md` resolves. | test, review |

Done when (PLAN): AC4 to AC11 together show that each job fails on a deliberate violation and
passes on a clean branch, at script level.

## 10. Security

**Attacker:** a PR author, either an agent or a fork. They control commit messages, branch
names, file names, file contents and diffs.

| Threat | Mitigation | Residual |
|---|---|---|
| Hostile repo config or attributes run a program through git | Section 3 git flags, AC12 | A new git config hook not covered by the flags |
| Option injection through revision or branch arguments | Argument regex, argv lists, AC7, AC11, AC14 | none known |
| Script injection through `${{ github.head_ref }}` in `run:` | `env:` only, test in AC16 | none known |
| Terminal or log injection through names or messages | `plain()`, no message text printed, AC13 | none known |
| Resource exhaustion (huge history, many files, long lines) | Bounds, fail closed, AC7, AC11 | runner timeout covers the rest |
| Catastrophic regex | Linear patterns, cut lines | none known |
| Symlink or path traversal in agent folders | No following, AC3 | none known |
| A PR edits the guard scripts or workflows so they pass | `.github/` is block lane; the human merges; code owners | The checks guard against agent mistakes. They cannot defend against a PR the human merges without reading it. A PR's own edited script judges that PR (Q3). |
| Branch-name spoofing: an agent commit on a non-`agent/` branch skips the check | Human merge; the monthly retro cross-checks trailers against `events.jsonl` | Accepted |
| Forged trailer: the committer writes any `Agent:` line | None. Trailers are self-declared, so this is a consistency check, not authentication | Accepted |
| PR code in `ci.yml` (Makefile) reads credentials | `pull_request` with a read-only token, no secrets, `persist-credentials: false`, no cache | Network egress of PR code is not limited |

Data class: commit messages and paths are Amber at most. Nothing leaves the runner.

## 11. Not verifiable here

GitHub Actions itself does not run here: trigger semantics, matrix display, the `if: always()`
aggregation, `head_ref` for forks, concurrency cancellation. Neither do a product repo's make
targets or the scanners (gitleaks, pip-audit, npm audit, semgrep, licence checkers), branch
protection, or code owners. The YAML is checked by line rules over a restricted subset, not by a
YAML parser or `actionlint`. The human verifies these on the first PR in the product repo (README
step), and the lead records the result there.

## 12. Non-goals

Running guards on `push` to `main`. Caching. Reusable workflows. A release or deploy workflow.
Enforcing the trailer format on human branches. Parsing `references/commit-trailers.md` at
runtime. Changing `risk_gate.py` or the policy. Making `check_agents` resolve model IDs against
a catalog (WP0, by hand). Semantic test-strength analysis (mutation testing).

## 13. Open questions

- **Q1** OpenCode files have no effort key, and models.md lists `high` for `third-voice` and
  `low` for `bulk-worker`. The spec compares effort only when `reasoningEffort` is present.
  Should a missing effort be a problem instead, with the staged files changed?
- **Q2** 30 of 60 non-merge commits on `HEAD` do not use `Agent: role / model / effort`. Recent
  lead commits use `Agent: implementer (claude-opus-5-5, medium); lead (...)`. The check follows
  `commit-trailers.md` and would fail those commits. Should the lead's footer habit change, or
  should the regex also accept the parenthesis form?
- **Q3** Run the guard scripts from the base commit (`git show "$BASE_SHA":.github/ci/<x>.py`),
  as the gate README does for its dry run, so a PR cannot weaken the script that judges it?
  This is cheap and stronger, but the installing PR fails its own guards and needs a by-hand
  merge.

## 14. Lead decisions on the open questions

- **Q1:** a missing effort in an OpenCode file is not drift (as written in section 4). The staged
  files stay as they are.
- **Q2:** the check follows `commit-trailers.md` strictly. The footer habit changes: commits on this
  branch use one `Agent: <role> / <model> / <effort>` line per agent. Older commits are outside any
  checked range.
- **Q3:** yes, base copy (section 7, AC19).
- The decisions listed under "not given" in the spec author's report are confirmed as written.
