"""Acceptance tests for retro_report.py (spec 008-wp8-retro-report). One TestCase class per AC.

Run from this folder:  python3 -m unittest test_retro_report -v
Fixtures live in temp dirs only. The script is called by subprocess; nothing is imported from it.

Section headings and the exact FLAG / evidence wording are not fixed by the spec, so tables are
found by their header row (spec 4.2 / 4.1 / 4.3) and results are compared as exact cell strings.
Every place where the spec leaves a choice is marked AMBIGUITY and takes the most literal reading.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tokenize
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SCRIPT = SCRIPTS / "retro_report.py"
REPO = "demo"
TODAY = "2026-03-01"
ZERO = {"blocker": 0, "should": 0, "nit": 0, "security": 0}
ONE_BLOCKER = {"blocker": 1, "should": 0, "nit": 0, "security": 0}
AGENT = "Agent: implementer / claude-opus-5-5 / medium"

H_X = ["item", "log", "git"]
H_M1 = ["role", "tier", "model", "n", "approved", "rate", "result"]
H_M2 = ["tier", "slices", "mean rounds", "at budget", "result"]
H_M3 = ["tier", "reverts"]
H_M4 = ["week", "hits"]
H_P = ["signal", "evidence", "proposal"]
EMPTY_LOG_LINE = "Events: 0 (malformed skipped: 0). Slices: 0."


# ---------------------------------------------------------------- parsing helpers

def cells(line):
    s = line.strip()[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def parse_tables(out):
    """All Markdown tables of the output as (header cells, body rows); separator rows dropped."""
    tabs, cur = [], []
    for line in out.splitlines() + [""]:
        if line.lstrip().startswith("|"):
            cur.append(cells(line))
        elif cur:
            tabs.append(cur)
            cur = []
    res = []
    for t in tabs:
        body = [c for c in t[1:] if not all(re.fullmatch(r":?-+:?", x) for x in c)]
        res.append((t[0], body))
    return res


def rows(out, header, allow_absent=False):
    for head, body in parse_tables(out):
        if head == header:
            return body
    if allow_absent:
        return None
    raise AssertionError(f"no table with header {header} in stdout:\n{out}")


def find(table_rows, *key):
    hit = [r for r in table_rows if r[:len(key)] == list(key)]
    if len(hit) != 1:
        raise AssertionError(f"expected exactly one row starting {list(key)}, got {hit} in {table_rows}")
    return hit[0]


def proposals(out):
    return rows(out, H_P, allow_absent=True) or []


def fired(out, text):
    return [r for r in proposals(out) if text in r[2]]


def flags(out):
    return [l for l in out.splitlines() if l.startswith("FLAG:")]


def normalise(text):
    """For the golden test: drop blank lines, separator rows, section headings (other than the
    title line) and the free-wording 'finding text' note; canonicalise table cells; sort the body
    rows of each table (row order inside a table is not fixed by the spec)."""
    out, block, first = [], [], True

    def flush():
        if block:
            head, *body = block
            out.append("| " + " | ".join(head) + " |")
            out.extend(sorted("| " + " | ".join(r) + " |" for r in body))
            block.clear()

    for line in text.splitlines():
        line = line.rstrip()
        if line.startswith("|"):
            c = cells(line)
            if not all(re.fullmatch(r":?-+:?", x) for x in c):
                block.append(c)
            continue
        flush()
        if not line.strip():
            continue
        if line.startswith("#") and not first:
            continue
        if "finding text" in line.lower():
            continue
        first = False
        out.append(line)
    flush()
    return out


# ---------------------------------------------------------------- event helpers

def T(hour, minute=0, day="2026-01-05"):
    return f"{day}T{hour:02d}:{minute:02d}:00Z"


def ev(slice_, stage, role="lead", model="m1", tier="T1", ts=None, **extra):
    d = {"slice": slice_, "tier": tier, "stage": stage, "role": role, "model": model,
         "effort": "high", "ts": ts or T(10)}
    d.update(extra)
    return d


def review(slice_, stage, rnd, verdict, tier="T1", ts=None, role=None, model="r1", **extra):
    role = role or ("spec-reviewer" if stage == "spec-review" else "code-reviewer")
    return ev(slice_, stage, role, model, tier, ts or T(11), round=rnd, verdict=verdict, **extra)


def spec_batch(prefix, n, approved, role="spec-writer", model="m1", tier="T1"):
    """n slices, each: one spec event by role/model, one round-1 spec-review; the first `approved` APPROVE."""
    evs = []
    for i in range(n):
        s = f"{prefix}{i}"
        evs.append(ev(s, "spec", role, model, tier, ts=T(10)))
        evs.append(review(s, "spec-review", 1, "APPROVE" if i < approved else "CHANGES", tier))
    return evs


def rounds_batch(prefix, tier, rounds_list, findings=ZERO):
    """One slice per entry; the slice gets code-review events with round 1..r."""
    evs = []
    for i, r in enumerate(rounds_list):
        for k in range(1, r + 1):
            evs.append(review(f"{prefix}{i}", "code-review", k, "CHANGES" if k < r else "APPROVE",
                              tier, ts=T(11, k), findings=findings))
    return evs


def ships(*slices):
    return [ev(s, "ship", ts=T(12)) for s in slices]


def snapshot(root):
    """Names (all), size and mtime (outside .git; inside .git names only) of everything under root."""
    out = {}
    for dp, dn, fn in os.walk(root):
        for name in dn + fn:
            p = os.path.join(dp, name)
            rel = os.path.relpath(p, root)
            st = os.lstat(p)
            out[rel] = None if ".git" in rel.split(os.sep) else (st.st_size, st.st_mtime_ns)
    return out


class GitRepo:
    def __init__(self, path, env):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.env = dict(env)
        self.env.update(GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                        GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
        self.git("init", "-q")
        self.git("symbolic-ref", "HEAD", "refs/heads/main")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args, input=None, date=None):
        env = dict(self.env)
        if date:
            env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"{date}T12:00:00+0000"
        cp = subprocess.run(["git", "-C", str(self.path), *args], env=env, input=input,
                            capture_output=True, text=True, check=True)
        return cp.stdout.strip()

    def commit(self, subject, body="", trailers=(), date="2026-01-05"):
        parts = [subject] + ([body] if body else []) + (["\n".join(trailers)] if trailers else [])
        self.git("commit", "-q", "--allow-empty", "-F", "-", input="\n\n".join(parts) + "\n", date=date)
        return self.git("rev-parse", "HEAD")


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.state = self.tmp / "state"
        self.cwd = self.tmp / "cwd"  # not a git work tree
        self.cwd.mkdir()
        self.home = self.tmp / "home"
        self.home.mkdir()

    # -- process helpers
    def env(self, **extra):
        e = {k: v for k, v in os.environ.items() if k != "T3_BUILD_STATE" and not k.startswith("GIT_")}
        e.update(PYTHONDONTWRITEBYTECODE="1", HOME=str(self.home), GIT_CEILING_DIRECTORIES=str(self.tmp),
                 GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        e.update(extra)
        return e

    def run_rr(self, *args, env=None, cwd=None):
        self.assertTrue(SCRIPT.is_file(), f"script missing: {SCRIPT}")
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=str(cwd or self.cwd),
                              env=env or self.env(), capture_output=True, text=True,
                              errors="replace", timeout=120)

    def report(self, *extra, repo=REPO, today=TODAY, state=True, **kw):
        args = ["--repo", repo]
        if state:
            args += ["--state-dir", str(self.state)]
        if today:
            args += ["--today", today]
        return self.run_rr(*args, *extra, **kw)

    def ok(self, cp):
        self.assertEqual(cp.returncode, 0, f"exit {cp.returncode}; stderr: {cp.stderr}")
        return cp.stdout

    def usage_error(self, cp):
        self.assertEqual(cp.returncode, 2, f"stderr: {cp.stderr}")
        self.assertEqual(cp.stdout, "", "nothing on stdout on exit 2")
        self.assertNotEqual(cp.stderr.strip(), "", "message on stderr on exit 2")

    # -- fixtures
    def write_log(self, items, repo=REPO, state=None, final_newline=True):
        d = Path(state or self.state) / repo
        d.mkdir(parents=True, exist_ok=True)
        lines = []
        for it in items:
            if isinstance(it, dict):
                it = json.dumps(it, separators=(",", ":")).encode()
            elif isinstance(it, str):
                it = it.encode()
            lines.append(it)
        data = b"\n".join(lines) + (b"\n" if final_newline and lines else b"")
        (d / "events.jsonl").write_bytes(data)
        return d / "events.jsonl"

    def new_repo(self, name="work"):
        return GitRepo(self.tmp / name, self.env())

    def full_repo(self, name="work"):
        """Fixture history, all dates 12:00 UTC. Real merge at the end so S is off the first-parent line.
        git totals: merged PRs 4, Agent commits 3 (A, B, C), without Tier 1 (C), reverts 1 (of A, a T1 commit)."""
        r = self.new_repo(name)
        s = {}
        s["A"] = r.commit("Add feature A", trailers=[AGENT, "Tier: T1"], date="2026-01-05")
        r.commit("Merge pull request #1 from o/agent/a", date="2026-01-06")
        s["B"] = r.commit("Add feature B CANARY-9f3", body="CANARY-9f3 details",
                          trailers=[AGENT, "Tier: T2"], date="2026-01-07")
        r.commit("Merge pull request #2 from o/agent/b", date="2026-01-08")
        s["C"] = r.commit("Add feature C", trailers=[AGENT], date="2026-01-09")
        r.commit("Human fix", body="Human note about the Agent and the Tier.", date="2026-01-10")
        s["R"] = r.commit('Revert "Add feature A"', body=f"This reverts commit {s['A']}.", date="2026-01-11")
        s["PR3"] = r.commit("Merge pull request #3 from o/agent/c", date="2026-01-12")
        r.git("checkout", "-q", "-b", "side")
        s["S"] = r.commit("Side work", date="2026-01-13")
        r.git("checkout", "-q", "main")
        r.git("merge", "--no-ff", "-q", "-m", "Merge pull request #4 from o/side", "side", date="2026-01-14")
        s["PR4"] = r.git("rev-parse", "HEAD")
        s["path"] = str(r.path)
        return r, s

    def revert_repo(self, targets, name="rev"):
        """One commit per target kind ('T0','T1','T2','agent' = Agent without Tier,'human'), then one
        revert commit per target, alternating `.` after the sha (git's own format) and none."""
        r = self.new_repo(name)
        r.commit("Base", date="2026-01-01")
        made = []
        for i, kind in enumerate(targets):
            tr = {"T0": [AGENT, "Tier: T0"], "T1": [AGENT, "Tier: T1"], "T2": [AGENT, "Tier: T2"],
                  "agent": [AGENT], "human": []}[kind]
            made.append(r.commit(f"Work {i}", trailers=tr, date=f"2026-01-{2 + i:02d}"))
        for i, sha in enumerate(made):
            r.commit(f"Revert work {i}", body=f"This reverts commit {sha}{'.' if i % 2 == 0 else ''}",
                     date=f"2026-01-{15 + i:02d}")
        return r

    def ci_file(self, entries, name="ci.json"):
        p = self.tmp / name
        p.write_text(json.dumps(entries))
        return str(p)


# ---------------------------------------------------------------- AC1

class TestAC1Interface(Base):
    def test_missing_log_is_not_an_error(self):
        out = self.ok(self.report())
        self.assertEqual(out.splitlines()[0], f"# Retro report {REPO} {TODAY}")
        self.assertIn(EMPTY_LOG_LINE, out.splitlines())
        self.assertIn("keep collecting", out)
        self.assertFalse(self.state.exists(), "a read must not create the state dir")

    def test_missing_log_shows_no_proposal(self):
        out = self.ok(self.report())
        self.assertIn("none: keep collecting", out.splitlines())

    def test_state_dir_flag(self):
        self.write_log([ev("s1", "spec")])
        out = self.ok(self.report())
        self.assertIn("Events: 1 (malformed skipped: 0). Slices: 1.", out.splitlines())

    def test_state_dir_from_environment(self):
        other = self.tmp / "envstate"
        self.write_log([ev("s1", "spec"), ev("s2", "spec")], state=other)
        out = self.ok(self.report(state=False, env=self.env(T3_BUILD_STATE=str(other))))
        self.assertIn("Events: 2 (malformed skipped: 0). Slices: 2.", out.splitlines())

    def test_state_dir_flag_beats_environment(self):
        other = self.tmp / "envstate"
        self.write_log([ev("s1", "spec"), ev("s2", "spec")], state=other)
        self.write_log([ev("s1", "spec")])
        out = self.ok(self.report(env=self.env(T3_BUILD_STATE=str(other))))
        self.assertIn("Events: 1 (malformed skipped: 0). Slices: 1.", out.splitlines())

    def test_state_dir_default_under_home(self):
        self.write_log([ev("s1", "spec"), ev("s2", "spec"), ev("s3", "spec")],
                       state=self.home / ".local" / "state" / "t3-build")
        out = self.ok(self.report(state=False))
        self.assertIn("Events: 3 (malformed skipped: 0). Slices: 3.", out.splitlines())

    def test_repo_name_rejected(self):
        for bad in ["", "-x", ".x", "a b", "a/b", "../x", "a" * 65, "a;b", "a\nb"]:
            with self.subTest(repo=bad):
                self.usage_error(self.run_rr(f"--repo={bad}", "--state-dir", str(self.state)))
        self.assertFalse(self.state.exists())

    def test_repo_name_accepted(self):
        for good in ["a" * 64, "r.e-p_o1", "9x"]:
            with self.subTest(repo=good):
                out = self.ok(self.run_rr(f"--repo={good}", "--state-dir", str(self.state), "--today", TODAY))
                self.assertEqual(out.splitlines()[0], f"# Retro report {good} {TODAY}")

    def test_repo_required(self):
        self.usage_error(self.run_rr("--state-dir", str(self.state)))

    def test_unknown_option_exit2(self):
        self.usage_error(self.report("--frobnicate"))

    def test_since_rejected(self):
        for bad in ["2026-02-29", "2026-13-01", "2026-1-1", "20260101", "garbage", ""]:
            with self.subTest(since=bad):
                self.usage_error(self.report(f"--since={bad}"))

    def test_since_accepted(self):
        self.ok(self.report("--since", "2026-02-28"))

    def test_today_rejected(self):
        for bad in ["2026-02-30", "tomorrow", "2026/03/01", ""]:
            with self.subTest(today=bad):
                self.usage_error(self.report(f"--today={bad}", today=None))

    def test_explicit_git_path_missing_exit2(self):
        self.usage_error(self.report("--git", str(self.tmp / "nope")))

    def test_explicit_git_path_not_a_worktree_exit2(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        self.usage_error(self.report("--git", str(plain)))

    def test_explicit_git_path_is_a_file_exit2(self):
        f = self.tmp / "file.txt"
        f.write_text("x")
        self.usage_error(self.report("--git", str(f)))

    def test_git_omitted_and_cwd_not_a_worktree(self):
        out = self.ok(self.report())  # cwd is a plain temp dir
        self.assertIn("git: not available", out.splitlines())

    def test_git_default_is_current_directory(self):
        r, s = self.full_repo()
        out = self.ok(self.report(cwd=s["path"]))
        self.assertNotIn("git: not available", out)
        self.assertEqual(find(rows(out, H_X), "pull requests merged")[2], "4")

    def test_ci_runs_missing_file_exit2(self):
        self.usage_error(self.report("--ci-runs", str(self.tmp / "nope.json")))

    def test_ci_runs_is_a_directory_exit2(self):
        self.usage_error(self.report("--ci-runs", str(self.tmp)))

    def test_ci_runs_invalid_json_exit2(self):
        # AMBIGUITY: spec says "unreadable --ci-runs"; the most literal reading counts a file that
        # is not the documented JSON list as unreadable too (exit 2, nothing on stdout).
        p = self.tmp / "bad.json"
        p.write_text("{not json")
        self.usage_error(self.report("--ci-runs", str(p)))

    def test_ci_runs_not_a_list_exit2(self):
        # AMBIGUITY: same as above for valid JSON that is not a list.
        self.usage_error(self.report("--ci-runs", self.ci_file({"sha": "x", "conclusion": "failure"})))

    def test_unreadable_log_exit1(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores file modes")
        p = self.write_log([ev("s1", "spec")])
        p.chmod(0)
        self.addCleanup(lambda: p.chmod(stat.S_IRUSR | stat.S_IWUSR))
        self.assertEqual(self.report().returncode, 1)

    def test_log_path_is_a_directory_exit1(self):
        (self.state / REPO / "events.jsonl").mkdir(parents=True)
        self.assertEqual(self.report().returncode, 1)


# ---------------------------------------------------------------- AC2

class TestAC2Malformed(Base):
    def test_every_kind_of_malformed_line_is_counted_and_skipped(self):
        good1 = ev("s1", "spec")
        good2 = ev("s2", "spec")
        lines = [
            good1,
            "this is not json{",                                   # bad JSON
            "42",                                                  # valid JSON, not an object
            "[1,2]",                                               # not an object
            '"text"',                                              # not an object
            json.dumps(ev("s3", "spec")).encode() + b" " * 5000,   # valid, but longer than 4096 bytes
            ev("s4", "spec", tier="T9"),                           # invalid enum
            {k: v for k, v in ev("s5", "spec").items() if k != "role"},  # missing required field
            ev("s6", "spec", surprise=1),                          # unknown field
            b"\xff\xfe{}",                                         # not UTF-8
            good2,
        ]
        self.write_log(lines, final_newline=False)
        out = self.ok(self.report())
        self.assertIn("Events: 2 (malformed skipped: 8). Slices: 2.", out.splitlines())

    def test_all_lines_malformed_still_exit0(self):
        self.write_log(["nope", "[]", {"slice": "x"}])
        out = self.ok(self.report())
        self.assertIn("Events: 0 (malformed skipped: 3). Slices: 0.", out.splitlines())

    def test_malformed_line_does_not_hide_valid_lines_after_it(self):
        self.write_log(["garbage", ev("s1", "spec"), "garbage", ev("s1", "spec-review", round=1, verdict="APPROVE")])
        out = self.ok(self.report())
        self.assertIn("Events: 2 (malformed skipped: 2). Slices: 1.", out.splitlines())

    def test_ts_is_ignored_by_validation(self):
        # the lines carry ts; log_event.validate rejects ts, so the script must remove it first
        self.write_log([ev("s1", "spec", ts="2026-01-05T10:00:00Z")])
        out = self.ok(self.report())
        self.assertIn("Events: 1 (malformed skipped: 0). Slices: 1.", out.splitlines())

    def test_slices_counts_distinct_slice_values(self):
        self.write_log([ev("a", "spec"), ev("a", "tests"), ev("b", "spec"), ev("b", "ship"), ev("c", "ship")])
        out = self.ok(self.report())
        self.assertIn("Events: 5 (malformed skipped: 0). Slices: 3.", out.splitlines())


# ---------------------------------------------------------------- AC3

class TestAC3CrossCheck(Base):
    def run_full(self, log, *extra):
        r, s = self.full_repo()
        if log is not None:
            self.write_log(log)
        return self.ok(self.report("--git", s["path"], *extra)), s

    def test_rows_in_order_with_log_and_git_columns(self):
        out, _ = self.run_full(ships("s1", "s2"))
        table = rows(out, H_X)
        self.assertEqual([r[0] for r in table], [
            "pull requests merged", "commits with Agent trailer", "agent commits without Tier trailer",
            "reverts", "failed checks after merge"])
        self.assertEqual(table[0], ["pull requests merged", "2", "4"])
        self.assertEqual(table[1], ["commits with Agent trailer", "-", "3"])
        self.assertEqual(table[2], ["agent commits without Tier trailer", "-", "1"])
        self.assertEqual(table[3], ["reverts", "-", "1"])
        self.assertEqual(table[4], ["failed checks after merge", "-", "n/a (no --ci-runs)"])

    def test_log_ship_count_is_slices_not_events(self):
        out, _ = self.run_full(ships("s1", "s2") + ships("s2") + [ev("s3", "spec")])
        self.assertEqual(find(rows(out, H_X), "pull requests merged")[1], "2")

    def test_failed_checks_count_only_failures_on_first_parent_history(self):
        r, s = self.full_repo()
        self.write_log(ships("s1"))
        ci = self.ci_file([
            {"sha": s["B"], "conclusion": "failure"},     # counts
            {"sha": s["C"], "conclusion": "failure"},     # counts
            {"sha": s["S"], "conclusion": "failure"},     # second parent of the last merge: not first-parent
            {"sha": "f" * 40, "conclusion": "failure"},   # not in this history
            {"sha": s["A"], "conclusion": "success"},     # not a failure
            {"sha": s["PR3"], "conclusion": "cancelled"},
        ])
        out = self.ok(self.report("--git", s["path"], "--ci-runs", ci))
        self.assertEqual(find(rows(out, H_X), "failed checks after merge"), ["failed checks after merge", "-", "2"])

    def test_empty_ci_runs_list_gives_zero(self):
        r, s = self.full_repo()
        out = self.ok(self.report("--git", s["path"], "--ci-runs", self.ci_file([])))
        self.assertEqual(find(rows(out, H_X), "failed checks after merge")[2], "0")

    def test_flag_git_has_more_merged_prs_than_log_ship_slices(self):
        out, _ = self.run_full(ships("s1", "s2"))  # 2 < 4
        hits = [f for f in flags(out) if "incomplete" in f]  # AMBIGUITY: wording not fixed; key word from the spec
        self.assertEqual(len(hits), 1, out)

    def test_no_incomplete_flag_when_log_covers_all_prs(self):
        out, _ = self.run_full(ships("s1", "s2", "s3", "s4"))  # 4 == 4
        self.assertEqual([f for f in flags(out) if "incomplete" in f], [])

    def test_flag_agent_commits_without_tier_trailer(self):
        out, _ = self.run_full(ships("s1", "s2", "s3", "s4"))
        hits = [f for f in flags(out) if "Tier" in f]  # AMBIGUITY: key word `Tier` taken from the spec
        self.assertEqual(len(hits), 1, out)

    def test_flag_agent_commits_exist_and_log_has_no_events(self):
        out, _ = self.run_full(None)  # no log file at all
        hits = [f for f in flags(out) if "no events" in f]  # AMBIGUITY: key words `no events` from the spec
        self.assertEqual(len(hits), 1, out)

    def test_exactly_the_expected_flags_when_log_has_events_but_not_all_prs(self):
        out, _ = self.run_full(ships("s1", "s2"))
        self.assertEqual(len(flags(out)), 2, flags(out))  # incomplete + missing Tier; log is not empty
        self.assertEqual([f for f in flags(out) if "no events" in f], [])

    def test_no_flag_for_a_consistent_history(self):
        r = self.new_repo("clean")
        r.commit("Add x", trailers=[AGENT, "Tier: T1"], date="2026-01-05")
        r.commit("Merge pull request #1 from o/x", date="2026-01-06")
        self.write_log(ships("s1"))
        out = self.ok(self.report("--git", str(r.path)))
        self.assertEqual(flags(out), [])

    def test_only_subjects_starting_with_merge_pull_request_hash_count(self):
        r = self.new_repo("subjects")
        r.commit("Merge pull request #12 from o/x", date="2026-01-05")
        r.commit("Fix: Merge pull request #5 later", date="2026-01-06")
        r.commit("Merge pull request without number", body="Merge pull request #7", date="2026-01-07")
        out = self.ok(self.report("--git", str(r.path)))
        self.assertEqual(find(rows(out, H_X), "pull requests merged")[2], "1")

    def test_revert_line_with_and_without_trailing_period(self):
        r = self.revert_repo(["T1", "T1", "human"])  # reverts alternate '.' and no '.'
        out = self.ok(self.report("--git", str(r.path)))
        self.assertEqual(find(rows(out, H_X), "reverts")[2], "3")

    def test_agent_trailer_counts(self):
        r = self.new_repo("trailers")
        r.commit("One", trailers=[AGENT, "Agent: reviewer / gpt-6.1-sol / medium (2 rounds, APPROVE)", "Tier: T2"])
        r.commit("Two", trailers=[AGENT])
        r.commit("Three", body="The Agent did a thing and the Tier is T1.")
        out = self.ok(self.report("--git", str(r.path)))
        table = rows(out, H_X)
        self.assertEqual(find(table, "commits with Agent trailer")[2], "2")  # commits, not trailer lines
        self.assertEqual(find(table, "agent commits without Tier trailer")[2], "1")


# ---------------------------------------------------------------- AC4

class TestAC4Metrics(Base):
    # ---- M1
    def m1(self, events, *extra):
        self.write_log(events)
        return rows(self.ok(self.report(*extra)), H_M1)

    def test_m1_boundary_0_90_is_keep(self):
        t = self.m1(spec_batch("p", 20, 18))
        self.assertEqual(find(t, "spec-writer", "T1", "m1"),
                         ["spec-writer", "T1", "m1", "20", "18", "0.90", "keep"])

    def test_m1_just_above_0_90_is_try_one_tier_down(self):
        t = self.m1(spec_batch("p", 20, 19))
        self.assertEqual(find(t, "spec-writer", "T1", "m1"),
                         ["spec-writer", "T1", "m1", "20", "19", "0.95", "try one tier down"])

    def test_m1_boundary_0_60_is_keep(self):
        t = self.m1(spec_batch("p", 20, 12))
        self.assertEqual(find(t, "spec-writer", "T1", "m1"),
                         ["spec-writer", "T1", "m1", "20", "12", "0.60", "keep"])

    def test_m1_just_below_0_60_is_one_tier_up(self):
        t = self.m1(spec_batch("p", 20, 11))
        self.assertEqual(find(t, "spec-writer", "T1", "m1"),
                         ["spec-writer", "T1", "m1", "20", "11", "0.55", "one tier up"])

    def test_m1_n_19_keeps_collecting_even_at_full_rate(self):
        t = self.m1(spec_batch("p", 19, 19))
        self.assertEqual(find(t, "spec-writer", "T1", "m1"),
                         ["spec-writer", "T1", "m1", "19", "19", "1.00", "keep collecting (n=19)"])

    def test_m1_n_20_all_approved(self):
        t = self.m1(spec_batch("p", 20, 20))
        self.assertEqual(find(t, "spec-writer", "T1", "m1")[5:], ["1.00", "try one tier down"])

    def test_m1_two_models_of_one_role_make_two_rows(self):
        t = self.m1(spec_batch("a", 2, 1, model="m1") + spec_batch("b", 3, 3, model="m2"))
        self.assertEqual(find(t, "spec-writer", "T1", "m1"),
                         ["spec-writer", "T1", "m1", "2", "1", "0.50", "keep collecting (n=2)"])
        self.assertEqual(find(t, "spec-writer", "T1", "m2"),
                         ["spec-writer", "T1", "m2", "3", "3", "1.00", "keep collecting (n=3)"])

    def test_m1_model_change_restarts_the_count(self):
        # 20 slices on m1 and 5 on m2: m1 has its 20 points, m2 is still collecting
        t = self.m1(spec_batch("a", 20, 20, model="m1") + spec_batch("b", 5, 5, model="m2"))
        self.assertEqual(find(t, "spec-writer", "T1", "m1")[6], "try one tier down")
        self.assertEqual(find(t, "spec-writer", "T1", "m2")[6], "keep collecting (n=5)")

    def test_m1_tiers_are_separate_rows(self):
        t = self.m1(spec_batch("a", 2, 2, tier="T1") + spec_batch("b", 4, 1, tier="T2"))
        self.assertEqual(find(t, "spec-writer", "T1", "m1")[3:6], ["2", "2", "1.00"])
        self.assertEqual(find(t, "spec-writer", "T2", "m1")[3:6], ["4", "1", "0.25"])

    def test_m1_earliest_round1_review_judges_the_latest_spec(self):
        evs = [
            ev("a", "spec", "spec-writer", "m1", "T1", ts=T(10, 0)),
            review("a", "spec-review", 1, "CHANGES", "T1", ts=T(10, 10)),   # earliest round 1: counts
            ev("a", "spec", "spec-writer-t2", "m2", "T1", ts=T(10, 20)),    # latest spec: the judged event
            review("a", "spec-review", 1, "APPROVE", "T1", ts=T(10, 30)),   # later round 1: ignored
            review("a", "spec-review", 2, "APPROVE", "T1", ts=T(10, 40)),   # round 2: ignored
            ev("b", "spec", "spec-writer", "m1", "T1", ts=T(10, 0)),
            review("b", "spec-review", 2, "APPROVE", "T1", ts=T(10, 10)),   # no round 1 review: slice not judged
        ]
        t = self.m1(evs)
        self.assertEqual(t, [["spec-writer-t2", "T1", "m2", "1", "0", "0.00", "keep collecting (n=1)"]])

    def test_m1_slice_without_a_producer_event_is_role_and_model_unknown(self):
        t = self.m1([review("u", "spec-review", 1, "APPROVE", "T0")])
        self.assertEqual(t, [["unknown", "T0", "unknown", "1", "1", "1.00", "keep collecting (n=1)"]])

    def test_m1_tier_is_the_reviewer_events_tier(self):
        evs = []
        for i in range(20):
            s = f"c{i}"
            evs.append(ev(s, "implement", "implementer", "m1", "T1", ts=T(10)))
            evs.append(review(s, "code-review", 1, "APPROVE" if i < 10 else "CHANGES", "T2", findings=ZERO))
        t = self.m1(evs)
        self.assertEqual(find(t, "implementer"), ["implementer", "T2", "m1", "20", "10", "0.50", "one tier up"])

    def test_m1_code_review_judges_the_latest_implement_event(self):
        evs = [
            ev("a", "implement", "implementer", "m1", "T1", ts=T(10, 0)),
            ev("a", "implement", "implementer", "m2", "T1", ts=T(10, 20)),
            review("a", "code-review", 1, "APPROVE", "T1", ts=T(10, 30), findings=ZERO),
        ]
        t = self.m1(evs)
        self.assertEqual(t, [["implementer", "T1", "m2", "1", "1", "1.00", "keep collecting (n=1)"]])

    def test_m1_spec_and_code_side_are_counted_independently(self):
        evs = [
            ev("a", "spec", "spec-writer", "m1", "T1", ts=T(9)),
            review("a", "spec-review", 1, "CHANGES", "T1", ts=T(9, 30)),
            ev("a", "implement", "implementer", "m1", "T1", ts=T(10)),
            review("a", "code-review", 1, "APPROVE", "T1", ts=T(11), findings=ZERO),
        ]
        t = self.m1(evs)
        self.assertEqual(find(t, "spec-writer")[3:6], ["1", "0", "0.00"])
        self.assertEqual(find(t, "implementer")[3:6], ["1", "1", "1.00"])

    # ---- M2
    def m2(self, events):
        self.write_log(events)
        return rows(self.ok(self.report()), H_M2)

    def test_m2_at_budget_share_0_30_is_specs_too_vague(self):
        t = self.m2(rounds_batch("a", "T2", [1] * 14 + [3] * 6))
        self.assertEqual(find(t, "T2"), ["T2", "20", "1.60", "6", "specs too vague"])

    def test_m2_share_below_0_30_is_keep(self):
        t = self.m2(rounds_batch("a", "T2", [1] * 15 + [3] * 5))
        self.assertEqual(find(t, "T2"), ["T2", "20", "1.50", "5", "keep"])

    def test_m2_under_20_slices_keeps_collecting(self):
        t = self.m2(rounds_batch("a", "T2", [3] * 19))
        self.assertEqual(find(t, "T2"), ["T2", "19", "3.00", "19", "keep collecting"])

    def test_m2_rounds_are_the_highest_round_and_mean_has_two_decimals(self):
        t = self.m2(rounds_batch("a", "T1", [1, 1, 2]) + rounds_batch("b", "T2", [4]))
        self.assertEqual(find(t, "T1"), ["T1", "3", "1.33", "0", "keep collecting"])
        self.assertEqual(find(t, "T2"), ["T2", "1", "4.00", "1", "keep collecting"])

    def test_m2_four_or_more_rounds_count_as_at_budget(self):
        t = self.m2(rounds_batch("a", "T1", [2, 3, 5]))
        self.assertEqual(find(t, "T1")[3:4], ["2"])

    # ---- M3
    def test_m3_reverts_counted_under_the_tier_of_the_reverted_commit(self):
        r = self.revert_repo(["T1", "T1", "T2", "agent", "human"])
        out = self.ok(self.report("--git", str(r.path)))
        self.assertEqual(rows(out, H_M3), [["T0", "0"], ["T1", "2"], ["T2", "1"], ["unknown", "2"]])

    def test_m3_t0_revert(self):
        r = self.revert_repo(["T0"])
        out = self.ok(self.report("--git", str(r.path)))
        self.assertEqual(rows(out, H_M3), [["T0", "1"], ["T1", "0"], ["T2", "0"], ["unknown", "0"]])

    def test_m3_all_four_rows_present_without_reverts(self):
        r = self.revert_repo([])
        out = self.ok(self.report("--git", str(r.path)))
        self.assertEqual(rows(out, H_M3), [["T0", "0"], ["T1", "0"], ["T2", "0"], ["unknown", "0"]])

    def test_m3_fixture_history_revert_of_a_t1_commit(self):
        r, s = self.full_repo()
        out = self.ok(self.report("--git", s["path"]))
        self.assertEqual(rows(out, H_M3), [["T0", "0"], ["T1", "1"], ["T2", "0"], ["unknown", "0"]])

    # ---- M4
    def test_m4_iso_weeks_across_year_boundaries(self):
        hit = lambda s, day: ev(s, "implement", ts=f"{day}T09:00:00Z", quota_pause=True)
        evs = [
            hit("q1", "2024-12-29"),                                   # Sunday      -> 2024-W52
            hit("q2", "2024-12-30"), hit("q3", "2025-01-02"),          # Mon, Thu    -> 2025-W01 (2 hits)
            hit("q4", "2025-01-06"),                                   # Monday      -> 2025-W02
            hit("q5", "2025-12-28"),                                   # Sunday      -> 2025-W52
            hit("q6", "2025-12-29"), hit("q7", "2026-01-01"),          # Mon, Thu    -> 2026-W01 (2 hits)
            ev("q8", "implement", ts="2025-02-03T09:00:00Z", quota_pause=False),  # false: no hit
            ev("q9", "implement", ts="2025-02-04T09:00:00Z"),                      # key absent: no hit
        ]
        self.write_log(list(reversed(evs)))  # file order must not matter, weeks come out ascending
        out = self.ok(self.report())
        self.assertEqual(rows(out, H_M4), [["2024-W52", "1"], ["2025-W01", "2"], ["2025-W02", "1"],
                                           ["2025-W52", "1"], ["2026-W01", "2"]])

    def test_m4_no_hits_means_no_week_rows(self):
        self.write_log([ev("q", "implement", quota_pause=False)])
        out = self.ok(self.report())
        self.assertEqual(rows(out, H_M4, allow_absent=True) or [], [])  # header-only table or none

    # ---- M5
    def test_m5_is_not_recorded(self):
        out = self.ok(self.report())
        self.assertTrue([l for l in out.splitlines() if "not recorded in the event log" in l], out)


# ---------------------------------------------------------------- AC5

class TestAC5Proposals(Base):
    def out_for(self, events, *extra):
        self.write_log(events)
        return self.ok(self.report(*extra))

    # ---- nothing fires
    def test_nothing_fires_prints_none_line_and_no_table(self):
        out = self.out_for(spec_batch("a", 3, 3))
        self.assertIn("none: keep collecting", out.splitlines())
        self.assertIsNone(rows(out, H_P, allow_absent=True))

    def test_under_20_points_no_proposal_for_any_rule(self):
        evs = (spec_batch("a", 19, 19)                                              # rule 1: n=19, rate 1.00
               + [ev(f"g{i}", "human-gate", outcome="blocked") for i in range(19)]  # rule 5: 19 of 19 blocked
               + [ev(f"i{i}", "implement", attempt=3) for i in range(19)]           # rule 6: 19 of 19 attempt 3
               + [review(f"r{i}", "code-review", 3, "APPROVE", "T1", findings=ZERO) for i in range(19)])  # rules 2, 7
        out = self.out_for(evs)
        self.assertIn("none: keep collecting", out.splitlines())
        self.assertEqual(proposals(out), [])

    # ---- rule 1
    def test_rule1_above_0_90_fires_with_role_and_tier(self):
        out = self.out_for(spec_batch("a", 20, 19, role="spec-writer", tier="T1"))
        hit = fired(out, "try one tier down")
        self.assertEqual(len(hit), 1, out)
        self.assertTrue("spec-writer" in " ".join(hit[0]) and "T1" in " ".join(hit[0]), hit[0])

    def test_rule1_at_0_90_does_not_fire(self):
        out = self.out_for(spec_batch("a", 20, 18))
        self.assertEqual(fired(out, "try one tier down"), [])

    def test_rule1_n19_does_not_fire(self):
        out = self.out_for(spec_batch("a", 19, 19))
        self.assertEqual(fired(out, "try one tier down"), [])

    def test_rule1_below_0_60_fires_one_tier_up(self):
        out = self.out_for(spec_batch("a", 20, 11, tier="T2"))
        hit = fired(out, "one tier up")
        self.assertEqual(len(hit), 1, out)
        self.assertTrue("spec-writer" in " ".join(hit[0]) and "T2" in " ".join(hit[0]), hit[0])

    def test_rule1_at_0_60_does_not_fire(self):
        out = self.out_for(spec_batch("a", 20, 12))
        self.assertEqual(fired(out, "one tier up"), [])

    def test_rule1_is_per_model_row(self):
        out = self.out_for(spec_batch("a", 20, 20, model="m1") + spec_batch("b", 19, 19, model="m2"))
        self.assertEqual(len(fired(out, "try one tier down")), 1)

    # ---- rule 2
    def test_rule2_share_0_30_at_20_slices_fires(self):
        out = self.out_for(rounds_batch("a", "T2", [1] * 14 + [3] * 6))
        self.assertEqual(len(fired(out, "specs too vague")), 1, out)

    def test_rule2_share_below_0_30_does_not_fire(self):
        out = self.out_for(rounds_batch("a", "T2", [1] * 15 + [3] * 5))
        self.assertEqual(fired(out, "specs too vague"), [])

    def test_rule2_19_slices_does_not_fire(self):
        out = self.out_for(rounds_batch("a", "T2", [3] * 19))
        self.assertEqual(fired(out, "specs too vague"), [])

    # ---- rule 3
    def test_rule3_revert_of_a_t1_commit_fires(self):
        r = self.revert_repo(["T1"])
        out = self.ok(self.report("--git", str(r.path)))
        self.assertEqual(len(fired(out, "extend the T2 trigger list")), 1, out)

    def test_rule3_revert_of_a_t0_commit_fires(self):
        r = self.revert_repo(["T0"])
        out = self.ok(self.report("--git", str(r.path)))
        self.assertEqual(len(fired(out, "extend the T2 trigger list")), 1, out)

    def test_rule3_reverts_of_t2_or_untiered_commits_do_not_fire(self):
        r = self.revert_repo(["T2", "agent", "human"])
        out = self.ok(self.report("--git", str(r.path)))
        self.assertEqual(fired(out, "extend the T2 trigger list"), [])

    def test_rule3_no_reverts_does_not_fire(self):
        r = self.revert_repo([])
        out = self.ok(self.report("--git", str(r.path)))
        self.assertEqual(fired(out, "extend the T2 trigger list"), [])

    # ---- rule 4
    def hits(self, *days):
        return [ev(f"q{i}", "implement", ts=f"{d}T09:00:00Z", quota_pause=True) for i, d in enumerate(days)]

    def test_rule4_three_hits_in_one_week_fires(self):
        out = self.out_for(self.hits("2026-02-02", "2026-02-03", "2026-02-04"))  # Mon-Wed, 2026-W06
        self.assertEqual(len(fired(out, "shift heavy jobs, reduce parallel workers")), 1, out)

    def test_rule4_two_hits_in_one_week_does_not_fire(self):
        out = self.out_for(self.hits("2026-02-02", "2026-02-03"))
        self.assertEqual(fired(out, "shift heavy jobs, reduce parallel workers"), [])

    def test_rule4_three_hits_over_two_weeks_does_not_fire(self):
        out = self.out_for(self.hits("2026-02-01", "2026-02-02", "2026-02-03"))  # Sun W05, Mon+Tue W06
        self.assertEqual(fired(out, "shift heavy jobs, reduce parallel workers"), [])

    # ---- rule 5
    def gates(self, n, blocked):
        return [ev(f"g{i}", "human-gate", outcome="blocked" if i < blocked else "ok") for i in range(n)]

    def test_rule5_20_gates_30_percent_blocked_fires(self):
        out = self.out_for(self.gates(20, 6))
        self.assertEqual(len(fired(out, "improve the decision brief")), 1, out)

    def test_rule5_below_30_percent_does_not_fire(self):
        out = self.out_for(self.gates(20, 5))
        self.assertEqual(fired(out, "improve the decision brief"), [])

    def test_rule5_19_gates_do_not_fire(self):
        out = self.out_for(self.gates(19, 19))
        self.assertEqual(fired(out, "improve the decision brief"), [])

    # ---- rule 6
    def attempts(self, n_impl, n_tests, high):
        evs = []
        for i in range(n_impl):
            evs.append(ev(f"i{i}", "implement", attempt=3 if i < high else 1))
        for i in range(n_tests):
            evs.append(ev(f"t{i}", "tests", attempt=4 if i < high else 2))
        return evs

    def test_rule6_20_events_30_percent_attempt_3_or_more_fires(self):
        # implement and tests events are pooled: 10 + 10 events; 6 of the 20 have attempt >= 3
        evs = ([ev(f"i{i}", "implement", attempt=3 if i < 3 else 1) for i in range(10)]
               + [ev(f"t{i}", "tests", attempt=4 if i < 3 else 2) for i in range(10)])
        out = self.out_for(evs)
        self.assertEqual(len(fired(out, "slices too large or specs too vague")), 1, out)

    def test_rule6_below_30_percent_does_not_fire(self):
        evs = ([ev(f"i{i}", "implement", attempt=3 if i < 3 else 1) for i in range(10)]
               + [ev(f"t{i}", "tests", attempt=4 if i < 2 else 2) for i in range(10)])  # 5 of 20
        out = self.out_for(evs)
        self.assertEqual(fired(out, "slices too large or specs too vague"), [])

    def test_rule6_19_events_do_not_fire(self):
        evs = [ev(f"i{i}", "implement", attempt=3) for i in range(19)]
        out = self.out_for(evs)
        self.assertEqual(fired(out, "slices too large or specs too vague"), [])

    # ---- rule 7
    def r7(self, prefix, tier, n_zero, n_blocker, rounds=(3, 4), n_early=0):
        """code-review events with round >= 3: n_zero with blocker 0, n_blocker with blocker 1;
        plus n_early round-2 events with blocker 1 that must not count."""
        evs = []
        for i in range(n_zero):
            evs.append(review(f"{prefix}z{i}", "code-review", rounds[i % len(rounds)], "APPROVE", tier, findings=ZERO))
        for i in range(n_blocker):
            evs.append(review(f"{prefix}b{i}", "code-review", 3, "CHANGES", tier, findings=ONE_BLOCKER))
        for i in range(n_early):
            evs.append(review(f"{prefix}e{i}", "code-review", 2, "CHANGES", tier, findings=ONE_BLOCKER))
        return evs

    def test_rule7_20_events_90_percent_zero_blocker_fires(self):
        out = self.out_for(self.r7("a", "T1", 18, 2, n_early=5))  # round 2 events must not dilute the share
        hit = fired(out, "lower the round budget for that tier")
        self.assertEqual(len(hit), 1, out)
        self.assertIn("T1", " ".join(hit[0]))

    def test_rule7_below_90_percent_does_not_fire(self):
        out = self.out_for(self.r7("a", "T1", 17, 3))
        self.assertEqual(fired(out, "lower the round budget for that tier"), [])

    def test_rule7_19_events_do_not_fire(self):
        out = self.out_for(self.r7("a", "T1", 19, 0))
        self.assertEqual(fired(out, "lower the round budget for that tier"), [])

    def test_rule7_one_row_per_tier_meeting_it(self):
        out = self.out_for(self.r7("a", "T1", 20, 0) + self.r7("b", "T2", 20, 0) + self.r7("c", "T0", 17, 3))
        hit = fired(out, "lower the round budget for that tier")
        self.assertEqual(len(hit), 2, out)
        text = [" ".join(h) for h in hit]
        self.assertEqual(sorted(t for t in ("T1", "T2") if any(t in x for x in text)), ["T1", "T2"])
        self.assertFalse(any("T0" in x for x in text))

    def test_finding_text_signals_are_declared_missing(self):
        out = self.out_for(self.gates(20, 6))
        # AMBIGUITY: wording not fixed; the spec says one line that "finding text" is not in the log
        self.assertTrue([l for l in out.splitlines() if "finding text" in l.lower()], out)


# ---------------------------------------------------------------- AC6

class TestAC6Since(Base):
    def test_since_filters_events_on_or_after_the_date(self):
        self.write_log([
            ev("s1", "spec", ts="2026-01-04T23:59:59Z"),   # before
            ev("s2", "spec", ts="2026-01-05T00:00:00Z"),   # on the date: kept
            ev("s3", "spec", ts="2026-01-06T10:00:00Z"),
        ])
        out = self.ok(self.report("--since", "2026-01-05"))
        self.assertIn("Events: 2 (malformed skipped: 0). Slices: 2.", out.splitlines())

    def test_since_changes_metrics_not_only_the_count(self):
        old = spec_batch("old", 20, 20)
        for e in old:
            e["ts"] = "2025-06-01T10:00:00Z"
        self.write_log(old + spec_batch("new", 3, 3))
        out = self.ok(self.report("--since", "2026-01-01"))
        self.assertEqual(find(rows(out, H_M1), "spec-writer")[3:5], ["3", "3"])

    def test_since_filters_commits_on_or_after_the_commit_date(self):
        r, s = self.full_repo()
        self.write_log(ships("s1"))
        out = self.ok(self.report("--git", s["path"], "--since", "2026-01-09"))
        t = rows(out, H_X)
        # kept: C (01-09, Agent, no Tier), human fix, revert R, PR #3 (01-12), side work, PR #4 (01-14)
        self.assertEqual(find(t, "pull requests merged")[2], "2")
        self.assertEqual(find(t, "commits with Agent trailer")[2], "1")
        self.assertEqual(find(t, "agent commits without Tier trailer")[2], "1")
        self.assertEqual(find(t, "reverts")[2], "1")
        # AMBIGUITY: whether the revert counts under T1 when the reverted commit is before --since
        # is not specified; the M3 table is deliberately not asserted here.

    def test_since_excludes_everything_before_the_boundary_day(self):
        r, s = self.full_repo()
        out = self.ok(self.report("--git", s["path"], "--since", "2026-01-12"))
        t = rows(out, H_X)
        self.assertEqual(find(t, "pull requests merged")[2], "2")   # #3 on 01-12, #4 on 01-14
        self.assertEqual(find(t, "commits with Agent trailer")[2], "0")
        self.assertEqual(find(t, "reverts")[2], "0")

    def test_since_after_all_history(self):
        r, s = self.full_repo()
        out = self.ok(self.report("--git", s["path"], "--since", "2027-01-01"))
        t = rows(out, H_X)
        self.assertEqual([find(t, k)[2] for k in ("pull requests merged", "commits with Agent trailer", "reverts")],
                         ["0", "0", "0"])

    def test_today_only_fills_the_header(self):
        r, s = self.full_repo()
        self.write_log([ev("s1", "spec", ts="2026-04-01T10:00:00Z"), ev("s2", "ship", ts="2026-04-02T10:00:00Z")])  # after both --today values
        a = self.ok(self.report("--git", s["path"], today="2026-03-01"))
        b = self.ok(self.report("--git", s["path"], today="2020-01-01"))
        self.assertEqual(a.splitlines()[0], f"# Retro report {REPO} 2026-03-01")
        self.assertEqual(b.splitlines()[0], f"# Retro report {REPO} 2020-01-01")
        self.assertEqual(a.splitlines()[1:], b.splitlines()[1:])
        self.assertIn("Events: 2 (malformed skipped: 0). Slices: 2.", a.splitlines())

    def test_default_today_is_the_current_utc_date(self):
        before = datetime.now(timezone.utc).date().isoformat()
        out = self.ok(self.report(today=None))
        after = datetime.now(timezone.utc).date().isoformat()
        self.assertIn(out.splitlines()[0], {f"# Retro report {REPO} {before}", f"# Retro report {REPO} {after}"})


# ---------------------------------------------------------------- AC7

class TestAC7ReadOnly(Base):
    def test_a_full_run_changes_no_file_or_directory(self):
        r, s = self.full_repo()
        self.write_log(spec_batch("a", 5, 3) + ships("s1") + ["garbage"])
        ci = self.ci_file([{"sha": s["B"], "conclusion": "failure"}])
        before = snapshot(self.tmp)  # covers state dir, git work tree (.git by names), cwd and new siblings
        self.ok(self.report("--git", s["path"], "--ci-runs", ci, "--since", "2026-01-01"))
        self.assertEqual(snapshot(self.tmp), before)

    def test_missing_log_and_state_dir_stay_missing(self):
        before = snapshot(self.tmp)
        self.ok(self.report())
        self.assertEqual(snapshot(self.tmp), before)
        self.assertFalse(self.state.exists())

    def test_commit_messages_are_never_printed(self):
        r, s = self.full_repo()
        self.write_log(ships("s1"))
        cp = self.report("--git", s["path"], "--ci-runs", self.ci_file([{"sha": s["B"], "conclusion": "failure"}]))
        self.ok(cp)
        self.assertNotIn("CANARY-9f3", cp.stdout)
        self.assertNotIn("CANARY-9f3", cp.stderr)
        for text in ("Add feature", "Human fix", "Side work", "Human note"):
            self.assertNotIn(text, cp.stdout)

    def test_malformed_log_text_is_never_echoed(self):
        self.write_log(["not json LEAK-5c1 {", {"slice": "LEAK-5c1"}, ev("s1", "spec", tier="T9-LEAK-5c1"),
                        ev("s2", "spec")])
        cp = self.report()
        self.ok(cp)
        self.assertNotIn("LEAK-5c1", cp.stdout)

    def test_git_receives_only_read_commands_as_argument_lists(self):
        spy_dir = self.tmp / "spy"
        spy_dir.mkdir()
        log = self.tmp / "spy.log"
        spy = spy_dir / "git"
        spy.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$SPY_LOG"\nexec "$REAL_GIT" "$@"\n')
        spy.chmod(0o755)
        r, s = self.full_repo()
        env = self.env(PATH=f"{spy_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                       SPY_LOG=str(log), REAL_GIT=shutil.which("git"))
        self.ok(self.report("--git", s["path"], env=env))
        forbidden = {"commit", "push", "fetch", "pull", "add", "checkout", "switch", "reset", "restore", "config",
                     "gc", "clean", "rm", "mv", "merge", "rebase", "cherry-pick", "revert", "tag", "branch",
                     "update-ref", "init", "clone", "stash", "apply", "am", "remote", "prune", "repack"}
        for line in (log.read_text().splitlines() if log.exists() else []):
            args, sub, i = line.split(), None, 0
            while i < len(args):
                if args[i] in ("-C", "-c"):
                    i += 2
                    continue
                if args[i].startswith("-"):
                    i += 1
                    continue
                sub = args[i]
                break
            self.assertNotIn(sub, forbidden, f"git {line}")

    def test_git_path_with_shell_metacharacters_is_not_interpreted(self):
        r = self.new_repo("w x;touch PWNED $(touch PWNED2)")
        r.commit("Merge pull request #1 from o/x", date="2026-01-05")
        out = self.ok(self.report("--git", str(r.path)))
        self.assertEqual(find(rows(out, H_X), "pull requests merged")[2], "1")
        for d in (self.cwd, self.tmp, r.path):
            self.assertFalse((d / "PWNED").exists() or (d / "PWNED2").exists(), d)

    def test_source_has_no_shell_true_and_no_network_modules(self):
        if not SCRIPT.is_file():
            self.skipTest("retro_report.py does not exist yet")
        with open(SCRIPT, "rb") as f:
            toks = [t for t in tokenize.tokenize(f.readline) if t.type in (tokenize.NAME, tokenize.OP)]
        names = {t.string for t in toks if t.type == tokenize.NAME}
        for bad in ("socket", "urllib", "requests"):
            self.assertNotIn(bad, names, f"{bad} used in retro_report.py")
        seq = [t.string for t in toks]
        for i in range(len(seq) - 2):
            self.assertFalse(seq[i] == "shell" and seq[i + 1] == "=" and seq[i + 2] == "True", "shell=True")


# ---------------------------------------------------------------- AC8

GOLDEN = """\
# Retro report demo 2026-03-01
Events: 6 (malformed skipped: 1). Slices: 1.

## Cross-check with git
| item | log | git |
|---|---|---|
| pull requests merged | 1 | 1 |
| commits with Agent trailer | - | 1 |
| agent commits without Tier trailer | - | 0 |
| reverts | - | 0 |
| failed checks after merge | - | n/a (no --ci-runs) |

## M1 first-pass approval
| role | tier | model | n | approved | rate | result |
|---|---|---|---|---|---|---|
| spec-writer | T1 | m1 | 1 | 1 | 1.00 | keep collecting (n=1) |
| implementer | T1 | m1 | 1 | 0 | 0.00 | keep collecting (n=1) |

## M2 review rounds per slice
| tier | slices | mean rounds | at budget | result |
|---|---|---|---|---|
| T1 | 1 | 2.00 | 0 | keep collecting |

## M3 escaped defects per tier
| tier | reverts |
|---|---|
| T0 | 0 |
| T1 | 0 |
| T2 | 0 |
| unknown | 0 |

## M4 limit hits per ISO week
| week | hits |
|---|---|
| 2026-W06 | 1 |

## M5 OpenRouter spend per task
not recorded in the event log

## Proposed changes
none: keep collecting
"""


class TestAC8Golden(Base):
    def fixture(self):
        r = self.new_repo("golden")
        r.commit("Add x", trailers=[AGENT, "Tier: T1"], date="2026-02-01")
        r.commit("Merge pull request #1 from o/x", date="2026-02-02")
        d = "2026-02-02"
        self.write_log([
            ev("s1", "spec", "spec-writer", "m1", "T1", ts=f"{d}T09:00:00Z"),
            review("s1", "spec-review", 1, "APPROVE", "T1", ts=f"{d}T09:10:00Z"),
            ev("s1", "implement", "implementer", "m1", "T1", ts=f"{d}T10:00:00Z", quota_pause=True),
            review("s1", "code-review", 1, "CHANGES", "T1", ts=f"{d}T10:30:00Z", findings=ZERO),
            "this line is malformed",
            review("s1", "code-review", 2, "APPROVE", "T1", ts=f"{d}T11:00:00Z", findings=ZERO),
            ev("s1", "ship", "lead", "m1", "T1", ts=f"{d}T12:00:00Z"),
        ])
        return str(r.path)

    def test_golden_report(self):
        # Compared after normalise(): headings, separator rows, blank lines, the 'finding text' note and
        # the row order inside each table are not fixed by the spec. Everything else must match exactly.
        out = self.ok(self.report("--git", self.fixture()))
        self.assertEqual(normalise(out), normalise(GOLDEN))

    def test_golden_run_is_reproducible(self):
        g = self.fixture()
        self.assertEqual(self.ok(self.report("--git", g)), self.ok(self.report("--git", g)))


if __name__ == "__main__":
    unittest.main()
