#!/usr/bin/env python3
"""Print the calibration retro report for one repo as Markdown (spec 008-wp8-retro-report).

Usage: retro_report.py --repo NAME [--state-dir DIR] [--git PATH] [--since YYYY-MM-DD]
                       [--ci-runs FILE] [--today YYYY-MM-DD]
Exit 0 report printed, 2 bad argument or unreadable --ci-runs, 1 I/O or permission problem on the log.
Read only: writes nothing, no network, git only with read commands and an argument list.
"""

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True  # importing log_event must not create __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent))
from log_event import MAX_LINE, TOKEN, validate  # noqa: E402

MAX_INT = 10**9       # a larger integer in an event is malformed (no real count comes near it)
MIN_POINTS = 20      # fewer data points: keep collecting, no proposal
RATE_DOWN = 0.90      # M1 rate above: try one tier down
RATE_UP = 0.60        # M1 rate below: one tier up
SHARE = 0.30          # share for M2 at budget, rule 5 blocked, rule 6 attempts
ROUND_BUDGET = 3      # rounds (M2, rule 7) and attempts (rule 6) counted as "at budget"
NO_BLOCKER = 0.90     # rule 7 share of late rounds without blocker
WEEK_HITS = 3         # rule 4 limit hits in one ISO week

TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
REVERT = re.compile(r"^This reverts commit ([0-9a-f]{40})\b", re.M)
TIERS = ("T0", "T1", "T2")


def day(s):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {s!r}")
    try:
        date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a valid date: {s!r}") from None
    return s


def repo_name(s):
    if not TOKEN.fullmatch(s):
        raise argparse.ArgumentTypeError(f"must match {TOKEN.pattern}")
    return s


def log_lines(path):
    """Yield each line without its newline; None for a line over MAX_LINE (drained, never kept).
    A missing file yields nothing; a symlink or other non-regular file raises OSError."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
        while raw := f.readline(MAX_LINE + 2):
            if len(raw) == MAX_LINE + 2 and not raw.endswith(b"\n"):
                while (chunk := f.readline(1 << 16)) and not chunk.endswith(b"\n"):
                    pass
                yield None
            else:
                yield raw[:-1] if raw.endswith(b"\n") else raw


def read_log(path, since):
    """Return (events sorted by ts, malformed count). Missing file: no events. OSError propagates."""
    events, bad = [], 0
    for raw in log_lines(path):
        try:
            if raw is None or len(raw) > MAX_LINE:
                raise ValueError
            e = json.loads(raw.decode("utf-8"))
            ts = e.pop("ts")  # also raises for a non-object
            # ts is required here: --since, ordering and M4 need it
            if not isinstance(ts, str) or not TS.fullmatch(ts) or validate(e):
                raise ValueError
            ints = [v for v in e.values() if type(v) is int] + list(e.get("findings", {}).values())
            if any(v > MAX_INT for v in ints):
                raise ValueError
            date.fromisoformat(ts[:10])
        # RecursionError (deeply nested JSON) and OverflowError (huge numbers) count as malformed
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError, ArithmeticError):
            bad += 1
            continue
        e["ts"] = ts
        if not since or ts[:10] >= since:
            events.append(e)
    events.sort(key=lambda e: e["ts"])  # stable: equal ts keep file order
    return events, bad


def git(path, *args):
    return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True,
                          errors="replace", check=True).stdout


def is_worktree(path):
    try:
        return git(path, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except (OSError, subprocess.CalledProcessError):
        return False


def read_git(path):
    """All commits on HEAD as dicts (sha, date, subject, agent, tier, reverts) and the first-parent shas."""
    # Agent:/Tier: sit in their own paragraph before Co-Authored-By: (references/commit-trailers.md), which
    # git's trailer parser ignores, so the lines are read from the message body.
    fmt = "%H%x1f%cs%x1f%s%x1f%B"
    try:
        # --no-show-signature: a hostile repo config (log.showSignature, gpg.program) must not run a program
        out = git(path, "log", "-z", "--no-show-signature", f"--format={fmt}", "HEAD")
        first = set(git(path, "rev-list", "--first-parent", "HEAD").split())
    except subprocess.CalledProcessError:  # no commit yet
        return [], set()
    commits = []
    for rec in out.split("\0"):
        if not rec.strip():
            continue
        sha, cdate, subject, body = rec.split("\x1f", 3)
        tier = re.search(r"^Tier: *(\S+)", body, re.M)
        tier = tier.group(1) if tier else ""
        commits.append({"sha": sha.strip(), "date": cdate, "pr": subject.startswith("Merge pull request #"),
                        "agent": bool(re.search(r"^Agent: ", body, re.M)),
                        "tier": tier if tier in TIERS else "unknown",
                        "has_tier": bool(tier), "reverts": REVERT.findall(body)})
    return commits, first


def table(header, body):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return out + ["| " + " | ".join(str(c) for c in r) + " |" for r in body]


def by_slice(events):
    s = defaultdict(list)
    for e in events:
        s[e["slice"]].append(e)
    return s


def m1(slices):
    """{(role, tier, model): [n, approved]} from the earliest round-1 review against the latest product."""
    acc = defaultdict(lambda: [0, 0])
    for evs in slices.values():
        for review, product in (("spec-review", "spec"), ("code-review", "implement")):
            first = next((e for e in evs if e["stage"] == review and e.get("round") == 1), None)
            if not first:
                continue
            judged = [e for e in evs if e["stage"] == product]
            role, model = (judged[-1]["role"], judged[-1]["model"]) if judged else ("unknown", "unknown")
            a = acc[(role, first["tier"], model)]
            a[0] += 1
            a[1] += first.get("verdict") == "APPROVE"
    return acc


def m1_result(n, rate):
    if n < MIN_POINTS:
        return f"keep collecting (n={n})"
    return "try one tier down" if rate > RATE_DOWN else "one tier up" if rate < RATE_UP else "keep"


def m2(slices):
    """{tier: [highest round per slice]}; the tier is that of the slice's highest-round code-review."""
    acc = defaultdict(list)
    for evs in slices.values():
        cr = [e for e in evs if e["stage"] == "code-review" and "round" in e]
        if cr:
            top = max(cr, key=lambda e: e["round"])
            acc[top["tier"]].append(top["round"])
    return acc


