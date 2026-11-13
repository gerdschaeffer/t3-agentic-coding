"""Acceptance tests for WP3 log_event.py (spec 003-wp3-event-log, AC1..AC10)."""

import copy
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG_EVENT = HERE / "log_event.py"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

BASE_ENV = {k: v for k, v in os.environ.items() if k != "T3_BUILD_STATE"}
BASE_ENV["PYTHONDONTWRITEBYTECODE"] = "1"

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
REQUIRED = ["slice", "tier", "stage", "role", "model", "effort"]
FINDINGS = {"blocker": 0, "should": 1, "nit": 2, "security": 3}


def valid(**over):
    ev = {
        "slice": "wp3",
        "tier": "T1",
        "stage": "tests",
        "role": "test-author",
        "model": "claude-sonnet-5-5",
        "effort": "medium",
    }
    ev.update(over)
    return ev


def full_event():
    return valid(
        attempt=2, round=3, verdict="CHANGES", findings=dict(FINDINGS), outcome="ok",
        duration=12.5, tier_raised=True, escalation="model-up", quota_pause=False,
    )


def log_event_mod():
    """Import log_event lazily so every test reports on its own."""
    return importlib.import_module("log_event")


def snap(root):
    """Snapshot of a tree (paths, modes, contents) or None when it does not exist."""
    root = Path(root)
    if not os.path.lexists(root):
        return None
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            st = p.lstat()
            data = p.read_bytes() if p.is_file() and not p.is_symlink() else None
            out[str(p.relative_to(root))] = (st.st_mode, data)
    out["."] = (root.lstat().st_mode, None)
    return out


def mode(p):
    return os.lstat(p).st_mode & 0o777


class Base(unittest.TestCase):
    def tmp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name).resolve()

    def cli(self, event=None, *, repo="wp3", state=None, stdin=None, env=None, home=None,
            umask0=False):
        """Run log_event.py. Fails (not passes) when the script is missing."""
        if not LOG_EVENT.is_file():
            raise AssertionError(f"{LOG_EVENT} does not exist")
        args = [sys.executable, str(LOG_EVENT), "--repo", repo]
        if state is not None:
            args += ["--state-dir", str(state)]
        if stdin is None:
            stdin = json.dumps(event)
        e = dict(BASE_ENV)
        e["HOME"] = str(home or self.tmp())  # never the real home
        e.update(env or {})
        return subprocess.run(
            args, input=stdin, capture_output=True, text=True, env=e, timeout=60,
            preexec_fn=(lambda: os.umask(0)) if umask0 else None,
        )

    def log_path(self, state, repo="wp3"):
        return Path(state) / repo / "events.jsonl"

    def lines(self, state, repo="wp3"):
        text = self.log_path(state, repo).read_text()
        self.assertTrue(text.endswith("\n"), "log does not end with a newline")
        return text[:-1].split("\n")

    def assertRejected(self, event=None, *, repo="wp3", stdin=None, label=""):
        state = self.tmp() / "state"
        r = self.cli(event, repo=repo, state=state, stdin=stdin)
        self.assertEqual(r.returncode, 2, f"{label}: exit {r.returncode}, stderr={r.stderr!r}")
        self.assertEqual(r.stdout, "", label)
        self.assertNotEqual(r.stderr.strip(), "", f"{label}: no message on stderr")
        self.assertFalse(os.path.lexists(state), f"{label}: state dir was created")
        return r


