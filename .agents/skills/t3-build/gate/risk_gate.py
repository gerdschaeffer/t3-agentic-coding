#!/usr/bin/env python3
"""Risk gate: rate a PR on regression, security and user impact, then pick a lane.

The lane comes from the highest rating, never an average. Path rules in
risk-policy.toml set a deterministic floor; the trusted risk-rating comment
(the reviewer's assessment, posted by the owner) can only raise it.

Runs in GitHub Actions on pull_request_target with the BASE branch checked out,
so a PR can never change the gate that judges it. It reads the PR via the API
only and never executes PR code.
"""

import json
import os
import re
import subprocess
import tomllib
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

DIMS = ("regression", "security", "user")
RATING_MARK = "<!-- risk-rating sha="
CARD_MARK = "<!-- risk-gate-card -->"
# GitHub's PR files API returns at most 3000 files; a PR that size cannot be rated fully.
MAX_FILES = 3000
NO_RATING_REASON = "no rating needed: only user-documentation files"


def load_policy(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def matches(path, pattern):
    # A pattern starting with "/" is anchored at the repo root and its * stays in one directory.
    if pattern.startswith("/"):
        return PurePosixPath("/" + path).match(pattern)
    # Otherwise fnmatch's * also crosses "/"; the "/" prefix lets "*/x" mean x in any directory,
    # root included.
    return fnmatch(path, pattern) or fnmatch("/" + path, pattern)


def rate_files(files, policy, fold=False):
    """Deterministic floor from changed paths and diff size: (scores, reasons).

    fold lowercases paths and patterns: a case-insensitive checkout treats claude.md as CLAUDE.md.
    """
    return _score(_found(files, policy, fold))


def floor(files, policy):
    """The gate's floor: per dimension the higher of the exact and the case-folded pass."""
    return _score(_found(files, policy, False) + _found(files, policy, True))


def _found(files, policy, fold):
    key = str.lower if fold else (lambda s: s)
    found = []  # (dim, level, text)
    if len(files) >= MAX_FILES:
        found.append(("regression", 3, f"{len(files)} files: too large to rate, split the PR"))
    lines = sum(f["additions"] + f["deletions"] for f in files)
    limit = policy.get("limits", {}).get("small_diff_lines", 200)
    if lines > limit:
        found.append(("regression", 2, f"{lines} changed lines (limit {limit})"))
    for f in files:
        if f.get("link"):  # a symlink or submodule can point a low-rated path at anything
            found.append(("regression", 2, f"{f['path']} ({f['link']})"))
        # A rename is rated under both names: moving .github/x away must not escape its floor.
        for path in filter(None, (f["path"], f.get("previous"))):
            hits = [
                r
                for r in policy.get("rule", [])
                if any(matches(key(path), key(p)) for p in r["paths"])
            ]
            for rule in hits or [policy.get("default", {"regression": 1})]:
                why = rule.get("reason", "ordinary code")
                for dim in DIMS:
                    found.append((dim, rule.get(dim, 0), f"{path} ({why})"))
                if "deletions_only" in rule and f["additions"] == 0 and f["deletions"] > 0:
                    found.append(
                        (
                            "regression",
                            rule["deletions_only"],
                            f"{path} (only removes lines: {why})",
                        )
                    )
    return found


def _score(found):
    scores = dict.fromkeys(DIMS, 0)
    for dim, level, _ in found:
        scores[dim] = max(scores[dim], level)
    reasons = list(
        dict.fromkeys(  # dedupe, keep order; only what sets each score
            f"{dim} {level}: {text}" for dim, level, text in found if level and level == scores[dim]
        )
    )
    return scores, reasons


def canonical(name):
    """A path as Git writes it: no "./", "//", "..", "\\", trailing "/" or hidden characters."""
    return (
        bool(name)
        and not name.startswith("/")
        and str(PurePosixPath(name)) == name
        and ".." not in name.split("/")
        and "\\" not in name
        and name.isascii()  # ſ (U+017F) folds to s on some filesystems, not under str.lower
        and name.isprintable()
    )


def exempt_from_rating(files, policy):
    """True if the PR may skip the rating: every name canonical and on no_rating, floor zero."""
    table = policy.get("no_rating", {})
    paths = table.get("paths", []) if isinstance(table, dict) else None
    # Only anchored patterns: an unanchored * crosses "/" and would exempt specs and agent files.
    if not isinstance(paths, list) or not all(
        isinstance(p, str) and p.startswith("/") for p in paths
    ):
        raise SystemExit("policy no_rating is malformed; risk-gate stays pending")
    if not files or any(
        any(rate_files(files, policy, fold=fold)[0].values()) for fold in (False, True)
    ):
        return False
    # Binary, mode-only and empty files, and 0/0 renames, report 0 changed lines and would slip
    # past the size rule.
    return all(
        f["additions"] + f["deletions"] > 0
        and all(
            canonical(name) and any(matches(name, p) for p in paths)
            for name in filter(None, (f["path"], f.get("previous")))
        )
        for f in files
    )


def parse_rating(comments, sha, trusted):
    """Newest rating for exactly this head commit by a trusted login, or None.

    The marker must be at character 0, exactly what the workflow filter accepts.
    Ratings are append-only: withdrawing means posting a newer rating (verdict CHANGES).
    shortcut: deleting or editing a rating can expose an older one; only the owner
    identity can do that, which is inside the accepted relay risk (README).
    """
    marker = f"{RATING_MARK}{sha} -->"
    for c in reversed(comments):
        if c["login"] in trusted and c["body"].startswith(marker):
            fields = {}
            for line in c["body"].splitlines()[1:]:
                key, sep, value = line.partition(":")
                if sep:
                    fields[key.strip().lower()] = value.strip()
            try:
                levels = {d: int(fields[d]) for d in DIMS}
            except (KeyError, ValueError):
                return None  # malformed rating counts as no rating: fail closed
            if not all(0 <= v <= 3 for v in levels.values()):
                return None
            return {**fields, **levels}
    return None


def decide(floor, floor_reasons, rating, policy, draft=False, exempt=False):
    """Return (lane, scores, reasons, blockers). The rating can raise scores, never lower them."""
    scores, reasons, blockers = dict(floor), list(floor_reasons), []
    if draft:
        blockers.append("draft PR")
    if rating is None:
        # Re-check the floor here so a wrong exempt=True from any caller stays harmless.
        if exempt and not any(scores.values()):
            reasons.append(NO_RATING_REASON)
        else:
            blockers.append("no trusted risk rating for the head commit")
    else:
        for dim in DIMS:
            if rating[dim] > scores[dim]:
                scores[dim] = rating[dim]
                reasons.append(
                    f"{dim} {rating[dim]}: reviewer: {rating.get('reasons', 'no reason given')}"
                )
        if rating.get("verdict", "").upper() != "APPROVE":
            blockers.append(f"review verdict is {rating.get('verdict') or 'missing'}, not APPROVE")
        if rating.get("disputed", "none").lower() not in ("", "none"):
            blockers.append(f"disputed findings: {rating['disputed']}")
        if scores["security"] >= 1 and rating.get("security_pass", "").lower() != "clean":
            blockers.append("security pass is not clean")
    live = policy.get("live", False)
    level = max(scores["regression"], scores["security"], scores["user"] if live else 0)
    lane = "block" if level >= 3 else "review" if level == 2 or blockers else "auto"
    return lane, scores, reasons, blockers


def in_merge_hours(policy, now=None):
    merge = policy.get("merge", {})
    if "hours" not in merge:
        return True
    now = now or datetime.now(ZoneInfo(merge.get("timezone", "UTC")))
    start, end = merge["hours"]
    return start <= now.hour < end


CARD_REASONS = 12


def show_reason(r):
    """One bullet per reason, no HTML: non-printables to spaces, cut at 300, then escape '<'."""
    r = "".join(c if c.isprintable() else " " for c in r)
    if len(r) > 300:
        r = r[:297] + "..."
    return "- " + r.replace("<", "&lt;")


def card_reasons(reasons):
    """Up to CARD_REASONS reasons, keeping each dimension's first highest-level reason."""
    if len(reasons) <= CARD_REASONS:
        return [show_reason(r) for r in reasons]
    best = {}
    for i, r in enumerate(reasons):
        m = re.match(rf"({'|'.join(DIMS)}) (\d+): ", r)
        if m and (m[1] not in best or int(m[2]) > best[m[1]][0]):
            best[m[1]] = (int(m[2]), i)
    keep = {i for _, i in best.values()}
    for i in range(len(reasons)):
        if len(keep) == CARD_REASONS:
            break
        keep.add(i)
    shown = [show_reason(reasons[i]) for i in sorted(keep)]
    return shown + [f"- ... and {len(reasons) - len(keep)} more (run the gate's dry run for the full list)"]


def render_card(lane, mode, scores, reasons, blockers, sha, live, hours_ok):
    title = lane if mode == "enforce" else f"shadow, would be {lane}"
    user = f"{scores['user']}" + ("" if live else " (not live, ignored)")
    lines = [
        CARD_MARK,
        f"### Risk gate: **{title}**",
        "",
        "| Regression | Security | User impact |",
        "|---|---|---|",
        f"| {scores['regression']} | {scores['security']} | {user} |",
        "",
        f"Head `{sha[:12]}`. Lane = highest rating: 0-1 auto, 2 review by the owner, 3 block.",
    ]
    if reasons:
        lines += ["", "**Why**"] + card_reasons(reasons)
    if blockers:
        lines += ["", "**Not eligible for auto-merge**"] + [show_reason(b) for b in blockers]
    if lane == "auto" and mode == "enforce" and not hours_ok:
        lines += ["", "Outside merge hours: re-run the gate later or merge by hand."]
    return "\n".join(lines)


def gh(*args, check=True):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and r.returncode:  # show GitHub's reason (e.g. a missing token permission) in the log
        raise SystemExit(f"gh {' '.join(args[:2])} failed: {r.stderr.strip()}")
    return r.stdout


def gh_lines(path, jq):
    """Paginated REST list, one compact JSON object per line."""
    return [
        json.loads(line) for line in gh("api", path, "--paginate", "--jq", jq).splitlines() if line
    ]


def set_status(repo, sha, state, description):
    gh(
        "api",
        f"repos/{repo}/statuses/{sha}",
        "-f",
        f"state={state}",
        "-f",
        "context=risk-gate",
        "-f",
        f"description={description[:140]}",
    )


def main():
    repo, pr = os.environ["REPO"], os.environ["PR"]
    # Dry run: the leader's own read-only check. Any workflow can write a commit status,
    # so the leader decides from this, never from the risk-gate status.
    dry = os.environ.get("RISK_GATE_DRY_RUN") == "1"
    pr_info = json.loads(gh("pr", "view", pr, "--repo", repo, "--json", "headRefOid,isDraft"))
    sha = pr_info["headRefOid"]
    # Pending first: the required check stays unmet until this run finishes. Any crash below
    # leaves it pending, so nothing merges on a stale success (fail closed).
    if not dry:
        set_status(repo, sha, "pending", "rating")

    # RISK_GATE_POLICY: the leader's dry run reads main's policy without a checkout.
    policy_path = os.environ.get("RISK_GATE_POLICY") or Path(__file__).with_name("risk-policy.toml")
    policy = load_policy(policy_path)
    files = [
        {
            "path": f["filename"],
            "previous": f["previous_filename"],
            "additions": f["additions"],
            "deletions": f["deletions"],
        }
        for f in gh_lines(
            f"repos/{repo}/pulls/{pr}/files",
            ".[] | {filename, previous_filename, additions, deletions}",
        )
    ]
    # The files API has no file modes: read symlinks (120000) and submodules (160000) from the
    # head tree. A tree too large to list fully flags every file (fail closed).
    tree = json.loads(
        gh(
            "api",
            f"repos/{repo}/git/trees/{sha}?recursive=1",
            "--jq",
            '{truncated, links: [.tree[] | select(.mode | IN("120000", "160000")) | .path]}',
        )
    )
    for f in files:
        if tree["truncated"]:
            f["link"] = "repo tree too large to check file modes"
        elif f["path"] in tree["links"]:
            f["link"] = "symlink or submodule"
    # The one login whose ratings count; must match the workflow filter. Default: the owner of a
    # personal repo (an org owner is not a user, so org repos must set `rater`).
    trusted = [policy.get("rater") or repo.split("/")[0]]
    comments = [
        {"login": c["login"], "body": c["body"].replace("\r\n", "\n")}
        for c in gh_lines(f"repos/{repo}/issues/{pr}/comments", ".[] | {login: .user.login, body}")
    ]

    rating = parse_rating(comments, sha, trusted)
    exempt = exempt_from_rating(files, policy)  # always: it validates the policy
    marker = f"{RATING_MARK}{sha} -->"
    if any(c["login"] in trusted and c["body"].startswith(marker) for c in comments):
        exempt = False  # a rating comment, even a malformed one, may be the rater's veto
    if exempt and rating is None:
        # Fork PRs run too: without a rating only the trusted login may author an exempt PR.
        author = json.loads(gh("pr", "view", pr, "--repo", repo, "--json", "author"))
        exempt = (author.get("author") or {}).get("login") in trusted
    lane, scores, reasons, blockers = decide(
        *floor(files, policy), rating, policy, pr_info["isDraft"], exempt=exempt
    )
    method = policy.get("merge", {}).get("method", "squash")
    if method not in ("squash", "merge", "rebase"):
        raise SystemExit(f"unknown merge method {method!r}; risk-gate stays pending")
    mode, live, hours_ok = (
        policy.get("mode", "shadow"),
        policy.get("live", False),
        in_merge_hours(policy),
    )
    automerge = mode == "enforce" and lane == "auto" and hours_ok
    if dry:
        rated = f"R{scores['regression']} S{scores['security']} U{scores['user']}"
        print(f"{lane} {rated} head {sha[:12]}")
        for line in reasons + [f"not auto: {b}" for b in blockers]:
            print(f"  {line}")
        return

    if not automerge:  # a stale auto-merge must never survive a lane change or a new push
        gh(
            "pr", "merge", pr, "--repo", repo, "--disable-auto", check=False
        )  # errors when none is armed
        armed = json.loads(gh("pr", "view", pr, "--repo", repo, "--json", "autoMergeRequest"))
        if armed["autoMergeRequest"] is not None:
            raise SystemExit("could not disable auto-merge; risk-gate stays pending")
    shown = lane if mode == "enforce" else f"shadow: {lane}"
    summary = f"{shown} R{scores['regression']} S{scores['security']} U{scores['user']}"
    card = render_card(lane, mode, scores, reasons, blockers, sha, live, hours_ok)
    if not any(c["body"].strip() == card for c in comments):
        gh("pr", "comment", pr, "--repo", repo, "--body", card)
    set_status(
        repo, sha, "failure" if mode == "enforce" and lane == "block" else "success", summary
    )
    if automerge:
        gh("pr", "merge", pr, "--repo", repo, "--auto", f"--{method}", "--match-head-commit", sha)
    print(summary)


if __name__ == "__main__":
    main()
