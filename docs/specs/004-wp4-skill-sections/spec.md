# 004 WP4: t3-build sections for refactoring, post-mortem, docs and log calls

Tier T2 by path (`tier.py`: `human`, skills are guardrail files). Prose only, no code. Base
`main` at `610cfc5`. Source: PLAN.md WP4, CONCEPT 6.5, 6.6, 6.10, 6.12.

**Intent.** `SKILL.md` tells the lead to (a) map before a refactoring, (b) write a post-mortem
after a severe or repeated bug, (c) put docs into the same PR, (d) call `log_event.py` after
every stage. Each is a few lines in `SKILL.md`; detail lives in `references/`.

## Files

| File | Change |
|---|---|
| `.agents/skills/t3-build/SKILL.md` | short additions to `refactor:`, `fix:`, step 6, section 2 and section 9 |
| `.agents/skills/t3-build/references/event-log.md` | new: when and how to call `log_event.py` |
| `.agents/skills/t3-build/references/postmortem.md` | new: trigger, questions, outputs |

Not touched: scripts, gate, staging, models.md, CONCEPT.md.

## Acceptance criteria

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | `refactor:` bullet: reason from the backlog, `code-scout` maps area and callers first, characterisation tests when coverage is thin, small steps with green tests, reviewer question "Did behaviour change anywhere?", tier follows the code touched. Every item present once. | read `SKILL.md`, grep for each phrase |
| AC2 | `fix:` bullet or the post-mortem pointer: after a severe or repeated bug the lead writes a post-mortem (`references/postmortem.md`): which gate missed it, and the gate fix goes out as a proposal (guardrail files are human-owned). | grep, read |
| AC3 | Docs: step 6 and the "Done" definition say that a behaviour change updates the docs in the same PR, `docs-writer` writes them from the approved spec and the diff, and decision records are written when the decision is made. | grep, read |
| AC4 | Log: `SKILL.md` says to call `scripts/log_event.py` after every stage (including each review round, each retry and each human gate), links `references/event-log.md`, and says an exit 2 means fix the event, never skip silently. | grep, read |
| AC5 | `references/event-log.md`: the call with a complete example event that `log_event.py` accepts (checked by running it into a temp state dir), the stage list from the script, the state-dir rule (`T3_BUILD_STATE` or `--state-dir` when `~/.local/state` is not writable), exit codes, and what not to log (no free text, no secrets). | run the example, diff the stage list against `ENUMS["stage"]` |
| AC6 | `references/postmortem.md` is at most 40 lines. | `wc -l` |
| AC7 | Links in `SKILL.md` and `references/*.md` resolve; `scripts/test_tier.py` AC11 passes. Net growth of `SKILL.md` is at most 25 lines. | unittest, `wc -l` |
| AC8 | Nothing else changes: `git diff --name-only main` lists only the five paths above plus this spec. | git |

## Non-goals

- Cutting `SKILL.md` to the 200-line guideline (CONCEPT "Rules for skills"). It is 315 lines
  before this change; trimming it is a restructuring of sections 5, 6 and 9 and gets its own work
  package. New sections here stay short and send detail to `references/`.
- Changing `log_event.py` (no `post-mortem` stage: the post-mortem goes to `decisions.md`).
- Weekly docs drift check (WP7).

## Roles

Lead (Sonnet, high) writes, reviewer (Sol, medium) reviews; PLAN WP4. No test author: no code.
