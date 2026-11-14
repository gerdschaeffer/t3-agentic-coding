# Findings ledger and report

The ledger lives only in `<state>/<repo>/audit-<milestone>/ledger.md`, never in the repo. Raw
replies stay in `replies/<sha>/`. The report is the only part that reaches the repo.

## Ledger columns

| id | severity | area | status | source | first seen | fix ref |
|---|---|---|---|---|---|---|

- `id`: `S<n>`, unique per milestone, never reused. First audit ids are kept. A second-key-only
  finding takes the next free number; its reply id goes in `source`.
- `severity`: `critical`, `high`, `medium` or `low`. It only rises.
- `status`: `open`, `fixed` or `accepted`. There is no `declined` for an audit finding.
  `fixed` needs a fix ref and a later round on a candidate that contains the fix. `accepted`
  needs the human's own message `accept risk <milestone>/<id>` in this session for this
  candidate; on a new candidate or session it is `open` again.
- `source`: key (`security-engineer` or `reviewer-security`) and its reply id; both when merged.
- `first seen`: candidate tag. `fix ref`: PR number and merged SHA.
- No description column. Exploit text stays in `replies/<sha>/`.

## Report template

`docs/security/audit-<milestone>.md`, written by the lead only when no row is `open`.

```
# Audit <milestone>

## Candidate
Tag `<tag>`, SHA `<40 characters>`, base `<sha>`, date <YYYY-MM-DD>, mode <release|standalone>.

## Verdicts
`security-engineer`: <verdict line copied verbatim>
`reviewer-security`: <verdict line copied verbatim>

## Scanners
| class | command name | ran | hits |
|---|---|---|---|

## Findings
| id | severity | area | status | fix ref |
|---|---|---|---|---|

## Human test
HUMAN-TEST: <recommended areas | not indicated>. A model audit is not a penetration test.
```

- Accepted rows: id, severity, area only. Fixed rows may add one weakness class (for example
  "missing tenant check"), never steps, payloads, endpoints or paths.
- No secret, config value or raw scanner output.
- The accepted-risk summary also goes to `docs/security/accepted-risks.md` as the `release`
  skill defines it (`<milestone>/<id>`, area, decision, date, candidate tag).
