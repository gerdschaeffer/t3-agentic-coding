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
| 0 | docs, comments, tests only | no security trigger touched | not reachable by users (internal, tests, flag off) |
| 1 | small change, changed lines covered by tests, no interface change | trigger touched only by additive hardening; both security reviews clean | user-visible but behind a flag or progressive rollout, reversible |
| 2 | public interface change, shared module with many callers, large diff, weak coverage, removed tests, dependency or config change | authn/authz, permissions, tenant or customer data, secrets, crypto, input parsing at a trust boundary, new dependency | user-visible without a flag, critical journey (login, payment, export), performance-sensitive path |
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
