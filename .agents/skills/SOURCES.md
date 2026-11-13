# Skill sources

Copied 2026-11-13. Third-party skills are a supply chain: read before updating, pin to a commit.

| Skill | Source | Pin |
|---|---|---|
| t3-build, sol-loop, grill-me, technical-spec-writing, file-pr, babysit-pr, leaving-pr-comments, takeover, modernize, council | `~/.agents/skills` (dotfiles repo) | dotfiles `fc2f060805228a31d2819a0a1befb1b333384446` |
| t3-build `references/{quota-recovery,locks,long-checks}.md`, `scripts/{agent-lock,detach}.sh` | `~/.agents/skills/t3-agentic-coding` (the only parts of it that stay in use, CONCEPT 7) | same |
| ponytail, ponytail-review | plugin `ponytail@ponytail` 5.3.2 | `09c434ab73ca43761c5d8b0c5d341926c2fba710` |
| security-review | built into Claude Code, no file | n/a |

Not copied on purpose: `t3-agentic-coding` (superseded, leader on a third party), `full-send`/`full-stop` (let the agent merge), `t3-coding-ninox` (Claude-only variant, copy it if code may not leave Anthropic).

Still to write (PLAN.md): `release`, `maintain`, `milestone-audit`, and the t3-build changes.
