"""Acceptance tests for rating_audit.py (spec 024-fu17-rating-audit, AC1..AC7)."""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "rating_audit.py"
REFS = HERE.parent / "references"
AUDIT_DOC = REFS / "rating-audit.md"
SHIP_DOC = REFS / "ship.md"

ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
SHA = "a" * 40
OTHER_SHA = "b" * 40
MAX_BYTES = 256 * 1024

# one allowed stdout line: a fixed token set, never file text
OUT_LINE = re.compile(
    r"^(FAIL|note) (verdict|regression|security|user|security_pass): "
    r"comment (APPROVE|CHANGES|clean|[0-3]), Sol (APPROVE|CHANGES|CLEAN|FINDINGS|unverified|[0-3])$"
)


def comment(verdict="APPROVE", reg=1, sec=0, user=0, sp="n/a", sha=SHA, first=None):
    head = first if first is not None else f"<!-- risk-rating sha={sha} -->"
    return (f"{head}\nverdict: {verdict}\nregression: {reg}\nsecurity: {sec}\nuser: {user}\n"
            f"security_pass: {sp}\ndisputed: none\nreasons: fine\n")


def reply(verdict="APPROVE", reg=1, sec=0, user=0, extra=""):
    return f"review text\n{extra}VERDICT: {verdict}\nRISK regression={reg} security={sec} user={user}\n"


class Base(unittest.TestCase):
    def tmp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name).resolve()

    def audit(self, comment_text, reply_text, security=None, sha=None, raw=False):
        """Run rating_audit.py. Fails (not errors) when the script is missing."""
        if not SCRIPT.is_file():
            raise AssertionError(f"{SCRIPT} does not exist")
        t = self.tmp()
        write = (lambda p, s: p.write_bytes(s)) if raw else (lambda p, s: p.write_bytes(s.encode()))
        write(t / "c.txt", comment_text)
        write(t / "r.txt", reply_text)
        args = [sys.executable, str(SCRIPT), "--comment", str(t / "c.txt"), "--reply", str(t / "r.txt")]
        if security is not None:
            (t / "s.txt").write_text(security)
            args += ["--security-reply", str(t / "s.txt")]
        if sha is not None:
            args += ["--sha", sha]
        return subprocess.run(args, capture_output=True, text=True, env=ENV, timeout=30)

    def run_args(self, *args):
        if not SCRIPT.is_file():
            raise AssertionError(f"{SCRIPT} does not exist")
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                              env=ENV, timeout=30)

    def ok(self, r):
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("FAIL", r.stdout)

    def fails(self, r, pattern):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertRegex(r.stdout, re.compile(pattern, re.M))

    def usage_error(self, r, label=""):
        self.assertEqual(r.returncode, 2, f"{label}: {r.stdout!r} {r.stderr!r}")
        self.assertEqual(r.stdout, "", label)
        self.assertNotEqual(r.stderr.strip(), "", f"{label}: no message on stderr")


class AC1Honest(Base):
    def test_ac1_equal_ratings_exit_0_without_fail(self):
        r = self.audit(comment(), reply())
        self.ok(r)

    def test_ac1_equal_changes_and_high_ratings_exit_0(self):
        self.ok(self.audit(comment("CHANGES", 3, 2, 1), reply("CHANGES", 3, 2, 1)))

    def test_ac1_matching_sha_is_accepted(self):
        self.ok(self.audit(comment(), reply(), sha=SHA))


class AC2Softening(Base):
    def test_ac2_verdict_approve_vs_changes_fails(self):
        r = self.audit(comment("APPROVE"), reply("CHANGES"))
        self.fails(r, r"^FAIL verdict: comment APPROVE, Sol CHANGES$")

    def test_ac2_each_lower_rating_fails_separately(self):
        cases = {
            "regression": (comment(reg=0, sec=1, user=1), reply(reg=2, sec=1, user=1), "0", "2"),
            "security": (comment(reg=1, sec=0, user=1), reply(reg=1, sec=2, user=1), "0", "2"),
            "user": (comment(reg=1, sec=1, user=1), reply(reg=1, sec=1, user=3), "1", "3"),
        }
        for field, (c, rp, x, y) in cases.items():
            with self.subTest(field=field):
                r = self.audit(c, rp)
                self.fails(r, rf"^FAIL {field}: comment {x}, Sol {y}$")
                others = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL") and field not in ln]
                self.assertEqual(others, [])

    def test_ac2_stricter_rating_is_a_note_and_exit_0(self):
        r = self.audit(comment(reg=2, sec=1, user=1), reply(reg=1, sec=1, user=0))
        self.ok(r)
        self.assertRegex(r.stdout, re.compile(r"^note regression: comment 2, Sol 1$", re.M))
        self.assertRegex(r.stdout, re.compile(r"^note user: comment 1, Sol 0$", re.M))

    def test_ac2_stricter_verdict_is_a_note_and_exit_0(self):
        r = self.audit(comment("CHANGES"), reply("APPROVE"))
        self.ok(r)
        self.assertRegex(r.stdout, re.compile(r"^note verdict: comment CHANGES, Sol APPROVE$", re.M))