class AC1Append(Base):
    def test_ac1_valid_event_appends_one_line(self):
        state = self.tmp() / "state"
        ev = valid()
        r = self.cli(ev, state=state)
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        (line,) = self.lines(state)
        obj = json.loads(line)
        self.assertIsInstance(obj, dict)
        self.assertEqual(list(obj), list(ev) + ["ts"])
        self.assertRegex(obj["ts"], TS_RE)
        want = json.dumps({**ev, "ts": obj["ts"]}, separators=(",", ":"), ensure_ascii=True)
        self.assertEqual(line, want)

    def test_ac1_input_key_order_is_kept_and_two_calls_two_lines(self):
        state = self.tmp() / "state"
        ev = dict(reversed(list(valid().items())))
        for _ in range(2):
            r = self.cli(ev, state=state)
            self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        lines = self.lines(state)
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertEqual(list(json.loads(line)), list(ev) + ["ts"])

    def test_ac1_full_event_with_every_optional_field(self):
        state = self.tmp() / "state"
        ev = full_event()
        r = self.cli(ev, state=state)
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        (line,) = self.lines(state)
        obj = json.loads(line)
        self.assertEqual(list(obj), list(ev) + ["ts"])
        self.assertEqual({k: v for k, v in obj.items() if k != "ts"}, ev)


class AC2Missing(Base):
    def test_ac2_missing_required_field_fresh_state(self):
        for field in REQUIRED:
            with self.subTest(field=field):
                ev = valid()
                del ev[field]
                state = self.tmp() / "state"
                r = self.cli(ev, state=state)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertEqual(r.stdout, "")
                self.assertIn(field, r.stderr)
                self.assertFalse(os.path.lexists(state))

    def test_ac2_missing_required_field_state_with_log_unchanged(self):
        state = self.tmp() / "state"
        ok = self.cli(valid(), state=state)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        before = snap(state)
        for field in REQUIRED:
            with self.subTest(field=field):
                ev = valid()
                del ev[field]
                r = self.cli(ev, state=state)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn(field, r.stderr)
                self.assertEqual(snap(state), before)


class AC3Rejected(Base):
    def cases(self):
        c = []
        add = lambda label, ev: c.append((label, ev))  # noqa: E731
        add("tier T3", valid(tier="T3"))
        add("unknown stage", valid(stage="bogus"))
        for v in (0, "1", True, 1.5):
            add(f"attempt {v!r}", valid(attempt=v))
        add("round 0", valid(round=0))
        for label, v in (("negative", -1), ("NaN", float("nan")),
                         ("Infinity", float("inf")), ("bool", True)):
            add(f"duration {label}", valid(duration=v))
        missing = {k: v for k, v in FINDINGS.items() if k != "nit"}
        add("findings missing key", valid(findings=missing))
        add("findings extra key", valid(findings={**FINDINGS, "extra": 0}))
        add("findings negative", valid(findings={**FINDINGS, "blocker": -1}))
        add("unknown verdict", valid(verdict="MAYBE"))
        add("unknown outcome", valid(outcome="done"))
        add("unknown escalation", valid(escalation="up"))
        for v in ("yes", 1, None):
            add(f"tier_raised {v!r}", valid(tier_raised=v))
        add("unknown field", valid(extra="x"))
        add("supplied ts", valid(ts="2026-01-01T00:00:00Z"))
        add("supplied date", valid(date="2026-01-01"))
        bad = {"space": "a b", "newline": "a\nb", "trailing newline": "ab\n", "non-ASCII": "café"}
        for field in ("model", "effort", "role", "slice"):
            for label, v in bad.items():
                add(f"{field} {label}", valid(**{field: v}))
        for field in ("effort", "role", "slice"):  # model allows "/" by its own rule
            add(f"{field} slash", valid(**{field: "a/b"}))
        return c

    def test_ac3_each_rejected_case_exits_2_and_writes_nothing(self):
        for label, ev in self.cases():
            with self.subTest(case=label):
                self.assertRejected(ev, label=label)


class AC4Repo(Base):
    def test_ac4_bad_repo_names_exit_2_and_create_nothing(self):
        for name in ("..", "a/b", ".hidden", "", "a" * 65, "a\nb", "ab\n"):
            with self.subTest(repo=name):
                self.assertRejected(valid(), repo=name, label=repr(name))

    def test_ac4_repo_name_of_64_chars_is_accepted(self):
        state = self.tmp() / "state"
        name = "a" * 64
        r = self.cli(valid(), repo=name, state=state)
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertEqual(len(self.lines(state, name)), 1)


