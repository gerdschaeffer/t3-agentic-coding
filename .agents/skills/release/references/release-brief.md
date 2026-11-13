# Release brief

One page when filled: at most 60 lines and 600 words. Copy verdict lines word for word. No
secrets, no config values, no exploit detail (ids and severity words only). The brief is shown
to the human in the lead's session, never committed or posted. Delete this paragraph in the
filled brief.

## Candidate
Tag `rc/<milestone>-<n>`, SHA `<40 characters>`. Base `<sha>` = last deployed `<tag>` (human confirms; first release: empty tree).
Local tag SHA = `<sha>`: <yes/no>. Remote tag SHA (`git ls-remote`) = `<sha>`: <yes/no>. Required checks (named by the human): `<names>`.

## What changed
<three to six lines from the changelog>

## T2 changes and security verdicts
<id, verdict line, one row each; above 15: count per verdict plus every non-clean row. A change
with no recorded reply: `verdict: not found`. A commit with no tier: `unrated`. Never "clean".>
Full table and raw replies: `<path to verdicts.md>`.

## Milestone audit and second key
`milestone-audit`: <verdict line>. `reviewer-security`: <verdict line>. Open ids: <none or ids>.

## QA result
<verdict line, evidence path, open defects: none or ids>

## Accepted security risks
| milestone/id | area | decision | date | candidate tag |
|---|---|---|---|---|

## Open risks
<id, severity, area per line; or none. No description.>

## Declined findings
<id, severity, area per line; or none. Never a stage 4 security finding. No description.>

## Migrations and irreversible changes
<numbered M1, M2: what, recovery path (restore from named backup, or forward fix only); or none. The approval must name each number.>

## Rollback plan
<runbook path, owner (the human), time to roll back, first step>

## Healthy checks
<named checks, each with pass condition and source (qa or human); watch window>

## Decision asked
Reply `approve release <tag> <sha>` (full SHA). If the migrations section is not "none": `approve release <tag> <sha> irreversible: M1,M2` (every item). Nothing deploys without it. Accepting a security risk is a separate message: `accept risk <milestone>/<id>`.
