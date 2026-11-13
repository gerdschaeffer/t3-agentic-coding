#!/usr/bin/env bash
# agent-lock.sh - one machine-wide lock so several agents do not run heavy checks
# at the same time on the same host.
#
#   agent-lock.sh run    <resource> <timeout-seconds> -- <command...>
#   agent-lock.sh status [<resource>]
#   agent-lock.sh clean
#
# Exit 75 means the lock was not free within the timeout; that is "someone else
# is working", not a failure. Every other exit code is the command's own. A
# command may itself exit 75; when it matters, read the stderr line
# "agent-lock: <resource> is busy after Ns".
#
# Why flock and not a pid file: the kernel releases an flock when the holding
# process dies, including SIGKILL. A lock therefore cannot go stale, and no
# liveness heuristic ("is pid 12345 still running?") is needed. The
# <resource>.status file is informational only; never trusted, always rebuilt.
#
# Environment:
#   AGENT_LOCK_DIR    default /tmp/agent-locks (same path for every session)
#   AGENT_LOCK_AGENT  who holds it, e.g. opencode/deepseek-v4.1 (default: unknown)
#   AGENT_LOCK_TASK   what it is doing, e.g. "spec 038 verify" (optional)
set -uo pipefail

DIR="${AGENT_LOCK_DIR:-/tmp/agent-locks}"
mkdir -p "$DIR" 2>/dev/null || true
chmod 1777 "$DIR" 2>/dev/null || true

die() { echo "agent-lock: $*" >&2; exit 2; }

valid() { [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "invalid resource name '$1' (letters, digits, dot, dash, underscore)"; }

# Probe a lock without waiting. Prints nothing.
#   0 = held by someone, 1 = free, 2 = the lock file cannot be used at all.
# Keeps contention apart from an unusable directory: treating both as "busy"
# would hide a broken configuration for as long as the agents keep deferring.
held() {
  local lock="$1" rc
  ( exec 8>>"$lock" 2>/dev/null || exit 2
    flock -n 8 2>/dev/null
    rc=$?
    case "$rc" in
      0) exit 1 ;;   # acquired: nobody holds it
      1) exit 0 ;;   # flock says busy
      *) exit 2 ;;   # bad descriptor, unsupported, ...
    esac )
}

show_one() {
  local res="$1"
  local lock="$DIR/$res.lock"
  local st="$DIR/$res.status"
  local state
  if [[ ! -d "$DIR" ]]; then
    echo "$res: cannot probe: $DIR is not a directory"
    return 0
  fi
  if [[ ! -e "$lock" ]]; then
    state=1
  else
    held "$lock"
    state=$?
  fi

  case "$state" in
    0)
      echo "$res: BUSY"
      [[ -s "$st" ]] && sed 's/^/  /' "$st"
      ;;
    1)
      if [[ -s "$st" ]]; then
        echo "$res: free - status file is stale (the holder died), remove it with: agent-lock.sh clean"
        sed 's/^/  /' "$st"
      else
        echo "$res: free"
      fi
      ;;
    *)
      echo "$res: cannot probe $lock (check permissions on $DIR)"
      ;;
  esac
  return 0
}

cmd_status() {
  if (( $# )); then
    valid "$1"
    show_one "$1"
    return 0
  fi
  local found=0 lock res
  if [[ ! -d "$DIR" ]]; then
    echo "cannot probe: $DIR is not a directory"
    return 0
  fi
  for lock in "$DIR"/*.lock; do
    [[ -e "$lock" ]] || continue
    res="$(basename "$lock" .lock)"
    show_one "$res"
    found=1
  done
  (( found )) || echo "no locks in $DIR"
  return 0
}

# Remove a status file only while holding that resource's lock, so a run that
# acquired it one instant earlier cannot lose its diagnostics.
cmd_clean() {
  local st res lock
  if [[ ! -d "$DIR" ]]; then
    echo "cannot clean: $DIR is not a directory"
    return 0
  fi
  for st in "$DIR"/*.status; do
    [[ -e "$st" ]] || continue
    res="$(basename "$st" .status)"
    lock="$DIR/$res.lock"
    if ( exec 8>>"$lock" 2>/dev/null && flock -n 8 && rm -f "$st" ) 2>/dev/null; then
      echo "$res: removed stale status"
    else
      echo "$res: busy or unreadable, left alone"
    fi
  done
  return 0
}

cmd_run() {
  (( $# >= 4 )) || die "usage: agent-lock.sh run <resource> <timeout-seconds> -- <command...>"
  local res="$1" wait_s="$2" lock st rc
  valid "$res"
  [[ "$3" == "--" ]] || die "expected -- before the command"
  [[ "$wait_s" =~ ^[0-9]+$ ]] || die "timeout must be seconds, not '$wait_s'"
  shift 3
  lock="$DIR/$res.lock"
  st="$DIR/$res.status"

  # A directory we cannot write must not look like contention.
  { : >>"$lock"; } 2>/dev/null \
    || die "cannot use $lock (check permissions on $DIR and the file's owner/group)"

  exec 9>>"$lock"
  flock -E 75 -w "$wait_s" 9
  rc=$?
  case "$rc" in
    0) ;;
    75)
      echo "agent-lock: $res is busy after ${wait_s}s; not starting." >&2
      [[ -s "$st" ]] && sed 's/^/  /' "$st" >&2
      exit 75
      ;;
    *)
      die "flock failed on $lock (rc=$rc); not starting"
      ;;
  esac

  {
    printf 'holder=%s\n' "${AGENT_LOCK_AGENT:-unknown}"
    printf 'task=%s\n' "${AGENT_LOCK_TASK:-}"
    printf 'host=%s\n' "$(hostname -s 2>/dev/null || echo unknown)"
    printf 'pid=%s\n' "$$"
    printf 'cwd=%s\n' "$PWD"
    printf 'started=%s\n' "$(date -u +%FT%TZ)"
    printf 'waited_for=%ss\n' "$wait_s"
    printf 'command=%s\n' "$*"
  } >"$st"
  on_exit() { : >"$st"; }
  trap on_exit EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM HUP

  "$@"
  rc=$?
  exit "$rc"
}

cmd=${1:?usage: agent-lock.sh run|status|clean ...}
shift
case "$cmd" in
  run) cmd_run "$@" ;;
  status) cmd_status "$@" ;;
  clean) cmd_clean ;;
  *) die "unknown command '$cmd' (run, status, clean)" ;;
esac
