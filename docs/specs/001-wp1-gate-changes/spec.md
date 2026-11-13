# 001 WP1: risk gate changes (no-rating list, `.opencode/`, payments template)

Tier T2. Base `9c482775e6ed4eb0511bd311063fe9380dc1b08e`. Files in scope:
`.agents/skills/t3-build/gate/{risk-policy.toml,risk_gate.py,test_risk_gate.py,README.md}`.

**Abstract.** T0 changes (user documentation) get no model review and so no rating. Today the
gate sends every unrated PR to the review lane. This spec adds a separate, minimal `no_rating`
path list: a PR whose every file is on it, rates 0 everywhere and was opened by the trusted
rater lands in the auto lane without a rating. It also adds `.opencode/` to the block rule and
ships an inert, commented policy template for payments and personal-data paths.

The keywords MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119.

## 1. Context

- `decide()` always adds the blocker `no trusted risk rating for the head commit` when
  `rating is None` (`risk_gate.py:112`), so no T0 PR can reach the auto lane.
- The policy's level 0 is too wide to reuse (CONCEPT failure mode 23): `*.md`, `*/docs/*` and the
  test patterns cross directories and cover specs, runbooks, tests and agent definitions in
  Markdown. The exemption therefore MUST be its own list, never a widening of the `docs` or
  `tests` rules.
- `.opencode/` is missing from the "CI, deploy, gate or agent config" rule (CONCEPT 790, 952).
- No rule covers payments or personal data (CONCEPT 752, 952).

## 2. Goals and non-goals

Goals: the three changes above, fail closed on every malformed or ambiguous input, existing API
and the 35 existing tests unchanged.

Non-goals (out of scope for this PR):

- Root `opencode.json` / `opencode.jsonc` and `.cursor/`. They steer agents but are not under a
  blocked folder; today they rate regression 1 (default). **Recommendation:** follow-up PR adds
  `"*/opencode.json", "*/opencode.jsonc", "*/.cursor/*"` to the block rule. (`AGENTS.override.md`
  and `GEMINI.md` are in scope since security round 1, §4.6.)
- Case-insensitive matching of the block rules themselves (`.OpenCode/` on a case-insensitive
  checkout). Pre-existing gap for `.claude/`, `.codex/`, `.agents/` too; see §10 OQ4. This PR only
  makes the exemption safe against it (§4.2 step 3).
- `risk-gate.yml`, `tier.py` (WP2), CI wiring (WP9).
- Recursive `**` patterns in `no_rating`.

## 3. Terminology

- **Floor** of a file list: `rate_files(files, policy)[0]`, the three-dimension score dict.
- **Zero floor**: all three of `regression`, `security`, `user` are 0 (user counts here even when
  `live = false`).
- **Exempt PR**: a PR for which `exempt_from_rating()` returns `True` (§4.2).
- **Trusted logins**: the existing `trusted` list in `main()` (`[policy rater or repo owner]`).

## 4. Specification

### 4.1 Policy schema

A new optional table:

```toml
[no_rating]
paths = ["/CHANGELOG.md", "/docs/user/*.md"]
```

