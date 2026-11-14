"""Acceptance tests for diff_guard.py (spec 015-wp9-ci, AC8 to AC15, AC20, section 6).

Run from this folder:  python3 -m unittest test_diff_guard -v
The CLI is run as a subprocess. The module is loaded inside single tests only (constants, AC15
drift, AC20 run_git), so a missing script fails each test on its own. Fixture commits are built in
a private index (`snap`), so case-only renames, symlinks, gitlinks and odd file names do not depend
on the file system; a test that needs work-tree attributes checks the head out. Each fixture that
relies on a git behaviour (rename detection, type change, binary detection) checks it with plain
git first, so a test cannot pass for the wrong reason.

Where the spec leaves the printed path open (old or new name of a rename), both are accepted.
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
import tomllib
import unittest
from pathlib import Path

sys.dont_write_bytecode = True  # loading the module under test must leave no __pycache__

from test_check_agents import source_scan, printable_only  # noqa: E402  (shared AC14 scan)
from test_check_trailers import Repo as _Repo, pid_dead, kill_quietly, FLAGS  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
SCRIPT = SCRIPTS / "diff_guard.py"
# This repo keeps the policy in gate/; a product repo copies it to .github/risk-gate/.
POLICIES = [SCRIPTS.parent / "gate" / "risk-policy.toml", SCRIPTS.parent / "risk-gate" / "risk-policy.toml"]
REAL_GIT = shutil.which("git")
TEST_PATTERNS = ["*/tests/*", "*/test/*", "*/test_*", "*_test.*", "*.test.*", "*.spec.*"]
SPEC_PATTERNS = ["*/docs/specs/*", "*/docs/*/spec.md", "*/docs/*.spec.md"]
NOTICE = "notice: tests removed or assertions shrink; spec changed: "
VIOLATION_PREFIXES = ("removed test file: ", "test file type changed: ", "assertions shrink: ")
LINE_FORMS = re.compile(r"(removed test file|test file type changed): (?P<v>.+)"
                        r"|assertions shrink: (?P<s>.+) \(-\d+ \+\d+\)"
                        r"|notice: tests removed or assertions shrink; spec changed: (?P<n>.+)"
                        r"|diff guard: \d+ test files checked"
                        r"|too large: .+ over \d+")
BAD = ["-x", "--output=PWNED", "a b", "", "x;y", "a$(id)", "HEAD~1", ".hidden", "/abs", "a" * 256, "a\nb"]


def py_test(n=8):
    """A Python test file with n assertion lines (`assert x == i`), large enough for -M."""
    lines = ["import unittest", "", "", "class T(unittest.TestCase):"]
    for i in range(n):
        lines += [f"    def test_{i}(self):", f"        x = {i}", f"        assert x == {i}"]
    return "\n".join(lines) + "\n"


PY = py_test()
SRC = "def f():\n    return 1\n"
FILLER = "".join(f"// filler line {i} with some text\n" for i in range(12))
BINARY_A = b"\x00\x01\x02 binary A " * 200
BINARY_B = b"\x00\x03\x04 binary B " * 200


def drop(text, line):
    assert line + "\n" in text, line
    return text.replace(line + "\n", "", 1)


def esc(s):
    return re.escape(s)


class Repo(_Repo):
    def raw(self, *args, input=None, env=None, check=True):
        cp = subprocess.run([REAL_GIT, "-C", str(self.path), *args], env=env or self.env, input=input,
                            capture_output=True, check=False)
        if check and cp.returncode:
            raise AssertionError(f"fixture git {args} failed: {cp.stderr.decode(errors='replace')}")
        return cp.stdout

    def blob(self, data):
        return self.raw("hash-object", "-w", "--stdin", input=data.encode() if isinstance(data, str) else data
                        ).decode().strip()

    def snap(self, files, parents=(), msg="Snap\n", ref=None):
        """A commit with exactly `files` ({path: text | bytes | (mode, data)}; a gitlink's data is a
        sha), built in a private index so the work tree and the real index stay untouched."""
        idx = self.path.parent / (self.path.name + ".snap-index")
        if idx.exists():
            idx.unlink()
        env = dict(self.env, GIT_INDEX_FILE=str(idx))
        entries = []
        for path, spec in files.items():
            mode, data = spec if isinstance(spec, tuple) else ("100644", spec)
            sha = data if mode == "160000" else self.blob(data)
            p = path if isinstance(path, bytes) else path.encode()
            entries.append(f"{mode} {sha}\t".encode() + p + b"\0")
        self.raw("update-index", "-z", "--index-info", input=b"".join(entries), env=env)
        tree = self.raw("write-tree", env=env).decode().strip()
        args = ["commit-tree", tree]
        for p in parents:
            args += ["-p", p]
        sha = self.raw(*args, "-F", "-", input=msg if isinstance(msg, bytes) else msg.encode(), env=env
                       ).decode().strip()
        idx.unlink()
        if ref:
            self.raw("update-ref", ref, sha)
        return sha

    def status(self, a, b):
        """Plain git's raw status: [(letter, old mode, new mode, paths...)]."""
        out = self.raw("diff", "-z", "--raw", "--no-abbrev", "-M", "--no-ext-diff", "--no-textconv", a, b)
        toks = out.split(b"\0")
        res, i = [], 0
        while i < len(toks) and toks[i]:
            meta = toks[i].decode().lstrip(":").split()
            letter = meta[4][0]
            n = 2 if letter in "RC" else 1
            res.append((letter, meta[0], meta[1], *[t.decode(errors="replace") for t in toks[i + 1:i + 1 + n]]))
            i += 1 + n
        return res

    def numstat(self, a, b, path):
        return self.raw("diff", "--numstat", "--no-ext-diff", "--no-textconv", a, b, "--", path).decode()


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

    def new_repo(self, name="work"):
        return Repo(self.tmp / name, self.env())

    def run_dg(self, *args, cwd=None, env=None, timeout=120):
        self.assertTrue(SCRIPT.is_file(), f"script missing: {SCRIPT}")
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=str(cwd or self.cwd),
                              env=env or self.env(), capture_output=True, text=True, errors="replace",
                              timeout=timeout)

    def guard(self, repo, base, head, *extra, **kw):
        return self.run_dg("--base", base, "--head", head, "--repo", repo.path, *extra, **kw)

    def pair(self, before, after, name="work"):
        r = self.new_repo(name)
        base = r.snap(before, ref="refs/heads/main")
        head = r.snap(after, parents=[base], ref="refs/heads/agent/x")
        return r, base, head

    # -- assertions
    def usage_error(self, cp, label=""):
        self.assertEqual(cp.returncode, 2, f"{label}: exit {cp.returncode}, stdout={cp.stdout!r}, stderr={cp.stderr!r}")
        self.assertEqual(cp.stdout, "", f"{label}: stdout must be empty on exit 2")
        self.assertNotEqual(cp.stderr.strip(), "", f"{label}: message on stderr on exit 2")

    def found(self, cp):
        return [l for l in cp.stdout.splitlines() if l.startswith(VIOLATION_PREFIXES)]

    def clean(self, cp, n=None):
        self.assertEqual(cp.returncode, 0, f"exit {cp.returncode}; stdout={cp.stdout!r}; stderr={cp.stderr!r}")
        lines = cp.stdout.splitlines()
        self.assertEqual(self.found(cp), [], cp.stdout)
        self.assertFalse([l for l in lines if l.startswith("notice:")], cp.stdout)
        self.assertTrue(lines, "no output")
        count = r"\d+" if n is None else str(n)
        self.assertRegex(lines[-1], "^diff guard: " + count + " test files checked$", cp.stdout)

    def match_found(self, cp, patterns):
        got = self.found(cp)
        for pat in patterns:
            hits = [l for l in got if re.fullmatch(pat, l)]
            self.assertEqual(len(hits), 1, f"want one line matching {pat!r}, stdout:\n{cp.stdout}")
        for l in got:
            self.assertTrue(any(re.fullmatch(p, l) for p in patterns), f"unexpected violation line {l!r}")
        return got

    def violations(self, cp, *patterns):
        """Exit 1, exactly these violation lines (regexes, fullmatch), no notice."""
        self.assertEqual(cp.returncode, 1, f"exit {cp.returncode}; stdout={cp.stdout!r}; stderr={cp.stderr!r}")
        self.match_found(cp, patterns)
        self.assertNotIn("notice:", cp.stdout)
        self.assertNotIn("test files checked", cp.stdout, "the clean line is printed only when clean")

    def exempt(self, cp, spec, *patterns, fold=False):
        """(c): exit 0, the violation lines, then the notice naming `spec` after all of them
        (fold: the path may be printed lowercased)."""
        self.assertEqual(cp.returncode, 0, f"exit {cp.returncode}; stdout={cp.stdout!r}; stderr={cp.stderr!r}")
        self.match_found(cp, patterns)
        lines = cp.stdout.splitlines()
        notices = [i for i, l in enumerate(lines) if l.startswith("notice:")]
        key = str.lower if fold else (lambda x: x)
        self.assertEqual([key(lines[i]) for i in notices], [key(NOTICE + spec)], cp.stdout)
        last_violation = max(i for i, l in enumerate(lines) if l.startswith(VIOLATION_PREFIXES))
        self.assertGreater(notices[0], last_violation, "the notice comes after the violation lines")


