"""Acceptance tests for WP2 tier.py and the skill docs (spec 002-wp2-tier-step, AC1..AC11)."""

import importlib
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
GATE = SKILL / "gate"
TIER = HERE / "tier.py"
POLICY_FILE = GATE / "risk-policy.toml"

for d in (str(GATE), str(HERE)):
    if d not in sys.path:
        sys.path.insert(0, d)

import risk_gate as g  # noqa: E402

POLICY = g.load_policy(POLICY_FILE)
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

FIXTURES = {
    "human": [
        "AGENTS.md", "CLAUDE.md", "src/CLAUDE.md", ".claude/settings.json",
        ".codex/agents/x.toml", ".opencode/agent/a.md", ".agents/skills/t3-build/SKILL.md",
        ".agents/skills/t3-build/gate/risk-policy.toml", ".github/workflows/ci.yml",
        ".mcp.json", "staging/claude/agents/lead.md", "docs/user/AGENTS.override.md",
        "docs/user/GEMINI.md",
    ],
    "T2": [
        "app/auth/login.py", "src/session.py", "src/crypto/aes.py", "package.json",
        "requirements.txt", "app/migrations/0002.py", "schema.sql", "Dockerfile",
        "deploy/prod.yml", "main.tf", "pytest.ini", "conftest.py",
        "docs/user/session-timeout.md",
    ],
    "T1": [
        "app/views.py", "README.md", "LICENSE", "docs/specs/x/spec.md", "docs/runbooks/x.md",
        "tests/test_x.py", "docs/user/sub/x.md", "docs/user/Install.MD",
    ],
    "T0": ["CHANGELOG.md", "docs/user/install.md", "docs/user/a.md"],
}

DEPLOY = ["*/Dockerfile*", "*/docker-compose*", "*/deploy/*", "*/infra/*", "*.tf"]


def tier_mod():
    """Import tier lazily so every test reports on its own."""
    return importlib.import_module("tier")


def cli(*args, cwd=None):
    """Run tier.py as a subprocess. Fails (not passes) when the script is missing."""
    if not TIER.is_file():
        raise AssertionError(f"{TIER} does not exist")
    with tempfile.TemporaryDirectory() as tmp:
        return subprocess.run(
            [sys.executable, str(TIER), *args],
            cwd=cwd or tmp, capture_output=True, text=True, env=ENV, timeout=120,
        )


def policy_with(no_rating_block):
    """Shipped policy with its [no_rating] table replaced (or removed when empty)."""
    text = POLICY_FILE.read_text()
    new, n = re.subn(r"\[no_rating\].*?(?=\n\[default\])", no_rating_block, text, flags=re.S)
    assert n == 1, "shipped policy layout changed"
    return new


class Base(unittest.TestCase):
    def write_policy(self, text):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = Path(tmp.name) / "policy.toml"
        p.write_text(text)
        return p

    def assertCliTier(self, args, tier, msg=None):
        r = cli(*args)
        self.assertEqual(r.returncode, 0, f"{args}: {r.stderr}")
        self.assertEqual(r.stdout.splitlines()[0], tier, msg or str(args))

    def assertCliRefuses(self, args, code=2):
        r = cli(*args)
        self.assertEqual(r.returncode, code, f"{args}: {r.stderr}")
        self.assertEqual(r.stdout, "", str(args))
        self.assertNotEqual(r.stderr.strip(), "", f"{args}: no message on stderr")


class AC1Fixtures(Base):
    def test_ac1_fixture_table(self):
        t = tier_mod()
        for want, paths in FIXTURES.items():
            for p in paths:
                with self.subTest(path=p, want=want):
                    got, reasons = t.classify([p], POLICY)
                    self.assertEqual(got, want)
                    self.assertIsInstance(reasons, list)
                    self.assertTrue(all(isinstance(x, str) for x in reasons))
                    self.assertTrue(reasons)
                    self.assertCliTier(["--policy", str(POLICY_FILE), p], want)


class AC2Mixed(Base):
    CASES = [
        (["CHANGELOG.md", "app/views.py"], "T1"),
        (["CHANGELOG.md", "package.json"], "T2"),
        (["CHANGELOG.md", ".claude/x.md"], "human"),
        (["app/views.py", "app/auth/login.py"], "T2"),
    ]

    def test_ac2_highest_tier_wins(self):
        t = tier_mod()
        for paths, want in self.CASES:
            with self.subTest(paths=paths):
                self.assertEqual(t.classify(paths, POLICY)[0], want)
                self.assertCliTier(["--policy", str(POLICY_FILE), *paths], want)


