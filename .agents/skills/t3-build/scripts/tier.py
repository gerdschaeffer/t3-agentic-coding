#!/usr/bin/env python3
"""Tier a task from the paths it will touch: human, T2, T0 or T1 (spec 002-wp2-tier-step).

Usage: tier.py [--policy FILE] [--trigger NAME]... -- PATH...
Put "--" before the paths so a path starting with "-" is not read as an option.
A non-ASCII path is tiered at least T2; guardrails also match its NFKC-casefold form (spec 010).
Passes no file status, so a test path is not rated as a changed existing test (the gate does that
on the finished PR); a spec or runbook path rates 2 and is T2 (spec 022).
Reuses the risk gate's matching, floor and no_rating check. No network, no subprocess, no writes.
"""

import argparse
import re
import sys
import tomllib
import unicodedata
from pathlib import Path

GATE = Path(__file__).resolve().parent.parent / "gate"
sys.path.insert(0, str(GATE))  # only this fixed directory, never the policy's
import risk_gate as g  # noqa: E402

# The gate rule "CI, deploy, gate or agent config" minus its deploy patterns (those are T2),
# plus the gate policy anywhere and agent definitions waiting for the human.
GUARDRAIL = (
    ".github/*", "*/AGENTS.md", "*/AGENTS.override.md", "*/AGENT.md", "*/CLAUDE.md",
    "*/CLAUDE.local.md", "*/GEMINI.md", "*/WARP.md", "*/CRUSH.md", "*/CRUSH.local.md",
    "*/QWEN.md", "*/.claude/*", "*/.codex/*", "*/.opencode/*", "*/.agents/*", "*/.mcp.json",
    "*/opencode.json", "*/opencode.jsonc", "*/.cursor/*", "*/.cursorrules", "*/.cursorignore", "*/risk-policy.toml", "staging/*",
)
TRIGGERS = ("dependency", "interface", "schema", "concurrency", "untrusted-input")


def classify(paths, policy, triggers=()):
    """Return (tier, reasons). ValueError on bad input; SystemExit on a malformed no_rating."""
    paths, triggers = list(paths), list(triggers)
    if not paths:
        raise ValueError("no paths given")
    # canonical except for non-ASCII: an ASCII-safe copy checks the shape, isprintable the rest.
    # The folded forms must keep the shape too: U+2025 folds to "..", U+FF0F to "/" (spec 010).
    bad = [p for p in paths
           if not all(g.canonical(re.sub(r"[^\x00-\x7f]", "x", f)) and f.isprintable()
                      for f in (p, unicodedata.normalize("NFKC", p), unicodedata.normalize("NFKC", p).casefold()))]
    if bad:
        raise ValueError(f"not a canonical repo-relative path: {bad[0]!r}")
    unknown = [t for t in triggers if t not in TRIGGERS]
    if unknown:
        raise ValueError(f"unknown trigger {unknown[0]!r}; allowed: {', '.join(TRIGGERS)}")
    # Every file, before any decision: a malformed no_rating must fail closed in every case.
    exempt = all([
        g.exempt_from_rating([{"path": p, "additions": 1, "deletions": 0}], policy) for p in paths
    ])
    human = [p for p in paths
             if any(g.matches(f, x.lower()) for x in GUARDRAIL
                    for f in (p.lower(), unicodedata.normalize("NFKC", p).casefold()))]
    if human:
        return "human", [f"guardrail file: {p}" for p in human]
    high = []
    for p in paths:
        for fold in (False, True):
            scores, why = g.rate_files([{"path": p, "additions": 0, "deletions": 0}], policy, fold)
            if max(scores.values()) >= 2:
                high.append(f"floor {max(scores.values())}: {p} ({'; '.join(why)})")
                break
    wide = [f"non-ASCII path: {p}" for p in paths if not p.isascii()]
    if high or triggers or wide:
        return "T2", high + [f"trigger: {t}" for t in triggers] + wide
    if exempt:
        return "T0", ["all files on no_rating list"]
    return "T1", ["default: ordinary files: " + ", ".join(paths)]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tier a task from the paths it will touch.")
    ap.add_argument("--policy", help="risk-policy.toml (default: the repo's, else the gate's)")
    ap.add_argument("--trigger", action="append", default=[], choices=TRIGGERS)
    ap.add_argument("paths", nargs="*")
    a = ap.parse_args(argv)
    local = Path(".github/risk-gate/risk-policy.toml")
    path = a.policy or (local if local.is_file() else GATE / "risk-policy.toml")
    try:
        tier, reasons = classify(a.paths, g.load_policy(path), a.trigger)
    except (ValueError, OSError, tomllib.TOMLDecodeError) as e:
        print(f"tier.py: {e}", file=sys.stderr)
        return 2
    print("\n".join([tier] + [f"  {r}" for r in reasons]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