def removed(path):
    return esc("removed test file: " + path)


def type_changed(path):
    return esc("test file type changed: " + path)


def shrink(paths, r, a):
    alts = "|".join(esc(p) for p in ([paths] if isinstance(paths, str) else paths))
    return rf"assertions shrink: (?:{alts}) \(-{r} \+{a}\)"


# ---------------------------------------------------------------- AC8

class TestAC8RemovedAndTypeChanged(Base):
    def test_delete_test_file_per_pattern(self):
        cases = {"tests/test_a.py": "*/tests/*", "src/test/Helper.java": "*/test/*", "test_x.py": "*/test_* at root",
                 "pkg/a_test.go": "*_test.*", "src/a.test.ts": "*.test.*", "src/b.spec.js": "*.spec.*",
                 "deep/er/tests/data/fixture.json": "*/tests/* nested"}
        for path, why in cases.items():
            with self.subTest(path=path, pattern=why):
                r, base, head = self.pair({path: PY, "src/a.py": SRC}, {"src/a.py": SRC}, name=f"r{len(why)}{abs(hash(path))}")
                self.violations(self.guard(r, base, head), removed(path))

    def test_delete_is_matched_on_the_lowercased_path(self):
        r, base, head = self.pair({"Tests/Test_A.py": PY, "src/a.py": SRC}, {"src/a.py": SRC})
        cp = self.guard(r, base, head)
        self.assertEqual(cp.returncode, 1, f"{cp.stdout!r} {cp.stderr!r}")
        got = self.found(cp)
        self.assertEqual(len(got), 1, cp.stdout)
        self.assertEqual(got[0].lower(), "removed test file: tests/test_a.py")

    def test_rename_test_to_non_test_fails(self):
        r, base, head = self.pair({"tests/test_a.py": PY}, {"src/a.py": PY})
        self.assertEqual([s[0] for s in r.status(base, head)], ["R"], "fixture: git sees a rename")
        self.violations(self.guard(r, base, head), removed("tests/test_a.py") + r"(?: .*)?")

    def test_rename_test_to_test_unchanged_passes(self):
        r, base, head = self.pair({"tests/test_a.py": PY}, {"tests/test_b.py": PY})
        self.assertEqual([s[0] for s in r.status(base, head)], ["R"])
        self.clean(self.guard(r, base, head), n=1)

    def test_similarity_rename_with_removed_assertion_fails(self):
        r, base, head = self.pair({"tests/test_a.py": PY}, {"tests/test_b.py": drop(PY, "        assert x == 3")})
        st = r.status(base, head)
        self.assertEqual([s[0] for s in st], ["R"], f"fixture: a similarity rename, got {st}")
        self.violations(self.guard(r, base, head), shrink(["tests/test_a.py", "tests/test_b.py"], 1, 0))

    def test_symlink_replacing_test_file_fails(self):
        r, base, head = self.pair({"tests/test_a.py": PY, "src/a.py": SRC},
                                  {"tests/test_a.py": ("120000", "../src/a.py"), "src/a.py": SRC})
        self.assertEqual([s[:3] for s in r.status(base, head)], [("T", "100644", "120000")], "fixture: type change T")
        self.violations(self.guard(r, base, head), type_changed("tests/test_a.py"))

    def test_submodule_replacing_test_file_fails(self):
        r, base, head = self.pair({"tests/test_a.py": PY}, {"tests/test_a.py": ("160000", "1" * 40)})
        st = r.status(base, head)
        self.assertEqual(st[0][2], "160000", f"fixture: gitlink on the head side, got {st}")
        self.violations(self.guard(r, base, head), type_changed("tests/test_a.py"))

    def test_new_symlink_on_test_path_fails(self):
        """(a2): any status where a test path gets mode 120000 on the head side, A included."""
        r, base, head = self.pair({"src/a.py": SRC}, {"src/a.py": SRC, "tests/test_link.py": ("120000", "../src/a.py")})
        self.assertEqual([s[:3] for s in r.status(base, head)], [("A", "000000", "120000")])
        self.violations(self.guard(r, base, head), type_changed("tests/test_link.py"))

    def test_new_submodule_on_test_path_fails(self):
        r, base, head = self.pair({"src/a.py": SRC}, {"src/a.py": SRC, "tests/vendor": ("160000", "2" * 40)})
        self.assertEqual([s[:3] for s in r.status(base, head)], [("A", "000000", "160000")])
        self.violations(self.guard(r, base, head), type_changed("tests/vendor"))

    def test_test_file_becoming_symlink_to_other_test_fails(self):
        before = {"tests/test_a.py": PY, "tests/test_b.py": PY + "# b\n"}
        after = {"tests/test_a.py": ("120000", "test_b.py"), "tests/test_b.py": PY + "# b\n"}
        r, base, head = self.pair(before, after)
        self.violations(self.guard(r, base, head), type_changed("tests/test_a.py"))

    def test_non_test_file_becoming_symlink_passes(self):
        r, base, head = self.pair({"src/a.py": SRC, "src/b.py": SRC + "#\n"},
                                  {"src/a.py": ("120000", "b.py"), "src/b.py": SRC + "#\n"})
        self.assertEqual([s[0] for s in r.status(base, head)], ["T"])
        self.clean(self.guard(r, base, head), n=0)

    def test_text_test_file_becoming_binary_fails(self):
        r, base, head = self.pair({"tests/test_a.py": PY}, {"tests/test_a.py": BINARY_A})
        self.assertTrue(r.numstat(base, head, "tests/test_a.py").startswith("-\t-\t"), "fixture: binary in numstat")
        self.violations(self.guard(r, base, head), type_changed("tests/test_a.py"))

    def test_text_fixture_under_tests_becoming_binary_fails(self):
        r, base, head = self.pair({"tests/data/fixture.txt": "plain text\n" * 20},
                                  {"tests/data/fixture.txt": BINARY_A})
        self.violations(self.guard(r, base, head), type_changed("tests/data/fixture.txt"))

    def test_binary_to_binary_passes(self):
        r, base, head = self.pair({"tests/data/fixture.bin": BINARY_A}, {"tests/data/fixture.bin": BINARY_B})
        self.assertEqual([s[0] for s in r.status(base, head)], ["M"])
        self.clean(self.guard(r, base, head), n=1)

    def test_case_only_rename_unchanged_passes(self):
        r, base, head = self.pair({"tests/test_a.py": PY}, {"tests/Test_A.py": PY})
        self.assertEqual([s[0] for s in r.status(base, head)], ["R"], "fixture: case-only rename")
        self.clean(self.guard(r, base, head), n=1)

    def test_case_only_rename_with_removed_assertion_fails(self):
        r, base, head = self.pair({"tests/test_a.py": PY}, {"tests/Test_A.py": drop(PY, "        assert x == 5")})
        self.assertEqual([s[0] for s in r.status(base, head)], ["R"])
        self.violations(self.guard(r, base, head), shrink(["tests/test_a.py", "tests/Test_A.py"], 1, 0))

    def test_deleting_non_test_file_passes(self):
        r, base, head = self.pair({"src/a.py": SRC, "src/b.py": SRC, "tests/test_a.py": PY},
                                  {"src/b.py": SRC, "tests/test_a.py": PY})
        self.clean(self.guard(r, base, head), n=0)

    def test_attribute_that_hides_a_test_diff_fails(self):
        """Security: a PR's .gitattributes marking a test file -diff makes git print it as binary,
        which would hide removed assertions from (b). The old side is text, so this is (a2) or (b)."""
        attrs = "tests/* -diff\n"
        r, base, head = self.pair({"tests/test_a.py": PY},
                                  {"tests/test_a.py": drop(PY, "        assert x == 3"), ".gitattributes": attrs})
        r.git("checkout", "-q", "-f", "agent/x")  # attributes are read from the work tree, as in CI
        self.assertTrue(r.numstat(base, head, "tests/test_a.py").startswith("-\t-\t"),
                        "fixture: the attribute makes git treat the file as binary")
        cp = self.guard(r, base, head)
        self.assertEqual(cp.returncode, 1, f"{cp.stdout!r} {cp.stderr!r}")
        self.match_found(cp, [type_changed("tests/test_a.py") + "|" + shrink("tests/test_a.py", 1, 0)])


# ---------------------------------------------------------------- AC9