class AC3Triggers(Base):
    def test_ac3_triggers(self):
        t = tier_mod()
        cases = [
            (["CHANGELOG.md"], ["schema"], "T2"),
            (["app/views.py"], ["concurrency"], "T2"),
            (["AGENTS.md"], ["schema"], "human"),
        ]
        for paths, trig, want in cases:
            with self.subTest(paths=paths, trig=trig):
                self.assertEqual(t.classify(paths, POLICY, trig)[0], want)
                args = ["--policy", str(POLICY_FILE)]
                for x in trig:
                    args += ["--trigger", x]
                self.assertCliTier(args + paths, want)

    def test_ac3_all_allowed_triggers_give_t2(self):
        t = tier_mod()
        for trig in ["dependency", "interface", "schema", "concurrency", "untrusted-input"]:
            with self.subTest(trig=trig):
                self.assertEqual(t.classify(["CHANGELOG.md"], POLICY, [trig])[0], "T2")
                self.assertCliTier(["--trigger", trig, "CHANGELOG.md"], "T2")

    def test_ac3_repeated_triggers(self):
        self.assertCliTier(["--trigger", "schema", "--trigger", "interface", "CHANGELOG.md"], "T2")

    def test_ac3_unknown_trigger_exits_2(self):
        t = tier_mod()
        with self.assertRaises(ValueError):
            t.classify(["CHANGELOG.md"], POLICY, ["nope"])
        self.assertCliRefuses(["--trigger", "nope", "CHANGELOG.md"])


class AC4CaseAndLookalikes(Base):
    def test_ac4_case_folding(self):
        t = tier_mod()
        cases = {
            "T2": ["docs/user/TOKEN.md", "docs/user/Session.md"],
            "human": ["docs/user/claude.md", ".Claude/x.md", "Docs/user/AGENTS.OVERRIDE.md"],
            "T1": ["docs/user/Install.MD", "changelog.md"],
        }
        for want, paths in cases.items():
            for p in paths:
                with self.subTest(path=p, want=want):
                    got = t.classify([p], POLICY)[0]
                    self.assertEqual(got, want)
                    self.assertNotEqual(got, "T0")
                    self.assertCliTier(["--policy", str(POLICY_FILE), p], want)


class AC5BadInput(Base):
    def test_ac5_cli_bad_input(self):
        cases = [
            [],
            ["./CHANGELOG.md"],
            ["/CHANGELOG.md"],
            ["docs/../AGENTS.md"],
            ["a\\b.md"],
            ["docs/user/a\nb.md"],
            ["docs/user/café.md"],
            [""],
            ["--trigger", "nope", "CHANGELOG.md"],
            ["--policy", "/nonexistent/dir/policy.toml", "CHANGELOG.md"],
            ["CHANGELOG.md", "docs/user/"],
            ["docs//a.md"],
            ["docs/user/a\tb.md"],
        ]
        for args in cases:
            with self.subTest(args=args):
                self.assertCliRefuses(args)

    def test_ac5_classify_raises_value_error(self):
        t = tier_mod()
        for paths in [[], ["./CHANGELOG.md"], ["/CHANGELOG.md"], ["docs/../AGENTS.md"],
                      ["a\\b.md"], ["docs/user/a\nb.md"], ["docs/user/café.md"], [""],
                      ["CHANGELOG.md", "docs/user/"]]:
            with self.subTest(paths=paths):
                with self.assertRaises(ValueError):
                    t.classify(paths, POLICY)


class AC6MalformedPolicy(Base):
    PATHS = ["CHANGELOG.md", "AGENTS.md", "app/auth/x.py"]

    def test_ac6_malformed_no_rating_fails_closed(self):
        t = tier_mod()
        blocks = ['[no_rating]\npaths = ["CHANGELOG.md"]', '[no_rating]\npaths = "x"']
        for block in blocks:
            pol = self.write_policy(policy_with(block))
            for p in self.PATHS:
                with self.subTest(block=block, path=p):
                    r = cli("--policy", str(pol), p)
                    self.assertNotEqual(r.returncode, 0)
                    self.assertEqual(r.stdout, "")
                    with self.assertRaises(SystemExit):
                        t.classify([p], g.load_policy(pol))

    def test_ac6_absent_no_rating_means_no_t0(self):
        pol = self.write_policy(policy_with(""))
        self.assertNotIn("no_rating", pol.read_text())
        t = tier_mod()
        self.assertEqual(t.classify(["CHANGELOG.md"], g.load_policy(pol))[0], "T1")
        self.assertCliTier(["--policy", str(pol), "CHANGELOG.md"], "T1")


class AC7SizeIgnored(Base):
    def test_ac7_3000_doc_files_are_t0(self):
        paths = [f"docs/user/f{n}.md" for n in range(3000)]
        t = tier_mod()
        self.assertEqual(t.classify(paths, POLICY)[0], "T0")
        r = cli("--policy", str(POLICY_FILE), *paths)  # about 60 KB of argv, under ARG_MAX
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "T0")


