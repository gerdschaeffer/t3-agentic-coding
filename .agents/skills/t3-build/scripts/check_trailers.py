#!/usr/bin/env python3
"""Check Agent:/Tier: lines on the commits of an agent branch (spec 015-wp9-ci, section 5).

Usage: check_trailers.py --branch NAME --base REV --head REV [--repo DIR]

A branch whose lowercased name does not start with agent/ is skipped without running git. Every
non-merge commit in BASE..HEAD must have, in any paragraph, a line matching AGENT_LINE and a line
matching TIER_LINE. Messages are read from the raw commit object (`git cat-file commit`) as lines,
never through git's trailer parser; a message over MAX_MESSAGE bytes or with a NUL fails.
Exit 0: pass. Exit 1: a failing commit, or input over a bound. Exit 2: bad arguments, not a
repository, an unknown revision, or a git timeout (stdout empty, message on stderr).

Limits: the lines are self-declared and any matching line counts, including one inside a quoted or
fenced example in the body; this is a consistency check, not authentication. Merge commits are
skipped, so changes made in a merge resolution carry no trailer requirement.

SIGTERM is deferred only while git runs: run_git installs a flag-setting handler right before it
starts git and restores the previous one after git is reaped; elsewhere SIGTERM has its default
effect. run_git sees the flag within 0.1 s, kills git's process group, reaps git (at most 5 s), and
after the restore the script exits 143. A group kill that fails, or a reap that does not finish, is
exit 2 (`could not terminate git`). SIGKILL of the script cannot be handled, so git's group is then
left to the runner.
"""

import argparse
import os
import re
import select
import signal
import subprocess
import sys
import time

GIT = "git"
MAX_COMMITS = 500
MAX_BYTES = 5_000_000  # git output per call
MAX_LINE = 4096
MAX_MESSAGE = 64 * 1024
GIT_TIMEOUT = 60  # seconds per git call
STEP = 0.1  # longest wait between two looks at the SIGTERM flag
stopped = False  # set by the SIGTERM handler
GIT_FLAGS = ["-c", "core.fsmonitor=false", "-c", "diff.external=", "-c", "core.pager=cat", "--no-pager"]
REV = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}")
SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
AGENT_LINE = re.compile(r"^Agent: [a-z0-9][a-z0-9-]{0,63} / [A-Za-z0-9][A-Za-z0-9._/:-]{0,127} / [a-z]{1,16}"
                        r"( \([^()]{0,200}\))?\s*$")
TIER_LINE = re.compile(r"^Tier: T[012](\s.*)?$")


def plain(value):
    """One line of plain text: every non-printable character becomes a space; cut at 200."""
    s = "".join(c if c.isprintable() else " " for c in str(value))
    return s if len(s) <= 200 else s[:200] + "..."


def die(message):
    print(f"check_trailers: {message}", file=sys.stderr)
    sys.exit(2)


def too_large(what, limit):
    print(f"too large: {what} over {limit}")
    sys.exit(1)


def run_git(args, repo):
    """The only place that starts git: bounded output (MAX_BYTES) and time (GIT_TIMEOUT)."""
    argv = [GIT, "-C", repo, *GIT_FLAGS, *args]
    # No inherited git behaviour (GIT_DIR, pathspec modes, config, ...) and no lazy fetch from a promisor.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0")
    global stopped
    stopped = False
    previous = signal.signal(signal.SIGTERM, on_sigterm)  # only while a child runs (AC36)
    try:
        try:
            p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 env=env, start_new_session=True)
        except OSError:
            die("cannot run git")
        out = bytearray()
        code = run_child(p, time.monotonic() + GIT_TIMEOUT, out)
    finally:
        signal.signal(signal.SIGTERM, previous)
    if stopped:  # decided after the restore, so a SIGTERM that came at the restore is not lost (AC37)
        sys.exit(143)
    if code:
        die(f"git {args[0]} failed (exit {code}): not a repository or unknown revision")
    return bytes(out)


def run_child(p, deadline, out):
    """git's exit code (None after a SIGTERM); its output goes to out. The cleanup always kills the group
    (unless git was reaped) and reaps git."""
    code = None
    try:
        fd = p.stdout.fileno()
        while not stopped:
            left = deadline - time.monotonic()
            if left <= 0:
                die("git timed out")
            if not select.select([fd], [], [], min(left, STEP))[0]:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            out += chunk
            if len(out) > MAX_BYTES:
                too_large("git output", f"{MAX_BYTES} bytes")
        while not stopped and code is None:
            left = deadline - time.monotonic()
            if left <= 0:
                die("git timed out")
            try:
                code = p.wait(timeout=min(left, STEP))
            except subprocess.TimeoutExpired:
                pass
    finally:
        # The handler raises nothing, so this cleanup always runs to the end.
        failed = False
        if code is None:  # timeout, overflow, SIGTERM or exception: kill the group before reaping (pgid stays git's)
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:  # fail closed, also for macOS EPERM on a group of zombies
                failed = True
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                failed = True
        p.stdout.close()
        if failed:
            die("could not terminate git")
    return code


def missing(message):
    """None when the message passes, else the failure text."""
    if len(message) > MAX_MESSAGE:
        return "message too large"
    if b"\0" in message:
        return "message contains NUL"
    lines = [l[:MAX_LINE] for l in message.decode("utf-8", "replace").replace("\r", "").split("\n")]
    agent = any(AGENT_LINE.match(l) for l in lines)
    tier = any(TIER_LINE.match(l) for l in lines)
    if agent and tier:
        return None
    return "missing Agent line" if tier else "missing Tier line" if agent else "missing Agent and Tier lines"


def on_sigterm(signum, frame):
    global stopped
    stopped = True


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check Agent:/Tier: lines on an agent branch.")
    ap.add_argument("--branch", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--repo", default=".")
    args = ap.parse_args(argv)
    for flag in ("branch", "base", "head"):
        if not REV.fullmatch(getattr(args, flag)):
            die(f"--{flag}: not a valid name")
    if not args.branch.lower().startswith("agent/"):
        print("trailers: not an agent branch, skipped")
        return 0
    out = run_git(["log", "-z", "--no-show-signature", "--no-merges", f"--max-count={MAX_COMMITS + 1}",
                   "--format=%H", f"{args.base}..{args.head}", "--"], args.repo)
    shas = [s.decode("ascii", "replace") for s in out.split(b"\0") if s]
    if len(shas) > MAX_COMMITS:
        too_large("commits", MAX_COMMITS)
    failures = []
    for sha in shas:
        if not SHA.fullmatch(sha):
            die("unexpected git log output")
        # The raw object, not %B: %B stops at a NUL and re-encodes, so the limit would miss bytes.
        message = run_git(["cat-file", "commit", sha], args.repo).partition(b"\n\n")[2]
        reason = missing(message)
        if reason:
            failures.append(f"{plain(sha[:12])}: {reason}")
    for f in failures:
        print(f)
    print(f"trailers: {len(shas)} commits checked, {len(failures)} failing")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
