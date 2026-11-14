#!/usr/bin/env python3
"""Append one validated JSON line per stage to <state>/<repo>/events.jsonl (spec 003-wp3-event-log).

Usage: log_event.py --repo NAME [--state-dir DIR] < event.json
Exit 0 appended, 2 invalid input (nothing created), 1 I/O or permission problem.
No network, no subprocess, reads no files.
"""

import argparse
import json
import math
import os
import re
import stat
import sys
import time
from pathlib import Path

MAX_LINE = 4096  # one O_APPEND write of this size is not interleaved with a parallel writer
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
ENUMS = {
    "tier": ("T0", "T1", "T2"),
    "stage": ("tier", "spec", "spec-review", "tests", "implement", "code-review",
              "security-pass", "rating", "ship", "human-gate", "qa", "release"),
    "verdict": ("APPROVE", "CHANGES", "CLEAN", "FINDINGS", "n/a"),
    "outcome": ("ok", "failed", "blocked", "escalated", "skipped", "disputed"),
    "escalation": ("none", "model-up", "lead", "human"),
    "lane": ("auto", "review", "block"),
}
PATTERNS = {
    "slice": TOKEN,
    "role": re.compile(r"[a-z0-9][a-z0-9-]{0,39}"),
    "model": re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:-]{0,79}"),
    "effort": re.compile(r"[a-z0-9]{1,16}"),
}
REQUIRED = ("slice", "tier", "stage", "role", "model", "effort")
FINDING_KEYS = {"blocker", "should", "nit", "security"}


def is_int(v, low):
    return type(v) is int and v >= low  # bool is excluded by the exact type check


def check(key, v):
    """Return a problem string for one field, or None."""
    if key in PATTERNS:
        ok = isinstance(v, str) and PATTERNS[key].fullmatch(v)
        return None if ok else f"{key}: must match {PATTERNS[key].pattern}"
    if key in ENUMS:
        return None if isinstance(v, str) and v in ENUMS[key] else f"{key}: must be one of {', '.join(ENUMS[key])}"
    if key in ("attempt", "round"):
        return None if is_int(v, 1) else f"{key}: must be an integer >= 1"
    if key == "pr":
        return None if is_int(v, 1) and v <= 10**9 else "pr: must be an integer from 1 to 1000000000"
    if key == "findings":
        ok = isinstance(v, dict) and set(v) == FINDING_KEYS and all(is_int(n, 0) for n in v.values())
        return None if ok else "findings: must have exactly blocker, should, nit, security, each an integer >= 0"
    if key == "duration":
        try:
            ok = type(v) in (int, float) and math.isfinite(v) and v >= 0
        except OverflowError:  # an int too large for a float
            ok = False
        return None if ok else "duration: must be a finite number >= 0"
    if key in ("tier_raised", "quota_pause"):
        return None if type(v) is bool else f"{key}: must be a boolean"
    if key in ("ts", "date"):
        return f"{key}: set by the script, must not be supplied"
    return f"{key}: unknown field"


def encode(event, ts):
    return json.dumps({**event, "ts": ts}, separators=(",", ":"), ensure_ascii=True) + "\n"


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def validate(event):
    """Return the list of problems (empty when valid). Touches nothing."""
    if not isinstance(event, dict):
        return ["event: must be a JSON object"]
    problems = [f"{k}: required field missing" for k in REQUIRED if k not in event]
    problems += [p for k, v in event.items() if (p := check(k, v))]
    if not problems and len(encode(event, now()).encode()) > MAX_LINE:
        problems.append(f"event: encoded line exceeds {MAX_LINE} bytes")
    return problems


def open_dir(name, dir_fd=None):
    """Create (0700) or reuse a directory we own; return an fd. Refuses symlinks, files, foreign owners."""
    try:
        os.mkdir(name, 0o700, dir_fd=dir_fd)
    except FileExistsError:
        pass  # existing dir, or a parallel first writer won the race
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
            raise PermissionError(f"{name}: not a directory owned by the current user")
        os.fchmod(fd, 0o700)  # after mkdir, so the umask cannot widen it
        return fd
    except BaseException:
        os.close(fd)
        raise


def append(root, repo, event):
    """Validate, then append one line to <root>/<repo>/events.jsonl. ValueError on invalid input."""
    problems = [] if isinstance(repo, str) and TOKEN.fullmatch(repo) else [f"--repo: must match {TOKEN.pattern}"]
    problems += validate(event)
    if problems:
        raise ValueError("\n".join(problems))
    line = encode(event, now()).encode()
    root = Path(root)
    root.parent.mkdir(parents=True, exist_ok=True)
    root_fd = open_dir(root)
    try:
        repo_fd = open_dir(repo, root_fd)
    finally:
        os.close(root_fd)
    try:
        # O_NOFOLLOW: a symlinked log fails (ELOOP) and its target stays untouched
        fd = os.open("events.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
                     0o600, dir_fd=repo_fd)
    finally:
        os.close(repo_fd)
    try:
        if os.write(fd, line) != len(line):
            raise OSError("short write to events.jsonl")
    finally:
        os.close(fd)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Append one event line to the t3-build event log.")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--state-dir")
    a = ap.parse_args(argv)
    root = (a.state_dir or os.environ.get("T3_BUILD_STATE")
            or Path.home() / ".local" / "state" / "t3-build")
    try:
        event = json.loads(sys.stdin.read())
    except (ValueError, UnicodeDecodeError) as e:
        print(f"log_event.py: input is not JSON: {e.__class__.__name__}", file=sys.stderr)
        return 2
    except RecursionError:
        print("log_event.py: input is nested too deeply", file=sys.stderr)
        return 2
    try:
        append(root, a.repo, event)
    except ValueError as e:
        print("\n".join(f"log_event.py: {p}" for p in str(e).splitlines()), file=sys.stderr)
        return 2
    except OSError as e:
        print(f"log_event.py: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
