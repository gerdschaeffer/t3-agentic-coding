# Commit trailers: agents and efforts

Every commit that agents worked on names each agent, its model and its effort. One trailer per agent, in the footer, before the harness attribution line.

```
Agent: lead / claude-sonnet-5-5 / high
Agent: spec-writer / claude-sonnet-5-5 / medium
Agent: test-author / claude-sonnet-5-5 / medium
Agent: implementer / claude-opus-5-5 / medium
Agent: reviewer / gpt-6.1-sol / medium (2 rounds, APPROVE)
Tier: T1
```

Rules:

- Role = agent file name. Model and effort = what the runtime confirmed. Unconfirmed effort: `default`.
- List every agent that contributed to the change in this commit, including the lead. A reviewer counts: add rounds and the last verdict in the parentheses.
- Retries and escalations: list the agent once per model used.
- `Tier:` is T0, T1 or T2.
- A human-only commit needs no trailers.
- The `commit-trailers` CI job (WP9) fails a commit on a branch named `agent/*` without at least one `Agent:` and one `Tier:` trailer. The monthly retro reads these trailers next to `events.jsonl` and cross-checks them.
- This replaces the old `Models:` footer of `t3-build` section 8.