| Input | Meaning |
|---|---|
| `no_rating` key absent | empty list: no PR is exempt (today's behaviour) |
| `no_rating` present but not a table | malformed: `SystemExit` |
| `[no_rating]` without `paths` | empty list |
| `paths` not an array, or any element not a string | malformed: `SystemExit` |
| any element not starting with `/` | malformed: `SystemExit` |
| other keys inside `[no_rating]` | ignored |

Patterns use the existing `matches()`. Anchored patterns are REQUIRED: `PurePosixPath.match`
keeps `*` inside one directory, so `/docs/user/*.md` matches `docs/user/a.md` but not
`docs/user/a/b.md` or `docs/specs/x/spec.md`.

> Rationale for anchoring: an unanchored pattern goes through `fnmatch`, whose `*` crosses `/`.
> `*.md` would cover specs, runbooks and agent definitions that rate 0, exactly the loophole of
> CONCEPT failure mode 23. The zero-floor check (§4.2 step 3) catches only files with a non-zero floor.

`**` is not recursive in `PurePosixPath.match` (Python 3.12 and 3.13 treat it like `*`, checked:
`/docs/user/**/*.md` matches `docs/user/a/b.md` but not `docs/user/x/y/z.md`). It fails narrow,
so it is not rejected, but README MUST say not to use it. **Nested docs** are not exempt by the
shipped list; a project that wants them adds one pattern per depth (`/docs/user/*/*.md`).

**Shipped default** (MUST be exactly this):

```toml
[no_rating]
# T0: files that may merge without a risk rating. User documentation only: never tests, specs,
# runbooks, agent definitions or anything that runs or steers. Patterns MUST start with "/":
# anchored, * stays in one directory, ** is not recursive. A file is exempt only if it also
# rates 0 on every dimension under the rules below, and only if the rater opened the PR.
paths = ["/CHANGELOG.md", "/docs/user/*.md"]
```

Justification: `/CHANGELOG.md` and `/docs/user/*.md` are user documentation by convention and
rate 0 under the current rules. `/README.md` is deliberately left out: it is the place users
copy install commands from and agents read as project context, and adding it flips the existing
test `test_rater_from_policy_counts_after_a_move_to_an_org` (README.md with an untrusted rating
expects `review`), which this WP must not change. See OQ2. `LICENSE` and `CONTRIBUTING.md` are
left out (legal text; contributor and agent instructions).

### 4.2 `exempt_from_rating(files, policy) -> bool`

New function in `risk_gate.py`. Same `files` dicts as `rate_files`. Behaviour, in order:

1. Validate `policy["no_rating"]` per §4.1. Malformed: `raise SystemExit("policy no_rating is
   malformed; risk-gate stays pending")`. Validation MUST run on every call, including for an
   empty `files` list.
2. Empty `files`: return `False` (fail closed).
3. PR floor not zero (`rate_files(files, policy)[0]`): return `False`. This covers
   `small_diff_lines` and `MAX_FILES`. The same must hold for the **case-folded floor**
   `rate_files(files, policy, fold=True)[0]`, where path and rule patterns are lowercased before
   matching (new keyword argument `fold=False`; default behaviour unchanged). Reason: on a
   case-insensitive checkout `docs/user/claude.md` is the nested `CLAUDE.md`, and the block rules
   match case-sensitively. Only the exemption folds; the lanes and ratings do not change.
4. For every file `f`, return `False` unless every non-empty name in
   `(f["path"], f.get("previous"))` is **canonical** (below) and matches at least one `no_rating`
   pattern (case-sensitive), and unless the file reports changed lines: `f["additions"] +
   f["deletions"] > 0`. No carve-out for renames: GitHub reports binary files, mode-only changes
   and a renamed binary with new content as 0 and 0, which would slip past the size rule, so a
   0/0 rename needs a rating (security round 2). (No per-file floor check: step 3 already makes every file's floor zero, because the
   PR floor is the maximum over its files; a second check would be dead code.)
5. Return `True`.

A name is **canonical** when all hold: non-empty; does not start with `/`;
`str(PurePosixPath(name)) == name` (rejects `./x`, `a//b`, `a/./b`, trailing `/`); no `..`
segment; no `\`; `name.isascii()` (security round 2: Unicode names such as `AGENTſ.md`, U+017F,
fold to `AGENTS.md` on case-folding filesystems but not under `str.lower`; a non-ASCII doc name
needs a rating); `name.isprintable()` (rejects newline, control and Unicode format characters
such as U+202E). Matching is case-sensitive and on exact code points (no normalisation).

The function is pure apart from the `SystemExit`; it performs no I/O.

### 4.3 `decide()` change

```python
NO_RATING_REASON = "no rating needed: only user-documentation files"

def decide(floor, floor_reasons, rating, policy, draft=False, exempt=False):
```

Only change: when `rating is None`, the blocker `no trusted risk rating for the head commit` is
replaced by appending `NO_RATING_REASON` to `reasons` **iff** `exempt` is true **and** `floor` is
zero. Otherwise (including `exempt=True` with a non-zero floor) the blocker is added as today.
When `rating` is not `None`, `exempt` is ignored and no reason line is added: the rating is
evaluated exactly as today (verdict, disputed, security pass). The `draft` blocker is unchanged
in all cases.

> Rationale for re-checking the floor inside `decide`: one condition makes a wrong `exempt=True`
> from any caller harmless.

### 4.4 `main()` wiring

After `files`, `trusted` and `comments` are built, before `decide()`:

```python
rating = parse_rating(comments, sha, trusted)
exempt = exempt_from_rating(files, policy)          # always: validates the policy
marker = f"{RATING_MARK}{sha} -->"
if any(c["login"] in trusted and c["body"].startswith(marker) for c in comments):
    exempt = False        # a rating comment, even a malformed one, means a rating is required
if exempt and rating is None:
    author = json.loads(gh("pr", "view", pr, "--repo", repo, "--json", "author"))
    exempt = ((author.get("author") or {}).get("login")) in trusted
lane, scores, reasons, blockers = decide(
    *rate_files(files, policy), rating, policy, pr_info["isDraft"], exempt=exempt
)
```

- The author lookup MUST happen only when `exempt and rating is None`. (Existing `Main` tests'
  fake `gh` answers `--json author` with `""`; none of them reaches this branch with the shipped
  list.) A failed lookup raises `SystemExit` via `gh(check=True)`: status stays pending.
- The first `pr view` call (`headRefOid,isDraft`) MUST stay byte-identical; the existing fake
  matches on that exact argument.
- Dry run uses the same code; the author lookup is read-only.
- The marker test uses the same start-of-body rule as `parse_rating`. A malformed rating by a
  trusted login (so `parse_rating` returns `None`) must not turn into an exemption: the owner may
  have meant a veto. Marker comments by other logins, or for another head SHA, do not count.

> Rationale for the author check (writer's addition, OQ1): `pull_request_target` runs for fork
> PRs. Without it, anyone could open a PR that edits only `/docs/user/*.md` (phishing link,
> malicious install command) and, in enforce mode, it would auto-merge with no owner action at
> all. Today every auto-merge needs a comment by the trusted login; requiring that login as PR
> author keeps the same trust anchor.

### 4.5 Output

No change to `render_card`, the status summary or the dry-run format. The reason line flows
through the existing `reasons` list. Worked example (illustrative; the strings are normative):
PR by the owner changing `CHANGELOG.md` (+5) and `docs/user/install.md` (+12), no rating.

```
$ RISK_GATE_DRY_RUN=1 ... python3 risk_gate.py
auto R0 S0 U0 head aaaaaaaaaaaa
  no rating needed: only user-documentation files
```

Card (enforce): `### Risk gate: **auto**`, then under `**Why**` the line
`- no rating needed: only user-documentation files`, and no "Not eligible" section. Status
description: `auto R0 S0 U0`.

### 4.6 `.opencode/` rule

Add `"*/.opencode/*"` to `paths` of the rule `reason = "CI, deploy, gate or agent config"`
(regression 3), next to `"*/.codex/*"`. Matches `.opencode/...` at the root and in any directory.
Also add `"*/AGENTS.override.md"` and `"*/GEMINI.md"` (instruction files that Codex and Gemini CLI
load from any directory; security round 1: `docs/user/AGENTS.override.md` was exempt), and, from
security round 2, the other default instruction filenames of agent tools that load them from
subdirectories: `"*/AGENT.md"`, `"*/WARP.md"`, `"*/CRUSH.md"`, `"*/CRUSH.local.md"`,
`"*/QWEN.md"`. The list is a deny list and cannot be complete (§8 residual risk).

