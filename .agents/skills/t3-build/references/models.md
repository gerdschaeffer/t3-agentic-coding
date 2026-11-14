# Models and efforts

The only place that names models (CONCEPT.md section 3). Agent files in `.claude/agents/`, `.codex/agents/` and `.opencode/agents/` mirror it; `scripts/check_agents.py` fails on drift. IDs below are from the T3 catalog on 2026-11-13: resolve them again with `orchestrator_capabilities` and never substitute a missing model, block the stage.

| Agent file | Role | Provider | Model | Effort |
|---|---|---|---|---|
| lead | Lead | Claude | claude-sonnet-5-5 | high |
| architect | Architect | Claude | claude-fable-5-2 | high |
| spec-writer | Spec writer T1 | Claude | claude-sonnet-5-5 | medium |
| spec-writer-t2 | Spec writer T2 | Claude | claude-opus-5-5 | high |
| spec-reviewer | Spec reviewer, unclear T1 design | Claude | claude-fable-5-2 | medium |
| reviewer | Spec reviewer T2, code reviewer, risk rater | Codex | gpt-6.1-sol | medium |
| reviewer-security | Security pass, milestone second key | Codex | gpt-6.1-sol | high |
| test-author | Test author T1 | Claude | claude-sonnet-5-5 | medium |
| test-author-t2 | Test author T2 | Claude | claude-opus-5-5 | high |
| implementer | Implementer T1, T2 | Claude | claude-opus-5-5 | medium (high: cross-cutting, security) |
| implementer-t0 | Implementer T0 | Claude | claude-haiku-5-5 | medium |
| security-engineer | Threat model, security review, audit | Claude | claude-fable-5-2 | high |
| qa | QA, end-to-end | Claude | claude-sonnet-5-5 | medium |
| code-scout, web-scout | Scouts | Claude | claude-haiku-5-5 | low |
| docs-writer | Docs | Claude | claude-haiku-5-5 | low |
| consultant | Consultant | Claude | claude-opus-5-5 | high |
| maintainer | Maintainer | Claude | claude-haiku-5-5 (Sonnet on escalation) | low |
| third-voice | Third voice | OpenRouter | google/gemini-4-hydrogen | high |
| bulk-worker | Bulk worker | OpenRouter | deepseek/deepseek-v4.1-flash-1017 | low |

Flags (the human flips them, via a merged change):

- `sonnet_t1_implementer`: **off**. Turn on when the replay test passes (CONCEPT 6.12, rule 5).
- Never pass `max`, `xhigh`, `ultra` or fast modes unless the human asks.
- Record the effort the runtime confirms. If it does not confirm one, write `default`.

## Effort and skills

Moved from `SKILL.md` section 1.

**Effort: the model's default unless `models.md` says otherwise.** The defaults are
tuned by the vendors and more effort costs budget without reliably better results.
Sol's own default is low, so medium is already a step up. Raise it one step only
when the work is truly hard (security, data loss, concurrency, a finding Sol and
the leader dispute) and say why in the ledger. Never pass `max`, `xhigh`,
`ultracode`, `ultrathink`, `ultra` or `fastMode: true` unless the user asks. Record
the effort the runtime confirms in the ledger; after 5 to 10 PRs compare: did medium
miss something that a higher setting found later? If not, medium stays.

Skills: read when the stage starts and pass absolute paths in briefs (children get
no history): `grill-me`, `technical-spec-writing`, `sol-loop`, `ponytail:ponytail`,
`ponytail:ponytail-review` (plugin; path = `installPath` of `ponytail@ponytail` in
`~/.claude/plugins/installed_plugins.json` + `/skills/<name>/SKILL.md`), and
[gate/README.md](../gate/README.md) for the risk scale. `security-review` is built into
Claude Code and has no file: tell the Claude delegate to invoke the
`security-review` skill with its Skill tool. Anything missing: stop that stage as
blocked and report it.
