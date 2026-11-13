#!/usr/bin/env bash
# detach.sh - run a long check outside the agent session's systemd scope.
#
#   detach.sh run    <name> [--cwd DIR] -- <command...>
#   detach.sh status <name>     # running | done | dead
#   detach.sh result <name>     # "EXIT=<code>" once finished, nothing while running
#   detach.sh log    <name>     # full output
#   detach.sh wait   <name> [seconds, default 3600]   # for humans; agents must not sleep-poll
#
# On Linux with a systemd user manager the command runs as a transient unit in its
# own cgroup (t3code-opencode-<id>.scope restarts no longer kill it). Elsewhere it
# starts in a new session with nohup (setsid, or perl on macOS, which has no setsid),
# which survives a login-shell hangup but not a cgroup stop.
# State lives in ${XDG_STATE_HOME:-~/.local/state}/detach because TMPDIR can differ
# between sessions: detach-<name>.log, and detach-<name>.exit holding EXIT=<code>.
# A systemd unit gets PATH but no other session variables; pass them as `env KEY=value`.
set -euo pipefail

cmd=${1:?usage: detach.sh run|status|result|log|wait <name> ...}
name=${2:?usage: detach.sh run|status|result|log|wait <name> ...}
shift 2

[[ $name =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "detach.sh: name must match [A-Za-z0-9_.-]+" >&2; exit 2; }

state_dir=${XDG_STATE_HOME:-$HOME/.local/state}/detach
# shortcut: a sandbox that cannot write the state dir uses TMPDIR, so a session with a different sandbox will not find the job; allow the dir in the sandbox if that bites.
{ mkdir -p "$state_dir" && touch "$state_dir/.writable"; } 2>/dev/null || state_dir=${TMPDIR:-/tmp}

log=$state_dir/detach-$name.log
exitfile=$state_dir/detach-$name.exit
pidfile=$state_dir/detach-$name.pid
runner=$state_dir/detach-$name.sh
unit=detach-$name

state() {
  if [[ -s $exitfile ]]; then echo done
  elif systemctl --user is-active --quiet "$unit" 2>/dev/null; then echo running
  # shortcut: a recycled PID after a kill or reboot reads as running; compare process start time if that bites.
  elif [[ -s $pidfile ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then echo running
  else echo dead; fi
}

case $cmd in
  run)
    cwd=$PWD
    if [[ ${1:-} == --cwd ]]; then cwd=$2; shift 2; fi
    [[ ${1:-} == -- ]] && shift
    (($#)) || { echo "detach.sh: no command" >&2; exit 2; }
    cwd=$(cd "$cwd" && pwd)
    [[ $(state) != running ]] || { echo "detach.sh: $name is already running" >&2; exit 1; }
    : >"$runner"
    chmod 700 "$runner" # the command line can carry credentials
    {
      echo '#!/usr/bin/env bash'
      printf 'echo $$ >%q\n' "$pidfile"
      printf 'export PATH=%q\n' "$PATH" # transient units do not inherit the session's PATH
      printf 'cd %q || exit 1\n' "$cwd"
      printf '%s>%q 2>&1\n' "$(printf '%q ' "$@")" "$log"
      # Separate file: command output can neither fake nor swallow the marker.
      printf 'echo EXIT=$? >%q\n' "$exitfile"
    } >"$runner"
    : >"$log"
    rm -f "$exitfile" "$pidfile"
    if command -v systemd-run >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
      systemd-run -q --user --unit="$unit" --collect "$runner" >/dev/null
    elif command -v setsid >/dev/null; then
      nohup setsid "$runner" </dev/null >/dev/null 2>&1 &
    else
      nohup perl -e 'use POSIX; POSIX::setsid(); exec @ARGV or die' "$runner" </dev/null >/dev/null 2>&1 &
    fi
    # The runner writes the pidfile after setsid, so returning now means it left this session.
    for _ in {1..100}; do [[ -s $pidfile ]] && break; sleep 0.1; done
    [[ -s $pidfile ]] || { echo "detach.sh: $name did not start" >&2; exit 1; }
    echo "$log"
    ;;
  status)
    state
    ;;
  result)
    cat "$exitfile" 2>/dev/null || true
    ;;
  log)
    cat "$log" 2>/dev/null || true
    ;;
  wait)
    limit=${1:-3600}; waited=0
    while ((waited < limit)); do
      if [[ -s $exitfile ]]; then cat "$exitfile"; exit 0; fi
      sleep 5; waited=$((waited + 5))
    done
    echo "timeout" >&2; exit 124
    ;;
  *)
    echo "usage: detach.sh run|status|result|log|wait <name> ..." >&2; exit 2
    ;;
esac