ROWS = [  # (file, removed line, table row)
    ("tests/test_r.py", "assert x == 1", r"^assert\b"),
    ("tests/test_r.py", "assert(x)", r"^assert\b"),
    ("tests/test_r.py", "self.assertEqual(a, b)", r"\bassert\w*\s*[(!]"),
    ("tests/test_r.py", "self.assertTrue (ok)", r"\bassert\w*\s*[(!]"),
    ("src/test/FooTest.java", "assertThat(x).isTrue();", r"\bassert\w*\s*[(!]"),
    ("tests/it.rs", "assert_eq!(a, b);", r"\bassert\w*\s*[(!]"),
    ("src/a.test.js", "await assert.strictEqual(a, b);", r"\b(assert|require)\.\w+\("),
    ("pkg/a_test.go", "require.NoError(t, err)", r"\b(assert|require)\.\w+\("),
    ("src/a.test.js", "expect(x).toBe(1);", r"\bexpect\s*\("),
    ("src/a.test.js", "expect (y).toEqual(2);", r"\bexpect\s*\("),
    ("src/a.test.js", "x.should.equal(1);", r"\.should\b"),
    ("src/a.test.js", "should.exist(x);", r"\bshould\."),
    ("pkg/a_test.go", 't.Error("bad")', r"\bt\.Error\("),
    ("pkg/a_test.go", 't.Errorf("bad %v", x)', r"\bt\.Errorf\("),
    ("pkg/a_test.go", "t.Fatal(err)", r"\bt\.Fatal\("),
    ("pkg/a_test.go", 't.Fatalf("bad %v", err)', r"\bt\.Fatalf\("),
    ("pkg/a_test.go", "t.Fail()", r"\bt\.Fail\("),
    ("pkg/a_test.go", "t.FailNow()", r"\bt\.FailNow\("),
    ("tests/test_r.py", "with pytest.raises(ValueError):", r"\bpytest\.raises\("),
    ("tests/test_r.py", 'self.fail("boom")', r"\bself\.fail\("),
]

NOT_ASSERTIONS = ["assertion_count = 3", "expected = 3", "shoulder = 1", "reassert(x)", "fmt.Errorf(\"x\")",
                  "t.Log(x)", "pytest.skip(\"x\")", "require_ok = True", "x = 1"]

COMMENTED = ["# assert x == 1", "// expect(x).toBe(1);", "/* assert(x) */", "* assertEqual(a, b)",
             "-- assert x", "<!-- expect(x) -->", "    # self.assertTrue(x)"]


class TestAC9AssertionsShrink(Base):
    def one(self, path, before, after):
        r, base, head = self.pair({path: before}, {path: after}, name=f"w{abs(hash((path, before, after)))}")
        return self.guard(r, base, head)

    def with_line(self, line):
        half = FILLER.splitlines(keepends=True)
        return "".join(half[:6]) + "    " + line + "\n" + "".join(half[6:])

    def test_removing_one_assert_fails(self):
        self.violations(self.one("tests/test_a.py", PY, drop(PY, "        assert x == 3")),
                        shrink("tests/test_a.py", 1, 0))

    def test_commenting_out_an_assert_fails(self):
        after = PY.replace("        assert x == 3\n", "        # assert x == 3\n")
        self.violations(self.one("tests/test_a.py", PY, after), shrink("tests/test_a.py", 1, 0))

    def test_remove_two_add_two_passes(self):
        after = PY.replace("assert x == 3\n", "assert x == 3, 'three'\n").replace("assert x == 4\n", "assert x >= 4\n")
        self.clean(self.one("tests/test_a.py", PY, after), n=1)

    def test_adding_only_passes(self):
        self.clean(self.one("tests/test_a.py", PY, PY + "\n\ndef test_more():\n    assert 1 + 1 == 2\n"), n=1)

    def test_new_test_file_passes(self):
        r, base, head = self.pair({"src/a.py": SRC}, {"src/a.py": SRC, "tests/test_new.py": PY})
        self.clean(self.guard(r, base, head))

    def test_counts_are_printed(self):
        after = drop(drop(drop(PY, "        assert x == 1"), "        assert x == 2"), "        assert x == 3")
        after += "\n\ndef test_extra():\n    assert True\n"
        self.violations(self.one("tests/test_a.py", PY, after), shrink("tests/test_a.py", 3, 1))

    def test_each_table_row_is_detected_in_a_removed_line(self):
        for path, line, row in ROWS:
            with self.subTest(row=row, line=line):
                before = self.with_line(line)
                self.violations(self.one(path, before, FILLER), shrink(path, 1, 0))

    def test_each_table_row_counts_as_added(self):
        """Removing one row-1 assertion and adding one of each other style balances out."""
        for path, line, row in ROWS:
            with self.subTest(row=row, line=line):
                before = self.with_line("assert gone")
                after = self.with_line(line)
                self.clean(self.one(path, before, after), n=1)

    def test_lines_outside_the_table_are_not_assertions(self):
        for line in NOT_ASSERTIONS:
            with self.subTest(line=line):
                self.clean(self.one("tests/test_a.py", self.with_line(line), FILLER), n=1)

    def test_line_counts_once(self):
        before = self.with_line("assert expect(x).should.be(1) and self.assertTrue(t.Fail())")
        self.violations(self.one("tests/test_a.py", before, FILLER), shrink("tests/test_a.py", 1, 0))

    def test_comment_only_change_passes(self):
        for line in COMMENTED:
            with self.subTest(line=line):
                self.clean(self.one("tests/test_a.py", self.with_line(line), FILLER), n=1)

    def test_comment_change_next_to_code_passes(self):
        before = PY.replace("        x = 3\n", "        # old note\n        x = 3\n")
        self.clean(self.one("tests/test_a.py", before, PY), n=1)

    def test_long_line_is_cut_before_matching(self):
        late = "x = '" + "y" * 5000 + "'; assert(z)"
        self.clean(self.one("tests/test_a.py", self.with_line(late), FILLER), n=1)

    def test_long_line_still_counts_within_max_line(self):
        early = "self.assertTrue(z)  # " + "y" * 5000
        self.violations(self.one("tests/test_a.py", self.with_line(early), FILLER), shrink("tests/test_a.py", 1, 0))

    def test_huge_line_is_fast(self):
        """Regex input bound: a 2 MB removed line of assertion-like text is cut, not scanned."""
        t0 = time.monotonic()
        cp = self.one("tests/test_a.py", self.with_line("expect " * 300_000), FILLER)
        self.assertLess(time.monotonic() - t0, 30)
        self.clean(cp, n=1)

    def test_shrink_in_two_files_reports_both(self):
        r, base, head = self.pair({"tests/test_a.py": PY, "src/b.test.js": self.with_line("expect(b).toBe(1);")},
                                  {"tests/test_a.py": drop(PY, "        assert x == 0"), "src/b.test.js": FILLER})
        self.violations(self.guard(r, base, head), shrink("tests/test_a.py", 1, 0), shrink("src/b.test.js", 1, 0))

    def test_modified_non_test_file_is_not_counted(self):
        before = self.with_line("assert x == 1")
        r, base, head = self.pair({"src/a.py": before}, {"src/a.py": FILLER})
        self.clean(self.guard(r, base, head), n=0)


# ---------------------------------------------------------------- AC10

