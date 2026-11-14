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
   calls, are read-only on the repo and never execute code from the PR. (`ci.yml` deliberately runs
   the PR's Makefile and tests: that is the job's purpose. It is untrusted execution on a
   read-only token without secrets, section 10.) In a product repo the scripts live in
   `.github/ci/`, a block-lane path, so a PR that edits them is merged by the human.
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
- Git runs through `subprocess.Popen` (never `run` or `check_output`, so output can be bounded,
  see Bounds) with the argv `[GIT, "-C", repo, "-c", "core.fsmonitor=false", "-c",
  "diff.external=", "-c", "core.pager=cat", "--no-pager", <cmd>, ...]` and `shell=False`;
  `GIT = "git"` is a module constant (tests replace it with a stub executable). `log` gets
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
  gate), `MAX_BYTES = 5_000_000` git output per call, `MAX_LINE = 4096`, `GIT_TIMEOUT = 60`
  seconds per git call. Input bounds **reject**, they never truncate a decision input: going over
  `MAX_COMMITS`, `MAX_FILES`, `MAX_BYTES`, a 64 KB commit message or an agent file limit prints
  `too large: <what> over <limit>` and exits 1 (a message over 64 KB fails that commit). Git
  output is collected in chunks through `Popen` and the process is killed as soon as `MAX_BYTES`
  is exceeded (checking the size after a full capture is not a bound). A git call that passes
  `GIT_TIMEOUT` is killed and exits 2 with `git timed out`. Only two things are cut: printed
  values (`plain()`), and a line longer than `MAX_LINE` before an assertion regex runs on it
  (regex input bound; the line still counts if it matches in its first `MAX_LINE` characters).
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
- A commit passes when its message (at most 64 KB, else the commit fails with `message too large`;
  `\r` removed) has, in **any** paragraph:
  - at least one line matching
    `^Agent: [a-z0-9][a-z0-9-]{0,63} / [A-Za-z0-9][A-Za-z0-9._/:-]{0,127} / [a-z]{1,16}( \([^()]{0,200}\))?\s*$`
    (the reviewer form `medium (2 rounds, APPROVE)` is allowed), and
  - at least one line matching `^Tier: T[012](\s.*)?$`. `T3` and `T20` fail. A note after a
    space is allowed (`Tier: T2 (gate code; human merges)`).
- The human-only exception of `commit-trailers.md` does not apply on `agent/*` branches. Every
  non-merge commit must carry both lines.
- **Limits (README and docstring):** the lines are self-declared and any matching line counts,
  including one inside a quoted or fenced example in the body (the check is a consistency check,
  not authentication; accepted). Merge commits are skipped, so changes authored in a merge
  resolution carry no trailer requirement (accepted; the human reviews merges).
- Failure line: `<12-hex sha>: missing Agent line`, `missing Tier line` or `missing Agent and
  Tier lines`. Exit 1 if any commit fails. Last line: `trailers: <n> commits checked, <m>
  failing`.

## 6. `diff_guard.py`

CLI: `python3 diff_guard.py --base REV --head REV [--repo DIR] [--spec PATTERN]...`.

- Diff base: `git merge-base BASE HEAD`. Files: `git diff -z --raw --no-abbrev -M --no-ext-diff
  --no-textconv <mb> HEAD`. At most `MAX_FILES`.
- Patterns use the gate's `matches` semantics (fnmatch, `*` crosses `/`, `*/x` also matches at
  the root, a leading `/` anchors) on the lowercased path. The script holds its own copy as
  constants (no import across folders):
  - `TEST_PATTERNS` = the policy's `tests` rule: `*/tests/*`, `*/test/*`, `*/test_*`, `*_test.*`,
    `*.test.*`, `*.spec.*`.
  - `SPEC_PATTERNS` default: `*/docs/specs/*`, `*/docs/*/spec.md`, `*/docs/*.spec.md`.
    `--spec`, repeatable, replaces the defaults. A path that matches a spec pattern is never a
    test file.
