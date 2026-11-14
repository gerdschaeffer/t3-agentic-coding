# Risk gate

Every PR is rated 0-3 on three dimensions. The lane comes from the **highest**
rating, never an average:

| Lane | Rule | What happens |
|---|---|---|
| auto | all ratings 0-1 and every condition below holds | auto-merge (`[merge] method`, default squash) on the exact head commit once CI passes |
| review | any rating 2, or a condition fails | waits; the owner reads the risk card, brings the branch up to date, and merges by hand once checks are green (not via auto-merge: the gate disarms it on the next push, it cannot tell the owner's click from an agent's) |
| block | any rating 3 | `risk-gate` status fails; split, flag, plan, or the owner overrides |

Auto-merge conditions: a trusted risk rating for the current head commit,
verdict APPROVE, no disputed findings, a clean security pass whenever security
is 1 or more, not a draft, inside merge hours. Exception: if every file is on
the `no_rating` list, rates 0 and the PR was opened by the rater, no rating is
needed (see "No-rating list (T0)").

## Shadow-mode repos: the leader's own check

Where the gate cannot enforce (no branch protection) and the leader merges the
auto lane itself, it decides from a read-only dry run of **main's** gate and
policy, never from the `risk-gate` status, which any workflow can write:

```
RISK_GATE_DRY_RUN=1 PR=<n> REPO=<owner/repo> \
  RISK_GATE_POLICY=<(git show origin/main:.github/risk-gate/risk-policy.toml) \
  python3 <(git show origin/main:.github/risk-gate/risk_gate.py)
```

It prints the lane, ratings and reasons and writes nothing. When the no-rating
exemption was used, it prints the reason line
`no rating needed: only user-documentation files`. Run it from a
recipe the branch cannot change (e.g. `make -f <(git show origin/main:Makefile)`).

## Scale (reviewers rate with this)

| Level | Regression | Security | User impact (only when `live = true`) |
|---|---|---|---|
| 0 | docs, comments, new test files only | no security trigger touched | not reachable by users (internal, tests, flag off) |
| 1 | small change, changed lines covered by tests, no interface change | trigger touched only by additive hardening; both security reviews clean | user-visible but behind a flag or progressive rollout, reversible |
| 2 | public interface change, shared module with many callers, large diff, weak coverage, removed tests, a change to a spec or runbook, a change to an existing test file, dependency or config change | authn/authz, permissions, tenant or customer data, secrets, crypto, input parsing at a trust boundary, new dependency | user-visible without a flag, critical journey (login, payment, export), performance-sensitive path |
| 3 | schema or data migration, CI/deploy/gate config | open security finding, exposed secret, permission model change | irreversible effects (emails, deletions, billing) or the error budget is burning |

## How the rating gets in

The policy file gives the deterministic floor. The reviewer's rating comes as a
PR comment from a trusted login (default: repo owner), first line exactly:

```
<!-- risk-rating sha=<full 40-char head SHA> -->
verdict: APPROVE
regression: 1
security: 0
user: 0
security_pass: n/a
disputed: none
reasons: <one line>
```

`security_pass` is `clean` or `n/a`; `disputed` is `none` or finding ids. Only
the repo owner can rate (personal repos). A rating for another commit, with
anything before the marker, or with a bad value is ignored. A new push needs a
new rating.

Ratings are **append-only**: the newest rating for the head commit wins, so to
withdraw an approval post a new rating with `verdict: CHANGES`. Never edit or
delete a rating: the gate re-evaluates, and an older approval for the same
commit can count again. Only the owner identity can do that, which is inside the
accepted risk below.

**Accepted risk (owner decision, 2026-11-07):** the leader posts the rating under
the owner's identity, so the gate cannot tell an honest relay of Sol's verdict
from a faked one. A buggy or prompt-injected leader could auto-merge ordinary
code. Path rules still hold: auth, secrets, dependencies, migrations and CI never
reach the auto lane. Upgrade path: run the rating inside CI and trust only
`github-actions[bot]`, or restrict the auto lane to a path floor of 0. The
no-rating exemption trusts the rater login as PR author, the same identity this
relay risk already covers. The open risks of the no-rating list are below; they
are not part of this decision.

## No-rating list (T0)

User documentation gets no model review, so it has no rating. The policy's
`[no_rating]` table lists the files that may merge without one:

```toml
[no_rating]
paths = ["/CHANGELOG.md", "/docs/user/*.md"]
```

- User documentation only: never tests, specs, runbooks, agent definitions or
  anything that runs or steers. Do not widen the `docs` or `tests` rules for it.
- Every pattern must start with `/` (anchored, `*` stays in one directory); an
  unanchored pattern makes the gate fail closed (status stays pending).
- Do not use `**`: it is not recursive. Nested docs need one pattern per depth
  (`/docs/user/*/*.md`).
- Every file must be on the list, under both names for a rename, and the whole PR
  must rate 0 on every dimension (diff size and file count included). One other
  file, a symlink or a block or security rule hit, and the PR needs a rating.
  The block rules are also checked case-insensitively for the exemption: an
  instruction file like `docs/user/claude.md` is never exempt.