### 4.7 Payments and personal-data template

At the end of `risk-policy.toml`, comment lines only. Explanation goes above the begin marker.
Between the markers every line is `# ` followed by one TOML line, or exactly `#`:

```toml
# Payments and personal data: fill the paths from the project's threat model, then uncomment
# the block (remove the leading "# " from each line). Docs on these paths lose the T0 exemption.
# BEGIN TEMPLATE payments-personal-data
# [[rule]]
# reason = "payments or personal data"
# paths = ["*/payment*", "*/billing/*", "*/checkout/*", "*/pii/*", "*/personal_data/*", "*/gdpr/*"]
# regression = 1               # a rule hit replaces [default]; keep ordinary code's floor
# security = 2
# user = 2
# END TEMPLATE payments-personal-data
```

Uncommenting rule (used by the test): for each line strictly between the markers, `#` becomes
an empty line and `# X` becomes `X`. The result MUST parse with `tomllib.loads` to exactly one
`rule` table.

## 5. Acceptance criteria

All tests live in `test_risk_gate.py`. Unit tests compute `exempt` with
`g.exempt_from_rating(files, policy)` and pass it to `g.decide(..., exempt=exempt)`. `Main` tests
need a fake `gh` that also answers `pr view --json author` (new helper or subclass; the existing
`run_main` MUST NOT be edited).