class AC3SecurityPass(Base):
    CLEAN = comment(sp="clean")

    def test_ac3_clean_without_security_reply_is_unverified(self):
        r = self.audit(self.CLEAN, reply())
        self.fails(r, r"^FAIL security_pass: comment clean, Sol unverified$")

    def test_ac3_clean_with_findings_fails(self):
        r = self.audit(self.CLEAN, reply(), security="SECURITY: FINDINGS\n")
        self.fails(r, r"^FAIL security_pass: comment clean, Sol FINDINGS$")

    def test_ac3_clean_with_clean_passes(self):
        self.ok(self.audit(self.CLEAN, reply(), security="notes\nSECURITY: CLEAN\n"))

    def test_ac3_verdict_line_counts_in_security_reply(self):
        self.ok(self.audit(self.CLEAN, reply(), security="VERDICT: CLEAN\n"))
        r = self.audit(self.CLEAN, reply(), security="VERDICT: FINDINGS\n")
        self.fails(r, r"^FAIL security_pass: comment clean, Sol FINDINGS$")

    def test_ac3_last_security_line_wins(self):
        self.ok(self.audit(self.CLEAN, reply(), security="SECURITY: FINDINGS\nSECURITY: CLEAN\n"))
        r = self.audit(self.CLEAN, reply(), security="SECURITY: CLEAN\nSECURITY: FINDINGS\n")
        self.fails(r, r"^FAIL security_pass: comment clean, Sol FINDINGS$")

    def test_ac3_na_needs_no_security_reply(self):
        self.ok(self.audit(comment(sp="n/a"), reply()))


class AC4LastLineWins(Base):
    def test_ac4_final_verdict_beats_round_one(self):
        self.ok(self.audit(comment("APPROVE"), reply("APPROVE", extra="VERDICT: CHANGES\n")))
        r = self.audit(comment("APPROVE"), "VERDICT: APPROVE\n" + reply("CHANGES"))
        self.fails(r, r"^FAIL verdict: comment APPROVE, Sol CHANGES$")

    def test_ac4_last_risk_line_wins(self):
        rp = "RISK regression=3 security=3 user=3\n" + reply(reg=1, sec=0, user=0)
        self.ok(self.audit(comment(reg=1), rp))
        rp = "RISK regression=0 security=0 user=0\nVERDICT: APPROVE\nRISK regression=2 security=0 user=0\n"
        self.fails(self.audit(comment(reg=1), rp), r"^FAIL regression: comment 1, Sol 2$")

    def test_ac4_decoy_lines_after_the_real_ones_do_not_count(self):
        decoys = ("  VERDICT: CHANGES\n  RISK regression=3 security=3 user=3\n"
                  "see VERDICT: CHANGES\nnote RISK regression=3 security=3 user=3\n")
        self.ok(self.audit(comment(), reply() + decoys))

    def test_ac4_only_decoy_verdict_or_risk_is_unauditable(self):
        self.usage_error(self.audit(comment(), "  VERDICT: APPROVE\nRISK regression=1 security=0 user=0\n"),
                         "indented VERDICT")
        self.usage_error(self.audit(comment(), "see VERDICT: APPROVE\nRISK regression=1 security=0 user=0\n"),
                         "embedded VERDICT")
        self.usage_error(self.audit(comment(), "VERDICT: APPROVE\n  RISK regression=1 security=0 user=0\n"),
                         "indented RISK")
        self.usage_error(self.audit(comment(), "VERDICT: APPROVE\nthe RISK regression=1 security=0 user=0\n"),
                         "embedded RISK")

    def test_ac4_crlf_input_works(self):
        c = comment("APPROVE", 1, 0, 0, "clean").replace("\n", "\r\n")
        rp = reply().replace("\n", "\r\n")
        sec = "SECURITY: CLEAN\r\n"
        self.ok(self.audit(c, rp, security=sec, sha=SHA))
        self.fails(self.audit(comment("APPROVE").replace("\n", "\r\n"), reply("CHANGES").replace("\n", "\r\n")),
                   r"^FAIL verdict: comment APPROVE, Sol CHANGES$")


