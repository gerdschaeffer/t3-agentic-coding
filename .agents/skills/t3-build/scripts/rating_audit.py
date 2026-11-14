#!/usr/bin/env python3
"""Check a PR's rating comment against Sol's own reply (spec 024-fu17-rating-audit).

Usage: rating_audit.py --comment FILE --reply FILE [--security-reply FILE] [--sha SHA]
Exit 0 no softening, 1 at least one FAIL line, 2 bad input (stdout empty).
Reads three files, writes nothing. No network, no subprocess. Prints only fixed tokens and digits.
"""

import argparse
import re
import sys

MAX_BYTES = 256 * 1024
MARKER = re.compile(r"<!-- risk-rating sha=([0-9a-f]{40}) -->")
SHA = re.compile(r"[0-9a-f]{40}")
FIELDS = {
    "verdict": re.compile(r"^verdict: (APPROVE|CHANGES)$", re.M),
    "regression": re.compile(r"^regression: ([0-3])$", re.M),
    "security": re.compile(r"^security: ([0-3])$", re.M),
    "user": re.compile(r"^user: ([0-3])$", re.M),
    "security_pass": re.compile(r"^security_pass: (clean|n/a)$", re.M),
}
VERDICT = re.compile(r"VERDICT: (APPROVE|CHANGES)")
RISK = re.compile(r"RISK regression=([0-3]) security=([0-3]) user=([0-3])")
SECURITY = re.compile(r"(?:SECURITY|VERDICT): (CLEAN|FINDINGS)")
RATINGS = ("regression", "security", "user")


class Bad(Exception):
    pass


def lines(path, label):
    """Read at most MAX_BYTES; return the lines without line ends. Bad on any problem."""
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_BYTES + 1)
    except OSError as e:
        raise Bad(f"{label}: cannot read ({e.__class__.__name__})")
    if len(data) > MAX_BYTES:
        raise Bad(f"{label}: larger than {MAX_BYTES} bytes")
    # split on \n only, so other separators cannot start a line
    return [ln.rstrip("\r") for ln in data.decode("utf-8", errors="replace").split("\n")]


def last(rows, prefixes, pattern, label):
    """The last line starting with a prefix must fully match pattern."""
    found = [ln for ln in rows if ln.startswith(prefixes)]
    name = " or ".join(p.strip() for p in prefixes)
    if not found:
        raise Bad(f"{label}: no {name} line")
    m = pattern.fullmatch(found[-1].rstrip())
    if not m:
        raise Bad(f"{label}: last {name} line has a bad value")
    return m


def parse_comment(rows, sha):
    m = MARKER.fullmatch(rows[0])
    if not m:
        raise Bad("comment: first line is not the risk-rating marker with a 40 hex SHA")
    if sha is not None and m.group(1) != sha:
        raise Bad("comment: marker SHA differs from --sha")
    text = "\n".join(rows)
    out = {}
    for field, pat in FIELDS.items():
        fm = pat.search(text)
        if not fm:
            raise Bad(f"comment: {field} missing or out of range")
        out[field] = fm.group(1)
    return out


def audit(c, sol_verdict, sol_risk, sol_security):
    """Return (fail, output lines)."""
    out, fail = [], False

    def emit(kind, field, x, y):
        out.append(f"{kind} {field}: comment {x}, Sol {y}")

    if c["verdict"] != sol_verdict:
        bad = c["verdict"] == "APPROVE"
        fail |= bad
        emit("FAIL" if bad else "note", "verdict", c["verdict"], sol_verdict)
    for field, y in zip(RATINGS, sol_risk):
        x = c[field]
        if x != y:
            bad = x < y  # single digits compare correctly as strings
            fail |= bad
            emit("FAIL" if bad else "note", field, x, y)
    if c["security_pass"] == "clean" and sol_security != "CLEAN":
        fail = True
        emit("FAIL", "security_pass", "clean", sol_security)
    return fail, out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Audit a rating comment against Sol's reply.")
    ap.add_argument("--comment", required=True)
    ap.add_argument("--reply", required=True)
    ap.add_argument("--security-reply")
    ap.add_argument("--sha")
    a = ap.parse_args(argv)
    try:
        if a.sha is not None and not SHA.fullmatch(a.sha):
            raise Bad("--sha: must be 40 lower-case hex")
        c = parse_comment(lines(a.comment, "comment"), a.sha)
        rows = lines(a.reply, "reply")
        verdict = last(rows, ("VERDICT:",), VERDICT, "reply").group(1)
        risk = last(rows, ("RISK ",), RISK, "reply").groups()
        security = "unverified"
        if a.security_reply is not None:
            srows = lines(a.security_reply, "security reply")
            security = last(srows, ("SECURITY:", "VERDICT:"), SECURITY, "security reply").group(1)
    except Bad as e:
        print(f"rating_audit.py: {e}", file=sys.stderr)
        return 2
    fail, out = audit(c, verdict, risk, security)
    for ln in out:
        print(ln)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
