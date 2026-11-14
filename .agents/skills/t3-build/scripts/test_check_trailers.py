"""Acceptance tests for check_trailers.py (spec 015-wp9-ci, AC5, AC6, AC7, AC12, AC13, AC14, AC20).

Run from this folder:  python3 -m unittest test_check_trailers -v
The CLI is run as a subprocess. The module is loaded inside single tests only (AC14 scan, AC20
run_git), so a missing script fails each test on its own. Fixture repos live in temp dirs, with
GIT_CONFIG_GLOBAL=/dev/null, GIT_CONFIG_NOSYSTEM=1 and a fixed author; the 501-commit history is
built with git fast-import.
"""

import ast
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True  # loading the module under test must leave no __pycache__

from test_check_agents import source_scan, printable_only  # noqa: E402  (shared AC14 scan)

SCRIPTS = Path(__file__).resolve().parent
SCRIPT = SCRIPTS / "check_trailers.py"
REAL_GIT = shutil.which("git")
AGENT = "Agent: implementer / claude-opus-5-5 / medium"
GOOD = f"Add a thing\n\nBody text.\n\n{AGENT}\nTier: T1\n"
SKIPPED = "trailers: not an agent branch, skipped"
DATE = "2026-01-05T12:00:00+0000"
FLAGS = ["-c", "core.fsmonitor=false", "-c", "diff.external=", "-c", "core.pager=cat", "--no-pager"]
LINE_FORMS = re.compile(r"[0-9a-f]{12}: (missing Agent line|missing Tier line|missing Agent and Tier lines"
                        r"|message too large)|trailers: \d+ commits checked, \d+ failing")


def summary(n, m):
    return f"trailers: {n} commits checked, {m} failing"


def pid_dead(pid, grace=3.0):
    """True when the process is gone (a zombie still counts as alive: os.kill(pid, 0) succeeds)."""
    end = time.monotonic() + grace
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        if time.monotonic() > end:
            return False
        time.sleep(0.05)


def kill_quietly(pid):
    try:
        os.kill(pid, 9)
    except OSError:
        pass


class Repo:
    def __init__(self, path, env):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.env = dict(env)
        self.env.update(GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                        GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid",
                        GIT_AUTHOR_DATE=DATE, GIT_COMMITTER_DATE=DATE)
        self.git("init", "-q")
        self.git("symbolic-ref", "HEAD", "refs/heads/main")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args, input=None, check=True):
        cp = subprocess.run([REAL_GIT, "-C", str(self.path), *args], env=self.env, input=input,
                            capture_output=True, check=False)
        if check and cp.returncode:
            raise AssertionError(f"fixture git {args} failed: {cp.stderr.decode(errors='replace')}")
        return cp.stdout.decode(errors="replace").strip()

    def commit(self, message, files=None, verbatim=False):
        for name, text in (files or {}).items():
            (self.path / name).write_text(text)
            self.git("add", name)
        args = ["commit", "-q", "--allow-empty", "-F", "-"]
        if verbatim:
            args.insert(2, "--cleanup=verbatim")
        self.git(*args, input=message.encode())
        return self.git("rev-parse", "HEAD")