- Raw status comes from `git diff -z --raw --no-abbrev -M ...` so file modes are known.
- **(a) Test file removed:** status `D` on a test file, or `R` with a test-file old name and a
  non-test new name. Line `removed test file: <path>`.
- **(a2) Test file type change:** status `T`, or any status where a test-pattern path gets mode
  `120000` (symlink) or `160000` (submodule) on the head side, or where a test file that was text
  becomes binary (`git diff --numstat` shows `-` for the new side and the old side was text).
  Line `test file type changed: <path>`. Binary to binary passes. A case-only rename is an `R`
  between two test paths and is counted as in (b); two paths that differ only by case in the same
  tree are not handled specially (case-insensitive checkouts are the human's concern, stated in
  the limits).
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
- **(c) Spec change:** when (a), (a2) or (b) fires and the diff has a file with status `A`, `M` or `R`
  (new name) matching a spec pattern (a deleted spec does not count), the guard passes, exits 0
  and prints `notice: tests removed or assertions shrink; spec changed: <spec path>` after the
  violation lines.
- Clean: exit 0, line `diff guard: <n> test files checked`.

**Limits (stated in the README and the script docstring):** this is a heuristic, not proof.
False positives: an assertion moved to another file, a test file renamed to a non-test name, a
removed fixture under `tests/`, an assertion rewritten into a helper. Any qualifying spec change, however small or
unrelated, authorizes every removal the guard found (PLAN's literal rule "without a spec change";
the human decides whether the spec change is relevant). False negatives: an
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
- `fetch-depth: 0` in `diff-guard`, `commit-trailers` **and `agents-drift`** (all three verify
  `BASE_SHA` and `HEAD_SHA` and extract a file from the base commit, so both commits must be in the
  checkout; a depth-one checkout of the merge commit need not hold them). The default depth in
  `ci.yml`.

Jobs:
- `ci.yml`: job `check` with a matrix `target: [format, lint, types, test, secrets, deps, sast,
  licence]`, `fail-fast: false`, step `make "$TARGET"`. A missing Makefile or target fails the
  job (fail closed). Job `guard-tests` runs `python3 -m unittest discover -s .github/risk-gate`
  and `python3 -m unittest discover -s .github/ci`. Job `ci` has `needs: [check, guard-tests]`
  and `if: always()`, and fails unless both results are `success`. Branch protection requires
  `ci`.
- `diff-guard.yml`, `commit-trailers.yml` and `agents-drift.yml` run the guard script from the
  **base commit**, not from the PR (lead decision Q3). `BASE_SHA` and `HEAD_SHA` come only from
  `github.event.pull_request.base.sha` and `.head.sha` in `env:`; the templates have no
  `workflow_dispatch`, no inputs and no other source. Steps, in order: (1) verify both commits
  exist (`git cat-file -e "$BASE_SHA^{commit}"` and the same for head; a failure fails the job);
  (2) `git ls-tree --name-only "$BASE_SHA" -- .github/ci/<script>`: a non-zero exit fails the job;
  empty output means the guard is not installed on the base: print
  `::notice::guards not installed on the base commit` and pass (the PR that installs the guards is
  a block-lane change the human merges by hand); otherwise (3) `git show
  "$BASE_SHA:.github/ci/<script>" > "$RUNNER_TEMP/<script>"`, whose failure fails the job; (4) run
  that copy with `python3`. No `|| true`; the branches are `if`/`else`. If the human removes a
  guard from the target branch, the job passes with the notice: the README says the notice means
  "not installed" and that the required check must stay required. This scope is only the guard
  entry steps: `guard-tests` and `check` deliberately run PR code (section 10). The command lines
  below show the script arguments only.
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
- Prerequisites the README states: the skill folder is installed in the product repo
  (`.agents/skills/t3-build/references/models.md` is read by `agents-drift`); the agent folders
  `.claude/agents`, `.codex/agents`, `.opencode/agents` are installed (WP0), else `agents-drift`
  exits 2 and fails; the risk gate needs its own token `RISK_GATE_TOKEN`, used only by
  `gate/risk-gate.yml` (the one workflow with a secret; none of the four templates has one).
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