| AC | Requirement | Test |
|---|---|---|
| AC1 | PR of only `no_rating` files, no rating, author trusted: lane `auto`, no "no trusted risk rating" blocker, `NO_RATING_REASON` in reasons. Enforce: last status `success` with `auto`, `--auto` merge on the head SHA. (PLAN check 1) | `test_no_rating_files_without_rating_are_auto`, `test_main_no_rating_pr_auto_merges` |
| AC2 | No rating and any of `docs/specs/x/spec.md`, `docs/runbooks/x.md`, `tests/test_x.py`, `README.md`, `docs/user/sub/x.md`, `.claude/agents/x.md`: not exempt, lane not `auto` (`review`, or `block` for agent config). (PLAN check 2) | `test_specs_runbooks_tests_and_agent_files_need_a_rating` |
| AC3 | Every file must qualify: `CHANGELOG.md` + `app/views.py` not exempt; `[]` not exempt. | `test_mixed_or_empty_pr_needs_a_rating` |
| AC4 | Every file's floor must be zero (enforced by the PR floor, §4.2 step 3): with `paths = ["/*.md", "/docs/user/*.md"]`, `AGENTS.md`, `docs/user/session-timeout.md` (security 2), `docs/user/CLAUDE.md` (block) and a `CHANGELOG.md` with `link` set are each not exempt. A file whose only non-zero score is `user` (test policy: a rule gives an allowed docs path `user = 1`, regression and security 0, `live = false`) is not exempt, and `decide(..., exempt=True)` on it keeps the missing-rating blocker. | `test_exempt_file_needs_floor_zero_on_every_dimension` |
| AC5 | PR floor must be zero: two `docs/user/*.md` files each at most `small_diff_lines` but together above it are not exempt (`review`); `MAX_FILES` `docs/user/*.md` files are `block`. | `test_pr_level_floor_cancels_exemption` |
| AC6 | Renames need both names on the list: `src/x.py` to `CHANGELOG.md` and `CHANGELOG.md` to `src/x.py` not exempt; `docs/user/a.md` to `CHANGELOG.md` exempt. Both names rate 0 but only one is on the list (so the list check, not the floor, must reject): `docs/specs/x/spec.md` to `CHANGELOG.md` and `CHANGELOG.md` to `docs/specs/x/spec.md` are not exempt. | `test_rename_needs_both_names_on_the_list` |
| AC7 | Non-canonical or lookalike names are never exempt: `./CHANGELOG.md`, `CHANGELOG.md/`, `docs//user/a.md`, `docs/user/a\nb.md`, `docs/user/‮x.md`, `changelog.md`, `CHANGELOG.md` with Cyrillic `С`. | `test_non_canonical_or_lookalike_paths_are_not_exempt` |
| AC8 | A rating for the head commit is evaluated as today on an exempt PR: `CHANGES` and `disputed` give `review`; `APPROVE` gives `auto` without `NO_RATING_REASON`. `draft=True` gives `review` with blocker `draft PR`, with and without a rating. | `test_rating_and_draft_still_count_when_exempt` |
| AC9 | `decide` default `exempt=False` keeps today's blocker; `exempt=True` with a non-zero floor keeps the blocker; `rate_files` still returns a 2-tuple. | `test_decide_exempt_flag_needs_zero_floor` |
| AC10 | Exempt PR, no rating, PR author not trusted (or `author` null): lane `review` with the "no trusted risk rating" blocker, no `--auto` merge. | `test_main_exemption_needs_a_trusted_author` |
| AC11 | Malformed `no_rating` (`["/CHANGELOG.md"]`, `"x"`, `{paths = "x"}`, `{paths = [1]}`, `{paths = ["CHANGELOG.md"]}`) raises `SystemExit` from `exempt_from_rating`, also for `[]`. In `main()` only the initial pending status is written, and no comment, no `pr merge` call. Absent `no_rating` and `[no_rating]` without `paths` mean empty list. | `test_malformed_no_rating_fails_closed`, `test_main_malformed_no_rating_stays_pending` |
| AC12 | Dry run of an exempt PR prints `auto R0 S0 U0 head <12>` then `  no rating needed: only user-documentation files`, and writes nothing; the card contains `- no rating needed: only user-documentation files`. | `test_dry_run_and_card_show_the_exemption` |
| AC13 | Shipped list is exactly `["/CHANGELOG.md", "/docs/user/*.md"]`; `CHANGELOG.md` and `docs/user/a.md` rate zero. | `test_shipped_no_rating_list_is_minimal` |
| AC14 | `.opencode/agent/lead.md`, `.opencode/opencode.json`, `pkg/.opencode/x.md`, `AGENTS.override.md`, `docs/user/GEMINI.md`, `a/b/AGENTS.override.md` are `block`, with an `APPROVE` rating and without. (PLAN check 3) | `test_opencode_config_blocks`, `test_agent_instruction_files_block` |
| AC15 | Template is comments only (no rule in `POLICY` has reason `payments or personal data`); uncommented per §4.7 it parses to one rule, and with it `app/payments/charge.py`, `src/billing/invoice.py`, `app/pii/export.py` rate security at least 2. | `test_payments_template_is_inert_and_valid_when_uncommented` |
| AC16 | Gate README documents the list, the `.opencode/` rule and the template (§7). | `test_readme_documents_no_rating_opencode_and_template` (asserts `[no_rating]`, `.opencode/`, `BEGIN TEMPLATE payments-personal-data` appear) |
| AC17 | The 35 existing tests are unchanged and pass: `git diff 9c48277 -- test_risk_gate.py` shows only added lines. | full suite: `cd .agents/skills/t3-build/gate && python3 -m unittest discover -s .` |
| AC18 | Case-folded floor: `docs/user/agents.md`, `docs/user/claude.md`, `docs/user/Claude.md`, `docs/user/agents.override.md`, `docs/user/gemini.md`, `docs/user/AGENTS.override.md`, `docs/user/GEMINI.md`, `.OpenCode/x.md` are not exempt (even with a project list that matches them). `rate_files(files, policy)` with the default `fold=False` returns exactly what it returned before (same scores and reasons for the existing fixtures). `CHANGELOG.md` and `docs/user/a.md` stay exempt. | `test_exemption_survives_case_variants_of_instruction_files` |
| AC19 | In `main()`, an exempt PR with a rating-marker comment for the head SHA by a trusted login is not exempt, whatever its body: well-formed APPROVE (rating path as today), CHANGES, malformed (`regression: 4`, `regression: n/a`, missing `user:`): lane `review` or `auto` only through a valid APPROVE rating, never through the exemption; no author lookup is needed. A marker comment by another login, or for another SHA, does not disable the exemption. | `test_main_malformed_rating_comment_disables_the_exemption` |
| AC20 | A file with 0 additions, 0 deletions and no `previous` (binary blob, mode change, empty file) on the list is not exempt, alone or among others. A 0/0 rename (with `previous`) is not exempt either, even with both names on the list. 2999 such files are not exempt. A rename that reports changed lines with both names on the list is exempt. | `test_zero_change_files_are_not_exempt` |