def m2_result(slices, at):
    if slices < MIN_POINTS:
        return "keep collecting"
    return "specs too vague" if at / slices >= SHARE else "keep"


def week(ts):
    y, w, _ = date.fromisoformat(ts[:10]).isocalendar()
    return f"{y}-W{w:02d}"


def report(a, events, bad, gitdata, ci):
    slices = by_slice(events)
    out = [f"# Retro report {a.repo} {a.today}", "",
           f"Events: {len(events)} (malformed skipped: {bad}). Slices: {len(slices)}.", "",
           "## Cross-check with git"]
    m3 = None
    if gitdata is None:
        out.append("git: not available")
    else:
        commits, first = gitdata
        tier_of = {c["sha"]: c["tier"] for c in commits}  # all history: a revert may target an older commit
        kept = [c for c in commits if not a.since or c["date"] >= a.since]
        kept_first = first & {c["sha"] for c in kept}  # --since also limits the CI failures counted
        ships = len({e["slice"] for e in events if e["stage"] == "ship"})
        prs = sum(c["pr"] for c in kept)
        agent = sum(c["agent"] for c in kept)
        no_tier = sum(c["agent"] and not c["has_tier"] for c in kept)
        reverted = [sha for c in kept for sha in c["reverts"]]
        failed = ("n/a (no --ci-runs)" if ci is None else
                  sum(isinstance(r, dict) and r.get("conclusion") == "failure"
                      and isinstance(r.get("sha"), str) and r["sha"] in kept_first for r in ci))
        out += table(["item", "log", "git"], [
            ["pull requests merged", ships, prs], ["commits with Agent trailer", "-", agent],
            ["agent commits without Tier trailer", "-", no_tier], ["reverts", "-", len(reverted)],
            ["failed checks after merge", "-", failed]])
        if prs > ships:
            out.append(f"FLAG: git has {prs} merged pull requests, the log {ships} shipped slices: "
                       "the log may be incomplete.")
        if agent and not events:
            out.append(f"FLAG: {agent} agent commits exist but the log has no events.")
        if no_tier:
            out.append(f"FLAG: {no_tier} agent commits have no Tier: trailer.")
        m3 = Counter({k: 0 for k in TIERS + ("unknown",)})
        m3.update(tier_of.get(sha, "unknown") for sha in reverted)

    props = []
    out += ["", "## M1 first-pass approval"]
    acc = m1(slices)
    body = []
    for (role, tier, model), (n, ok) in sorted(acc.items()):
        res = m1_result(n, ok / n)
        body.append([role, tier, model, n, ok, f"{ok / n:.2f}", res])
        if res in ("try one tier down", "one tier up"):
            props.append(["M1 first-pass approval", f"{role} {tier} {model}: {ok}/{n} = {ok / n:.2f}",
                          f"{res} for {role} {tier}"])
    out += table(["role", "tier", "model", "n", "approved", "rate", "result"], body) if body else ["keep collecting"]

    out += ["", "## M2 review rounds per slice"]
    body = []
    for tier, rounds in sorted(m2(slices).items()):
        n, at = len(rounds), sum(r >= ROUND_BUDGET for r in rounds)
        res = m2_result(n, at)
        body.append([tier, n, f"{sum(rounds) / n:.2f}", at, res])
        if res == "specs too vague":
            props.append(["M2 review rounds", f"{tier}: {at}/{n} slices at {ROUND_BUDGET} or more rounds", res])
    out += table(["tier", "slices", "mean rounds", "at budget", "result"], body) if body else ["keep collecting"]

    out += ["", "## M3 escaped defects per tier"]
    if m3 is None:
        out.append("git: not available")
    else:
        out += table(["tier", "reverts"], list(m3.items()))
        if m3["T0"] or m3["T1"]:
            props.append(["M3 escaped defects", f"reverts of T0 {m3['T0']}, T1 {m3['T1']}",
                          "extend the T2 trigger list"])

    out += ["", "## M4 limit hits per ISO week"]
    weeks = Counter(week(e["ts"]) for e in events if e.get("quota_pause") is True)
    out += table(["week", "hits"], sorted(weeks.items()))
    busy = [f"{w}: {h}" for w, h in sorted(weeks.items()) if h >= WEEK_HITS]
    if busy:
        props.append(["M4 limit hits", ", ".join(busy), "shift heavy jobs, reduce parallel workers"])

    out += ["", "## M5 OpenRouter spend per task", "not recorded in the event log"]

    gates = [e for e in events if e["stage"] == "human-gate"]
    blocked = sum(e.get("outcome") == "blocked" for e in gates)
    if len(gates) >= MIN_POINTS and blocked / len(gates) >= SHARE:
        props.append(["human gates blocked", f"{blocked}/{len(gates)}", "improve the decision brief"])
    work = [e for e in events if e["stage"] in ("implement", "tests")]
    high = sum(e.get("attempt", 0) >= ROUND_BUDGET for e in work)
    if len(work) >= MIN_POINTS and high / len(work) >= SHARE:
        props.append(["attempts", f"{high}/{len(work)} implement and tests events at attempt {ROUND_BUDGET} or more",
                      "slices too large or specs too vague"])
    late = defaultdict(list)
    for e in events:
        if e["stage"] == "code-review" and e.get("round", 0) >= ROUND_BUDGET:
            late[e["tier"]].append(e.get("findings", {}).get("blocker") == 0)
    for tier, zero in sorted(late.items()):
        if len(zero) >= MIN_POINTS and sum(zero) / len(zero) >= NO_BLOCKER:
            props.append(["late rounds without blocker",
                          f"{tier}: {sum(zero)}/{len(zero)} code-review events at round {ROUND_BUDGET} or more",
                          f"lower the round budget for that tier ({tier})"])

    out += ["", "## Proposed changes"]
    out += table(["signal", "evidence", "proposal"], props) if props else ["none: keep collecting"]
    out += ["", "Signals that need finding text (same finding returns, reviewer findings rejected) "
                "are not in the event log."]
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Print the t3-build retro report as Markdown.")
    ap.add_argument("--repo", required=True, type=repo_name)
    ap.add_argument("--state-dir")
    ap.add_argument("--git")
    ap.add_argument("--since", type=day)
    ap.add_argument("--ci-runs")
    ap.add_argument("--today", type=day, default=datetime.now(timezone.utc).date().isoformat())
    a = ap.parse_args(argv)

    ci = None
    if a.ci_runs is not None:
        try:
            ci = json.loads(Path(a.ci_runs).read_text())
        except (OSError, ValueError, RecursionError) as e:
            print(f"retro_report.py: --ci-runs unreadable: {e.__class__.__name__}", file=sys.stderr)
            return 2
        if not isinstance(ci, list):
            print("retro_report.py: --ci-runs must be a JSON list", file=sys.stderr)
            return 2

    path = a.git or os.getcwd()
    if is_worktree(path):
        gitdata = read_git(path)
    elif a.git is not None:
        print("retro_report.py: --git is not a git work tree", file=sys.stderr)
        return 2
    else:
        gitdata = None

    root = a.state_dir or os.environ.get("T3_BUILD_STATE") or Path.home() / ".local" / "state" / "t3-build"
    try:
        events, bad = read_log(Path(root) / a.repo / "events.jsonl", a.since)
    except OSError as e:
        print(f"retro_report.py: cannot read the event log: {e.__class__.__name__}", file=sys.stderr)
        return 1
    sys.stdout.write(report(a, events, bad, gitdata, ci))
    return 0


if __name__ == "__main__":
    sys.exit(main())