class Base(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = Path(d.name).resolve()
        self.cwd = self.tmp / "cwd"  # not a git work tree
        self.cwd.mkdir()
        self.home = self.tmp / "home"
        self.home.mkdir()

    def env(self, **extra):
        e = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        e.update(PYTHONDONTWRITEBYTECODE="1", HOME=str(self.home), GIT_CEILING_DIRECTORIES=str(self.tmp),
                 GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        e.update(extra)
        return e

    def run_ct(self, *args, cwd=None, env=None, timeout=120):
        self.assertTrue(SCRIPT.is_file(), f"script missing: {SCRIPT}")
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=str(cwd or self.cwd),
                              env=env or self.env(), capture_output=True, text=True, errors="replace",
                              timeout=timeout)

    def check(self, repo, base, head, branch="agent/x", **kw):
        return self.run_ct("--branch", branch, "--base", base, "--head", head, "--repo", repo.path, **kw)

    def new_repo(self, name="work"):
        return Repo(self.tmp / name, self.env())

    def branch_repo(self, *messages, verbatim=False, name="work"):
        """Base commit on main (no trailers), then agent/x with one commit per message."""
        r = self.new_repo(name)
        base = r.commit("Base without trailers\n")
        r.git("checkout", "-q", "-b", "agent/x")
        shas = [r.commit(m, verbatim=verbatim) for m in messages]
        return r, base, (shas[-1] if shas else base), shas

    # -- assertions
    def usage_error(self, cp, label=""):
        self.assertEqual(cp.returncode, 2, f"{label}: exit {cp.returncode}, stdout={cp.stdout!r}, stderr={cp.stderr!r}")
        self.assertEqual(cp.stdout, "", f"{label}: stdout must be empty on exit 2")
        self.assertNotEqual(cp.stderr.strip(), "", f"{label}: message on stderr on exit 2")

    def passes(self, cp, n):
        self.assertEqual(cp.returncode, 0, f"exit {cp.returncode}; stdout={cp.stdout!r}; stderr={cp.stderr!r}")
        lines = cp.stdout.splitlines()
        self.assertTrue(lines, "no output")
        self.assertEqual(lines[-1], summary(n, 0))
        self.assertFalse([l for l in lines if re.match(r"[0-9a-f]{12}: ", l)], cp.stdout)

    def fails(self, cp, failures, n):
        """failures: {sha: part}. Exactly these commits fail, each with its line."""
        self.assertEqual(cp.returncode, 1, f"exit {cp.returncode}; stdout={cp.stdout!r}; stderr={cp.stderr!r}")
        lines = cp.stdout.splitlines()
        self.assertTrue(lines, "no output")
        self.assertEqual(lines[-1], summary(n, len(failures)))
        got = [l for l in lines if re.match(r"[0-9a-f]{12}: ", l)]
        want = [f"{sha[:12]}: {part}" for sha, part in failures.items()]
        self.assertEqual(sorted(got), sorted(want), cp.stdout)

    def one(self, message, verbatim=True):
        r, base, head, _ = self.branch_repo(message, verbatim=verbatim)
        return self.check(r, base, head), head

    def one_passes(self, message, verbatim=True):
        cp, _ = self.one(message, verbatim)
        self.passes(cp, 1)

    def one_fails(self, message, part, verbatim=True):
        cp, sha = self.one(message, verbatim)
        self.fails(cp, {sha: part}, 1)


# ---------------------------------------------------------------- AC5

class TestAC5Commits(Base):
    def test_missing_agent_line(self):
        r, base, head, shas = self.branch_repo("Work\n\nTier: T1\n")
        self.fails(self.check(r, base, head), {shas[0]: "missing Agent line"}, 1)

    def test_missing_tier_line(self):
        r, base, head, shas = self.branch_repo(f"Work\n\n{AGENT}\n")
        self.fails(self.check(r, base, head), {shas[0]: "missing Tier line"}, 1)

    def test_missing_both_lines(self):
        r, base, head, shas = self.branch_repo("Work\n\nNo footer.\n")
        self.fails(self.check(r, base, head), {shas[0]: "missing Agent and Tier lines"}, 1)

    def test_clean_range(self):
        r, base, head, _ = self.branch_repo(GOOD, GOOD, GOOD)
        self.passes(self.check(r, base, head), 3)

    def test_only_the_failing_commit_is_named(self):
        r, base, head, shas = self.branch_repo(GOOD, "Bad\n\nTier: T2\n", GOOD)
        cp = self.check(r, base, head)
        self.fails(cp, {shas[1]: "missing Agent line"}, 3)
        for s in (shas[0], shas[2], base):
            self.assertNotIn(s[:12], cp.stdout)

    def test_base_commit_is_outside_the_range(self):
        r, base, head, _ = self.branch_repo(GOOD)  # base has no trailers
        self.passes(self.check(r, base, head), 1)

    def test_merge_commits_are_skipped(self):
        r = self.new_repo()
        base = r.commit("Base\n")
        r.git("checkout", "-q", "-b", "agent/x")
        r.commit(GOOD, files={"a.txt": "a\n"})
        r.git("checkout", "-q", "-b", "side", base)
        r.commit(GOOD, files={"b.txt": "b\n"})
        r.git("checkout", "-q", "agent/x")
        r.git("merge", "-q", "--no-ff", "-m", "Merge side without trailers", "side")
        head = r.git("rev-parse", "HEAD")
        self.assertEqual(len(r.git("rev-list", "--parents", "-n", "1", head).split()), 3, "fixture is a merge")
        self.passes(self.check(r, base, head), 2)

    def test_side_commit_of_a_merge_is_still_checked(self):
        r = self.new_repo()
        base = r.commit("Base\n")
        r.git("checkout", "-q", "-b", "agent/x")
        r.commit(GOOD, files={"a.txt": "a\n"})
        r.git("checkout", "-q", "-b", "side", base)
        bad = r.commit("Side work\n", files={"b.txt": "b\n"})
        r.git("checkout", "-q", "agent/x")
        r.git("merge", "-q", "--no-ff", "-m", "Merge side", "side")
        self.fails(self.check(r, base, r.git("rev-parse", "HEAD")), {bad: "missing Agent and Tier lines"}, 2)

    def test_zero_commits_pass(self):
        r, base, head, _ = self.branch_repo()
        self.assertEqual(base, head)
        self.passes(self.check(r, base, head), 0)

    def test_default_repo_is_cwd(self):
        r, base, head, shas = self.branch_repo("Work\n")
        cp = self.run_ct("--branch", "agent/x", "--base", base, "--head", head, cwd=r.path)
        self.fails(cp, {shas[0]: "missing Agent and Tier lines"}, 1)

    def test_branch_names_as_revisions(self):
        r, _, _, shas = self.branch_repo(GOOD, "Work\n\nTier: T0\n")
        self.fails(self.check(r, "main", "agent/x"), {shas[1]: "missing Agent line"}, 2)


# ---------------------------------------------------------------- AC6

class TestAC6LineRules(Base):
    def test_wp8_footer_before_co_authored_by(self):
        self.one_passes(f"Subject\n\nBody.\n\n{AGENT}\nTier: T2\n\n"
                        "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n")

    def test_lines_in_different_paragraphs(self):
        self.one_passes(f"Subject\n\n{AGENT}\n\nSome text.\n\nTier: T1\n")

    def test_lines_in_the_subject_paragraph(self):
        self.one_passes(f"Subject\n{AGENT}\nTier: T0\n")

    def test_reviewer_form_and_tier_note(self):
        self.one_passes("Subject\n\nAgent: x / m / medium (2 rounds, APPROVE)\nTier: T2 (gate code; human merges)\n")

    def test_several_agent_lines(self):
        self.one_passes("Subject\n\nAgent: lead / claude-sonnet-5-5 / high\n"
                        "Agent: reviewer / gpt-6.1-sol / medium (2 rounds, APPROVE)\nTier: T1\n")

    def test_model_with_slash_and_colon(self):
        self.one_passes("Subject\n\nAgent: bulk-worker / deepseek/deepseek-v4.1:free / low\nTier: T0\n")

    def test_trailing_whitespace_allowed(self):
        self.one_passes(f"Subject\n\n{AGENT}  \t\nTier: T1\n")

    def test_crlf_message(self):
        self.one_passes(f"Subject\r\n\r\n{AGENT}\r\nTier: T1\r\n")

    def test_parenthesis_form_fails(self):
        self.one_fails("Subject\n\nAgent: x (m, medium)\nTier: T1\n", "missing Agent line")

    def test_upper_case_role_fails(self):
        self.one_fails("Subject\n\nAgent: X / m / medium\nTier: T1\n", "missing Agent line")

    def test_two_parts_fail(self):
        self.one_fails("Subject\n\nAgent: x / m\nTier: T1\n", "missing Agent line")

    def test_indented_agent_fails(self):
        self.one_fails("Subject\n\n Agent: x / m / medium\nTier: T1\n", "missing Agent line")

    def test_agent_inside_longer_line_fails(self):
        self.one_fails("Subject\n\nSee Agent: x / m / medium\nTier: T1\n", "missing Agent line")

    def test_text_after_agent_effort_fails(self):
        self.one_fails("Subject\n\nAgent: x / m / medium extra\nTier: T1\n", "missing Agent line")

    def test_lower_case_key_fails(self):
        self.one_fails("Subject\n\nagent: x / m / medium\nTier: T1\n", "missing Agent line")

    def test_tier_t3_fails(self):
        self.one_fails(f"Subject\n\n{AGENT}\nTier: T3\n", "missing Tier line")

    def test_tier_t20_fails(self):
        self.one_fails(f"Subject\n\n{AGENT}\nTier: T20\n", "missing Tier line")

    def test_tier_without_space_fails(self):
        self.one_fails(f"Subject\n\n{AGENT}\nTier:T1\n", "missing Tier line")

    def test_indented_tier_fails(self):
        self.one_fails(f"Subject\n\n{AGENT}\n  Tier: T1\n", "missing Tier line")

    def test_tier_inside_longer_line_fails(self):
        self.one_fails(f"Subject\n\n{AGENT}\nThe Tier: T1 was set.\n", "missing Tier line")

    def test_both_lookalikes_fail(self):
        self.one_fails("Subject\n\nAgent: x (m, medium)\nTier: T3\n", "missing Agent and Tier lines")

    def test_message_over_64_kb_fails(self):
        body = ("filler line\n" * 6000)  # 72 000 bytes
        self.one_fails(f"Subject\n\n{body}\n{AGENT}\nTier: T1\n", "message too large")

    def test_message_just_under_64_kb_passes(self):
        body = ("filler line\n" * 5000)  # 60 000 bytes
        self.one_passes(f"Subject\n\n{body}\n{AGENT}\nTier: T1\n")

    def test_fenced_example_counts(self):
        """Stated limit: any matching line counts, also inside a fenced example."""
        self.one_passes(f"Subject\n\nExample footer:\n\n```\n{AGENT}\nTier: T1\n```\n")

    def test_merge_without_trailers_is_skipped(self):
        """Stated limit: merge commits carry no trailer requirement."""
        r = self.new_repo()
        base = r.commit("Base\n")
        r.git("checkout", "-q", "-b", "agent/x")
        r.git("checkout", "-q", "-b", "side")
        r.commit(GOOD, files={"s.txt": "s\n"})
        r.git("checkout", "-q", "agent/x")
        r.commit(GOOD, files={"a.txt": "a\n"})
        r.git("merge", "-q", "--no-ff", "-m", "Merge, no footer at all", "side")
        self.passes(self.check(r, base, r.git("rev-parse", "HEAD")), 2)


# ---------------------------------------------------------------- AC7

class TestAC7Branches(Base):
    def stub_path(self):
        """A `git` on PATH that leaves a marker; proves the skip path runs no git."""
        bin_ = self.tmp / "bin"
        bin_.mkdir()
        marker = self.tmp / "git-was-called"
        g = bin_ / "git"
        g.write_text(f"#!/bin/sh\necho called >> '{marker}'\nexit 1\n")
        g.chmod(0o755)
        return self.env(PATH=f"{bin_}{os.pathsep}{os.environ.get('PATH', '')}"), marker

    def assert_skipped(self, branch):
        env, marker = self.stub_path()
        cp = self.run_ct("--branch", branch, "--base", "main", "--head", "HEAD", "--repo", self.cwd, env=env)
        self.assertEqual(cp.returncode, 0, f"{branch}: exit {cp.returncode}; {cp.stdout!r} {cp.stderr!r}")
        self.assertEqual(cp.stdout.splitlines(), [SKIPPED], branch)
        self.assertFalse(marker.exists(), f"{branch}: git was run on the skip path")

    def test_feature_branch_skipped_without_git(self):
        self.assert_skipped("feature/x")

    def test_main_skipped_without_git(self):
        self.assert_skipped("main")

    def test_agents_prefix_is_not_agent(self):
        self.assert_skipped("agents/x")

    def test_nested_agent_is_not_agent(self):
        self.assert_skipped("feature/agent/x")

    def test_capitalised_agent_branch_is_checked(self):
        r, base, head, shas = self.branch_repo("Work\n\nTier: T1\n")
        self.fails(self.check(r, base, head, branch="Agent/x"), {shas[0]: "missing Agent line"}, 1)

    def test_upper_case_agent_branch_is_checked(self):
        r, base, head, shas = self.branch_repo("Work\n\nTier: T1\n")
        self.fails(self.check(r, base, head, branch="AGENT/X"), {shas[0]: "missing Agent line"}, 1)

    BAD = ["-x", "--output=PWNED", "a b", "", "x;y", "a$(id)", "HEAD~1", ".hidden", "/abs", "a" * 256, "a\nb"]

    def bad_value(self, flag):
        r, base, head, _ = self.branch_repo("Work\n")
        args = {"--branch": "agent/x", "--base": base, "--head": head}
        pwned = self.tmp / "pwned"
        for v in self.BAD:
            v = v.replace("PWNED", str(pwned))
            a = dict(args)
            a[flag] = v
            argv = [f"{k}={x}" for k, x in a.items()] + ["--repo", str(r.path)]
            with self.subTest(flag=flag, value=v[:40]):
                self.usage_error(self.run_ct(*argv), f"{flag}={v[:40]!r}")
                self.assertFalse(pwned.exists(), "option injection wrote a file")

    def test_bad_branch_value(self):
        self.bad_value("--branch")

    def test_bad_base_value(self):
        self.bad_value("--base")

    def test_bad_head_value(self):
        self.bad_value("--head")

    def test_255_characters_allowed(self):
        r, base, head, _ = self.branch_repo(GOOD)
        self.passes(self.check(r, base, head, branch="agent/" + "a" * 249), 1)

    def test_missing_arguments(self):
        r, base, head, _ = self.branch_repo(GOOD)
        self.usage_error(self.run_ct("--branch", "agent/x", "--head", head, "--repo", r.path), "no --base")
        self.usage_error(self.run_ct("--branch", "agent/x", "--base", base, "--repo", r.path), "no --head")
        self.usage_error(self.run_ct("--base", base, "--head", head, "--repo", r.path), "no --branch")

    def test_unknown_base(self):
        r, _, head, _ = self.branch_repo(GOOD)
        self.usage_error(self.check(r, "nosuchrev", head), "unknown base")

    def test_unknown_head(self):
        r, base, _, _ = self.branch_repo(GOOD)
        self.usage_error(self.check(r, base, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"), "unknown head")

    def test_not_a_repo_on_agent_branch(self):
        self.usage_error(self.run_ct("--branch", "agent/x", "--base", "main", "--head", "HEAD",
                                     "--repo", self.cwd), "not a repo")

    def fast_import_repo(self):
        """main = base; refs b500 and agent/x carry 500 and 501 clean commits on top."""
        r = self.new_repo("big")
        who = "Fixture <fixture@example.invalid> 1767614400 +0000"
        out = []

        def commit(ref, mark, msg, frm=None):
            data = msg.encode()
            out.append(f"commit {ref}\nmark :{mark}\nauthor {who}\ncommitter {who}\ndata {len(data)}\n".encode())
            out.append(data + b"\n")
            if frm:
                out.append(f"from :{frm}\n".encode())
            out.append(b"\n")

        commit("refs/heads/main", 1, "Base\n")
        for k in range(1, 502):
            commit("refs/heads/agent/x", k + 1, f"Work {k}\n\n{AGENT}\nTier: T1\n", frm=1 if k == 1 else None)
        out.append(b"reset refs/heads/b500\nfrom :501\n\n")
        r.git("fast-import", "--quiet", input=b"".join(out))
        self.assertEqual(r.git("rev-list", "--count", "main..agent/x"), "501")
        self.assertEqual(r.git("rev-list", "--count", "main..b500"), "500")
        return r

    def test_501_commits_too_large(self):
        r = self.fast_import_repo()
        cp = self.check(r, "main", "agent/x")
        self.assertEqual(cp.returncode, 1, f"{cp.stdout!r} {cp.stderr!r}")
        self.assertTrue(any(l.startswith("too large: ") for l in cp.stdout.splitlines()), cp.stdout)

    def test_500_commits_pass(self):
        r = self.fast_import_repo()
        self.passes(self.check(r, "main", "b500"), 500)


# ---------------------------------------------------------------- AC12

HOOKS = ("gpg", "external", "textconv", "fsmonitor", "pager", "pagerlog")


class TestAC12Hardening(Base):
    def hostile_repo(self):
        """Repo whose config and attributes would run a marker script through git; the controls at
        the end prove each mechanism is live for plain git before the script is judged."""
        hooks, self.markers = self.tmp / "hooks", self.tmp / "markers"
        hooks.mkdir()
        self.markers.mkdir()
        for h in HOOKS:
            p = hooks / f"{h}.sh"
            p.write_text(f"#!/bin/sh\necho \"$@\" > '{self.markers / h}'\nexit 0\n")
            p.chmod(0o755)
        r = self.new_repo("hostile")
        r.git("config", "log.showSignature", "true")
        r.git("config", "gpg.program", str(hooks / "gpg.sh"))
        r.git("config", "diff.external", str(hooks / "external.sh"))
        r.git("config", "diff.evil.textconv", str(hooks / "textconv.sh"))
        r.git("config", "core.fsmonitor", str(hooks / "fsmonitor.sh"))
        r.git("config", "core.pager", str(hooks / "pager.sh"))
        r.git("config", "pager.log", str(hooks / "pagerlog.sh"))
        base = r.commit("Base\n", files={".gitattributes": "* diff=evil\n", "a.txt": "one\n"})
        r.git("checkout", "-q", "-b", "agent/x")
        r.commit(GOOD, files={"a.txt": "two\n"})
        r.commit(GOOD, files={"a.txt": "three\n"})
        # Re-create the tip as a signed commit (fake signature) so log.showSignature calls gpg.program.
        raw = r.git("cat-file", "commit", "HEAD")
        head_part, msg = raw.split("\n\n", 1)
        sig = "gpgsig -----BEGIN PGP SIGNATURE-----\n \n iQEzBAABCAAdFiEE\n -----END PGP SIGNATURE-----"
        signed = r.git("hash-object", "-t", "commit", "-w", "--stdin",
                       input=f"{head_part}\n{sig}\n\n{msg}\n".encode())
        r.git("update-ref", "refs/heads/agent/x", signed)
        r.git("checkout", "-q", "-b", "agent/bad", base)
        bad = r.commit("No footer\n", files={"a.txt": "bad\n"})
        r.git("checkout", "-q", "agent/x")
        # Controls: each mechanism fires for plain git.
        r.git("log", "-1", "agent/x")
        self.assertTrue((self.markers / "gpg").exists(), "fixture: gpg.program not live")
        r.git("diff", base, "agent/x", check=False)
        self.assertTrue((self.markers / "external").exists(), "fixture: diff.external not live")
        r.git("diff", "--no-ext-diff", base, "agent/x")
        self.assertTrue((self.markers / "textconv").exists(), "fixture: textconv not live")
        r.git("status", check=False)
        self.assertTrue((self.markers / "fsmonitor").exists(), "fixture: core.fsmonitor not live")
        for m in self.markers.iterdir():
            m.unlink()
        return r, base, signed, bad

    def state(self, r):
        env = dict(r.env, GIT_OPTIONAL_LOCKS="0")
        q = lambda *a: subprocess.run([REAL_GIT, "-C", str(r.path), "-c", "core.fsmonitor=false", *a], env=env,
                                      capture_output=True, check=True).stdout
        files = {}
        for dp, _, fn in os.walk(r.path / ".git"):
            for n in fn:
                st = os.lstat(os.path.join(dp, n))
                files[os.path.relpath(os.path.join(dp, n), r.path)] = (st.st_size, st.st_mtime_ns)
        return (q("status", "--porcelain", "-uall"), q("for-each-ref", "--format=%(refname) %(objectname)"),
                q("symbolic-ref", "HEAD"), files)

    def assert_harmless(self, r, before, label):
        fired = sorted(m.name for m in self.markers.iterdir())
        self.assertEqual(fired, [], f"{label}: hostile config ran {fired}")
        self.assertEqual(self.state(r), before, f"{label}: repo, index or refs changed")

    def test_every_entry_point(self):
        r, base, head, bad = self.hostile_repo()
        before = self.state(r)
        runs = [
            ("clean range", lambda: self.check(r, base, head), 0),
            ("failing range", lambda: self.check(r, base, "agent/bad"), 1),
            ("zero commits", lambda: self.check(r, head, head), 0),
            ("unknown revision", lambda: self.check(r, "nosuchrev", head), 2),
            ("default repo", lambda: self.run_ct("--branch", "agent/x", "--base", base, "--head", head, cwd=r.path), 0),
            ("branch names", lambda: self.check(r, "main", "agent/x"), 0),
            ("non-agent branch", lambda: self.check(r, base, head, branch="feature/x"), 0),
        ]
        for label, run, code in runs:
            with self.subTest(run=label):
                cp = run()
                self.assertEqual(cp.returncode, code, f"{label}: {cp.stdout!r} {cp.stderr!r}")
                self.assert_harmless(r, before, label)

    def test_clean_range_result_under_hostile_config(self):
        r, base, head, _ = self.hostile_repo()
        self.passes(self.check(r, base, head), 2)

    def test_failing_range_result_under_hostile_config(self):
        r, base, _, bad = self.hostile_repo()
        self.fails(self.check(r, base, "agent/bad"), {bad: "missing Agent and Tier lines"}, 1)

    def test_git_argv_and_environment(self):
        """Section 3: every git call carries the fixed flags and GIT_TERMINAL_PROMPT=0; log gets
        --no-show-signature. A recording `git` on PATH hands each call to the real git."""
        r, base, head, _ = self.branch_repo(GOOD, "Work\n\nTier: T1\n")
        bin_ = self.tmp / "bin"
        bin_.mkdir()
        log = self.tmp / "calls.jsonl"
        g = bin_ / "git"
        g.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                     f"with open({str(log)!r}, 'a') as f:\n"
                     "    f.write(json.dumps({'argv': sys.argv[1:], 'prompt': os.environ.get('GIT_TERMINAL_PROMPT')}) + '\\n')\n"
                     f"os.execv({REAL_GIT!r}, [{REAL_GIT!r}] + sys.argv[1:])\n")
        g.chmod(0o755)
        env = self.env(PATH=f"{bin_}{os.pathsep}{os.environ.get('PATH', '')}")
        cp = self.check(r, base, head, env=env)
        self.assertEqual(cp.returncode, 1, f"{cp.stdout!r} {cp.stderr!r}")
        self.assertTrue(log.exists(), "git is not looked up as `git` on PATH (GIT constant)")
        calls = [json.loads(l) for l in log.read_text().splitlines()]
        self.assertTrue(any("log" in c["argv"] for c in calls), calls)
        for c in calls:
            a = c["argv"]
            self.assertEqual(a[0], "-C", a)
            self.assertEqual(Path(a[1]).resolve(), r.path.resolve(), a)
            self.assertEqual(a[2:2 + len(FLAGS)], FLAGS, a)
            self.assertEqual(c["prompt"], "0", f"GIT_TERMINAL_PROMPT for {a}")
            if a[2 + len(FLAGS)] == "log":
                self.assertIn("--no-show-signature", a)
                self.assertIn("--no-merges", a)
                self.assertIn("-z", a)


# ---------------------------------------------------------------- AC13

class TestAC13OutputSafety(Base):
    def assert_safe(self, cp):
        self.assertTrue(printable_only(cp.stdout), repr(cp.stdout))
        for l in cp.stdout.splitlines():
            self.assertTrue(LINE_FORMS.fullmatch(l), f"unexpected line {l!r}")

    def test_control_bytes_and_lookalikes_are_not_printed(self):
        msg = ("Subject CANARY-77ab \x1b[31mred\x1b[0m\x07\n\n"
               "Agent: x\x1b[2K / m / medium\n"
               "Agent: x / m / medium\u202e\n"
               "0123456789ab: missing Tier line\n"
               "trailers: 0 commits checked, 0 failing\n"
               "Tier: T9\x1b]0;title\x07\n")
        r, base, head, shas = self.branch_repo(msg, verbatim=True)
        cp = self.check(r, base, head)
        self.fails(cp, {shas[0]: "missing Agent and Tier lines"}, 1)
        self.assert_safe(cp)
        self.assertNotIn("CANARY-77ab", cp.stdout + cp.stderr)
        self.assertNotIn("0123456789ab", cp.stdout)

    def test_message_text_of_passing_commit_is_not_printed(self):
        r, base, head, _ = self.branch_repo(f"Subject CANARY-91cd\n\nSecret body CANARY-91cd\n\n{AGENT}\nTier: T1\n")
        cp = self.check(r, base, head)
        self.passes(cp, 1)
        self.assert_safe(cp)
        self.assertNotIn("CANARY-91cd", cp.stdout + cp.stderr)

    def test_large_message_text_is_not_printed(self):
        r, base, head, shas = self.branch_repo("Subject\n\n" + "CANARY-5e5e\n" * 7000, verbatim=True)
        cp = self.check(r, base, head)
        self.fails(cp, {shas[0]: "message too large"}, 1)
        self.assert_safe(cp)
        self.assertNotIn("CANARY-5e5e", cp.stdout + cp.stderr)


# ---------------------------------------------------------------- AC14

class TestAC14SourceScan(unittest.TestCase):
    def test_no_shell_eval_network(self):
        source_scan(self, SCRIPT)

    def test_git_through_popen_only(self):
        """Section 3: git runs through subprocess.Popen, never run, call or check_output."""
        tree = source_scan(self, SCRIPT)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "subprocess":
                self.assertIn(node.attr, ("Popen", "PIPE", "DEVNULL", "TimeoutExpired"),
                              f"line {node.lineno}: subprocess.{node.attr}")
            if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                for a in node.names:
                    self.assertIn(a.name, ("Popen", "PIPE", "DEVNULL", "TimeoutExpired"), f"from subprocess import {a.name}")


# ---------------------------------------------------------------- AC20

DRIVER = r"""
import importlib.util, sys
path, stub, max_bytes, timeout = sys.argv[1:5]
spec = importlib.util.spec_from_file_location("check_trailers", path)
m = importlib.util.module_from_spec(spec)
sys.modules["check_trailers"] = m
spec.loader.exec_module(m)
m.GIT, m.MAX_BYTES, m.GIT_TIMEOUT = stub, int(max_bytes), int(timeout)
sys.argv = [path] + sys.argv[5:]
sys.exit(m.main())
"""

SMALL = 100_000


class TestAC20RunGit(Base):
    def setUp(self):
        super().setUp()
        self.pidfile = self.tmp / "stub.pid"

    def load(self):
        self.assertTrue(SCRIPT.is_file(), f"script missing: {SCRIPT}")
        spec = importlib.util.spec_from_file_location("check_trailers_under_test", SCRIPT)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def stub(self, body):
        p = self.tmp / "git-stub"
        p.write_text(f"#!{sys.executable}\nimport os, sys, time\n"
                     f"open({str(self.pidfile)!r}, 'w').write(str(os.getpid()))\n" + body)
        p.chmod(0o755)
        return p

    def flood(self):
        # Ignores a closed pipe, so only a kill ends it.
        return self.stub("chunk = b'x' * 65536\ntry:\n    while True:\n        sys.stdout.buffer.write(chunk)\n"
                         "        sys.stdout.buffer.flush()\nexcept OSError:\n    time.sleep(60)\n")

    def sleeper(self):
        return self.stub("time.sleep(60)\n")

    def stub_pid(self):
        self.assertTrue(self.pidfile.exists(), "the stub never ran: run_git does not start GIT")
        pid = int(self.pidfile.read_text())
        self.addCleanup(kill_quietly, pid)
        return pid

    def call_run_git(self, m, stub, max_bytes=SMALL, timeout=2):
        m.GIT, m.MAX_BYTES, m.GIT_TIMEOUT = str(stub), max_bytes, timeout
        out, err = io.StringIO(), io.StringIO()
        t0 = time.monotonic()
        result, raised = None, None
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                result = m.run_git(["log", "--format=%H"], str(self.tmp))
            except BaseException as e:  # SystemExit or the script's own error type
                raised = e
        return result, raised, time.monotonic() - t0, out.getvalue(), err.getvalue()

    def drive(self, stub, max_bytes=SMALL, timeout=2):
        self.assertTrue(SCRIPT.is_file(), f"script missing: {SCRIPT}")
        r, base, head, _ = self.branch_repo(GOOD)
        t0 = time.monotonic()
        cp = subprocess.run([sys.executable, "-c", DRIVER, str(SCRIPT), str(stub), str(max_bytes), str(timeout),
                             "--branch", "agent/x", "--base", base, "--head", head, "--repo", str(r.path)],
                            cwd=str(self.cwd), env=self.env(), capture_output=True, text=True,
                            errors="replace", timeout=60)
        return cp, time.monotonic() - t0

    def test_constants(self):
        m = self.load()
        self.assertEqual(m.GIT, "git")
        self.assertEqual(m.MAX_COMMITS, 500)
        self.assertEqual(m.MAX_BYTES, 5_000_000)
        self.assertEqual(m.MAX_LINE, 4096)
        self.assertEqual(m.GIT_TIMEOUT, 60)

    def test_run_git_is_the_only_place_that_starts_git(self):
        self.assertTrue(SCRIPT.is_file(), f"script missing: {SCRIPT}")
        tree = ast.parse(SCRIPT.read_text())
        fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_git"]
        self.assertEqual(len(fns), 1, "one module-level run_git")
        self.assertEqual([a.arg for a in fns[0].args.args[:2]], ["args", "repo"])
        inside = {id(n) for n in ast.walk(fns[0])}
        popen = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and ((isinstance(n.func, ast.Attribute) and n.func.attr == "Popen")
                      or (isinstance(n.func, ast.Name) and n.func.id == "Popen"))]
        self.assertEqual(len(popen), 1, "exactly one Popen call")
        self.assertIn(id(popen[0]), inside, "the Popen call is inside run_git")

    def test_run_git_argv(self):
        m = self.load()
        log = self.tmp / "argv.json"
        stub = self.stub(f"import json\njson.dump({{'argv': sys.argv[1:], 'prompt': os.environ.get('GIT_TERMINAL_PROMPT')}},"
                         f" open({str(log)!r}, 'w'))\n")
        result, raised, _, _, _ = self.call_run_git(m, stub)
        self.assertIsNone(raised, f"run_git raised {raised!r} on a clean exit")
        self.stub_pid()
        rec = json.loads(log.read_text())
        a = rec["argv"]
        self.assertEqual(a[0], "-C")
        self.assertEqual(Path(a[1]).resolve(), self.tmp)
        self.assertEqual(a[2:2 + len(FLAGS)], FLAGS)
        rest = iter(a[2 + len(FLAGS):])
        self.assertTrue(all(x in rest for x in ["log", "--format=%H"]), f"args not passed in order: {a}")
        self.assertEqual(rec["prompt"], "0")
        self.assertEqual(result, b"")

    def test_a_output_over_max_bytes_is_killed(self):
        m = self.load()
        result, raised, took, out, _ = self.call_run_git(m, self.flood())
        pid = self.stub_pid()
        self.assertIsNotNone(raised, f"run_git returned {type(result).__name__} instead of failing")
        self.assertLess(took, 10, "run_git kept reading")
        self.assertTrue(pid_dead(pid), "stub still alive or a zombie after run_git")
        if isinstance(raised, SystemExit):
            self.assertEqual(raised.code, 1)
            self.assertIn("too large", out)

    def test_a_script_exits_1_too_large(self):
        cp, took = self.drive(self.flood())
        pid = self.stub_pid()
        self.assertEqual(cp.returncode, 1, f"{cp.stdout!r} {cp.stderr[-500:]!r}")
        self.assertTrue(any(l.startswith("too large: ") for l in cp.stdout.splitlines()), cp.stdout)
        self.assertLess(took, 20)
        self.assertTrue(pid_dead(pid), "stub still alive after the script exited")

    def test_b_timeout_is_killed(self):
        m = self.load()
        result, raised, took, out, _ = self.call_run_git(m, self.sleeper(), timeout=1)
        pid = self.stub_pid()
        self.assertIsNotNone(raised, f"run_git returned {result!r} after a timeout")
        self.assertLess(took, 10, "run_git waited past GIT_TIMEOUT")
        self.assertTrue(pid_dead(pid), "stub still alive or a zombie after the timeout")
        if isinstance(raised, SystemExit):
            self.assertEqual(raised.code, 2)
            self.assertEqual(out, "")

    def test_b_script_exits_2_git_timed_out(self):
        cp, took = self.drive(self.sleeper(), timeout=1)
        pid = self.stub_pid()
        self.assertEqual(cp.returncode, 2, f"{cp.stdout!r} {cp.stderr[-500:]!r}")
        self.assertEqual(cp.stdout, "")
        self.assertIn("git timed out", cp.stderr)
        self.assertLess(took, 20)
        self.assertTrue(pid_dead(pid), "stub still alive after the script exited")

    def test_c_exactly_max_bytes_is_returned(self):
        m = self.load()
        data_expr = f"(bytes(range(256)) * {SMALL // 256 + 1})[:{SMALL}]"
        stub = self.stub(f"sys.stdout.buffer.write({data_expr})\nsys.stdout.buffer.flush()\n")
        result, raised, _, _, _ = self.call_run_git(m, stub)
        self.stub_pid()
        self.assertIsNone(raised, f"run_git rejected exactly MAX_BYTES: {raised!r}")
        self.assertEqual(result, (bytes(range(256)) * (SMALL // 256 + 1))[:SMALL])

    def test_c_one_byte_over_is_rejected(self):
        m = self.load()
        stub = self.stub(f"sys.stdout.buffer.write(b'x' * {SMALL + 1})\nsys.stdout.buffer.flush()\n")
        result, raised, _, _, _ = self.call_run_git(m, stub)
        self.stub_pid()
        self.assertIsNotNone(raised, "MAX_BYTES + 1 accepted")


# ---------------------------------------------------------------- security round 1 (spec section 17)
# Shared with test_diff_guard.py (imported there): promisor fixture, environment and process-group stubs.

from unittest import mock  # noqa: E402

SCRUBBED_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"}
HOSTILE_PARENT_ENV = {
    "GIT_DIR": "/nonexistent/elsewhere.git", "GIT_WORK_TREE": "/nonexistent", "GIT_INDEX_FILE": "/nonexistent/index",
    "GIT_OBJECT_DIRECTORY": "/nonexistent/objects", "GIT_CONFIG_PARAMETERS": "'core.sshcommand'='/bin/false'",
    "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.pager", "GIT_CONFIG_VALUE_0": "/bin/false",
    "GIT_NO_LAZY_FETCH": "0", "GIT_OPTIONAL_LOCKS": "1", "GIT_TERMINAL_PROMPT": "1",
    "GIT_LITERAL_PATHSPECS": "1", "GIT_SSH_COMMAND": "/bin/false", "GIT_EXTERNAL_DIFF": "/bin/false",
    "GIT_TRACE": "1", "T3_ROUND1_CANARY": "kept",
}


def load_module(test, script, name):
    test.assertTrue(script.is_file(), f"script missing: {script}")
    spec = importlib.util.spec_from_file_location(name, script)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def call_run_git(m, stub, args, repo, max_bytes=100_000, timeout=2, environ=None):
    """m.run_git with GIT=stub, in this process (optionally under a patched os.environ)."""
    m.GIT, m.MAX_BYTES, m.GIT_TIMEOUT = str(stub), max_bytes, timeout
    out, err = io.StringIO(), io.StringIO()
    result, raised = None, None
    t0 = time.monotonic()
    with mock.patch.dict(os.environ, environ or {}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            result = m.run_git(list(args), str(repo))
        except BaseException as e:  # SystemExit or the script's own error type
            raised = e
    return result, raised, time.monotonic() - t0, out.getvalue(), err.getvalue()


def env_stub(path, record):
    """A `git` that records its environment as JSON and exits 0 with no output."""
    path.write_text(f"#!{sys.executable}\nimport json, os\n"
                    f"json.dump(dict(os.environ), open({str(record)!r}, 'w'))\n")
    path.chmod(0o755)
    return path


def group_stub(path, pidfile, childfile, mode):
    """A `git` that forks a sleeping child (pid written to childfile), then stalls or floods.

    The child stays in git's process group and holds git's stdout open, so only a kill of the whole
    group ends it."""
    assert mode in ("stall", "flood"), mode
    tail = ("chunk = b'x' * 65536\ntry:\n    while True:\n        sys.stdout.buffer.write(chunk)\n"
            "        sys.stdout.buffer.flush()\nexcept OSError:\n    time.sleep(60)\n") if mode == "flood" \
        else "time.sleep(60)\n"
    path.write_text(f"#!{sys.executable}\nimport os, sys, time\n"
                    f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
                    "child = os.fork()\n"
                    "if child == 0:\n    time.sleep(60)\n    os._exit(0)\n"
                    f"with open({str(childfile)!r}, 'w') as f:\n    f.write(str(child))\n" + tail)
    path.chmod(0o755)
    return path


def promisor_config(test, repo, marker_dir):
    """AC21 fixture config: a partial clone whose promisor remote is reached through a marker sshCommand."""
    marker = marker_dir / "ssh-marker.sh"
    fired = marker_dir / "ssh-fired"
    marker.write_text(f"#!/bin/sh\necho \"$@\" > '{fired}'\nexit 128\n")
    marker.chmod(0o755)
    for k, v in (("core.repositoryformatversion", "1"), ("extensions.partialClone", "origin"),
                 ("remote.origin.promisor", "true"), ("remote.origin.url", "ssh://example.invalid/r"),
                 ("core.sshCommand", str(marker))):
        repo.git("config", k, v)
    return fired


def drop_loose(test, repo, sha):
    p = repo.path / ".git" / "objects" / sha[:2] / sha[2:]
    test.assertTrue(p.is_file(), f"fixture: object {sha} is not a loose object")
    p.unlink()
    cp = subprocess.run([REAL_GIT, "-C", str(repo.path), "cat-file", "-e", sha],
                        env=dict(repo.env, GIT_NO_LAZY_FETCH="1"), capture_output=True)
    test.assertNotEqual(cp.returncode, 0, f"fixture: object {sha} is still readable")


def require_lazy_fetch(test, repo, fired, *git_args):
    """Precondition: plain git with this config (no scrubbing) runs the marker for `git_args`."""
    repo.git(*git_args, check=False)
    if not fired.exists():
        test.skipTest(f"precondition: plain git ({REAL_GIT}) did not attempt a lazy fetch for {git_args[0]}; "
                      "the AC21 fixture cannot show the bug with this git")
    fired.unlink()


class TestSecurityRound1AC21NoLazyFetch(Base):
    """AC21: with a parent commit missing in a promisor repo, check_trailers never runs the
    promisor's sshCommand (lazy fetch), and exits 2 with a message."""

    def fixture(self):
        r, base, head, shas = self.branch_repo(GOOD, GOOD, GOOD)
        fired = promisor_config(self, r, self.tmp)
        # The base is the parent of the first branch commit. git 2.56 does not lazy-fetch a parent met
        # inside the walk ("revision walk setup failed"), but it does fetch a missing revision argument.
        drop_loose(self, r, base)
        require_lazy_fetch(self, r, fired, "log", "--format=%H", f"{base}..{head}", "--")
        return r, base, head, fired

    def test_missing_parent_commit_no_fetch(self):
        r, base, head, fired = self.fixture()
        for label, env in (("plain env", self.env()), ("parent says GIT_NO_LAZY_FETCH=0", self.env(GIT_NO_LAZY_FETCH="0"))):
            with self.subTest(env=label):
                cp = self.check(r, base, head, env=env)
                self.assertFalse(fired.exists(), f"{label}: check_trailers ran the promisor sshCommand (lazy fetch)")
                self.usage_error(cp, label)
            if fired.exists():
                fired.unlink()


class TestSecurityRound1AC21GitEnvironment(Base):
    """AC21: run_git drops every GIT_* variable of the parent and sets exactly
    GIT_TERMINAL_PROMPT=0, GIT_NO_LAZY_FETCH=1 and GIT_OPTIONAL_LOCKS=0; other variables stay."""

    def test_child_environment(self):
        m = load_module(self, SCRIPT, "check_trailers_round1_env")
        record = self.tmp / "env.json"
        stub = env_stub(self.tmp / "git-env-stub", record)
        result, raised, _, _, _ = call_run_git(m, stub, ["log", "--format=%H"], self.tmp, environ=HOSTILE_PARENT_ENV)
        self.assertIsNone(raised, f"run_git raised {raised!r} on a clean exit")
        self.assertTrue(record.exists(), "the stub never ran")
        env = json.loads(record.read_text())
        self.assertEqual({k: v for k, v in env.items() if k.startswith("GIT_")}, SCRUBBED_ENV)
        self.assertEqual(env.get("T3_ROUND1_CANARY"), "kept", "non-GIT variables are kept")
        self.assertEqual(env.get("PATH"), os.environ.get("PATH"))


class TestSecurityRound1AC25ProcessGroup(Base):
    """AC25: on timeout or output overflow run_git kills git's whole process group; a child that
    git started is gone afterwards (a zombie counts as alive)."""

    def run_case(self, mode):
        m = load_module(self, SCRIPT, f"check_trailers_round1_pg_{mode}")
        pidfile, childfile = self.tmp / "stub.pid", self.tmp / "child.pid"
        stub = group_stub(self.tmp / f"git-{mode}", pidfile, childfile, mode)
        result, raised, took, out, _ = call_run_git(m, stub, ["log", "--format=%H"], self.tmp, timeout=2)
        self.assertTrue(pidfile.exists() and childfile.exists(), "the stub never started its child")
        pid, child = int(pidfile.read_text()), int(childfile.read_text())
        self.addCleanup(kill_quietly, pid)
        self.addCleanup(kill_quietly, child)
        self.assertIsNotNone(raised, f"run_git returned {result!r} instead of failing")
        self.assertLess(took, 10)
        self.assertTrue(pid_dead(pid), "git (stub) still alive after run_git")
        self.assertTrue(pid_dead(child), "git's child still alive after run_git: the process group was not killed")
        return raised, out

    def test_timeout_kills_the_group(self):
        raised, out = self.run_case("stall")
        if isinstance(raised, SystemExit):
            self.assertEqual(raised.code, 2)
            self.assertEqual(out, "")

    def test_overflow_kills_the_group(self):
        raised, out = self.run_case("flood")
        if isinstance(raised, SystemExit):
            self.assertEqual(raised.code, 1)
            self.assertIn("too large", out)


class TestSecurityRound1AC26RawMessage(Base):
    """AC26: the message comes from the raw commit object (`git cat-file commit <sha>`); a NUL
    fails the commit (`message contains NUL`); the 64 KB limit counts raw message bytes; `git log`
    only lists shas (`--format=%H`)."""

    def literal_commit(self, message, encoding=None):
        """A commit on agent/x made with hash-object --literally (git's checks would refuse a NUL)."""
        r = self.new_repo()
        base = r.commit("Base without trailers\n")
        tree = r.git("rev-parse", "HEAD^{tree}")
        stamp = "1767614400 +0000"
        head = (f"tree {tree}\nparent {base}\nauthor Fixture <fixture@example.invalid> {stamp}\n"
                f"committer Fixture <fixture@example.invalid> {stamp}\n")
        if encoding:
            head += f"encoding {encoding}\n"
        sha = r.git("hash-object", "-t", "commit", "-w", "--literally", "--stdin", input=head.encode() + b"\n" + message)
        self.assertRegex(sha, r"^[0-9a-f]{40}$")
        r.git("update-ref", "refs/heads/agent/x", sha)
        raw = subprocess.run([REAL_GIT, "-C", str(r.path), "cat-file", "commit", sha], env=r.env,
                             capture_output=True, check=True).stdout
        self.assertEqual(raw.split(b"\n\n", 1)[1], message, "fixture: raw message differs")
        return r, base, sha

    def percent_b(self, r, base, sha):
        out = subprocess.run([REAL_GIT, "-C", str(r.path), "log", "-z", "--format=%H%x1f%B", f"{base}..{sha}", "--"],
                             env=r.env, capture_output=True, check=True).stdout
        return out.split(b"\x1f", 1)[1]

    def test_nul_then_70000_bytes_fails(self):
        msg = f"Subject\n\n{AGENT}\nTier: T2\n".encode() + b"\0" + b"y" * 70_000
        r, base, sha = self.literal_commit(msg)
        self.assertLess(len(self.percent_b(r, base, sha)), 1000, "fixture: %B is expected to stop at the NUL")
        cp = self.check(r, base, "agent/x")
        self.assertEqual(cp.returncode, 1, f"exit {cp.returncode}; stdout={cp.stdout!r}; stderr={cp.stderr!r}")
        lines = cp.stdout.splitlines()
        self.assertEqual(lines[-1], summary(1, 1), cp.stdout)
        self.assertIn(lines[0], (f"{sha[:12]}: message contains NUL", f"{sha[:12]}: message too large"), cp.stdout)

    def test_small_message_with_nul_fails(self):
        msg = f"Subject\n\n{AGENT}\nTier: T2\n".encode() + b"\0hidden tail\n"
        r, base, sha = self.literal_commit(msg)
        self.fails(self.check(r, base, "agent/x"), {sha: "message contains NUL"}, 1)

    def test_nul_before_the_trailers_fails(self):
        msg = b"Subject\0\n\n" + f"{AGENT}\nTier: T2\n".encode()
        r, base, sha = self.literal_commit(msg)
        self.fails(self.check(r, base, "agent/x"), {sha: "message contains NUL"}, 1)

    def test_limit_counts_raw_bytes_not_reencoded(self):
        # 40,000 Latin-1 bytes are 80,000 bytes once git log re-encodes them to UTF-8 for %B.
        msg = b"Subject\n\n" + b"\xe9" * 40_000 + b"\n\n" + f"{AGENT}\nTier: T2\n".encode()
        r, base, sha = self.literal_commit(msg, encoding="ISO-8859-1")
        self.assertGreater(len(self.percent_b(r, base, sha)), 64 * 1024, "fixture: %B is expected to re-encode")
        self.assertLess(len(msg), 64 * 1024)
        self.passes(self.check(r, base, "agent/x"), 1)

    def test_raw_message_over_64_kb_fails(self):
        msg = f"Subject\n\n{AGENT}\nTier: T2\n\n".encode() + b"z" * (64 * 1024)
        r, base, sha = self.literal_commit(msg)
        self.fails(self.check(r, base, "agent/x"), {sha: "message too large"}, 1)

    def test_log_lists_shas_and_cat_file_reads_messages(self):
        r, base, head, shas = self.branch_repo(GOOD, "Work\n\nTier: T1\n")
        bin_ = self.tmp / "bin"
        bin_.mkdir()
        log = self.tmp / "calls.jsonl"
        g = bin_ / "git"
        g.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                     f"with open({str(log)!r}, 'a') as f:\n"
                     "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                     f"os.execv({REAL_GIT!r}, [{REAL_GIT!r}] + sys.argv[1:])\n")
        g.chmod(0o755)
        cp = self.check(r, base, head, env=self.env(PATH=f"{bin_}{os.pathsep}{os.environ.get('PATH', '')}"))
        self.fails(cp, {shas[1]: "missing Agent line"}, 2)
        calls = [json.loads(l)[2 + len(FLAGS):] for l in log.read_text().splitlines()]
        logs = [c for c in calls if c and c[0] == "log"]
        self.assertTrue(logs, calls)
        for c in logs:
            formats = [a for a in c if a.startswith(("--format", "--pretty"))]
            self.assertEqual(formats, ["--format=%H"], f"log asks for more than shas: {c}")
        for sha in shas:
            self.assertIn(["cat-file", "commit", sha], calls, f"no `cat-file commit {sha}` call")


if __name__ == "__main__":
    unittest.main()