class TestAC10SpecChange(Base):
    BEFORE = {"tests/test_a.py": PY, "tests/test_b.py": PY + "# b\n", "src/a.py": SRC,
              "docs/specs/x/spec.md": "# Spec x\n\nv1\n", "docs/specs/other/spec.md": "# Other\n\nv1\n"}

    def run_pair(self, after, *extra, before=None):
        r, base, head = self.pair(before or self.BEFORE, after)
        return self.guard(r, base, head, *extra)

    def test_removal_with_added_spec_passes_with_notice(self):
        after = {k: v for k, v in self.BEFORE.items() if k != "tests/test_a.py"}
        after["docs/specs/new/spec.md"] = "# New\n"
        self.exempt(self.run_pair(after), "docs/specs/new/spec.md", removed("tests/test_a.py"))

    def test_shrink_with_modified_spec_passes_with_notice(self):
        after = dict(self.BEFORE, **{"tests/test_a.py": drop(PY, "        assert x == 3"),
                                      "docs/specs/x/spec.md": "# Spec x\n\nv2\n"})
        self.exempt(self.run_pair(after), "docs/specs/x/spec.md", shrink("tests/test_a.py", 1, 0))

    def test_type_change_with_spec_passes_with_notice(self):
        after = dict(self.BEFORE, **{"tests/test_a.py": ("120000", "../src/a.py"),
                                      "docs/specs/x/spec.md": "# Spec x\n\nv2\n"})
        self.exempt(self.run_pair(after), "docs/specs/x/spec.md", type_changed("tests/test_a.py"))

    def test_renamed_spec_counts_by_new_name(self):
        body = "# Spec x\n\n" + "".join(f"Requirement {i}.\n" for i in range(20))
        before = dict(self.BEFORE, **{"docs/specs/x/spec.md": body})
        after = {k: v for k, v in before.items() if k not in ("tests/test_a.py", "docs/specs/x/spec.md")}
        after["docs/specs/y/spec.md"] = body + "Requirement 20.\n"
        r, base, head = self.pair(before, after)
        self.assertIn("R", [s[0] for s in r.status(base, head)], "fixture: spec rename")
        self.exempt(self.guard(r, base, head), "docs/specs/y/spec.md", removed("tests/test_a.py"))

    def test_only_deleted_spec_still_fails(self):
        after = {k: v for k, v in self.BEFORE.items() if k not in ("tests/test_a.py", "docs/specs/x/spec.md")}
        self.violations(self.run_pair(after), removed("tests/test_a.py"))

    def test_unrelated_one_character_spec_change_authorizes_all(self):
        """Stated limit: any qualifying spec change authorizes every finding."""
        after = {k: v for k, v in self.BEFORE.items() if k != "tests/test_a.py"}
        after["tests/test_b.py"] = drop(PY, "        assert x == 7") + "# b\n"
        after["docs/specs/other/spec.md"] = "# Other\n\nv2\n"
        self.exempt(self.run_pair(after), "docs/specs/other/spec.md",
                    removed("tests/test_a.py"), shrink("tests/test_b.py", 1, 0))

    def test_no_violation_with_spec_change_is_clean(self):
        after = dict(self.BEFORE, **{"docs/specs/x/spec.md": "# Spec x\n\nv2\n"})
        self.clean(self.run_pair(after))

    def test_non_spec_doc_change_does_not_authorize(self):
        after = {k: v for k, v in self.BEFORE.items() if k != "tests/test_a.py"}
        after["README.md"] = "# Readme\n"
        after["docs/guide.md"] = "# Guide\n"
        self.violations(self.run_pair(after), removed("tests/test_a.py"))

    def test_other_default_spec_patterns(self):
        for spec in ("docs/design/spec.md", "docs/api.spec.md", "sub/docs/specs/z.md", "Docs/Specs/Up/SPEC.md"):
            with self.subTest(spec=spec):
                after = {k: v for k, v in self.BEFORE.items() if k != "tests/test_a.py"}
                after[spec] = "# Spec\n"
                r, base, head = self.pair(self.BEFORE, after, name=f"s{abs(hash(spec))}")
                self.exempt(self.guard(r, base, head), spec, removed("tests/test_a.py"), fold=spec != spec.lower())

    def test_spec_option_replaces_defaults(self):
        after = {k: v for k, v in self.BEFORE.items() if k != "tests/test_a.py"}
        after["docs/specs/x/spec.md"] = "# Spec x\n\nv2\n"
        self.violations(self.run_pair(after, "--spec", "docs/other/*"), removed("tests/test_a.py"))

    def test_spec_option_pattern_authorizes(self):
        after = {k: v for k, v in self.BEFORE.items() if k != "tests/test_a.py"}
        after["docs/other/y.md"] = "# Y\n"
        self.exempt(self.run_pair(after, "--spec", "docs/other/*"), "docs/other/y.md", removed("tests/test_a.py"))

    def test_spec_option_is_repeatable(self):
        for spec in ("docs/one/a.md", "docs/two/b.md"):
            with self.subTest(spec=spec):
                after = {k: v for k, v in self.BEFORE.items() if k != "tests/test_a.py"}
                after[spec] = "# S\n"
                r, base, head = self.pair(self.BEFORE, after, name=f"p{abs(hash(spec))}")
                self.exempt(self.guard(r, base, head, "--spec", "docs/one/*", "--spec", "docs/two/*"), spec,
                            removed("tests/test_a.py"))

    def test_spec_option_uses_gate_matches_semantics(self):
        """A leading / anchors and its * stays in one folder; otherwise * crosses /."""
        cases = [("/specs/*", "specs/a.md", True), ("/specs/*", "specs/sub/a.md", False),
                 ("/specs/*", "x/specs/a.md", False), ("specs/*", "specs/sub/a.md", True),
                 ("*/specs/*", "specs/a.md", True)]
        for pattern, spec, authorizes in cases:
            with self.subTest(pattern=pattern, spec=spec):
                after = {k: v for k, v in self.BEFORE.items() if k != "tests/test_a.py"}
                after[spec] = "# S\n"
                r, base, head = self.pair(self.BEFORE, after, name=f"m{abs(hash((pattern, spec)))}")
                cp = self.guard(r, base, head, "--spec", pattern)
                if authorizes:
                    self.exempt(cp, spec, removed("tests/test_a.py"))
                else:
                    self.violations(cp, removed("tests/test_a.py"))

    def test_spec_named_like_a_test_is_a_spec(self):
        before = dict(self.BEFORE, **{"docs/specs/a.spec.md": "# A\n\n" + "expect(x)\n" * 3})
        with self.subTest("deleted"):
            after = {k: v for k, v in before.items() if k != "docs/specs/a.spec.md"}
            self.clean(self.run_pair(after, before=before))
        with self.subTest("assertion-like lines removed"):
            after = dict(before, **{"docs/specs/a.spec.md": "# A\n"})
            self.clean(self.run_pair(after, before=before))
        with self.subTest("authorizes"):
            after = {k: v for k, v in before.items() if k != "tests/test_a.py"}
            after["docs/specs/a.spec.md"] = "# A\n\nchanged\n"
            self.exempt(self.run_pair(after, before=before), "docs/specs/a.spec.md", removed("tests/test_a.py"))

    def test_spec_option_path_is_never_a_test(self):
        before = dict(self.BEFORE, **{"docs/other/test_x.py": PY})
        after = {k: v for k, v in before.items() if k != "docs/other/test_x.py"}
        self.clean(self.run_pair(after, "--spec", "docs/other/*", before=before))
        before = dict(self.BEFORE, **{"docs/specs/test_y.py": PY})
        after = {k: v for k, v in before.items() if k != "docs/specs/test_y.py"}
        self.clean(self.run_pair(after, before=before))


# ---------------------------------------------------------------- AC11