Run from the repo root: `(cd .agents/skills/t3-build/scripts && python3 -m unittest
test_check_agents test_check_trailers test_diff_guard)`, then `(cd .agents/skills/t3-build/ci &&
python3 -m unittest test_ci_templates)`. Paths in AC4 are relative to `scripts/`. Fixture repos are built in temp dirs with
`GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1` and a fixed author. Large histories are
built with `git fast-import`.

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | `check_agents`: exit 2 with empty stdout on a missing `--root`, a missing or unreadable `--models`, no matching table, a row with a wrong cell count, an unknown provider, both `.claude/agents` and `claude/agents` present, no provider folder at all. | tests |
| AC2 | `check_agents` parses the table formats of section 4: a comma-separated agent cell, `model (note)`, `effort (note)`, OpenRouter to `opencode` with the `openrouter/` prefix. | tests on a fixture `models.md` |
| AC3 | `check_agents` exits 1 with one line each on: a model differs, an effort differs (Claude, Codex, OpenCode with `reasoningEffort`), a file with no row, a row with no file in an existing folder, a `name` that differs from the stem, a duplicate key, no front matter, broken TOML, a symlinked file, a file over 64 KB, more than 200 files. A row whose provider has no folder is not reported. | tests |
| AC4 | Clean and real: `python3 check_agents.py --root ../../../../staging --models <absolute path of references/models.md>` (from `scripts/`; a `..` component in `--models` is rejected, AC31) on this repo exits 0. A copy of `staging/` with one effort changed exits 1 and names that file. Skipped when `staging/` is not found, for example in `.github/ci/`. | test |
| AC5 | `check_trailers` on an `agent/` branch: a commit without `Agent:`, without `Tier:` or without both fails with its 12-hex sha and the missing part. A clean range exits 0. Merge commits are skipped. Zero commits pass. | tests, fixture repo |
| AC6 | Line rules: trailers in a paragraph followed by `Co-Authored-By:` pass (WP8 case). `Agent: x / m / medium (2 rounds, APPROVE)` and `Tier: T2 (note)` pass. These fail: `Agent: x (m, medium)`, `Agent: X / m / medium`, `Agent: x / m`, an indented ` Agent:`, `Tier: T3`, `Tier: T20`, `Tier:T1`, and `Agent:` inside a longer line. A message of over 64 KB fails with `message too large`. A fenced example inside the body that contains both lines passes (stated limit), and a merge commit without trailers is skipped (stated limit). | tests |
| AC7 | Branches: `feature/x` and `main` exit 0 without running git (a non-repo `--repo` still exits 0). `Agent/x` is checked. A bad `--branch`, `--base` or `--head` (leading `-`, a space, an empty value) exits 2. An unknown revision exits 2. 501 commits exit 1 with `too large`. | tests |
| AC8 | `diff_guard` (a): deleting `tests/test_a.py`, `src/a.test.ts` or `test_x.py` at the root fails. Renaming `tests/test_a.py` to `src/a.py` fails. A rename test-to-test with unchanged assertions passes, and fails when the same commit also removes an assertion (similarity rename plus edit). Replacing a test file by a symlink or a submodule, a type change (`T`) and a text test becoming binary fail (a2); binary to binary passes. A case-only rename of a test file with unchanged assertions passes. Deleting a non-test file passes. | tests, fixture repo |
| AC9 | `diff_guard` (b): removing one `assert` line fails. Replacing `assert x` with `# assert x` fails. Removing 2 and adding 2 passes. Adding only passes. Each row of the assertion table is detected in a removed line. A comment-only change passes. A line longer than `MAX_LINE` is cut before matching. Counts appear as `(-r +a)`. | tests |
| AC10 | `diff_guard` (c): (a) or (b) together with an added or modified `docs/specs/x/spec.md` exits 0 and prints the notice. With only a deleted spec it still fails. A one-character change to an unrelated spec authorizes all findings (the limit is stated). `--spec docs/other/*` replaces the defaults. `docs/specs/a.spec.md` is a spec, not a test file. | tests |
| AC11 | `diff_guard`: bad revisions exit 2. More than `MAX_FILES` changed files exits 1 with `too large`. The diff base is the merge base: a commit that lands on base after the branch point and deletes a test does not fail the branch. | tests |
| AC12 | Hardening, all three scripts: a fixture repo with `log.showSignature=true`, `gpg.program`, `diff.external`, a textconv driver in `.gitattributes` with `diff.<x>.textconv`, and `core.fsmonitor`, each pointing to a script that writes a marker file. After every script run, no marker exists. The repo's `git status --porcelain` and refs are unchanged. | tests |
| AC13 | Output safety: a file name with `\x1b[31m`, `\n` or 300 characters, and a commit message with control bytes and an `Agent:` lookalike, appear only through `plain()` (no control characters on stdout, values cut at 200). No message text is printed. | tests |
| AC14 | Source scan of the three scripts: no `shell=True`, `os.system`, `eval(`, `exec(`, `socket`, `urllib`, `http.client`, `requests`. Every `subprocess` call passes a list. | tests |
| AC15 | Drift: `diff_guard.TEST_PATTERNS` equals the `paths` of the `tests` rule in `gate/risk-policy.toml` (skipped when the policy is not found). | test |
| AC16 | Templates (section 7): trigger, permissions, no `secrets.`, no `${{` in `run:`, concurrency, timeouts, `uses:` SHA or allowlist, `persist-credentials: false`, `fetch-depth: 0` in the three guard workflows (named in section 7), no fallbacks, YAML subset. Each check fails on a mutated copy of a template (for example with `secrets.X` inserted). | tests |
| AC17 | `ci.yml`: exactly the eight matrix targets, `fail-fast: false`, `guard-tests` runs both discover commands, `ci` needs both and checks results under `if: always()`. Every `.github/ci/*` and `.github/risk-gate/*` path named in a template is in the README copy table, and its source file exists. The README names the allowlist entry, the five required checks and the secrets rule. | tests |
| AC18 | Docs: `references/commit-trailers.md` has the pointer line. Every relative Markdown link in `ci/README.md` resolves. | test, review |
| AC19 | The three guard templates take `BASE_SHA`/`HEAD_SHA` only from the pull request event fields, verify both commits, then run the script from the base commit through `$RUNNER_TEMP` with the `git ls-tree` present/absent/error branches and the notice text of section 7; none runs `.github/ci/<script>` from the checked-out PR directly in its guard entry steps. A mutated copy that does fails the test. Executable fixture tests (a shell run of the extracted step logic against a temp repo; the step text is pulled from the template) cover: script present (runs, result used), absent (notice, pass), git error or missing base object (fail). | test |
| AC20 | Bounds are executable at the helper level. `check_trailers.py` and `diff_guard.py` (the two scripts that call git; `check_agents.py` reads files only and is bounded by AC3) each expose `run_git(args, repo)`, the only place that starts git. With `GIT` replaced by a stub executable written by the test and `MAX_BYTES`/`GIT_TIMEOUT` reduced: (a) a stub writing more than `MAX_BYTES` without exiting makes `run_git` stop reading and kill it, and the script exits 1 with `too large`; (b) a stub that sleeps without writing past `GIT_TIMEOUT` is killed and the script exits 2 with `git timed out` and empty stdout; (c) a stub writing exactly `MAX_BYTES` and exiting 0 makes `run_git` return exactly those bytes (the helper accepts the limit; the arbitrary bytes are not parsed). In (a) and (b) the call returns within a few seconds and the stub process is dead afterwards (its pid is checked, no zombie). | tests |