## 6. Failure cases

| Case | Observable result |
|---|---|
| `no_rating` malformed | `SystemExit` before `decide`; status stays `pending`; no card, no merge call; dry run prints the error and no lane |
| Author lookup fails (API error) | `SystemExit` from `gh`; status stays `pending` |
| Author is a deleted user (`author: null`) | not exempt; review lane |
| Tree truncated | every file gets `link`; floor regression 2; not exempt |
| Rating present but malformed | `parse_rating` returns `None`; not exempt (§4.4 marker test): review lane |
| Rating for an older commit | ignored as today; exemption may apply |

## 7. README changes (same PR)

- **Lane table / "Auto-merge conditions"**: add the exception "or every file is on the
  `no_rating` list, rates 0 and the PR was opened by the rater: no rating needed".
- **New section "No-rating list (T0)"** after "How the rating gets in": what may go on it (user
  documentation only), the anchored-pattern rule, no `**`, nested docs need one pattern per
  depth, the all-files and zero-floor rules, the author rule, the reason line in card and dry run.
- **"Shadow-mode repos"**: the dry run prints the reason line when the exemption was used.
- **"Accepted risk"**: one sentence that the exemption trusts the rater login as PR author, the
  same identity the relay risk already covers.
- **"Setup per repository"**: new step "fill the payments and personal-data template from the
  threat model and uncomment it", and note that agent config under `.opencode/` blocks like
  `.claude/` and `.codex/`.
  Also one sentence: the tests `test_shipped_no_rating_list_is_minimal` and
  `test_payments_template_is_inert_and_valid_when_uncommented` check the policy as shipped; a
  project that customises the policy adapts them. The README test is skipped when `README.md` is
  not next to the tests (README is not copied in setup step 1).

