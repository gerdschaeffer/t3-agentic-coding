# Rating audit

The leader relays Sol's verdict and ratings into the PR comment that the gate trusts, under the
owner's identity (an accepted risk, [gate/README.md](../gate/README.md)). The audit makes that
relay checkable after the fact: it compares each rating comment with Sol's own final reply.

## Inputs

1. The rating comment of the PR for the head SHA, as the gate reads it: the newest comment by the
   trusted login (the repo owner, or `rater` in the policy) whose body starts with
   `<!-- risk-rating sha=<head sha> -->`. List with pagination and keep the id, the time and the body
   of each match, then take the newest; save that body to a file:
   `gh api --paginate repos/<o>/<r>/issues/<n>/comments --jq '.[] | select(.user.login == "<rater>") | {id, created_at, body}'`.
   Older comments for the same head SHA are history; the script reads a comment exactly as the gate
   does (last occurrence of a field wins, keys and values trimmed, keys case-insensitive).
2. Sol's raw final reply of the review round that the comment claims to relay: read it with
   `t3_thread_read` from the delegate thread (`childThreadId`, which the leader writes to the ledger
   at every review round) and copy the final reply verbatim to a file. Do not take it from a file
   the leader wrote: the leader that relays the rating could also edit its own copy; the delegate
   thread is Sol's own record.
3. For `security_pass: clean`: the reply of the security round, the same way.

## Run

```
python3 <skill dir>/scripts/rating_audit.py --comment C.txt --reply R.txt [--security-reply S.txt] --sha <head sha>
```

Exit 0: nothing softened. Exit 1: a `FAIL` line names the field (verdict `APPROVE` against Sol's
`CHANGES`, a rating below Sol's, `security_pass: clean` without a clean security reply). A comment
stricter than Sol prints a `note` and passes. Exit 2: the input cannot be audited (no marker, no
`RISK` line, a bad value, a file over 256 KiB): fix the input; never read exit 2 as a pass.

## When and by whom

- **Monthly**, with the retro: the newest rating comment of every PR merged since the last audit
  (for each head SHA that was merged), by a party other than the thread that relayed it: the human,
  or the lead in a fresh thread started for the audit. The `maintainer` agent cannot do it (it has
  no T3 thread access); [maintain checks](../../maintain/references/checks.md) section 5 reports
  `not run: no thread access`. A FAIL is reported
  to the human with the PR; the PR is not reverted by the audit.
- **Before a leader merge in shadow mode**: the leader runs it on the head rating once, as a sanity
  check. This one is by the relaying thread itself and only catches mistakes, not intent.

## What is not checked

- `disputed` and `reasons` of the comment, and whether `n/a` was honest where a security pass was due.
- That the reply is Sol's final round for this head (the auditor picks the thread; check the SHA and
  the round in the ledger), or that a review happened at all.
- A comment that was edited or deleted after posting, and a leader that never posted one (the gate
  then sends the PR to the review lane).
- The security reply for `clean` when the auditor reads it from the leader instead of the thread.