**Done, local scope:** AC1 to AC20 pass in this repo (script behaviour on fixture repos, static
checks of the templates, the base-copy step logic). **PLAN's "done when"** (each job fails on a
deliberate violation and passes on a clean branch) is only fully met by the product-repo
demonstration in section 11, with a recorded clean run and a recorded failing run for each of the
five jobs. Until the human or the lead records that, WP9 stays open in the PLAN sense, as WP5 and
WP6 do.

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
| A PR edits the guard scripts or workflows so they pass | `.github/` is block lane; the human merges; code owners | The checks guard against agent mistakes. They cannot defend against a PR the human merges without reading it. The guards run from the base commit (section 7), so a PR cannot weaken the script that judges it; the installing PR and a human-removed guard pass with a notice. |
| Branch-name spoofing: an agent commit on a non-`agent/` branch skips the check | Human merge; the monthly retro cross-checks trailers against `events.jsonl` | Accepted |
| Forged trailer: the committer writes any `Agent:` line | None. Trailers are self-declared, so this is a consistency check, not authentication | Accepted |
| PR code in `ci.yml` (Makefile) and `guard-tests` runs untrusted code, deliberately; it may read credentials | `pull_request` with a read-only token, no secrets, `persist-credentials: false`, no cache | Network egress of PR code is not limited |

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

