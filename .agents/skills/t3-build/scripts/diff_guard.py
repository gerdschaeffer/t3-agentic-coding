#!/usr/bin/env python3
"""Fail when a branch removes test files or assertions without a spec change (spec 015-wp9-ci, section 6).

Usage: diff_guard.py --base REV --head REV [--repo DIR] [--spec PATTERN]...

The diff runs from `git merge-base BASE HEAD` to HEAD. Findings: (a) a test file deleted, or renamed
to a non-test name; (a2) a test file that changes type (status T, a symlink or gitlink on a test
path, a text test file that becomes binary); (b) a test file whose removed assertion lines outnumber
its added ones. Any added, modified or renamed spec file turns the findings into a notice (exit 0).
Exit 0: pass. Exit 1: a finding, or input over a bound. Exit 2: bad arguments, not a repository, an
unknown revision, or a git timeout (stdout empty, message on stderr).

Limits: this is a heuristic, not proof. False positives: an assertion moved to another file, a test
file renamed to a non-test name, a removed fixture under tests/, an assertion rewritten into a
helper. Any qualifying spec change, however small or unrelated, authorizes every finding; the human
decides whether it is relevant. False negatives: an equal-count rewrite (`assert x == 1` to
`assert True`), an assertion style the table does not list, test logic weakened without touching
assertion lines. Two paths that differ only by case are not handled specially. The human reviews
the diff in every case.
"""

import argparse
import os
import re
import select
import signal
import subprocess
import sys
import time
from fnmatch import fnmatch
from pathlib import PurePosixPath

GIT = "git"
MAX_FILES = 3000
MAX_BYTES = 5_000_000  # git output per call
MAX_LINE = 4096
GIT_TIMEOUT = 60  # seconds per git call
GIT_FLAGS = ["-c", "core.fsmonitor=false", "-c", "diff.external=", "-c", "core.pager=cat", "--no-pager"]
DIFF_FLAGS = ["--no-ext-diff", "--no-textconv", "--no-color"]
REV = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}")
SHA = re.compile(rb"[0-9a-f]{40}|[0-9a-f]{64}")
# Copy of the `tests` rule in risk-policy.toml (a test checks the drift).
TEST_PATTERNS = ["*/tests/*", "*/test/*", "*/test_*", "*_test.*", "*.test.*", "*.spec.*"]
SPEC_PATTERNS = ["*/docs/specs/*", "*/docs/*/spec.md", "*/docs/*.spec.md"]
COMMENT = ("#", "//", "/*", "*", "--", "<!--")
ASSERTION = [re.compile(p) for p in (
    r"^assert\b",
    r"\bassert\w*\s*[(!]",
    r"\b(assert|require)\.\w+\(",
    r"\bexpect\s*\(",
    r"\.should\b|\bshould\.",
    r"\bt\.(Error|Errorf|Fatal|Fatalf|Fail|FailNow)\(",
    r"\b(pytest\.raises|self\.fail)\(",
)]
SPECIAL_MODES = ("120000", "160000")  # symlink, gitlink
REGULAR_MODES = ("100644", "100755")


def plain(value):
    """One line of plain text: every non-printable character becomes a space; cut at 200."""
    s = "".join(c if c.isprintable() else " " for c in str(value))
    return s if len(s) <= 200 else s[:200] + "..."


def die(message):
    print(f"diff_guard: {message}", file=sys.stderr)
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
    try:
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             env=env, start_new_session=True)
    except OSError:
        die("cannot run git")
    deadline, out, code = time.monotonic() + GIT_TIMEOUT, bytearray(), None
    try:
        fd = p.stdout.fileno()
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                die("git timed out")
            if not select.select([fd], [], [], left)[0]:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            out += chunk
            if len(out) > MAX_BYTES:
                too_large("git output", f"{MAX_BYTES} bytes")
        try:
            code = p.wait(timeout=max(deadline - time.monotonic(), 0.01))
        except subprocess.TimeoutExpired:
            die("git timed out")
    finally:
        if p.poll() is None or code is None:  # timeout or overflow: git and everything it started
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.wait()
        p.stdout.close()
    if code:
        die(f"git {args[0]} failed (exit {code}): not a repository or unknown revision")
    return bytes(out)


def matches(path, pattern):
    """risk_gate.matches: a leading / anchors and its * stays in one folder; otherwise * crosses /."""
    if pattern.startswith("/"):
        return PurePosixPath("/" + path).match(pattern)
    return fnmatch(path, pattern) or fnmatch("/" + path, pattern)


def any_match(path, patterns):
    return any(matches(path.lower(), p.lower()) for p in patterns)