class TestAC11RevisionsBoundsMergeBase(Base):
    def test_bad_revision_values(self):
        r, base, head = self.pair({"tests/test_a.py": PY}, {"tests/test_a.py": PY + "#\n"})
        pwned = self.tmp / "pwned"
        for flag in ("--base", "--head"):
            for v in BAD:
                v = v.replace("PWNED", str(pwned))
                a = {"--base": base, "--head": head, flag: v}
                with self.subTest(flag=flag, value=v[:40]):
                    self.usage_error(self.run_dg(*[f"{k}={x}" for k, x in a.items()], "--repo", r.path),
                                     f"{flag}={v[:40]!r}")
                    self.assertFalse(pwned.exists(), "option injection wrote a file")

    def test_unknown_revisions(self):
        r, base, head = self.pair({"tests/test_a.py": PY}, {"tests/test_a.py": PY + "#\n"})
        self.usage_error(self.guard(r, "nosuchrev", head), "unknown base")
        self.usage_error(self.guard(r, base, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"), "unknown head")

    def test_not_a_repo(self):
        self.usage_error(self.run_dg("--base", "main", "--head", "HEAD", "--repo", self.cwd), "not a repo")

    def test_missing_arguments(self):
        r, base, head = self.pair({"a": "1\n"}, {"a": "2\n"})
        self.usage_error(self.run_dg("--head", head, "--repo", r.path), "no --base")
        self.usage_error(self.run_dg("--base", base, "--repo", r.path), "no --head")

    def test_default_repo_is_cwd(self):
        r, base, head = self.pair({"tests/test_a.py": PY}, {})
        self.violations(self.run_dg("--base", base, "--head", head, cwd=r.path), removed("tests/test_a.py"))

    def test_branch_names_as_revisions(self):
        r, _, _ = self.pair({"tests/test_a.py": PY}, {})
        self.violations(self.run_dg("--base", "main", "--head", "agent/x", "--repo", r.path), removed("tests/test_a.py"))

    def many_files_repo(self):
        """main = one file; big3000 and big3001 add 3000 and 3001 non-test files."""
        r = self.new_repo("big")
        who = "Fixture <fixture@example.invalid> 1767614400 +0000"
        out = [b"blob\nmark :1\ndata 2\nx\n\n"]

        def commit(ref, mark, n, frm=None):
            out.append(f"commit {ref}\nmark :{mark}\ncommitter {who}\ndata 5\nsnap\n".encode())
            if frm:
                out.append(f"from :{frm}\n".encode())
            out.append(b"M 100644 :1 README\n")
            out.extend(f"M 100644 :1 f/{k:05d}.txt\n".encode() for k in range(n))
            out.append(b"\n")

        commit("refs/heads/main", 2, 0)
        commit("refs/heads/big3000", 3, 3000, frm=2)
        commit("refs/heads/big3001", 4, 3001, frm=2)
        r.git("fast-import", "--quiet", input=b"".join(out))
        self.assertEqual(len(r.git("diff", "--name-only", "main", "big3001").splitlines()), 3001)
        return r

    def test_more_than_max_files_is_too_large(self):
        r = self.many_files_repo()
        cp = self.run_dg("--base", "main", "--head", "big3001", "--repo", r.path)
        self.assertEqual(cp.returncode, 1, f"{cp.stdout!r} {cp.stderr!r}")
        self.assertTrue(any(l.startswith("too large: ") for l in cp.stdout.splitlines()), cp.stdout)
        self.assertNotIn("test files checked", cp.stdout)

    def test_max_files_passes(self):
        r = self.many_files_repo()
        self.clean(self.run_dg("--base", "main", "--head", "big3000", "--repo", r.path), n=0)

    def test_git_output_over_max_bytes_is_too_large(self):
        """A per-file diff over MAX_BYTES rejects; it is never truncated into a decision."""
        big = "".join(f"x{i} = {i}  # {'a' * 60}\n" for i in range(80_000))  # about 6 MB of added lines
        r, base, head = self.pair({"tests/test_big.py": "assert x\n"}, {"tests/test_big.py": "assert x\n" + big})
        cp = self.guard(r, base, head)
        self.assertEqual(cp.returncode, 1, f"{cp.stdout[-500:]!r} {cp.stderr[-500:]!r}")
        self.assertTrue(any(l.startswith("too large: ") for l in cp.stdout.splitlines()), cp.stdout[-500:])

    def diverged(self):
        """B0 on main; agent/x branches off with a src change; main moves on afterwards."""
        r = self.new_repo()
        b0_files = {"tests/test_a.py": PY, "tests/test_b.py": PY + "# b\n", "src/a.py": SRC}
        b0 = r.snap(b0_files, ref="refs/heads/main")
        head = r.snap(dict(b0_files, **{"src/a.py": SRC + "# branch\n"}), parents=[b0], ref="refs/heads/agent/x")
        return r, b0, b0_files, head

    def test_base_deleting_a_test_after_branch_point_passes(self):
        r, b0, files, head = self.diverged()
        moved = r.snap({k: v for k, v in files.items() if k != "tests/test_b.py"}, parents=[b0], ref="refs/heads/main")
        self.clean(self.guard(r, moved, head))
        self.clean(self.run_dg("--base", "main", "--head", "agent/x", "--repo", r.path))

    def test_base_adding_a_test_after_branch_point_passes(self):
        """A plain base..head diff would show the new base test as deleted on the branch."""
        r, b0, files, head = self.diverged()
        moved = r.snap(dict(files, **{"tests/test_new.py": PY}), parents=[b0], ref="refs/heads/main")
        self.assertIn(("D", "tests/test_new.py"), [(s[0], s[-1]) for s in r.status(moved, head)])
        self.clean(self.guard(r, moved, head))

    def test_base_adding_an_assertion_after_branch_point_passes(self):
        r, b0, files, head = self.diverged()
        moved = r.snap(dict(files, **{"tests/test_a.py": PY + "assert 'base'\n"}), parents=[b0], ref="refs/heads/main")
        self.clean(self.guard(r, moved, head), n=0)

    def test_branch_deletion_found_after_base_moved(self):
        r, b0, files, head = self.diverged()
        moved = r.snap(dict(files, **{"src/b.py": SRC}), parents=[b0], ref="refs/heads/main")
        bad = r.snap({k: v for k, v in files.items() if k != "tests/test_a.py"}, parents=[head], ref="refs/heads/agent/x")
        self.violations(self.guard(r, moved, bad), removed("tests/test_a.py"))


# ---------------------------------------------------------------- AC12

HOOKS = ("gpg", "external", "textconv", "command", "fsmonitor", "pager", "pagerdiff")


class TestAC12Hardening(Base):
    def hostile_repo(self):
        """Repo whose config and attributes would run a marker script through git. The controls at
        the end prove each mechanism is live for plain git before the script is judged."""
        hooks, self.markers = self.tmp / "hooks", self.tmp / "markers"
        hooks.mkdir()
        self.markers.mkdir()
        for h in HOOKS:
            p = hooks / f"{h}.sh"
            p.write_text(f"#!/bin/sh\necho \"$@\" > '{self.markers / h}'\nexit 0\n")
            p.chmod(0o755)
        r = self.new_repo("hostile")
        base_files = {".gitattributes": "*.py diff=evil\n", "tests/test_a.py": PY, "tests/test_b.py": PY + "# b\n",
                      "tests/data.txt": "text\n" * 20, "src/a.py": SRC}
        b = r.snap(base_files, ref="refs/heads/main")
        heads = {
            "clean": dict(base_files, **{"tests/test_a.py": PY + "assert 'more'\n", "src/a.py": SRC + "#\n"}),
            "shrink": dict(base_files, **{"tests/test_a.py": drop(PY, "        assert x == 2")}),
            "delete": {k: v for k, v in base_files.items() if k != "tests/test_b.py"},
            "binary": dict(base_files, **{"tests/data.txt": BINARY_A}),
            "symlink": dict(base_files, **{"tests/test_b.py": ("120000", "test_a.py")}),
            "rename": {**{k: v for k, v in base_files.items() if k != "tests/test_a.py"},
                       "tests/test_c.py": drop(PY, "        assert x == 4")},
            "spec": {**{k: v for k, v in base_files.items() if k != "tests/test_b.py"}, "docs/specs/x/spec.md": "# x\n"},
        }
        refs = {name: r.snap(files, parents=[b], ref=f"refs/heads/agent/{name}") for name, files in heads.items()}
        # Re-create the clean tip as a signed commit (fake signature), so log/show signatures call gpg.
        raw = r.git("cat-file", "commit", refs["clean"])
        head_part, msg = raw.split("\n\n", 1)
        sig = "gpgsig -----BEGIN PGP SIGNATURE-----\n \n iQEzBAABCAAdFiEE\n -----END PGP SIGNATURE-----"
        signed = r.git("hash-object", "-t", "commit", "-w", "--stdin", input=f"{head_part}\n{sig}\n\n{msg}\n".encode())
        r.git("update-ref", "refs/heads/agent/clean", signed)
        refs["clean"] = signed
        r.git("config", "log.showSignature", "true")
        r.git("config", "gpg.program", str(hooks / "gpg.sh"))
        r.git("config", "diff.external", str(hooks / "external.sh"))
        r.git("config", "diff.evil.textconv", str(hooks / "textconv.sh"))
        r.git("config", "diff.evil.command", str(hooks / "command.sh"))
        r.git("config", "core.fsmonitor", str(hooks / "fsmonitor.sh"))
        r.git("config", "core.pager", str(hooks / "pager.sh"))
        r.git("config", "pager.diff", str(hooks / "pagerdiff.sh"))
        r.git("checkout", "-q", "-f", "agent/clean")
        # Controls: each mechanism fires for plain git.
        r.git("log", "-1", "agent/clean")
        self.assertTrue((self.markers / "gpg").exists(), "fixture: gpg.program not live")
        r.git("diff", b, "agent/binary", check=False)  # tests/data.txt has no driver: diff.external runs
        self.assertTrue((self.markers / "external").exists(), "fixture: diff.external not live")
        r.git("diff", b, "agent/shrink", check=False)
        self.assertTrue((self.markers / "command").exists(), "fixture: diff driver command not live")
        r.git("diff", "--no-ext-diff", b, "agent/shrink")
        self.assertTrue((self.markers / "textconv").exists(), "fixture: textconv not live")
        r.git("status", check=False)
        self.assertTrue((self.markers / "fsmonitor").exists(), "fixture: core.fsmonitor not live")
        for m in self.markers.iterdir():
            m.unlink()
        return r, b, refs

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

    def test_every_entry_point(self):
        r, b, refs = self.hostile_repo()
        before = self.state(r)
        runs = [("clean (signed tip)", lambda: self.guard(r, b, refs["clean"]), 0),
                ("assertions shrink", lambda: self.guard(r, b, refs["shrink"]), 1),
                ("removed test", lambda: self.guard(r, b, refs["delete"]), 1),
                ("text to binary", lambda: self.guard(r, b, refs["binary"]), 1),
                ("symlink", lambda: self.guard(r, b, refs["symlink"]), 1),
                ("similarity rename", lambda: self.guard(r, b, refs["rename"]), 1),
                ("spec exemption", lambda: self.guard(r, b, refs["spec"]), 0),
                ("spec option", lambda: self.guard(r, b, refs["spec"], "--spec", "docs/other/*"), 1),
                ("unknown revision", lambda: self.guard(r, "nosuchrev", refs["clean"]), 2),
                ("default repo", lambda: self.run_dg("--base", b, "--head", refs["shrink"], cwd=r.path), 1),
                ("branch names", lambda: self.run_dg("--base", "main", "--head", "agent/clean", "--repo", r.path), 0)]
        for label, run, code in runs:
            with self.subTest(run=label):
                cp = run()
                self.assertEqual(cp.returncode, code, f"{label}: {cp.stdout!r} {cp.stderr!r}")
                fired = sorted(m.name for m in self.markers.iterdir())
                self.assertEqual(fired, [], f"{label}: hostile config ran {fired}")
                self.assertEqual(self.state(r), before, f"{label}: repo, index or refs changed")

    def test_results_under_hostile_config(self):
        r, b, refs = self.hostile_repo()
        self.clean(self.guard(r, b, refs["clean"]), n=1)
        self.violations(self.guard(r, b, refs["shrink"]), shrink("tests/test_a.py", 1, 0))
        self.violations(self.guard(r, b, refs["delete"]), removed("tests/test_b.py"))
        self.violations(self.guard(r, b, refs["binary"]), type_changed("tests/data.txt"))
        self.violations(self.guard(r, b, refs["symlink"]), type_changed("tests/test_b.py"))
        self.violations(self.guard(r, b, refs["rename"]), shrink(["tests/test_a.py", "tests/test_c.py"], 1, 0))

    def test_git_argv_and_environment(self):
        """Section 3 and 6: every git call carries the fixed flags and GIT_TERMINAL_PROMPT=0; diff
        gets --no-ext-diff --no-textconv --no-color; raw and numstat read with -z; the diff base is
        `git merge-base`. A recording `git` on PATH hands each call to the real git."""
        r, b, refs = self.hostile_repo()
        mixed = r.snap({".gitattributes": "*.py diff=evil\n", "tests/test_a.py": drop(PY, "        assert x == 2"),
                        "tests/data.txt": BINARY_A, "tests/test_c.py": PY + "# b\n", "src/a.py": SRC},
                       parents=[b], ref="refs/heads/agent/mixed")
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
        cp = self.guard(r, b, mixed, env=env)
        self.assertEqual(cp.returncode, 1, f"{cp.stdout!r} {cp.stderr!r}")
        self.assertTrue(log.exists(), "git is not looked up as `git` on PATH (GIT constant)")
        calls = [json.loads(l)["argv"] for l in log.read_text().splitlines()]
        prompts = [json.loads(l)["prompt"] for l in log.read_text().splitlines()]
        cmds = [a[2 + len(FLAGS)] for a in calls if len(a) > 2 + len(FLAGS)]
        self.assertIn("merge-base", cmds, "the diff base comes from git merge-base")
        self.assertTrue(any("--raw" in a for a in calls), "files come from git diff --raw")
        for a, prompt in zip(calls, prompts):
            self.assertEqual(a[0], "-C", a)
            self.assertEqual(Path(a[1]).resolve(), r.path.resolve(), a)
            self.assertEqual(a[2:2 + len(FLAGS)], FLAGS, a)
            self.assertEqual(prompt, "0", f"GIT_TERMINAL_PROMPT for {a}")
            self.assertFalse({"--textconv", "--filters", "--ext-diff"} & set(a), a)
            sub = a[2 + len(FLAGS)]
            if sub == "diff":
                for flag in ("--no-ext-diff", "--no-textconv", "--no-color"):
                    self.assertIn(flag, a)
            if "--raw" in a:
                for flag in ("-z", "--no-abbrev", "-M"):
                    self.assertIn(flag, a)
            if "--numstat" in a:
                self.assertIn("-z", a)
            if sub == "log":
                self.assertIn("--no-show-signature", a)


# ---------------------------------------------------------------- AC13

class TestAC13OutputSafety(Base):
    def assert_safe(self, cp):
        self.assertTrue(printable_only(cp.stdout), repr(cp.stdout))
        for l in cp.stdout.splitlines():
            m = LINE_FORMS.fullmatch(l)
            self.assertTrue(m, f"unexpected line {l!r}")
            value = m["v"] or m["s"] or m["n"] or ""
            self.assertLessEqual(len(value), 203, f"value not cut at 200: {l!r}")

    def test_hostile_file_names(self):
        names = ["tests/test_\x1b[31mred.py", "tests/test_a\nb.py", "tests/test_" + "a" * 300 + ".py",
                 b"tests/test_\xff\xfe.py", "tests/test_\u202etxt.py", "tests/test_\x07bell.py"]
        before = {n: PY + f"# {i}\n" for i, n in enumerate(names)}
        before["src/a.py"] = SRC
        r = self.new_repo()
        base = r.snap(before)
        head = r.snap({"src/a.py": SRC}, parents=[base],
                      msg="Subject \x1b[31mCANARY-a1\x07\n\nAgent: x\x1b[2K / m / medium\nTier: T9\x1b]0;t\x07\n")
        cp = self.guard(r, base, head)
        self.assertEqual(cp.returncode, 1, f"{cp.stdout!r} {cp.stderr!r}")
        self.assert_safe(cp)
        self.assertEqual(len(self.found(cp)), len(names), cp.stdout)
        self.assertNotIn("CANARY-a1", cp.stdout + cp.stderr)
        long = [l for l in self.found(cp) if "aaaaaaaaaa" in l]
        self.assertEqual(len(long), 1, cp.stdout)
        value = long[0][len("removed test file: "):]
        self.assertTrue(value.endswith("..."), f"long value is not cut with ...: {value!r}")
        self.assertTrue(value.startswith("tests/test_aaaa"), value)

    def test_hostile_names_in_shrink_and_notice(self):
        t = "tests/test_\x1b[0mz.py"
        s = "docs/specs/\x1b[2Jx/spec.md"
        r, base, head = self.pair({t: PY, s: "# s\n"}, {t: drop(PY, "        assert x == 1"), s: "# s2\n"})
        cp = self.guard(r, base, head)
        self.assertEqual(cp.returncode, 0, f"{cp.stdout!r} {cp.stderr!r}")
        self.assert_safe(cp)
        self.assertEqual(len([l for l in cp.stdout.splitlines() if l.startswith("notice: ")]), 1, cp.stdout)
        self.assertEqual(len([l for l in cp.stdout.splitlines() if l.startswith("assertions shrink: ")]), 1, cp.stdout)

    def test_file_contents_are_not_printed(self):
        before = PY + "assert secret == 'CANARY-b2'\n"
        r, base, head = self.pair({"tests/test_a.py": before}, {"tests/test_a.py": PY + "# CANARY-c3\n"})
        cp = self.guard(r, base, head)
        self.violations(cp, shrink("tests/test_a.py", 1, 0))
        self.assert_safe(cp)
        for c in ("CANARY-b2", "CANARY-c3"):
            self.assertNotIn(c, cp.stdout + cp.stderr)

    def test_commit_message_is_not_printed(self):
        r = self.new_repo()
        base = r.snap({"tests/test_a.py": PY})
        head = r.snap({"tests/test_a.py": PY + "#\n"}, parents=[base], msg="CANARY-d4 \x1b[31m\n\nAgent: a / b / c\n")
        cp = self.guard(r, base, head)
        self.clean(cp, n=1)
        self.assert_safe(cp)
        self.assertNotIn("CANARY-d4", cp.stdout + cp.stderr)


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

    def test_no_import_across_folders(self):
        """Section 6: the script holds its own pattern copy; it imports no gate or PR module."""
        tree = source_scan(self, SCRIPT)
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for n in names:
                self.assertNotIn("risk_gate", n, f"line {node.lineno}: imports {n}")
            if isinstance(node, ast.Attribute) and node.attr in ("path",) and isinstance(node.value, ast.Name) \
                    and node.value.id == "sys":
                self.fail(f"line {node.lineno}: sys.path changed or read; no import across folders")


# ---------------------------------------------------------------- module load (constants, AC15, AC20)

def load(test):
    test.assertTrue(SCRIPT.is_file(), f"script missing: {SCRIPT}")
    spec = importlib.util.spec_from_file_location("diff_guard_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestConstants(unittest.TestCase):
    def test_bounds_and_git(self):
        m = load(self)
        self.assertEqual(m.GIT, "git")
        self.assertEqual(m.MAX_FILES, 3000)
        self.assertEqual(m.MAX_BYTES, 5_000_000)
        self.assertEqual(m.MAX_LINE, 4096)
        self.assertEqual(m.GIT_TIMEOUT, 60)

    def test_patterns(self):
        m = load(self)
        self.assertEqual(list(m.TEST_PATTERNS), TEST_PATTERNS)
        self.assertEqual(list(m.SPEC_PATTERNS), SPEC_PATTERNS)


class TestAC15Drift(unittest.TestCase):
    def test_test_patterns_match_policy(self):
        policy = next((p for p in POLICIES if p.is_file()), None)
        if policy is None:
            self.skipTest(f"risk-policy.toml not found (looked in {', '.join(str(p.parent) for p in POLICIES)})")
        with open(policy, "rb") as f:
            rules = [r for r in tomllib.load(f).get("rule", []) if r.get("reason") == "tests"]
        self.assertEqual(len(rules), 1, "one `tests` rule in the policy")
        m = load(self)
        self.assertEqual(list(m.TEST_PATTERNS), list(rules[0]["paths"]),
                         f"diff_guard.TEST_PATTERNS drifted from the `tests` rule in {policy}")


DRIVER = r"""
import importlib.util, sys
path, stub, max_bytes, timeout = sys.argv[1:5]
spec = importlib.util.spec_from_file_location("diff_guard", path)
m = importlib.util.module_from_spec(spec)
sys.modules["diff_guard"] = m
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
                result = m.run_git(["diff", "--raw"], str(self.tmp))
            except BaseException as e:  # SystemExit or the script's own error type
                raised = e
        return result, raised, time.monotonic() - t0, out.getvalue(), err.getvalue()

    def drive(self, stub, max_bytes=SMALL, timeout=2):
        self.assertTrue(SCRIPT.is_file(), f"script missing: {SCRIPT}")
        r, base, head = self.pair({"tests/test_a.py": PY}, {"tests/test_a.py": PY + "#\n"})
        t0 = time.monotonic()
        cp = subprocess.run([sys.executable, "-c", DRIVER, str(SCRIPT), str(stub), str(max_bytes), str(timeout),
                             "--base", base, "--head", head, "--repo", str(r.path)],
                            cwd=str(self.cwd), env=self.env(), capture_output=True, text=True,
                            errors="replace", timeout=60)
        return cp, time.monotonic() - t0

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
        m = load(self)
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
        self.assertTrue(all(x in rest for x in ["diff", "--raw"]), f"args not passed in order: {a}")
        self.assertEqual(rec["prompt"], "0")
        self.assertEqual(result, b"")

    def test_a_output_over_max_bytes_is_killed(self):
        m = load(self)
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
        m = load(self)
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
        m = load(self)
        data_expr = f"(bytes(range(256)) * {SMALL // 256 + 1})[:{SMALL}]"
        stub = self.stub(f"sys.stdout.buffer.write({data_expr})\nsys.stdout.buffer.flush()\n")
        result, raised, _, _, _ = self.call_run_git(m, stub)
        self.stub_pid()
        self.assertIsNone(raised, f"run_git rejected exactly MAX_BYTES: {raised!r}")
        self.assertEqual(result, (bytes(range(256)) * (SMALL // 256 + 1))[:SMALL])

    def test_c_one_byte_over_is_rejected(self):
        m = load(self)
        stub = self.stub(f"sys.stdout.buffer.write(b'x' * {SMALL + 1})\nsys.stdout.buffer.flush()\n")
        result, raised, _, _, _ = self.call_run_git(m, stub)
        self.stub_pid()
        self.assertIsNotNone(raised, "MAX_BYTES + 1 accepted")


# ---------------------------------------------------------------- review round 1 (AC8, section 6)

ATTR_DIFF = "*.py diff\n"  # forces a text patch even for a file with NUL bytes
PAD = "# " + "x" * 9000 + "\n"  # one comment line spanning byte 8000


def nul_at(text, index):
    """`text` as bytes with the pad character at `index` replaced by a NUL."""
    data = text.encode()
    assert data[index:index + 1] == b"x", f"fixture: no pad character at {index}"
    return data[:index] + b"\0" + data[index + 1:]


class TestReviewRound1BinaryFromBlobs(Base):
    """Binary status of a test file comes from its old and new blobs (NUL in the first 8000 bytes,
    git's rule), never from the patch: a `diff` attribute makes git print a text patch for a file
    with NUL bytes, which would hide a text-to-binary change."""

    def attr_pair(self, before, after, extra_after=None):
        before = dict({".gitattributes": ATTR_DIFF}, **before)
        after = dict({".gitattributes": ATTR_DIFF}, **after, **(extra_after or {}))
        r, base, head = self.pair(before, after)
        r.git("checkout", "-q", "-f", "agent/x")  # attributes are read from the work tree, as in CI
        return r, base, head

    def text_patch(self, r, base, head, path):
        """Fixture: plain git prints a text patch for `path` despite the NUL byte."""
        out = r.raw("diff", "--no-ext-diff", "--no-textconv", base, head, "--", path)
        self.assertNotIn(b"Binary files ", out, "fixture: the attribute forces a text patch")
        self.assertIn(b"\0", out, "fixture: the NUL byte is in the patch")
        self.assertFalse(r.numstat(base, head, path).startswith("-\t-\t"), "fixture: numstat shows counts")

    # (a)
    def test_text_to_binary_under_diff_attribute_fails(self):
        r, base, head = self.attr_pair({"test_a.py": "assert True\n"}, {"test_a.py": b"assert True\n\0binary\n"})
        self.assertEqual([s[0] for s in r.status(base, head)], ["M"])
        self.text_patch(r, base, head, "test_a.py")
        self.violations(self.guard(r, base, head), type_changed("test_a.py"))

    # (b)
    def test_text_shrink_under_diff_attribute_fails(self):
        r, base, head = self.attr_pair({"tests/test_a.py": PY}, {"tests/test_a.py": drop(PY, "        assert x == 3")})
        self.violations(self.guard(r, base, head), shrink("tests/test_a.py", 1, 0))

    def test_text_equal_counts_under_diff_attribute_passes(self):
        after = PY.replace("        assert x == 3\n", "        assert x == 33\n", 1)
        r, base, head = self.attr_pair({"tests/test_a.py": PY}, {"tests/test_a.py": after})
        self.clean(self.guard(r, base, head), n=1)

    # (c)
    def test_binary_to_binary_under_diff_attribute_passes(self):
        r, base, head = self.attr_pair({"test_a.py": b"assert True\n\0old\n"}, {"test_a.py": b"assert True\n\0new\n"})
        self.assertEqual([s[0] for s in r.status(base, head)], ["M"])
        self.text_patch(r, base, head, "test_a.py")
        self.clean(self.guard(r, base, head), n=1)

    # (d)
    def test_text_to_binary_under_diff_attribute_with_spec_passes_with_notice(self):
        r, base, head = self.attr_pair({"test_a.py": "assert True\n", "docs/specs/x/spec.md": "# Spec x\n\nv1\n"},
                                       {"test_a.py": b"assert True\n\0binary\n", "docs/specs/x/spec.md": "# Spec x\n\nv2\n"})
        self.text_patch(r, base, head, "test_a.py")
        self.exempt(self.guard(r, base, head), "docs/specs/x/spec.md", type_changed("test_a.py"))

    # (f) the 8000-byte boundary of git's rule: index 8000 is outside it, index 7999 inside
    def boundary(self, head_text, index, attr):
        before, after = {"tests/test_a.py": PY + PAD}, {"tests/test_a.py": nul_at(head_text + PAD, index)}
        if attr:
            return self.attr_pair(before, after)
        return self.pair(before, after)

    def test_git_agrees_on_the_8000_byte_boundary(self):
        for index, binary in ((8000, False), (7999, True)):
            with self.subTest(index=index):
                r, base, head = self.boundary(PY, index, attr=False)
                self.assertEqual(r.numstat(base, head, "tests/test_a.py").startswith("-\t-\t"), binary)

    def test_nul_after_8000_bytes_is_text_and_shrink_fails(self):
        for attr in (False, True):
            with self.subTest(attr=attr):
                r, base, head = self.boundary(drop(PY, "        assert x == 3"), 8000, attr)
                self.violations(self.guard(r, base, head), shrink("tests/test_a.py", 1, 0))

    def test_nul_after_8000_bytes_is_text_and_equal_counts_pass(self):
        after = PY.replace("        assert x == 3\n", "        assert x == 33\n", 1)
        for attr in (False, True):
            with self.subTest(attr=attr):
                r, base, head = self.boundary(after, 8000, attr)
                self.clean(self.guard(r, base, head), n=1)

    def test_nul_within_8000_bytes_under_diff_attribute_is_binary(self):
        r, base, head = self.boundary(PY, 7999, attr=True)
        self.violations(self.guard(r, base, head), type_changed("tests/test_a.py"))


# ---------------------------------------------------------------- security round 1 (spec section 17)

from test_check_trailers import (  # noqa: E402  (shared round 1 helpers)
    HOSTILE_PARENT_ENV, SCRUBBED_ENV, call_run_git, drop_loose, env_stub, group_stub, load_module,
    promisor_config, require_lazy_fetch)


class TestSecurityRound1AC21NoLazyFetch(Base):
    """AC21: with a tree missing in a promisor repo, diff_guard never runs the promisor's
    sshCommand (lazy fetch), and exits 2 with a message."""

    def test_missing_tree_no_fetch(self):
        r, base, head = self.pair({"tests/test_a.py": PY, "src/a.py": SRC},
                                  {"tests/test_a.py": drop(PY, "        assert x == 3"), "src/a.py": SRC})
        self.violations(self.guard(r, base, head), shrink("tests/test_a.py", 1, 0))  # fixture is live
        fired = promisor_config(self, r, self.tmp)
        drop_loose(self, r, r.raw("rev-parse", f"{head}:tests").decode().strip())
        require_lazy_fetch(self, r, fired, "diff", "--raw", base, head, "--")
        for label, env in (("plain env", self.env()), ("parent says GIT_NO_LAZY_FETCH=0", self.env(GIT_NO_LAZY_FETCH="0"))):
            with self.subTest(env=label):
                cp = self.guard(r, base, head, env=env)
                self.assertFalse(fired.exists(), f"{label}: diff_guard ran the promisor sshCommand (lazy fetch)")
                self.usage_error(cp, label)
            if fired.exists():
                fired.unlink()


class TestSecurityRound1AC21GitEnvironment(Base):
    """AC21: run_git drops every GIT_* variable of the parent and sets exactly
    GIT_TERMINAL_PROMPT=0, GIT_NO_LAZY_FETCH=1 and GIT_OPTIONAL_LOCKS=0; other variables stay."""

    def test_child_environment(self):
        m = load_module(self, SCRIPT, "diff_guard_round1_env")
        record = self.tmp / "env.json"
        stub = env_stub(self.tmp / "git-env-stub", record)
        result, raised, _, _, _ = call_run_git(m, stub, ["diff", "--raw"], self.tmp, environ=HOSTILE_PARENT_ENV)
        self.assertIsNone(raised, f"run_git raised {raised!r} on a clean exit")
        self.assertTrue(record.exists(), "the stub never ran")
        env = json.loads(record.read_text())
        self.assertEqual({k: v for k, v in env.items() if k.startswith("GIT_")}, SCRUBBED_ENV)
        self.assertEqual(env.get("T3_ROUND1_CANARY"), "kept", "non-GIT variables are kept")
        self.assertEqual(env.get("PATH"), os.environ.get("PATH"))


PATHSPEC_VARS = ("GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS")
GLOB_PATH = "tests/test_[x]*.py"


class TestSecurityRound1AC22PathspecEnv(Base):
    """AC22: a pathspec variable in the parent environment does not stop diff_guard from finding a
    removed assertion, on a plain path and on a path with glob characters."""

    def fixture(self):
        removed = drop(PY, "        assert x == 3")
        r, base, head = self.pair({GLOB_PATH: PY, "tests/test_xa.py": PY, "tests/test_plain.py": PY},
                                  {GLOB_PATH: removed, "tests/test_xa.py": PY, "tests/test_plain.py": removed})
        return r, base, head

    def script_diff(self, r, base, head, env):
        """The per-file diff diff_guard runs (:(literal) pathspecs), with plain git."""
        return r.raw("diff", "-U0", "-M", "--text", "--no-ext-diff", "--no-textconv", "--no-color", base, head,
                     "--", ":(literal)" + GLOB_PATH, ":(literal)" + GLOB_PATH, env=env, check=False)

    def test_baseline_without_variables(self):
        r, base, head = self.fixture()
        self.violations(self.guard(r, base, head), shrink(GLOB_PATH, 1, 0), shrink("tests/test_plain.py", 1, 0))

    def test_each_variable(self):
        r, base, head = self.fixture()
        plain_out = self.script_diff(r, base, head, r.env)
        self.assertIn(b"-        assert x == 3", plain_out, "fixture: plain git shows the removed assertion")
        for var in PATHSPEC_VARS:
            with self.subTest(var=var):
                if self.script_diff(r, base, head, dict(r.env, **{var: "1"})) == plain_out:
                    self.skipTest(f"precondition: with {var}=1 this git still honours :(literal), so the "
                                  "fixture cannot show the bug for this variable")
                cp = self.guard(r, base, head, env=self.env(**{var: "1"}))
                self.violations(cp, shrink(GLOB_PATH, 1, 0), shrink("tests/test_plain.py", 1, 0))


class TestSecurityRound1AC25ProcessGroup(Base):
    """AC25: on timeout or output overflow run_git kills git's whole process group; a child that
    git started is gone afterwards (a zombie counts as alive)."""

    def run_case(self, mode):
        m = load_module(self, SCRIPT, f"diff_guard_round1_pg_{mode}")
        pidfile, childfile = self.tmp / "stub.pid", self.tmp / "child.pid"
        stub = group_stub(self.tmp / f"git-{mode}", pidfile, childfile, mode)
        result, raised, took, out, _ = call_run_git(m, stub, ["diff", "--raw"], self.tmp, timeout=2)
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


# ---------------------------------------------------------------- security round 2 (spec section 18)

from test_check_trailers import kill_order_case, sigterm_case, sigterm_not_at_import  # noqa: E402


class TestSecurityRound2AC28Sigterm(Base):
    """AC28: diff_guard installs a SIGTERM handler in main that raises, so run_git's cleanup
    kills git's process group; the script exits 143 or 2."""

    def test_sigterm_kills_git_and_its_child(self):
        sigterm_case(self, DRIVER, SCRIPT, ["--base", "main", "--head", "HEAD", "--repo", self.tmp])

    def test_handler_not_installed_at_import(self):
        sigterm_not_at_import(self, SCRIPT, "diff_guard_round2_import")


class TestSecurityRound2AC29KillBeforeReap(Base):
    """AC29: on timeout and overflow run_git calls os.killpg(pgid, SIGKILL) before any poll/wait."""

    def test_timeout_kills_first(self):
        raised, out = kill_order_case(self, SCRIPT, "diff_guard_round2_order_stall", "stall", ["diff", "--raw"])
        if isinstance(raised, SystemExit):
            self.assertEqual(raised.code, 2)
            self.assertEqual(out, "")

    def test_overflow_kills_first(self):
        raised, out = kill_order_case(self, SCRIPT, "diff_guard_round2_order_flood", "flood", ["diff", "--raw"])
        if isinstance(raised, SystemExit):
            self.assertEqual(raised.code, 1)
            self.assertIn("too large", out)



# ---------------------------------------------------------------- security round 3 (spec section 19)

import errno  # noqa: E402

from test_check_trailers import (bounded_reap_case, bounded_select_case, deferred_sigterm_case,  # noqa: E402
                                 kill_failure_case)


class TestSecurityRound3AC30DeferredSigterm(Base):
    """AC30: the SIGTERM handler only sets a flag; run_git sees it within 0.1 s, kills the group, reaps
    git and exits 143. A SIGTERM at any point leaves no git and no child alive."""

    def args(self):
        return ["--base", "main", "--head", "HEAD", "--repo", self.tmp]

    def test_a_sigterm_while_git_runs_and_ignores_sigterm(self):
        deferred_sigterm_case(self, SCRIPT, self.args())

    def test_b_two_sigterms_in_a_row(self):
        deferred_sigterm_case(self, SCRIPT, self.args(), signals=2)

    def test_c_sigterm_right_after_popen_returns(self):
        deferred_sigterm_case(self, SCRIPT, self.args(), hook="popen")

    def test_d_sigterm_during_cleanup(self):
        deferred_sigterm_case(self, SCRIPT, self.args(), hook="killpg")

    def test_d_sigterm_during_cleanup_with_lingering_child(self):
        deferred_sigterm_case(self, SCRIPT, self.args(), hook="killpg", linger=True)

    def test_select_wait_is_bounded(self):
        bounded_select_case(self, SCRIPT, "diff_guard_round3_select", ["diff", "--raw"])


class TestSecurityRound3AC32KillFailures(Base):
    """AC32: an OSError from killpg other than ProcessLookupError makes run_git fail with exit 2 and
    `could not terminate git`; the reap is bounded (wait(timeout=5)); ProcessLookupError is ignored."""

    DIFF = ["diff", "--raw"]

    def eperm(self):
        return PermissionError(errno.EPERM, "Operation not permitted")

    def esrch(self):
        return ProcessLookupError(errno.ESRCH, "No such process")

    def test_permission_error_on_timeout(self):
        raised, out, err = kill_failure_case(self, SCRIPT, "diff_guard_round3_eperm_stall", "stall",
                                             self.DIFF, self.eperm())
        self.assertEqual(raised.code, 2)
        self.assertIn("could not terminate git", err)

    def test_permission_error_on_overflow(self):
        raised, out, err = kill_failure_case(self, SCRIPT, "diff_guard_round3_eperm_flood", "flood",
                                             self.DIFF, self.eperm())
        self.assertEqual(raised.code, 2)
        self.assertIn("could not terminate git", err)

    def test_process_lookup_error_is_ignored_on_timeout(self):
        raised, out, err = kill_failure_case(self, SCRIPT, "diff_guard_round3_esrch_stall", "stall",
                                             self.DIFF, self.esrch())
        self.assertEqual(raised.code, 2)
        self.assertIn("timed out", err)
        self.assertNotIn("could not terminate", err)

    def test_process_lookup_error_is_ignored_on_overflow(self):
        raised, out, err = kill_failure_case(self, SCRIPT, "diff_guard_round3_esrch_flood", "flood",
                                             self.DIFF, self.esrch())
        self.assertEqual(raised.code, 1)
        self.assertIn("too large", out)
        self.assertNotIn("could not terminate", err)

    def test_reap_timeout_fails_with_exit_2(self):
        bounded_reap_case(self, SCRIPT, "diff_guard_round3_reap", self.DIFF)

if __name__ == "__main__":
    unittest.main()