## 15. Review log

Sol round 1 (CHANGES): findings 2 to 10 are accepted and applied in sections 1, 3 to 10 and AC9, AC6,
AC19. Finding 1 (the gate files are malformed) is declined with evidence: `risk-policy.toml` parses
with `tomllib` (`small_diff_lines = 200` under `[limits]`, the gate tests load it) and
`risk-gate.yml` parses as YAML (Ruby's `YAML.load_file(...).keys` returns `["name", true, "permissions", "jobs"]`; `true` is the key `on`, which YAML 1.1 reads as a boolean); the review read
them through a lossy view. WP9 does not depend on a repair of those files.
Sol round 2 (CHANGES, one blocker and two shoulds, finding 1 supported by the bytes): applied:
`fetch-depth: 0` for all three guard workflows (section 7, AC16), `Popen` with a `GIT` constant
instead of `subprocess.run` (section 3), and AC20 for the byte bound, the kill and the timeout.

Sol round 3 (CHANGES, two shoulds on AC20): applied (helper-level test of `run_git`, `check_agents` excluded). The spec review budget (3 rounds for T2) is used; no further round.

## 16. Conventions the tests fix (confirmed by the lead; the implementer follows the tests)

Where the spec left a detail open, the frozen tests decide it. The ones that matter:

- **Templates:** job ids equal the required check names (`diff-guard`, `commit-trailers`,
  `agents-drift`; in `ci.yml` the jobs `check`, `guard-tests`, `ci`). Workflow names are distinct.
  `concurrency` is top-level. `github.token`, `secrets: inherit` and `inputs` are banned; the word
  `secrets.` is banned even in comments. In a guard job's run text `.github/ci/` appears only
  after `-- ` or `:` (in the `ls-tree` and `show` lines), never as a command to run. Script
  arguments are exactly those of section 7. Guard branches are shell `if`/`else` (no step-level
  `if:`); the `ci` job reads the `needs` results through `env:`. The step emulator of the tests runs
  bash only and allows the expressions base sha, head sha, `head_ref`, `matrix.target` and
  `needs.<job>.result`.
- **`check_agents`:** `<rel path>` in a problem line is relative to the checked folder root or
  shorter (the tests accept any leading folder). A symlinked file may also give a second line.
  `main(argv=None)` is required; `run_git` and constants are read at call time.
- **`diff_guard`:** a rename to a non-test name names the old path; a new symlink or gitlink on a test
  path fails; whether the old side of a test file was text is decided from the blob content, not
  from `numstat` alone (a `-diff` attribute must not hide removed assertions); a printed value is at
  most 203 characters ending in `...`.
- **Copy table:** `test_check_trailers.py` and `test_diff_guard.py` import helpers from earlier
  test files, so the three script test files are copied to `.github/ci/` together.

## 17. Security round 1 (Sol, reviewer-security): findings and required changes

Six findings, all accepted (a security finding is fixed or escalated, never declined). New ACs:

| AC | Requirement | Evidence |
|---|---|---|
| AC21 | **No lazy fetch, no inherited git behaviour.** `run_git` builds the child environment from the parent's by dropping every variable that starts with `GIT_` and then setting `GIT_TERMINAL_PROMPT=0`, `GIT_NO_LAZY_FETCH=1` and `GIT_OPTIONAL_LOCKS=0`. A fixture repo with `extensions.partialClone=origin`, `remote.origin.promisor=true`, `remote.origin.url=ssh://example.invalid/r` and `core.sshCommand=<marker script>`, with a needed object removed (a tree for `diff_guard`, a parent commit for `check_trailers`), never runs the marker script; the script exits 2 with a message. In the three workflow templates the guard step has `GIT_NO_LAZY_FETCH: "1"` in its `env:` (the preflight, `ls-tree` and `show` commands run in a PR checkout too). | tests |
| AC22 | **Pathspec variables.** With `GIT_LITERAL_PATHSPECS=1`, `GIT_GLOB_PATHSPECS=1`, `GIT_NOGLOB_PATHSPECS=1` or `GIT_ICASE_PATHSPECS=1` set in the parent environment, `diff_guard` still fails on a removed assertion in `tests/test_[x]*.py` (and on a path with glob characters). | tests |
| AC23 | **Models file is read like an agent file.** `check_agents.py` opens `--models` with `O_NOFOLLOW | O_NONBLOCK`, requires a regular file (`fstat`), reads at most `MAX_MODELS_BYTES = 1_000_000` (more: a line `too large: models file over 1000000` and exit 1, as for any input over a bound; unreadable, not regular or a symlink: exit 2 with empty stdout). A symlink, a FIFO (no hang: returns within a few seconds) and a 1,000,001-byte regular file are each rejected; a normal file passes. | tests |
| AC24 | **Front-matter keys.** In Claude and OpenCode front matter a top-level key must match `^[A-Za-z_][A-Za-z0-9_-]*$`; any other key syntax (quoted keys, keys with control characters or a NUL, keys with spaces) makes the file unparsable (exit 1, one line). `"model": evil` after `model: expected` is rejected as unparsable, not accepted. Duplicate detection is exact-match on the key. | tests |
| AC25 | **Process group.** `run_git` starts git with `start_new_session=True` and, on timeout or output overflow, kills the whole process group (`os.killpg(..., SIGKILL)`) and reaps git. A stub git that spawns a child (`sleep`) and then stalls or floods leaves no descendant alive after the call (checked with the child's pid written by the stub). | tests |
| AC26 | **Message size and NUL.** `check_trailers` obtains each commit's message from the raw commit object (`git cat-file commit <sha>`, bounded by `MAX_BYTES`; the message is the part after the first empty line), not from `%B`. A message that contains a NUL byte fails the commit (`message contains NUL`); the 64 KB limit applies to the raw message bytes. `git log` is used only for the list of shas (`--format=%H`). A commit made with `git hash-object --literally` whose message is `Agent: ... \nTier: T2\n\0` followed by 70,000 bytes fails. | tests |

The three scripts keep every earlier AC. Where an existing test pins `%B` or the exact `log` format, the frozen test wins and the implementer reports the conflict instead of changing it.

## 18. Security round 2 (Sol): three low findings, all accepted

| AC | Requirement | Evidence |
|---|---|---|
| AC27 | **Parent symlinks of the models file.** When `--models` lies inside `--root`, every path component below `--root` (folders and the file) must not be a symlink; one that is gives exit 2 with empty stdout (as for a symlinked file). A `--models` outside `--root` (the default `<script dir>/../references/models.md`) only has its leaf checked: absolute ancestors such as `/var` or `/tmp` may be symlinks on a runner. Fixture: `root/refs -> elsewhere` with `--models root/refs/models.md` exits 2. | tests |
| AC28 | **SIGTERM unwinds.** `check_trailers.py` and `diff_guard.py` install a SIGTERM handler (in `main`, not at import) that raises so the `run_git` cleanup runs: after a SIGTERM sent to the script while a stub git and its child sleep, both are dead and the script exits non-zero (143 or 2). SIGKILL of the script cannot be handled and is left to the runner (stated in the docstring). | tests |
| AC29 | **Kill before reaping.** On timeout, overflow or any exception, `run_git` calls `os.killpg(pgid, SIGKILL)` first (ignoring `ProcessLookupError`) and only then waits for and reaps git; it does not call `poll()` before the kill. Test: with `os.killpg` and `Popen.poll`/`wait` wrapped to record order, the kill comes first in the overflow and timeout cases. | tests |

## 19. Security round 3 (Sol): five low findings, all accepted

Round 2 closed. Round 3 found five lows. Shipped `agents-drift.yml` passes fixed `--root .` and
`--models .agents/skills/t3-build/references/models.md` (no PR value reaches them), so findings 2, 3
and 5 need a changed workflow or a concurrent writer in the same checkout; they are fixed anyway
(a security finding is fixed or escalated, never declined).

| AC | Requirement | Evidence |
|---|---|---|
| AC30 | **SIGTERM is deferred, not raised.** The handler of AC28 only sets a flag. `run_git` checks the flag at every loop step (the `select` wait is at most 0.1 s), and after the flag is seen it runs the normal cleanup (kill the group, reap) and then exits through `SystemExit(143)`. The cleanup is never interrupted by an exception raised from the handler: a SIGTERM sent (a) while git is running, (b) twice in a row, (c) between `Popen` returning and the first loop step, (d) during cleanup (stub git that ignores SIGTERM and children that linger) leaves no git and no child alive and exits 143. | tests |
| AC31 | **Models path forms.** `check_agents.py` rejects, with exit 2 and empty stdout, a `--models` value that contains a `..` component, an empty component (`//`, trailing `/`) or is empty; `.` components are removed. "Inside `--root`" is decided by directory identity: the lexical ancestors of the models path are compared with `--root` using `os.path.samefile`; a path whose ancestor is the same directory as `--root` (a case alias such as `ROOT` on a case-insensitive filesystem, or a bind of the same inode) is inside and gets the AC27 component walk below that ancestor. | tests |
| AC32 | **Kill failures are reported and the wait is bounded.** In `run_git` cleanup an `OSError` from `killpg` other than `ProcessLookupError` is recorded (not swallowed) and, after the reap, makes the call fail with exit 2 and `could not terminate git`; the reap uses `wait(timeout=5)` and on expiry the same message and exit 2. A `ProcessLookupError` is ignored. Test by wrapping `os.killpg` to raise `PermissionError`, and by a stub that ignores SIGKILL's group (simulate with a wrapper whose `wait` raises `TimeoutExpired`). | tests |
| AC33 | **Agent folders are pinned.** `check_agents.py` opens each provider agents folder once with `O_RDONLY\|O_DIRECTORY\|O_NOFOLLOW` (walking from `--root` component by component with `dir_fd`, as AC27) and lists and reads the files through that descriptor (`os.listdir(fd)`, `os.open(name, ..., dir_fd=fd)`): replacing `claude/agents` by a symlink after the folder was opened does not change which files are read. Test with a patched `os.open` hook that swaps the folder for a symlink to an external folder after the first open of the folder and asserts the external file is not read. | tests |
| AC34 | All earlier ACs and the suites still pass; the docstrings state what SIGTERM, SIGKILL and `--models` forms do. | tests |
