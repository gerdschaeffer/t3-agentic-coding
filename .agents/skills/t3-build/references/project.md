# Project workflow

Moved from `SKILL.md` section 5. A project runs every milestone as a feature run (`SKILL.md` section 4).

1. **Discover, no questions yet.** Read the repo, docs and given material. Send
   read-only Sonnet research delegates for unknowns (libraries, APIs, existing
   code), each with one precise question, in parallel. Ponytail scoping: the
   smallest product that does the core job; the rest are non-goals.
2. **Ask only what only the user can answer**, grill-me style, one at a time with
   a recommendation: goals and success criteria, users, priorities and trade-offs,
   must-have vs nice-to-have, hard constraints (stack, hosting, compliance,
   deadlines), access and accounts, risk appetite. Anything code, docs or research
   can answer becomes a stated assumption in the concept. Stop when further
   answers would not change the plan; usually 10 questions or fewer.
3. **Spec.** Write `spec.md` with `technical-spec-writing` (delegate it to the Opus
   spec writer when a security trigger hits or the architecture is unclear): behavior, AC IDs,
   non-goals, architecture, data model, interfaces, a security section (assets,
   trust boundaries, abuse cases, mitigations), test strategy per AC, open
   questions, and milestones. Each milestone is one feature run and one PR, small
   enough for the auto lane where possible, with its ACs and expected risk lane.
   The first milestone sets up CI, staging and the risk gate (section 6) unless
   they exist. The spec reviewer (section 1) reviews the spec with the spec brief
   ([brief.md](brief.md)).
4. **Concept: the one approval gate.** At most one page in chat (also `concept.md`):
   - goal, success criteria, scope, non-goals
   - architecture in five lines; milestones with ACs and expected lane
   - security: main risks and mitigations
   - assumptions made for the user (to correct now)
   - what GO authorizes. Defaults: branches, commits, push, PRs yes; risk gate
     in `shadow` for the first ~10 PRs, then the user switches to `enforce`;
     new dependencies only if listed; deploy to production no; when Sol hits its
     limit, wait for reset (alternative: the latest Claude Fable as reviewer)
   - what still stops and asks: security risk acceptance, scope or behavior
     change, missing access or secret, budget used up
   - budget (section 7)

   Wait for GO; silence or a timeout is not GO. Changes: update the spec, re-run
   the spec review only if behavior changed, present again.
5. **Run the milestones** as feature runs from step 1 (tier), in order.
   Dependent milestones stack (`baseRef` = previous branch) until it merges.
   A spec change inside scope and not security: decide, log, go on. One that
   changes behavior, scope or security: pause that milestone, ask the user one
   question with a recommendation, continue independent milestones.
6. **Finish:** section 8.