class AC8CustomPolicy(Base):
    def test_ac8_custom_no_rating_makes_t0(self):
        pol = self.write_policy(policy_with('[no_rating]\npaths = ["/NOTES.md"]'))
        t = tier_mod()
        self.assertEqual(t.classify(["NOTES.md"], g.load_policy(pol))[0], "T0")
        self.assertCliTier(["--policy", str(pol), "NOTES.md"], "T0")

    def test_ac8_no_rating_cannot_lower_a_floor_of_2(self):
        pol = self.write_policy(policy_with('[no_rating]\npaths = ["/session.md"]'))
        t = tier_mod()
        self.assertEqual(t.classify(["session.md"], g.load_policy(pol))[0], "T2")
        self.assertCliTier(["--policy", str(pol), "session.md"], "T2")


class AC9Drift(Base):
    def test_ac9_guardrail_matches_gate_rule(self):
        t = tier_mod()
        rules = [r for r in POLICY["rule"] if r["reason"] == "CI, deploy, gate or agent config"]
        self.assertEqual(len(rules), 1)
        gate = {p.lower() for p in rules[0]["paths"]}  # matching is case-folded
        guard = {p.lower() for p in t.GUARDRAIL}
        deploy = {p.lower() for p in DEPLOY}
        self.assertTrue(deploy <= gate, "deploy patterns changed in the gate rule")
        for p in sorted(gate - deploy):
            with self.subTest(pattern=p):
                self.assertIn(p, guard)
        for p in sorted(deploy):
            with self.subTest(deploy=p):
                self.assertNotIn(p, guard)
        for p in ("*/risk-policy.toml", "staging/*"):
            with self.subTest(extra=p):
                self.assertIn(p, guard)


class FU5Guardrail(Base):  # docs/specs/011-fu5-gate-block-rules/spec.md AC6
    def test_fu5_opencode_and_cursor_are_guardrail(self):
        t = tier_mod()
        for p in ("*/opencode.json", "*/opencode.jsonc", "*/.cursor/*"):
            with self.subTest(pattern=p):
                self.assertIn(p, t.GUARDRAIL)
        for p in ("opencode.json", "sub/opencode.jsonc", ".cursor/rules/x.mdc"):
            with self.subTest(path=p):
                self.assertEqual(t.classify([p], POLICY)[0], "human")


class AC10CliOutput(Base):
    def test_ac10_line_format(self):
        cases = [
            ("docs/user/session-timeout.md", "T2"),
            ("AGENTS.md", "human"),
            ("app/views.py", "T1"),
            ("CHANGELOG.md", "T0"),
        ]
        for p, want in cases:
            with self.subTest(path=p):
                r = cli("--policy", str(POLICY_FILE), p)
                self.assertEqual(r.returncode, 0, r.stderr)
                lines = r.stdout.splitlines()
                self.assertEqual(lines[0], want)
                self.assertGreaterEqual(len(lines), 2)
                for line in lines[1:]:
                    self.assertTrue(line.startswith("  ") and not line.startswith("   "), repr(line))
                if want != "T0":
                    self.assertTrue(any(p in line for line in lines[1:]), r.stdout)

    def test_ac10_reason_names_trigger(self):
        r = cli("--trigger", "schema", "CHANGELOG.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "T2")
        self.assertTrue(any("schema" in line for line in lines[1:]), r.stdout)

    def test_ac10_default_policy_from_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = cli("CHANGELOG.md", "docs/user/a.md", cwd=tmp)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.splitlines()[0], "T0")
            r = cli("AGENTS.md", cwd=tmp)
            self.assertEqual(r.stdout.splitlines()[0], "human")

    def test_ac10_cwd_policy_is_preferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / ".github" / "risk-gate"
            d.mkdir(parents=True)
            (d / "risk-policy.toml").write_text(policy_with('[no_rating]\npaths = ["/NOTES.md"]'))
            r = cli("NOTES.md", cwd=tmp)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.splitlines()[0], "T0")


LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def relative_links(md):
    out = []
    for href in LINK.findall(md.read_text()):
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", href) or href.startswith("#"):
            continue
        out.append(href.split("#", 1)[0])
    return out


class AC11SkillDocs(Base):
    def test_ac11_links_resolve(self):
        skill_md = SKILL / "SKILL.md"
        self.assertTrue(skill_md.is_file())
        docs = [skill_md, *sorted((SKILL / "references").glob("*.md"))]
        for md in docs:
            for href in relative_links(md):
                with self.subTest(doc=md.name, link=href):
                    self.assertTrue((md.parent / href).exists(), f"{md}: broken link {href}")

    def test_ac11_skill_links_reference_docs(self):
        links = {os.path.normpath(h) for h in relative_links(SKILL / "SKILL.md")}
        for name in ("risk-tiers", "budgets", "brief", "models"):
            with self.subTest(ref=name):
                self.assertIn(f"references/{name}.md", links)
                self.assertTrue((SKILL / "references" / f"{name}.md").is_file())


if __name__ == "__main__":
    unittest.main()
