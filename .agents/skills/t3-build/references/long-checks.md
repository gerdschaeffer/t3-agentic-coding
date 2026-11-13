# Long checks outlive the session

A full gate (`make verify`, a full Playwright run, a long build or crawl) can take
30-60 minutes. The agent session does not necessarily live that long.

## The failure mode

T3 runs each agent session in a transient systemd scope
(`t3code-opencode-<id>.scope`, `t3code-claude-<id>.scope`). When the session is
replaced — provider reconnect, tool-catalog change, app update — the scope stops
and every child process it started gets **SIGTERM**. A check started as an
ordinary background shell therefore dies mid-run with exit **143** and no test
result, often at 80 % after 30 minutes of CPU.

Observed on this machine (2026-11-02): `make verify` was killed at 11:19 and
15:10, each time within seconds of `t3code-opencode-…scope` being stopped; the
journal shows the scope's final "Consumed … wall clock time" line. It was not an
OOM kill (that is SIGKILL/137) and not a test failure.

## The rule

Any check that may run longer than **five minutes** runs through
`scripts/detach.sh`, never as a session child, and under the machine lock from
`scripts/agent-lock.sh` when other agents share the host
([machine locks](locks.md)):

```sh
S=~/.dotfiles/agents/skills/t3-agentic-coding/scripts/detach.sh
L=~/.dotfiles/agents/skills/t3-agentic-coding/scripts/agent-lock.sh

N=1  # new number for every attempt: job name and test database stay unique

# start (record the printed log path and the unit name in the task record).
# Detach outside, lock inside: a systemd unit does not inherit the caller's
# lock descriptor, so the opposite order releases the lock while it runs.
$S run spec041-verify-$N --cwd /path/to/repo -- \
  env TEST_DATABASE_NAME=test_guidelino_verify_$N \
  $L run heavy-check 3600 -- make verify

# later, on the next turn/wake - never inside the starting turn
$S status spec041-verify-$N    # running | done | dead
$S result spec041-verify-$N    # EXIT=0 only when it really finished
$S log spec041-verify-$N
```

On Linux with a systemd user manager it runs as the unit `detach-<name>` in its
own cgroup, so a session restart cannot reap it. A systemd unit gets the session's
`PATH` but no other session variables, so pass everything the check needs as
`env KEY=value`.

Without systemd (macOS) there is no unit: record `none`. The check starts in a new
session with nohup, which survives a hangup but **not** a cgroup stop.

If `status` says `dead` and `result` prints nothing, the run was killed and must be
restarted with a new number.

`detach.sh wait <name> <seconds>` exists for humans; an agent must not use it or
sleep-poll. End the turn and check `status` when the next turn starts (a
scheduled wake-up or the owner's next message).

## Recording it

In the task record, next to the check result, store:

- unit name (or `none`) and log path;
- the exact command and its working directory;
- the test-database name (one per attempt, as with `$N` above, so a killed attempt
  cannot poison the next one);
- the last known stage, so a wake can resume without re-reading the transcript.

## Rules of thumb

- Start the gate detached *before* the final review, not after it; the review
  and the gate then overlap.
- Never start a second gate while one is running (CPU and shared `site/`, `.mo`).
- A killed run is not a failure and not a pass: restart it, or split the gate
  into documented parts and report which parts were not run.
- `EXIT=` absent is "unknown", never "green".
