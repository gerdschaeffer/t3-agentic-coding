# Handover

What exists, how to check it, and what only the human can do. State: see [PLAN.md](../PLAN.md) "State now".

## What exists

| Part | Where | What it does |
|---|---|---|
| Skill `t3-build` | `.agents/skills/t3-build/SKILL.md`, `references/` | The feature and project runs: tier, spec, tests first, implement, review, ship |
| Risk gate | `.agents/skills/t3-build/gate/` | `risk_gate.py`, `risk-policy.toml`, `risk-gate.yml`, its tests and [README](../.agents/skills/t3-build/gate/README.md). Rates a PR, routes it to the auto, review or block lane. `mode = "shadow"` |
| Tier script | `.agents/skills/t3-build/scripts/tier.py` | Reads the policy, prints `human`, `T0`, `T1` or `T2` for a path list |
| Event log, retro report | `scripts/log_event.py`, `scripts/retro_report.py` | One JSON line per stage; monthly calibration report from the log and git |
| CI templates and guards | `.agents/skills/t3-build/ci/`, `scripts/{check_agents,check_trailers,diff_guard}.py` | `ci`, `diff-guard`, `commit-trailers`, `agents-drift` for a product repo ([README](../.agents/skills/t3-build/ci/README.md)) |
| Skills `release`, `milestone-audit`, `maintain` | `.agents/skills/<name>/` | Release with a human gate; milestone security audit with a second key; scheduled checks |
| Agent definitions | `staging/{claude,codex,opencode}/agents/` | 20 files, waiting for the human to install them (WP0) |
| Specs | `docs/specs/001` to `021` | One spec per work package and follow-up, with the review logs and the escalated items |

## How to run each suite

From the repo root. Set `PYTHONDONTWRITEBYTECODE=1`.

| Suite | Command | Time |
|---|---|---|
| Scripts (tier, event log, retro report, CI guards) | `cd .agents/skills/t3-build/scripts && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover` | about 2 minutes |
| Gate | `cd .agents/skills/t3-build/gate && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest` | short |
| CI templates | `cd .agents/skills/t3-build/ci && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_ci_templates` | short |
| Agent files against `models.md` | `python3 .agents/skills/t3-build/scripts/check_agents.py --root staging --models .agents/skills/t3-build/references/models.md` | short |
| Trailers of a branch | `python3 .agents/skills/t3-build/scripts/check_trailers.py --branch agent/<slug> --base origin/main --head HEAD` | short |
| Tier of a change | `python3 .agents/skills/t3-build/scripts/tier.py -- <paths>` | short |

Python 3.11 or newer.

## Do not touch / human-only list

Installed agent files outside the repo; GitHub settings, branch protection, required checks, rulesets, merge queue; tokens and secrets; plan terms for unattended runs; scheduling (WP11); `risk-gate` enforce mode; `sonnet_t1_implementer`; pilot product repo (WP10) and release (WP5) and audit (WP6) 'done when' dry runs; pinning `actions/checkout` to a SHA in a real product repo; decisions escalated in specs 015 to 021 (structural stale-success fix, `pull_request_target` for the guards, the spec-change notice and redundant-parent trailer skip, SIGINT/setsid residuals, API-outage window).

## Where the escalated decisions are written down

| Decision | Spec |
|---|---|
| Structural fix for a stale `success` after a retarget (publish the result per base and head, new status context) | [017](specs/017-fu10-gate-retarget/spec.md), [020](specs/020-fu13-auto-base/spec.md) |
| `pull_request_target` for the three guards; spec-change notice; redundant second parent skips the trailer check; SIGINT and `setsid` residuals | [015](specs/015-wp9-ci/spec.md) (review log at the end) |
| Shared check runs on one head SHA (N2): a repository ruleset with required workflows pinned to a ref, or a gate that checks where each required check run came from | [015](specs/015-wp9-ci/spec.md) section 22, [ci README](../.agents/skills/t3-build/ci/README.md) |
| `issue_comment` runs have no event head SHA, so no early `pending` before the first pull request read; an API outage can prevent every status write | [021](specs/021-fu14-early-pending/spec.md), gate README |