class AC5Input(Base):
    def test_ac5_unparseable_or_wrong_shape_input(self):
        for label, raw in (
            ("not json", "not json"), ("truncated", '{"slice": '), ("array", "[1]"),
            ("string", '"x"'), ("number", "123"), ("null", "null"), ("empty", ""),
            ("whitespace", "  \n "),
        ):
            with self.subTest(case=label):
                self.assertRejected(stdin=raw, label=label)

    def test_ac5_oversized_input(self):
        big = valid(slice="a" * 5000)
        self.assertRejected(big, label="line over 4096 bytes")
        huge = json.dumps(valid(slice="a" * 2_000_000))
        self.assertRejected(stdin=huge, label="2 MB input")

    def test_ac5_all_problems_listed(self):
        ev = valid(tier="T3", stage="bogus")
        del ev["role"]
        ev["extra"] = 1
        r = self.assertRejected(ev)
        problems = [ln for ln in r.stderr.splitlines() if ln.strip()]
        self.assertGreaterEqual(len(problems), 4, r.stderr)
        for field in ("tier", "stage", "role", "extra"):
            self.assertTrue(any(field in ln for ln in problems), f"{field} not named: {r.stderr}")


class AC6Modes(Base):
    def test_ac6_fresh_dirs_0700_and_log_0600(self):
        state = self.tmp() / "state"
        r = self.cli(valid(), state=state, umask0=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(mode(state), 0o700)
        self.assertEqual(mode(state / "wp3"), 0o700)
        self.assertEqual(mode(self.log_path(state)), 0o600)

    def test_ac6_existing_0755_dirs_become_0700(self):
        state = self.tmp() / "state"
        (state / "wp3").mkdir(parents=True)
        os.chmod(state, 0o755)
        os.chmod(state / "wp3", 0o755)
        r = self.cli(valid(), state=state)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(mode(state / "wp3"), 0o700)
        self.assertEqual(mode(state), 0o700)
        self.assertEqual(mode(self.log_path(state)), 0o600)


class AC7Symlinks(Base):
    def assertExit1(self, r):
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertNotEqual(r.stderr.strip(), "")

    def test_ac7_log_symlink_to_existing_file(self):
        t = self.tmp()
        state = t / "state"
        (state / "wp3").mkdir(parents=True, mode=0o700)
        target = t / "target.txt"
        target.write_bytes(b"precious\n")
        os.symlink(target, self.log_path(state))
        self.assertExit1(self.cli(valid(), state=state))
        self.assertEqual(target.read_bytes(), b"precious\n")

    def test_ac7_log_symlink_dangling_creates_no_target(self):
        t = self.tmp()
        state = t / "state"
        (state / "wp3").mkdir(parents=True, mode=0o700)
        target = t / "missing.txt"
        os.symlink(target, self.log_path(state))
        self.assertExit1(self.cli(valid(), state=state))
        self.assertFalse(os.path.lexists(target))

    def test_ac7_repo_dir_symlink(self):
        t = self.tmp()
        state = t / "state"
        state.mkdir(mode=0o700)
        other = t / "other"
        other.mkdir()
        (other / "keep.txt").write_bytes(b"keep")
        os.symlink(other, state / "wp3")
        before = snap(other)
        self.assertExit1(self.cli(valid(), state=state))
        self.assertEqual(snap(other), before)

    def test_ac7_state_root_symlink(self):
        t = self.tmp()
        real = t / "real"
        real.mkdir()
        state = t / "state"
        os.symlink(real, state)
        before = snap(real)
        self.assertExit1(self.cli(valid(), state=state))
        self.assertEqual(snap(real), before)


class AC8Precedence(Base):
    def test_ac8_state_dir_flag_beats_env_and_home(self):
        t = self.tmp()
        flag, envdir, home = t / "flag", t / "env", t / "home"
        home.mkdir()
        r = self.cli(valid(), state=flag, env={"T3_BUILD_STATE": str(envdir)}, home=home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.lines(flag)), 1)
        self.assertFalse(os.path.lexists(envdir))
        self.assertFalse(os.path.lexists(home / ".local"))

    def test_ac8_env_beats_home(self):
        t = self.tmp()
        envdir, home = t / "env", t / "home"
        home.mkdir()
        r = self.cli(valid(), env={"T3_BUILD_STATE": str(envdir)}, home=home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.lines(envdir)), 1)
        self.assertFalse(os.path.lexists(home / ".local"))

    def test_ac8_home_default(self):
        home = self.tmp() / "home"
        home.mkdir()
        r = self.cli(valid(), home=home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.lines(home / ".local" / "state" / "t3-build")), 1)