## 8. Security considerations

**Asset.** The lane decision, above all a wrong `auto`, which in enforce mode merges without the
owner.

**Trust boundaries.** PR file paths, renames, line counts and the author come from the GitHub API
and are attacker-controlled for fork PRs. The policy and the gate code come from the base branch
(`pull_request_target`); a PR that edits either is rated 3 by the existing rules. Comments other
than the trusted login's are ignored.

| Abuse case | Mitigation |
|---|---|
| Fork PR edits only user docs to plant a phishing link or install command | author must be a trusted login (§4.4, AC10) |
| Broad pattern like `*.md` exempts specs, runbooks, agent Markdown | patterns must be anchored (§4.1, AC11); zero floor (AC4) |
| Code file looks like docs by case (`Changelog.MD`) | case-sensitive exact match: not exempt (AC7) |
| Trailing slash, `./`, `//`, `..` | canonical check (AC7); Git also rejects most of these |
| Rename exempt to code, or code to exempt | both names must match and rate 0 (AC6) |
| Symlink or submodule named `CHANGELOG.md` | `link` gives floor 2 (AC4); truncated tree flags all files |
| Docs file matching both `no_rating` and a block or security rule (`docs/user/CLAUDE.md`, `docs/user/session-timeout.md`) | zero floor required (AC4); block rule still blocks |
| Huge diff or 3000+ files of docs | PR floor zero required (AC5) |
| Unicode lookalike (`СHANGELOG.md`, U+202E) | exact code-point match fails; `isprintable` rejects format characters (AC7) |
| Malformed policy creates an accidental exemption | `SystemExit`, stays pending (AC11) |
| Caller passes `exempt=True` wrongly | `decide` re-checks the zero floor (AC9) |