class AC5Exit2(Base):
    def test_ac5_missing_file(self):
        t = self.tmp()
        (t / "r.txt").write_text(reply())
        (t / "c.txt").write_text(comment())
        self.usage_error(self.run_args("--comment", str(t / "nope.txt"), "--reply", str(t / "r.txt")), "comment")
        self.usage_error(self.run_args("--comment", str(t / "c.txt"), "--reply", str(t / "nope.txt")), "reply")
        self.usage_error(self.run_args("--comment", str(t / "c.txt"), "--reply", str(t / "r.txt"),
                                       "--security-reply", str(t / "nope.txt")), "security reply")

    def test_ac5_bad_arguments(self):
        t = self.tmp()
        (t / "c.txt").write_text(comment())
        self.usage_error(self.run_args(), "no args")
        self.usage_error(self.run_args("--comment", str(t / "c.txt")), "no --reply")
        self.usage_error(self.run_args("--bogus"), "unknown flag")

    def test_ac5_file_over_256_kib(self):
        pad = "x" * (MAX_BYTES + 1 - len(reply()) + len("review text"))
        big = reply().replace("review text", pad.replace("x", "y"), 1)
        self.assertGreater(len(big.encode()), MAX_BYTES)
        self.usage_error(self.audit(comment(), big), "big reply")
        big_c = comment() + "reasons: " + "z" * MAX_BYTES + "\n"
        self.usage_error(self.audit(big_c, reply()), "big comment")
        self.usage_error(self.audit(comment(sp="clean"), reply(), security="a" * (MAX_BYTES + 1)),
                         "big security reply")

    def test_ac5_comment_without_marker_first_line(self):
        for label, c in (
            ("no marker", comment(first="verdict: APPROVE")),
            ("text before marker", f"hello\n{comment()}"),
            ("blank line before", f"\n{comment()}"),
            ("marker not first", comment().replace("<!-- risk-rating", "x <!-- risk-rating", 1)),
            ("no sha", comment(first="<!-- risk-rating -->")),
            ("empty", ""),
        ):
            with self.subTest(case=label):
                self.usage_error(self.audit(c, reply()), label)

    def test_ac5_marker_sha_must_be_40_lowercase_hex(self):
        for label, s in (("39", "a" * 39), ("41", "a" * 41), ("upper", "A" * 40), ("non-hex", "g" * 40),
                         ("short", "abc123")):
            with self.subTest(case=label):
                self.usage_error(self.audit(comment(sha=s), reply()), label)

    def test_ac5_marker_sha_differing_from_sha_option(self):
        self.usage_error(self.audit(comment(sha=SHA), reply(), sha=OTHER_SHA), "differs")
        self.usage_error(self.audit(comment(sha=SHA), reply(), sha="A" * 40), "bad --sha")

    def test_ac5_bad_or_missing_values_in_comment(self):
        base = comment()
        cases = {
            "regression 4": comment(reg=4),
            "security -1": comment(sec=-1),
            "user x": comment(user="x"),
            "verdict MAYBE": comment(verdict="MAYBE"),
            "security_pass maybe": comment(sp="maybe"),
            "no regression": base.replace("regression: 1\n", ""),
            "no security": base.replace("security: 0\n", ""),
            "no user": base.replace("user: 0\n", ""),
            "no verdict": base.replace("verdict: APPROVE\n", ""),
            "no security_pass": base.replace("security_pass: n/a\n", ""),
            "empty value": base.replace("regression: 1", "regression:"),
        }
        for label, c in cases.items():
            with self.subTest(case=label):
                self.usage_error(self.audit(c, reply()), label)

    def test_ac5_reply_without_verdict_or_risk(self):
        self.usage_error(self.audit(comment(), "RISK regression=1 security=0 user=0\n"), "no VERDICT")
        self.usage_error(self.audit(comment(), "VERDICT: APPROVE\n"), "no RISK")
        self.usage_error(self.audit(comment(), ""), "empty reply")
        self.usage_error(self.audit(comment(), "VERDICT: MAYBE\nRISK regression=1 security=0 user=0\n"),
                         "bad verdict value")
        self.usage_error(self.audit(comment(), "VERDICT: APPROVE\nRISK regression=9 security=0 user=0\n"),
                         "bad risk value")


