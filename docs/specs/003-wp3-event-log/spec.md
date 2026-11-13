# 003 WP3: event log script

Work package tier T1; `tier.py` rates the files `human` (they sit in `.agents/`), so the human
merges. Branch `agent/wp3-event-log`, stacked on WP2 head `ea448ba`.
Source: PLAN.md WP3, CONCEPT.md 6.12 ("What is recorded", "Limits").

**Intent.** `scripts/log_event.py` appends one JSON line per stage to
`<state>/<repo>/events.jsonl`. It validates every field, adds the time, and writes nothing on
invalid input. The lead calls it after each stage (the SKILL.md call is WP4). The log feeds the
monthly report (WP8); this WP only writes.

Keywords MUST, MUST NOT as in RFC 2119.

## 1. Files

| File | Author |
|---|---|
| `.agents/skills/t3-build/scripts/test_log_event.py` | test-author (first, red) |
| `.agents/skills/t3-build/scripts/log_event.py` | implementer |

Not touched: `SKILL.md` (WP4), `tier.py`, `gate/`.

## 2. Interface

`python3 log_event.py --repo NAME [--state-dir DIR] < event.json`

One JSON object on stdin. Exit 0: one line appended, stdout empty. Exit 2: invalid input,
message on stderr (one problem per line), **nothing written, no directory created**. Exit 1: I/O
or permission problem (message on stderr, nothing appended).

State root, first that is set: `--state-dir`, env `T3_BUILD_STATE`, `~/.local/state/t3-build`.
Log path: `<root>/<repo>/events.jsonl`.

Module API for tests: `validate(event) -> list[str]` (problems, empty when valid) and
`append(root, repo, event)`: `root` is a `str` or `Path`; it validates `repo` and `event` and raises
`ValueError` (message: the problems) before touching the filesystem; it returns `None`. A missing
`--repo` exits 2 (argparse). Creating the directories tolerates a parallel first writer.

## 3. Event fields

Unknown fields are rejected. `ts` and `date` are set by the script; an event that contains
either is rejected. The script adds `ts` (UTC, `YYYY-MM-DDTHH:MM:SSZ`).

| Field | Required | Rule |
|---|---|---|
| `slice` | yes | token: `[A-Za-z0-9][A-Za-z0-9._-]{0,63}` |
| `tier` | yes | `T0`, `T1` or `T2` |
| `stage` | yes | one of `tier`, `spec`, `spec-review`, `tests`, `implement`, `code-review`, `security-pass`, `rating`, `ship`, `human-gate`, `qa`, `release` |
| `role` | yes | agent file name: `[a-z0-9][a-z0-9-]{0,39}` |
| `model` | yes | executed model ID: `[A-Za-z0-9][A-Za-z0-9._/:-]{0,79}` |
| `effort` | yes | `[a-z0-9]{1,16}` (use `default` when the runtime did not confirm one) |
| `attempt` | no | integer >= 1 (JSON bool is not an integer) |
| `round` | no | integer >= 1 |
| `verdict` | no | `APPROVE`, `CHANGES`, `CLEAN`, `FINDINGS` or `n/a` |
| `findings` | no | object with exactly the keys `blocker`, `should`, `nit`, `security`, each an integer >= 0 |
| `outcome` | no | `ok`, `failed`, `blocked`, `escalated`, `skipped`, `disputed` |
| `duration` | no | number >= 0 (seconds), not bool, finite |
| `tier_raised` | no | boolean |
| `escalation` | no | `none`, `model-up`, `lead`, `human` |
| `quota_pause` | no | boolean |

Free text is not allowed anywhere: no field can carry a prompt, a secret or a newline.
`--repo` follows the `slice` token rule (so `..`, `/`, empty and leading `.` are rejected).
The encoded line (with `ts`, newline included) MUST NOT exceed 4096 bytes, so that an
`O_APPEND` write is not interleaved with a parallel writer.

## 4. Files and permissions

- State root and `<root>/<repo>` are created with mode 0700 (parents of the root as needed with
  the default mode). An existing directory owned by the current user is set to 0700. A path that is
  a symlink, not a directory, or owned by someone else: exit 1.
- `events.jsonl` is opened `O_WRONLY|O_APPEND|O_CREAT|O_NOFOLLOW`, mode 0600; the line is written
  with one `write`. A symlink at that path: exit 1, the target is untouched.
- Output line: `json.dumps(event, separators=(",", ":"), ensure_ascii=True)`, keys in input order,
  `ts` last.

## 5. Acceptance criteria

Tests in `scripts/test_log_event.py`, run `cd .agents/skills/t3-build/scripts && python3 -m unittest`.
Every CLI test uses a temp `--state-dir` or `T3_BUILD_STATE`; none touches `~`.

| AC | Requirement |
|---|---|
| AC1 | A valid event appends exactly one line: a JSON object with the given fields in order and `ts` last (matches the format); two calls give two lines; exit 0, stdout empty. A full event with every optional field is accepted. |
| AC2 | Each missing required field exits 2, stderr names the field, state dir not created (or unchanged when it existed with a log). |
| AC3 | Rejected, exit 2, nothing written, one case each: `tier` `T3`; unknown `stage`; `attempt` 0, `"1"`, `true`, 1.5; `round` 0; negative, `NaN`, `Infinity` and bool `duration`; `findings` with a missing, extra or negative key; unknown `verdict`, `outcome`, `escalation`; non-bool `tier_raised`; unknown field; supplied `ts` or `date`; `model`, `effort`, `role`, `slice` with a space, newline or non-ASCII character; `effort`, `role`, `slice` with `/` (`model` may contain `/`). |
| AC4 | `--repo` `..`, `a/b`, `.hidden`, empty, a 65-character name and a name with a newline exit 2 and create nothing. |
| AC5 | Input that is not JSON, not an object, empty, or too large (line over 4096 bytes) exits 2, nothing written. Several problems in one event are all listed on stderr. |
| AC6 | After a write the state root and the repo directory are 0700 and the log is 0600; a pre-existing 0755 repo directory becomes 0700. |
| AC7 | `events.jsonl` as a symlink to another file: exit 1, the target is unchanged. `<root>/<repo>` as a symlink: exit 1, nothing written through it. |
| AC8 | State root precedence: `--state-dir` beats `T3_BUILD_STATE`, which beats `$HOME/.local/state/t3-build` (test sets `HOME` to a temp dir). |
| AC9 | 20 parallel processes appending give exactly 20 intact JSON lines. |
| AC10 | Module API: `validate` returns `[]` for a valid event and a list of strings for an invalid one, without touching the filesystem. |

## 6. Security

Asset: the log's integrity and privacy (it records which models did what; the retro agent
trusts it). Trust boundary: stdin and argv from the lead. Abuse cases: path traversal through
`--repo` (AC4); symlink redirect of the log or its directory (AC7); log injection with newlines or
control characters (closed by token rules, AC3); secrets or prompts in the log (no free-text field);
world-readable log (AC6); torn lines under parallel slices (AC9). The script reads no files, runs
no subprocess and uses no network.

Limits (from CONCEPT 6.12, not fixed here): the lead writes the log and can omit lines; a second
machine keeps its own log; `O_APPEND` atomicity holds on local filesystems.

## 7. Roles

Spec: lead. Tests: test-author (Sonnet, medium). Script: implementer (Opus, medium). Review:
reviewer (Sol, medium), security-weighted brief (path and symlink handling). No separate spec
review and no second-vendor security pass: the PLAN stage table lists neither.
