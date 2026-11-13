<img src="assets/icon.svg" width="64" height="64" alt="">

# t3-agentic-coding

A setup for one human and a team of AI agents that build and maintain an application. Claude Code leads, Codex reviews, OpenRouter models take budgeted bulk work. Created in [T3 Code](https://t3.codes).

| File | What it is |
|---|---|
| [CONCEPT.md](CONCEPT.md) | The design: team, risk tiers, flows, security, costs, traps |
| [PLAN.md](PLAN.md) | Work packages that turn the concept into a working setup |
| [AGENTS.md](AGENTS.md) | Project rules every agent reads |
| `.agents/skills/` | The team's skills. [SOURCES.md](.agents/skills/SOURCES.md) lists origin and pins |
| `staging/` | Agent definitions for Claude Code, Codex and OpenCode, waiting to be installed |
| [install-staging.sh](install-staging.sh) | Installs the staged agents and the skills link |

## Requirements

- [T3 Code](https://t3.codes) with the Claude, Codex and OpenCode providers enabled for cross-provider delegation
- Claude Code (Max plan), Codex (Pro plan), OpenCode with an OpenRouter key
- `git`, `python3` 3.11 or newer (for the risk gate), `gh` for pull requests
- Read the Max and Pro plan terms on unattended automation before you run agents overnight

## Install

1. **Get the repo.**

   ```sh
   git clone https://github.com/gerdschaeffer/t3-agentic-coding.git && cd t3-agentic-coding
   ```

2. **Install the agents.** Agents may not write `.claude/`, `.codex/` or `.opencode/`, so a human does it:

   ```sh
   ./install-staging.sh
   git status && git diff --stat   # read what was added
   ```

   It copies 16 Claude agents to `.claude/agents/`, two Codex agents to `.codex/agents/`, two OpenCode agents to `.opencode/agents/`, and links `.claude/skills` to `.agents/skills`.

3. **Check the skills link.** Start Claude Code in the repo and confirm `t3-build` is in the skills list. If it is not, replace the folder link with one link per skill:

   ```sh
   rm .claude/skills && mkdir .claude/skills
   for s in .agents/skills/*/; do ln -s ../../.agents/skills/$(basename $s) .claude/skills/$(basename $s); done
   ```

4. **Fix the model settings.**
   - Open `.opencode/agents/third-voice.md` and replace the placeholder Gemini slug with the one from your OpenCode catalog.
   - Compare the IDs in `.agents/skills/t3-build/references/models.md` with the T3 Code model catalog. Change them in that file first, then in the agent files.
   - Confirm that the effort levels in `models.md` exist for each model. Never use `max`, `xhigh` or fast modes by default.

5. **Set up OpenRouter.** Dollar limit on the API key with monthly reset, a guardrail that enforces zero data retention, a provider allowlist, safety retention off.

6. **Configure the sandbox** for agent sessions: write access to the worktree only, skill folders and secret paths denied, network allowlist. Keep secrets in a secret manager and give agents test-only credentials.

7. **Start the lead.**

   ```sh
   claude --agent lead
   ```

## Where to adjust agents and providers

One rule: a model is named in one table, and the agent files mirror it. Change the table first, then the files. Agent files and skills are guardrail files: change them by pull request that a human merges.

| I want to change | Edit |
|---|---|
| Which model or effort a role uses | `.agents/skills/t3-build/references/models.md`, then the same role's file below |
| A Claude agent (model, effort, tools, instructions) | `.claude/agents/<role>.md`, frontmatter keys `model`, `effort`, `tools`, `skills` |
| The Codex reviewer (model, effort, sandbox) | `.codex/agents/reviewer.toml`, `reviewer-security.toml`: `model`, `model_reasoning_effort`, `sandbox_mode` |
| The OpenRouter agents (model slug, permissions) | `.opencode/agents/bulk-worker.md`, `third-voice.md`: `model`, `permission` |
| OpenRouter spend and data retention | OpenRouter account settings: key limit, zero-retention guardrail, provider allowlist. Budgets per task: `references/budgets.md` |
| Which provider may see which data | Data classes in [CONCEPT.md](CONCEPT.md) section 2 and the Rules in [AGENTS.md](AGENTS.md) |
| Use a different provider for a role | Change the `Provider` and `Model` columns in `models.md`, then replace the role's agent file in the harness folder of the new provider (Markdown for Claude Code and OpenCode, TOML for Codex). Keep the role name |
| Add a role | New agent file per harness, a row in `models.md`, a line in [CONCEPT.md](CONCEPT.md) section 3 |
| Review loop limits, tiers, who merges | `references/budgets.md`, `references/risk-tiers.md`, `.agents/skills/t3-build/gate/risk-policy.toml` |

Code is reviewed by another vendor than the one that wrote it. If you move the reviewer to Claude, writer and reviewer share a vendor and the vendor separation of CONCEPT section 3 no longer holds.

After a change, check that the model IDs exist in the T3 Code catalog. A missing model blocks its stage; nobody swaps models silently.

## Use in a product repository

The skills and agents are written for a product repository, not for this one. To use them in a new project:

1. Copy `.agents/skills/`, `AGENTS.md`, `CLAUDE.md` and `staging/` into it, then run `./install-staging.sh` there.
2. Copy `.agents/skills/t3-build/gate/` to `.github/risk-gate/` and follow `gate/README.md`: policy file, workflow, GitHub token, required checks `ci` and `risk-gate`, code owners for guardrail files. Start the gate in shadow mode. After about ten pull requests compare its lanes with your own decisions, then switch to enforce.
3. Set branch protection: required checks, no admin bypass, no force push.
4. Tell the lead what to build. It starts with the `project` workflow of `t3-build`: research, questions, spec, one concept approval, then one pull request per milestone.

## Commit messages

Every commit that agents worked on names each agent, model and effort:

```
Agent: lead / claude-sonnet-5-5 / high
Agent: implementer / claude-opus-5-5 / medium
Tier: T1
```

Format: [commit-trailers.md](.agents/skills/t3-build/references/commit-trailers.md).

## License

[MIT](LICENSE). `ponytail` and `ponytail-review` keep their own MIT license files. The other copied skills come from the author's `~/.agents/skills` and carry no separate license file: check their origin before you publish, see [SOURCES.md](.agents/skills/SOURCES.md).

## Status

The skills `release`, `maintain` and `milestone-audit` and the `t3-build` changes are not written yet. [PLAN.md](PLAN.md) lists them as WP2 to WP7. The risk gate has not been run against a real repository. The `effort` key in the agent files and the whole-folder skills link are untested.