def raw_entries(out):
    """[(status letter, old mode, new mode, old sha, new sha, old path, new path)] from diff -z --raw."""
    toks, entries, i = out.split(b"\0"), [], 0
    while i < len(toks) and toks[i]:
        meta = toks[i].split(b" ")
        if len(meta) != 5 or not meta[0].startswith(b":") or not meta[4]:
            die("unexpected git diff output")
        letter = chr(meta[4][0])
        n = 2 if letter in "RC" else 1
        paths = [os.fsdecode(t) for t in toks[i + 1:i + 1 + n]]
        if len(paths) != n or not all(paths) or not (SHA.fullmatch(meta[2]) and SHA.fullmatch(meta[3])):
            die("unexpected git diff output")
        entries.append((letter, meta[0][1:].decode(), meta[1].decode(), meta[2].decode(), meta[3].decode(),
                        paths[0], paths[-1]))
        i += 1 + n
        if len(entries) > MAX_FILES:
            too_large("changed files", MAX_FILES)
    return entries


def is_binary(data):
    return b"\0" in data[:8000]  # git's own content rule (buffer_is_binary)


def count(diff):
    """(removed, added) assertion lines in a -U0 diff of one file; headers before the first @@ skipped."""
    removed = added = 0
    in_hunk = False
    for line in diff.split(b"\n"):
        if line.startswith(b"@@"):
            in_hunk = True
            continue
        if not in_hunk or line[:1] not in (b"-", b"+"):
            continue
        text = line[1:1 + MAX_LINE].decode("utf-8", "replace").strip()
        if text.startswith(COMMENT) or not any(p.search(text) for p in ASSERTION):
            continue
        if line[:1] == b"-":
            removed += 1
        else:
            added += 1
    return removed, added


def shrink(repo, mb, head, old_sha, new_sha, old, new):
    """None, "type" (text became binary) or (removed, added) for a text-to-text change.

    Binary is decided from the blobs, never from the patch (a `diff` attribute hides "Binary files").
    A blob over MAX_BYTES fails the run as too large (run_git's bound).
    """
    if is_binary(run_git(["cat-file", "blob", old_sha], repo)):
        return None
    if is_binary(run_git(["cat-file", "blob", new_sha], repo)):
        return "type"
    return count(run_git(["diff", "-U0", "-M", "--text", *DIFF_FLAGS, mb, head, "--",
                          ":(literal)" + old, ":(literal)" + new], repo))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fail when tests or assertions are removed without a spec change.")
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--spec", action="append", metavar="PATTERN")
    args = ap.parse_args(argv)
    for flag in ("base", "head"):
        if not REV.fullmatch(getattr(args, flag)):
            die(f"--{flag}: not a valid revision")
    specs = args.spec or SPEC_PATTERNS
    repo, head = args.repo, args.head

    def is_spec(path):
        return any_match(path, specs)

    def is_test(path):
        return not is_spec(path) and any_match(path, TEST_PATTERNS)

    mb = run_git(["merge-base", args.base, head], repo).strip()
    if not SHA.fullmatch(mb):
        die("unexpected git merge-base output")
    mb = mb.decode()
    entries = raw_entries(run_git(["diff", "-z", "--raw", "--no-abbrev", "-M", *DIFF_FLAGS, mb, head, "--"], repo))

    findings, spec_changed, checked = [], None, 0
    for letter, old_mode, new_mode, old_sha, new_sha, old, new in entries:
        if spec_changed is None and letter in "AMR" and is_spec(new):
            spec_changed = new
        old_test, new_test = letter != "A" and is_test(old), letter != "D" and is_test(new)
        if not (old_test or new_test):
            continue
        checked += 1
        if letter == "D" or (letter == "R" and old_test and not new_test):
            findings.append(f"removed test file: {plain(old)}")
        elif new_test and (letter == "T" or new_mode in SPECIAL_MODES):
            findings.append(f"test file type changed: {plain(new)}")
        elif letter == "T" and old_test:
            findings.append(f"test file type changed: {plain(old)}")
        elif letter in "MR" and old_test and new_test and old_mode in REGULAR_MODES and new_mode in REGULAR_MODES:
            result = shrink(repo, mb, head, old_sha, new_sha, old, new)
            if result == "type":
                findings.append(f"test file type changed: {plain(new)}")
            elif result and result[0] > result[1]:
                findings.append(f"assertions shrink: {plain(new)} (-{result[0]} +{result[1]})")

    for f in findings:
        print(f)
    if findings and spec_changed is not None:
        print(f"notice: tests removed or assertions shrink; spec changed: {plain(spec_changed)}")
        return 0
    if findings:
        return 1
    print(f"diff guard: {checked} test files checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
