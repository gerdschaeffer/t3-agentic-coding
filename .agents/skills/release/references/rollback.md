# Rollback runbook skeleton

`docs-writer` fills this per product from the specs and the diff. The human owns the steps and
runs them. Agents may read it and present it; they never run a rollback. No credentials, tokens
or config values in this file: name where the human finds them.

## Owner and trigger
- Owner: <the human>. Where credentials are kept: <name of the secret manager, no values>.
- Roll back when: any named check from the brief fails inside the watch window, or the human
  decides to.
- Watch window: <minutes>. Default when empty: 30.

## Named checks
| check | pass condition | source |
|---|---|---|

## Smoke test
<commands that need no production credentials, expected output>

## Steps
1. <stop the rollout / pause traffic>
2. <redeploy the previous release tag `<tag>`>
3. <verify with the smoke test>
4. <tell users, if required>

## Data and migrations
- Reversible migrations: <how to reverse, in order>.
- Irreversible: <what cannot be undone>. Recovery: <restore from the named backup, or forward fix only>.
- Backup taken before the deploy: <name, time, who confirmed>.

## After
- Record the rollback decision and reason in `decisions.md`.
- Severe or repeated failure: post-mortem per t3-build (`references/postmortem.md`).