class AC6NoEcho(Base):
    def test_ac6_reply_text_is_never_printed(self):
        garbage = "G4RBAGE" + "\x1b[31m\x07\x00\x08" + "Z" * 5000
        rp = f"{garbage}\n{garbage} VERDICT: APPROVE\n" + reply("CHANGES", 2, 1, 1)
        r = self.audit(comment("APPROVE", 0, 0, 0), rp)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("G4RBAGE", r.stdout)
        self.assertNotIn("\x1b", r.stdout)
        self.assertNotIn("\x00", r.stdout)
        lines = r.stdout.splitlines()
        self.assertTrue(lines)
        for ln in lines:
            self.assertRegex(ln, OUT_LINE)

    def test_ac6_comment_text_is_never_printed(self):
        c = comment("APPROVE", 0, 0, 0).replace("reasons: fine", "reasons: C0MMENTTEXT\x1b[0m" + "Q" * 3000)
        r = self.audit(c, reply("CHANGES", 2, 0, 0))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("C0MMENTTEXT", r.stdout)
        for ln in r.stdout.splitlines():
            self.assertRegex(ln, OUT_LINE)


class AC7Docs(unittest.TestCase):
    LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")

    def read(self, p):
        self.assertTrue(p.is_file(), f"{p} does not exist")
        return p.read_text()

    def links(self, p):
        out = []
        for target in self.LINK.findall(self.read(p)):
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("#"):
                continue
            out.append(target.split("#", 1)[0])
        return out

    def test_ac7_audit_doc_content(self):
        text = self.read(AUDIT_DOC)
        low = text.lower()
        self.assertIn("t3_thread_read", text)
        self.assertIn("monthly", low)
        self.assertIn("disputed", low)
        self.assertIn("not checked", low)

    def test_ac7_ship_links_to_audit_doc(self):
        self.assertRegex(self.read(SHIP_DOC), r"\]\([^)\s]*rating-audit\.md(#[^)\s]*)?\)")

    def test_ac7_relative_links_resolve(self):
        self.read(AUDIT_DOC)  # missing doc is a failure, not a vacuous pass
        for doc in (AUDIT_DOC, SHIP_DOC):
            for target in self.links(doc):
                if not target:
                    continue
                with self.subTest(doc=doc.name, link=target):
                    self.assertTrue((doc.parent / target).exists(), f"{target} does not resolve")


class AC4bReasons(Base):
    REASONS = "any text, with commas; and RISK-like words"

    def rp(self, risk):
        return f"review text\nVERDICT: APPROVE\n{risk}\n"

    def test_ac4b_reasons_suffix_accepted(self):
        self.ok(self.audit(comment(), self.rp(f"RISK regression=1 security=0 user=0 reasons: {self.REASONS}")))

    def test_ac4b_reasons_never_printed_and_softening_still_fails(self):
        r = self.audit(comment("APPROVE", 0, 0, 0),
                       self.rp(f"RISK regression=1 security=0 user=0 reasons: {self.REASONS}"))
        self.fails(r, r"^FAIL regression: comment 0, Sol 1$")
        self.assertNotIn("any text", r.stdout)
        self.assertNotIn("commas", r.stdout)

    def test_ac4b_empty_reasons_accepted(self):
        self.ok(self.audit(comment(), self.rp("RISK regression=1 security=0 user=0 reasons: ")))

    def test_ac4b_other_trailing_text_still_exit_2(self):
        for risk in ("RISK regression=1 security=0 user=0 and more",
                     "RISK regression=1 security=0 user=0reasons: x",
                     "RISK regression=4 security=0 user=0 reasons: x"):
            with self.subTest(risk=risk):
                self.usage_error(self.audit(comment(), self.rp(risk)), risk)


if __name__ == "__main__":
    unittest.main()
