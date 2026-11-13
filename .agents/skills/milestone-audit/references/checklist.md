# Audit checklist

Run per milestone at one SHA. Each item gets `ok`, a finding id, or `n/a: <why>` in
`checklist-<sha>.md`. Findings carry no exploit steps outside the audit folder.

## Assets and boundaries

- List the assets from the threat model: data classes (Red never leaves, Amber stays with Claude
  and Codex), credentials, accounts, money, availability.
- List every trust boundary the diff touches: user to service, service to service, service to
  store, agent to repo, CI to deploy, dependency to build.
- No threat model: finding (area `process`). Still list assets and boundaries from the specs.

## Per class (read callers and the path to the sink)

- Authn and authz: every new route, job or handler checks identity and permission server side.
- Tenant and data isolation: every query and cache key carries the tenant.
- Injection: SQL, shell, template, path, header, log. Input validated at the boundary.
- Secrets and crypto: none in code, config, logs, errors or fixtures; standard primitives; keys
  rotatable.
- SSRF and redirects: outbound calls limited; redirects checked.
- File handling: upload type, size and path; archive extraction; temp files.
- Sensitive data in logs, errors, metrics and the brief.
- Data-losing or irreversible migrations: backup named, reversal stated.
- Concurrency and shared state around money, quotas, sessions.
- Dependencies: new packages (reason, maintenance, licence, pinned), major bumps, lockfile diff
  matches the manifest, registry source.
- CI and deploy config: secrets absent on agent branches, triggers, token permissions, who can
  deploy, no agent path to production.
- Agent configuration: skills, `AGENTS.md`, settings and risk policy changed only by a human PR.

## Scanners

Four classes, each run with the command the repo already defines; find it in CI workflows, the
Makefile, package scripts or pre-commit config. Record the command name, exit code and hit count.

| Class | Not defined, missing or failing means |
|---|---|
| secret | not clean |
| dependency | not clean |
| static analysis | not clean |
| licence | not clean |

Every hit is a finding. No agent dismisses a hit as a false positive; only the human accepts it,
as with any finding. Never install a scanner, never run an install script, never replace a
scanner with another tool.

## Earlier verdicts

For each T2 change in `<base>..<sha>`: its recorded security-pass verdict line. `unrated` or
`verdict: not found` is a finding. Accepted risks of earlier candidates are `open` until the human
repeats the acceptance for this one.
