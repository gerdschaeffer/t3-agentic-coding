# Ship and the risk gate

Moved from `SKILL.md` section 6. Same for both workflows. Without a gate in the repo: commit, push and PR only on
the user's request, and the user merges.

1. Commit on the feature branch, push, open the PR. Body: spec link, ACs with
   evidence, Sol's actual verdict line from sol-loop, security pass result. A T0 PR
   has none of these: body is the one-line task record and the CI result, and
   step 2 is skipped. The gate merges it without a rating (see
   [risk-tiers.md](risk-tiers.md)); CI and the gate still run.
2. Post the **risk rating** as a PR comment (not for T0), format in
   [gate/README.md](../gate/README.md), for the exact head SHA: Sol's last verdict,
   Sol's three ratings (ask for them in the final review round, scale from the
   README), security pass, disputed ids. Copy Sol's values; never lower or edit
   them. Any new push needs a new review and a new rating. The gate trusts this
   relay (an accepted risk, see the README), so faking or softening a rating is
   the one thing this workflow must never do; keep Sol's raw reply in the record.
   Ratings are append-only: never edit or delete one; to withdraw, post a newer
   rating with `verdict: CHANGES`.
   Write the review's `childThreadId` into the ledger: [rating-audit.md](rating-audit.md) compares
   each rating comment with Sol's reply in that thread, monthly and before a shadow-mode merge.
   Bring the branch up to date before Sol's final round. If `main` moves after
   the rating, update it (`gh api -X PUT repos/<o>/<r>/pulls/<n>/update-branch`;
   older `gh` has no `pr update-branch`). That is a new head commit: it needs a
   narrow Sol re-check and a new rating before it can auto-merge.
3. The gate rates, comments its risk card and routes: auto merges by itself,
   review and block wait for the user. `watch_pull_request` from this thread and
   fix failing CI (counts against the budget).
   In a shadow-mode repo where the leader merges the auto lane, take the lane
   from the dry run of main's gate (gate/README.md), never from the status.
   Log the `ship` event with `pr` (the pull request number) and `lane` (`auto`,
   `review` or `block`, as the gate routed it) once the gate has routed the PR:
   the monthly retro uses them to count merged PRs and reverts per lane
   ([event-log.md](event-log.md)). Log a new `ship` event if the lane changes.
4. The leader never merges, never enables auto-merge itself, and never changes
   gate files, policy or branch protection, except in the setup milestone, which
   always lands in the block lane for the user.
5. Before shipping the next PR: `main`'s latest CI and staging deploy must be
   green. Red: no further PRs into the gate until it is fixed; revert the
   offending merge if one fix attempt fails.
6. Do not split a change only to get a lower lane; the user reviews lanes
   against milestones.

**Gate setup** (setup milestone): copy the files and follow the steps in
[gate/README.md](../gate/README.md); the user does the GitHub settings, token and
branch protection. Run the gate's own tests in CI.
