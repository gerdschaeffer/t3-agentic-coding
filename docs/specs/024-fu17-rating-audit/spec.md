# 024 FU17: rating audit (leader's relay of Sol's ratings)

Process depth T1 (a small read-only script and a procedure; `tier.py` says `human` for `.agents/`; the
owner permitted the lead to merge this run). Branch `agent/rating-audit`, from `main` `f5859e5`.
Source: the Opus 5.5 advisory on automation risk (risk 2): the leader posts the rating comment under
the owner's identity, so the gate cannot tell an honest relay of Sol's verdict from a softened one
(accepted risk in `gate/README.md`). This audit makes the relay checkable after the fact. Lead writes
this spec. MUST as in RFC 2119.

## Change

1. **`scripts/rating_audit.py`** (new). Usage: `rating_audit.py --comment FILE --reply FILE
   [--security-reply FILE] [--sha SHA]`. Reads three small text files and writes nothing; no network,
   no subprocess, no T3 calls.
   - Comment (the PR's rating comment body): first line exactly `<!-- risk-rating sha=<40 hex> -->`,
     then `verdict:`, `regression:`, `security:`, `user:`, `security_pass:` lines (format of
     `gate/README.md`). With `--sha`, the marker SHA must equal it.
   - Reply (Sol's raw final reply of the code review): the last line that starts with `VERDICT:` gives
     `APPROVE` or `CHANGES`; the last line that starts with `RISK ` gives `regression=N security=N user=N`.
   - Security reply (optional): the last line starting with `SECURITY:` or `VERDICT:` gives `CLEAN` or
     `FINDINGS`.
   - Findings, each a line `FAIL <field>: comment <x>, Sol <y>`: the comment's verdict is `APPROVE`
     and Sol's is `CHANGES`; a rating in the comment is lower than Sol's (regression, security, user);
     `security_pass: clean` while `--security-reply` is absent (`unverified`) or not `CLEAN`.
   - A comment stricter than Sol is allowed and printed as `note <field>: comment <x>, Sol <y>`.
   - Exit 0: no FAIL. Exit 1: at least one FAIL. Exit 2: bad arguments, an unreadable or oversized file
     (over 256 KiB), a comment without the marker line or with a bad value, a reply with no `VERDICT:`
     or no `RISK` line (it cannot be audited): message on stderr, stdout empty. Every printed value
     is one of the fixed tokens or a digit; file text is never echoed.
2. **`references/rating-audit.md`** (new): who audits, when, and how. The reply is read from the
   delegate thread of the review round (`t3_thread_read` of the `childThreadId` kept in the ledger),
   not from a file the leader wrote, because the leader that relays the rating could also edit its own
   copy; the thread is Sol's own record. The audit runs by a party other than the relaying thread
   where possible (the human, or the `maintain` run) and covers: every rating comment of a merged PR
   (monthly, with the retro) and the head rating before a leader merge in shadow mode. What the
   script does not check (`disputed`, `reasons`, an edited or deleted comment, a reply that is not the
   final round, a leader that never posts one) is listed.
3. **`references/ship.md`** step 2 gets one sentence pointing to the audit and to keeping
   `childThreadId` in the ledger.

## Files

| File | Author |
|---|---|
| `scripts/test_rating_audit.py` | test-author (first, red) |
| `scripts/rating_audit.py` | implementer |
| `references/rating-audit.md`, `references/ship.md` | lead |

## Acceptance criteria

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | An honest relay (comment equals Sol's verdict and ratings, `security_pass: n/a`) exits 0 with no FAIL. | test |
| AC2 | Each softening exits 1 and prints its FAIL line: verdict APPROVE vs CHANGES; each of regression, security, user lower than Sol's (all three tested separately). A stricter comment (higher rating, CHANGES vs APPROVE) exits 0 with a `note` line. | tests |
| AC3 | `security_pass: clean` with no `--security-reply`, with `FINDINGS`, or with `CLEAN`: FAIL `unverified`, FAIL, pass. `security_pass: n/a` needs no security reply. | tests |
| AC4 | The last matching line wins in the reply (a round-1 `VERDICT: CHANGES` followed by a final `VERDICT: APPROVE`; two RISK lines); a `RISK` or `VERDICT:` text inside a longer line or an indented one does not count. CRLF input works. | tests |
| AC5 | Exit 2 with empty stdout for: a missing file, a file over 256 KiB, a comment whose first line is not the marker, a marker SHA that is not 40 lower-case hex or differs from `--sha`, a missing or out-of-range value (`regression: 4`), a reply with no `VERDICT:` or no `RISK`. | tests |
| AC6 | The script never prints file text beyond the fixed tokens (a reply with control characters and a long garbage line prints none of it). | test |
| AC7 | Docs: `references/rating-audit.md` exists and names the thread-read source, the cadence, the two auditors and the list of what is not checked; `ship.md` links to it; links resolve. | text tests, link check |

## Security

Inputs are the owner-authored comment and a model reply, both treated as data: fixed regexes only, size
bound, no execution, no network, nothing echoed. The audit does not change the gate. Residual: the
audit detects softening only if the auditor takes the reply from the thread, not from the leader.

## Non-goals

Changing the gate or the rating format; verifying `disputed` or `reasons`; reading T3 threads from a
script; automating the monthly run.