class AC9Parallel(Base):
    def test_ac9_twenty_parallel_appends(self):
        if not LOG_EVENT.is_file():
            raise AssertionError(f"{LOG_EVENT} does not exist")
        state = self.tmp() / "state"
        (state / "wp3").mkdir(parents=True, mode=0o700)
        env = dict(BASE_ENV, HOME=str(self.tmp()))
        procs = []
        for i in range(20):
            p = subprocess.Popen(
                [sys.executable, str(LOG_EVENT), "--repo", "wp3", "--state-dir", str(state)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env,
            )
            procs.append((i, p))
        for i, p in procs:
            out, err = p.communicate(json.dumps(valid(slice=f"s{i}")), timeout=60)
            self.assertEqual((p.returncode, out), (0, ""), f"proc {i}: {err}")
        lines = self.lines(state)
        self.assertEqual(len(lines), 20)
        slices = sorted(json.loads(line)["slice"] for line in lines)
        self.assertEqual(slices, sorted(f"s{i}" for i in range(20)))


class AC10Module(Base):
    def test_ac10_validate_valid_returns_empty_list(self):
        m = log_event_mod()
        self.assertEqual(m.validate(valid()), [])
        self.assertEqual(m.validate(full_event()), [])

    def test_ac10_validate_invalid_returns_list_of_str(self):
        m = log_event_mod()
        missing = valid()
        del missing["model"]
        for label, ev in (("T3", valid(tier="T3")), ("missing", missing),
                          ("ts", valid(ts="2026-01-01T00:00:00Z")),
                          ("two problems", valid(tier="T3", stage="x"))):
            with self.subTest(case=label):
                got = m.validate(ev)
                self.assertIsInstance(got, list)
                self.assertTrue(got)
                self.assertTrue(all(isinstance(p, str) for p in got))
        self.assertGreaterEqual(len(m.validate(valid(tier="T3", stage="x"))), 2)

    def test_ac10_validate_touches_no_filesystem_and_keeps_event(self):
        m = log_event_mod()
        t = self.tmp()
        old_cwd, old_home = os.getcwd(), os.environ.get("HOME")
        self.addCleanup(os.chdir, old_cwd)
        self.addCleanup(lambda: os.environ.__setitem__("HOME", old_home) if old_home is not None
                        else os.environ.pop("HOME", None))
        os.chdir(t)
        os.environ["HOME"] = str(t)
        for ev in (valid(), valid(tier="T3"), full_event()):
            before = copy.deepcopy(ev)
            m.validate(ev)
            self.assertEqual(ev, before)
        self.assertEqual(os.listdir(t), [])

    def test_ac10_append_writes_one_line_with_ts_last(self):
        m = log_event_mod()
        root = self.tmp() / "root"
        ev = valid()
        m.append(str(root), "wp3", dict(ev))
        (line,) = self.lines(root)
        obj = json.loads(line)
        self.assertEqual(list(obj), list(ev) + ["ts"])
        self.assertRegex(obj["ts"], TS_RE)


if __name__ == "__main__":
    unittest.main()