- Every file must change at least one line: a binary, mode-only or empty file, or
  a rename that reports 0 changed lines, is not exempt (GitHub reports a renamed
  binary with new content the same way).
- Names must be ASCII: `AGENTſ.md` (U+017F) folds to `AGENTS.md` on some
  filesystems, so a non-ASCII documentation name needs a rating.
- The PR author must be the rater login. A PR by anyone else needs a rating.
- A rating for the head commit, if present, counts as usual. A rating-marker
  comment by the rater for the head commit, even a malformed one, means the
  exemption does not apply.
- The card and the dry run show the reason line
  `no rating needed: only user-documentation files`.

**Open risks of the no-rating list** (the owner accepts them by merging a policy
that has the list; they are not covered by the dated decision above):

- The exemption binds to who opened the PR, not who wrote the head commit: a PR
  branch opened by the rater must not accept pushes from others.
- The instruction filenames in the block rule are a deny list: the default names
  of Claude Code, Codex, Gemini CLI, OpenCode, Amp, Warp, Crush and Qwen Code.
  Other names (known: `CONTEXT.md`, `IFLOW.md`, `CODEBUDDY.md`) and any `*.md`
  that a tool config names (Codex `project_doc_fallback_filenames`, Gemini and
  Qwen `context.fileName`, `opencode.json` `instructions`, an `@import`) can sit
  in an exempt folder and merge unreviewed. A project using another agent adds
  its names to the block rule, keeps the `no_rating` list narrow, and never
  names an exempt file in an agent config.
- A veto comment that misses the exact marker (first line, full head SHA) does
  not stop an exempt PR, and the workflow does not re-run on it: convert the PR
  to draft or close it.
- The case check (the exemption and the floor) lowercases rule patterns. A character range that spans cases
  (`[A-_]`) is not folded correctly; write such patterns without it.

## Specs, runbooks and changed tests

Rule `specs and runbooks` (regression 2): `docs/specs/*`, `docs/*/spec.md`, `docs/*.spec.md` (the
spec patterns of `diff_guard`), `runbooks/*` and `runbook*.md`. Rule key `existing_changed = N` on
the `tests` rule: a test file the PR changes, removes or renames (any file status except `added`)
rates regression N, in the exact and in the case-folded pass, and for the old name of a rename.
Why: `diff_guard` passes a removed test or a shrinking assertion with a notice when the diff also
changes a spec, so a weakening could be laundered through a spec or an edited test and still rate 0.

The gate reads each file's `status` from the GitHub files API in `main`. A file without a `status`
key (callers such as `tier.py`, which tiers before any diff exists, and unit tests) counts as not
changed; `main` maps a missing or null `status` to `unknown`, which counts as changed, so a response
without the field fails closed.

Two fold rules keep the floor from being dodged by a name (Sol security pass, spec 022). The
case-folded pass is the higher of two keyings, `lower()` and NFKC plus `casefold()` (NFKC alone can
lower a floor: it composes `N` and U+0303 into `Ñ`), so `docs/ſpecs/x.md`
(U+017F) is the spec directory on a case-folding checkout and rates like `docs/specs/x.md`; the
exact pass is unchanged. And `main` lists the head tree and marks an `added` file `case collision`,
which `existing_changed` rates as a changed file, when its name is non-ASCII, when the tree holds
any non-ASCII path, or when another tree path has the same `lower()` name (a `tests/test_login.py`
next to `tests/test_Login.py` replaces it on a case-insensitive checkout). The aliases a filesystem
applies (Unicode normalisation, ignorable characters such as U+200C on HFS+, upcase tables) cannot
be listed, so the rule is conservative: in a repository with any non-ASCII file name, a new test file
goes to the review lane. Not detected: ASCII aliases other than case (Windows 8.3 short names;
git's `core.protectNTFS` rejects trailing dots and spaces, but its short-name checks cover only Git
metadata names, so an arbitrary 8.3 alias is not refused). A lookalike that no
fold maps (Cyrillic letters) is a different directory on disk and is not folded.

Consequence: a PR that adds a spec under `docs/specs/` goes to the review lane. A PR that adds only
new test files stays at regression 0 while the head tree is all ASCII and no new name collides by
case; otherwise those test files rate 2. To keep new specs in the auto lane, give the
specs rule `existing_changed = 2` and no `regression` of its own; nothing else changes.

## When the gate runs

The workflow runs on `pull_request_target` (opened, synchronize, reopened, ready for review,
converted to draft) and on the owner's rating comments. It also runs again when the base branch of
a pull request is changed (a retarget is an `edited` event), so the rating, the lane and the status
follow the new base and an armed auto-merge is re-evaluated. Editing only the title or the body
does not start the gate: the job condition lets an `edited` event through only when
`changes.base.ref.from` is present (compared with the empty string: `!= null` would fail for a
branch named `0`).

