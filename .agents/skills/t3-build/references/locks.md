# One machine, several agents

Several agents share this host: their own T3 sessions, worktrees, CI runner jobs
and the user's own commands. They do not share a database (the Makefile derives
one test database per checkout) but they share **CPU, Docker and the dev
PostgreSQL**. Two full `make verify` runs at once do not corrupt each other; they
starve each other, and the timing-sensitive tests flake — usually the same one in
both directions, so each agent blames its own diff.

Observed on this machine (2026-11-02): two 50-minute gates ran alongside another
agent's gate; both failed only in
`test_year_closing_concurrency.py::…[shortcut]`, passed 3/3 in isolation, and the
third agent's gate passed. Serialising them would have cost nothing and saved two
hours.

## The rule

Before a heavy check, take the machine lock:

```sh
L=~/.dotfiles/agents/skills/t3-agentic-coding/scripts/agent-lock.sh

AGENT_LOCK_AGENT="<harness>/<model>" AGENT_LOCK_TASK="spec 038 verify" \
  $L run heavy-check 3600 -- \
  env TEST_DATABASE_NAME=test_guidelino_verify_1 make verify
```

Composed with the long-check rule, so both problems are handled in one call. The
detach goes **outside** and the lock **inside**:

```sh
$S run verify-1 --cwd /path/to/repo -- \
  env AGENT_LOCK_AGENT="<harness>/<model>" AGENT_LOCK_TASK="spec 038 gate" \
      TEST_DATABASE_NAME=test_guidelino_verify_1 \
  $L run heavy-check 3600 -- make verify
```

This order is not cosmetic. A systemd unit is forked by the user manager, not by
the caller, so it does **not** inherit the lock descriptor: with the lock outside,
`agent-lock.sh` exits as soon as the unit starts and the lock is released while
the check keeps running. Verified on this machine (2026-11-02): outer lock +
inner detach reported `free` while the unit was still `running`. With the lock
inside, the unit's own process tree holds it. The unit also inherits only `PATH`,
so pass every `AGENT_LOCK_*` and test-database variable with `env`.

A lock timeout then shows up as `EXIT=75` in `detach.sh result`, which the task
record must read as "did not start", not as a failure.

**Exit 75 means the lock was not free within the timeout.** That is not a
failure: someone else is working. Report it, and either wait for the next turn or
schedule a wake-up; never start a second run beside it and never "just try again"
in a loop. A command may itself exit 75, so when it matters read the stderr line
`agent-lock: <resource> is busy after Ns`; a broken lock directory or a failed
`flock` exits 2 with its own message instead, so a configuration fault can never
masquerade as contention.

## Why flock, not a pid file

`flock` is released by the kernel when the holding process dies, `SIGKILL`
included. A lock therefore cannot go stale and needs no liveness heuristic
("is pid 12345 still alive?", "is /proc/12345/starttime recent?"). A pid file can
go stale; an flock cannot.

Consequence worth knowing: the lock is held while **any descendant** of
`agent-lock.sh run` is alive, because children inherit the descriptor. That is
intentional — a check that outlived its parent is still running and still needs
the machine. A lock released on parent death would let a second agent start on
top of a live run.

## Status, and the only staleness case

`<resource>.status` is informational: who holds it, on which host, from which
working directory, since when, and the command. It is written after acquiring and
emptied on exit.

| Lock | Status file | Meaning | Action |
|---|---|---|---|
| held | present | work is running | wait |
| held | empty | a descendant holds it (the wrapper exited, e.g. after a detach) | treat as running |
| free | present | the holder died before its exit trap ran | `agent-lock.sh clean` |
| free | empty | normal completion, or just acquired and not yet written | acquire |
| unreadable | any | the lock file cannot be opened | fix permissions; `status` says so |

`agent-lock.sh status [<resource>]` shows all of this and always exits 0, so a
caller can read it without comparing exit codes. `agent-lock.sh clean` removes
status files whose lock is free, holding the lock while it deletes, and leaves
busy ones alone. There is no heartbeat and no timeout to tune — the kernel is the
authority.

## Resources

Use a resource per contention domain, not one global lock:

| Resource | Held for |
|---|---|
| `heavy-check` | `make verify`, full Playwright runs, long builds |
| `dev-db` | anything that mutates the shared dev PostgreSQL beyond its own test database |
| `docker-stack` | `up`/`down` of `guidelino-dev` / `guidelino-infra` |
| `deploy` | `scripts/deploy.sh <env>` |

## Limits, stated honestly

- **Opt-in.** It only works when every agent takes the lock. Until every session
  and the CI runner do, it makes contention visible but does not prevent it.
- **It costs throughput.** Two 50-minute gates become 100 minutes. That is still
  cheaper than two failed 50-minute gates, but it is not free, and it should be
  said out loud rather than discovered.
- **Same host only.** `/tmp` is per machine; agents on another host coordinate
  with nothing.
- **Same user assumed.** The directory is 1777 so another user can create files
  in it, but a lock file created under a 022 umask is 0644: a second user can
  probably not open it for writing. Multi-user use needs a shared group and a
  matching umask; the helper does not arrange that.

## Recording it

In the task record: the resource, the holder label, the start time, and the exit
code. `75` belongs in the record as "did not start, lock busy", never as a
failure and never as a pass.
