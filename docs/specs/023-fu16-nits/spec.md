# 023 FU16: agent file extension case, README test for the shared-check-runs section

Process depth T1 (the change only adds a failure to a guard script; no new input, no trust boundary).
`tier.py` says `human` for the paths (`.agents/`); the owner permitted the lead to merge this run.
Branch `agent/nits-ext-case-readme`, from `main` `96d3b99`. Source: the two nits left open in spec 015
section 23. Lead writes this spec. MUST as in RFC 2119.

## Change

1. **`check_agents.py`.** A file in an agents folder whose name ends with the folder's extension in a
   letter case other than lowercase (`lead.MD`, `x.Toml`) is a problem `<rel>: unexpected extension
   case` (exit 1). The file is still read and checked for model and effort drift as before (AC51 of spec
   015 stays: a drifting `alpha.MD` is still reported), so both problems can appear. Lowercase names,
   symlinks, folders and the other problems are unchanged.
2. **README test.** The second nit (a test for the "Known design limit: shared check runs" section of
   `ci/README.md`) is already done: `test_ci_templates.py` `TestFU9AC4ReadmeSharedCheckRuns` (FU9 AC4)
   checks the heading, the tokens `branches`, `main` and `ruleset`, the replace and pending statements,
   with a self-test that mutates each. Nothing to add.
3. **Docs.** `ci/README.md` one sentence on the `agents-drift` bullet.

## Files

| File | Author |
|---|---|
| `scripts/test_check_agents.py` | test-author (first, red). One existing test changes meaning and is edited, listed: `test_upper_case_extension_matching_its_row_passes` (spec 015 AC51) now expects exit 1 with exactly the extension problem for `alpha.MD` and `rev.TOML` and no other problem; the other AC51 tests stay as they are |
| `scripts/check_agents.py` | implementer |
| `ci/README.md` | lead |

## Acceptance criteria

| AC | Requirement | Evidence |
|---|---|---|
| AC1 | `claude/agents/alpha.MD`, `alpha.Md`, `opencode/agents/voice.MD`, `codex/agents/rev.TOML`, `rev.Toml` (each the only variant of its file, matching its row) give exit 1 and the line `<rel>: unexpected extension case` and nothing else. | tests |
| AC2 | The same files with a drifting model also report the model problem (both lines). A new `EVIL.MD` not in models.md reports the extension problem and the existing `no row in models.md` problem. | tests |
| AC3 | All-lowercase folders still exit 0 (`agents: N files match models.md`); the real `staging/` still passes. | existing tests |
| AC4 | `ci/README.md` says an agent file with a non-lowercase extension is a problem. | test (text) |

## Security

Strictly additive failure; the message goes through `plain()` like every printed name.