Warning: a retarget keeps the head SHA, so the old `risk-gate` success stands until the new run
writes `pending`. If auto-merge is armed and the actor has write access, it can consume that old
success in the gap (GitHub documents that it disables auto-merge when someone without write access changes the base).
Disable auto-merge before retargeting a pull request. The structural fix is not part of this
workflow: publish the required result for the current merge candidate (base and head), and, to
retire head-only successes, use a new required status context in the same change. A new context
alone does not help: once it has succeeded on an unchanged head, another retarget can reuse it.

## Pending before any fallible read, and on failure

The workflow passes the event's head SHA to the script as `HEAD_SHA`. Before its first read of the
pull request (which supplies the real head SHA) the script writes `pending` for that SHA, when it has the
exact shape of 40 lower-case hex characters; any other value is ignored. If the first read fails, or
the job fails at any point, the required check stays unmet instead of showing an old success on a
retargeted pull request.

A second step, `if: failure()`, writes `pending` for the same `HEAD_SHA` (same shape test, same
token and context) when the gate step failed, so a crash anywhere in the script is covered too.

Limits: `issue_comment` runs carry no pull request payload, so there is no head SHA from the event and
both mechanisms do nothing for them (a comment cannot retarget a pull request). If the GitHub API is
unavailable, no status can be written by either mechanism, and an old success stays. The early
`pending` can precede a run that later stops for another reason; that is intended: the status stays
pending until a run finishes.

## Auto-merge only into the default branch

By default only the repository's default branch is eligible for auto-merge. A PR whose base branch is
outside the allowed list (the default branch, or `auto_base_branches`) gets the blocker `base branch
<name> is not an auto-merge branch (<allowed>)`: an `auto` lane becomes `review` (in shadow mode the
card says `shadow, would be review`), while a `block` lane stays `block`. Stacked PRs are merged by the owner, and
the bottom of the stack lands in the default branch. This also closes the retarget race for the
common case: a PR that was small against a stacked base is not auto-armed there, so retargeting it
to the default branch cannot consume an old success.

To allow other bases set `auto_base_branches = ["main", "release/1"]` under `[merge]` in
`risk-policy.toml`; only the listed names count (the default branch is not implied). A malformed
list, or an unreadable base or default branch, stops the run with the status pending (the one
exception: a run without a usable event head SHA, that is an `issue_comment` run, or one where the
status write itself fails, cannot write a status before the first pull request read; see the section
on pending before any fallible read). The gate reads
the default branch with one extra `gh api repos/<repo>` call per run.

## Setup per repository

1. Copy `risk_gate.py`, `risk-policy.toml`, `test_risk_gate.py` to
   `.github/risk-gate/`, and `risk-gate.yml` to `.github/workflows/`.
   Run the tests in CI too: `python3 -m unittest discover -s .github/risk-gate`.
2. Create a fine-grained token for this repo only (Contents, Pull requests,
   Commit statuses: read and write) and store it as secret `RISK_GATE_TOKEN`.
   The default `GITHUB_TOKEN` would merge without starting the staging deploy.
   Check the permissions really saved: with read-only access the gate fails at
   its first status write (`gh api statuses failed: ... 403`) and nothing merges.
3. Repo settings: allow auto-merge and the merge method the repo uses (set `[merge] method` to match; a repo that needs merge commits must not squash); delete branches on merge.
4. Branch protection (or a ruleset) on `main`: require a PR, require status
   checks `ci` and `risk-gate`, require branches to be up to date. Do not
   require approvals: you cannot approve your own PR. GitHub Free has no branch
   protection for private repos; use a public repo or a paid plan.
5. Staging deploy runs on every push to `main`, followed by a smoke test.
6. Start with `mode = "shadow"`. After about 10 PRs compare the gate's lanes
   with your own decisions, adjust the rules, then switch to `enforce`.
   In shadow mode `risk-gate` is always green ("shadow: would be ..."). The gate
   uses the policy on `main`, so the PR that switches to enforce is still judged
   in shadow mode; merge it by hand.
7. Label PRs that caused a revert or a red staging `caused-failure`; once a
   month compare failures per lane and tighten rules that let failures through.
8. Fill the payments and personal-data template at the end of
   `risk-policy.toml` (between `# BEGIN TEMPLATE payments-personal-data` and
   `# END TEMPLATE payments-personal-data`) from the threat model and uncomment it.

Agent config under `.opencode/` and `.cursor/`, and `opencode.json`, `opencode.jsonc`,
`.cursorrules` or `.cursorignore` in any directory, block like `.claude/` and `.codex/`. The gate's floor is case-folded for every path:
the larger of the exact and the lowercased rating counts, so `.Claude/x` and `Opencode.JSON`
block too. A case variant never rates lower than the exact name. The tests
`test_shipped_no_rating_list_is_minimal` and
`test_payments_template_is_inert_and_valid_when_uncommented` check the policy as
shipped; a project that customises the policy adapts them. The README test is
skipped when `README.md` is not next to the tests (README is not copied in step 1).

Later, when production CD exists: `live = true`, feature flags off by default,
tested rollback, merge hours. Canary analysis and error-budget freezes only once
there is enough traffic to measure.