| Instruction file inside the exempt folder (`docs/user/AGENTS.override.md`, `GEMINI.md`, `claude.md` on a case-insensitive checkout) | block rule additions (§4.6); case-folded floor (§4.2 step 3, AC18) |
| Malformed rating by the trusted login ignored, exemption applies | any trusted marker comment for the head SHA disables the exemption (§4.4, AC19) |
| Binary or mode-only change, or a renamed binary, reported as 0/0 lines in an exempt path | a file with 0/0 is not exempt (§4.2 step 4, AC20) |
| Non-ASCII name that folds to an instruction file (`AGENTſ.md`, U+017F) | `canonical` requires `isascii` (§4.2, AC7, AC18) |

**Residual risk (open to the human, accepted only when the human merges knowing it).**

1. A PR authored by the rater identity that edits only exempt docs merges with no model review.
   That is the T0 design and lies inside the accepted relay risk (README). Content of user docs
   (wrong but harmless text) is not checked.
2. (S3, low) The check binds to who opened the PR, not who wrote the head commit: a collaborator
   with push rights to a PR branch the rater opened can add a docs-only commit that merges unseen.
   README: rater PR branches must not be shared.
3. The instruction-file list is a deny list. Names that load as agent instructions and are not
   blocked (known: `CONTEXT.md`, `IFLOW.md`, `CODEBUDDY.md`; any `*.md` that a tool config, such as
   `project_doc_fallback_filenames`, `context.fileName`, `opencode.json` `instructions` or an
   `@import`, names) can sit in an exempt folder and merge without review. Keeping the `no_rating`
   list narrow and never naming exempt files in an agent config limits it.
4. A veto comment by the rater that misses the exact marker line (full head SHA, character 0) does
   not stop an exempt PR, and the workflow does not re-run on it: convert the PR to draft or close it.
5. Case folding lowercases rule patterns: a character range that spans cases (`[A-_]`) is not
   folded correctly (project-authored patterns only).
6. Newline-bearing filenames in non-exempt reasons are printed into the dry run as today
   (pre-existing, OQ5).

## 9. Alternatives considered

- **Widen the `docs` rule / reuse level 0**: rejected, CONCEPT failure mode 23.
- **Top-level `no_rating = [...]` array**: TOML forces it above the first table; appended at the
  end it silently becomes a key of the last `[[rule]]`. A table can go anywhere.
- **Per-file floor check on top of the PR floor**: rejected as dead code; the PR floor is the
  maximum of the file floors.
- **Compute the exemption inside `decide`**: `decide` has no file list; would change its
  positional signature.
- **Require `isCrossRepository == false` instead of the author**: lets any collaborator with write
  access skip the owner; the author check reuses the existing trust anchor.
- **Add `author` to the first `pr view` call**: breaks the existing `Main` test fake.

## 10. Open questions

- **OQ1 (accepted by the lead):** the author check (§4.4, AC10) and the anchored-only rule
  (§4.1, AC11 last case) stay. Both close abuse cases in §8.
- **OQ2 (decided):** `/README.md` stays off the default list; adding it would need a change to
  `test_rater_from_policy_counts_after_a_move_to_an_org`. Projects opt in.
- **OQ3:** follow-up PR for root `opencode.json`/`opencode.jsonc` and `.cursor/` in the block
  rule (`AGENTS.override.md`, `GEMINI.md` moved into this PR). Recommendation: yes, next gate PR.
- **OQ4:** case-insensitive matching of the block rules for every path. Recommendation: separate
  PR, all rules at once (the exemption is already safe, §4.2 step 3).
- **OQ5:** sanitise control characters in printed reasons (dry run is read by the leader).
  Pre-existing; separate PR.

## 11. Effort and roles

test-author-t2 (high) writes the 17 tests first; implementer (high) about 40 lines of code, 10
of policy, 30 of README; security pass by reviewer-security and security-engineer.
