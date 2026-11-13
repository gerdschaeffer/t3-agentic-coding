#!/bin/sh
# Human step. Installs the staged agent files and the skills link into the guardrail folders. Run from the repo root.
set -e
mkdir -p .claude .codex/agents .opencode/agents
cp -R staging/claude/agents .claude/
cp staging/codex/agents/* .codex/agents/
cp staging/opencode/agents/* .opencode/agents/
[ -e .claude/skills ] || ln -s ../.agents/skills .claude/skills
echo "Installed. Review with: git status && git diff --stat"
