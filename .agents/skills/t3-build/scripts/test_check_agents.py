"""Acceptance tests for check_agents.py (spec 015-wp9-ci, AC1 to AC4, AC13, AC14).

Run from this folder:  python3 -m unittest test_check_agents -v
The script is run as a subprocess; only the source scan (AC14) reads the file. Fixtures live in
temp dirs. Problem lines are matched as whole lines; the `<rel path>` prefix may carry any leading
folder (the spec does not fix what the path is relative to), so patterns start with `(?:\\S*/)?`.
"""

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SCRIPT = SCRIPTS / "check_agents.py"
REAL_MODELS = SCRIPTS.parent / "references" / "models.md"
REAL_STAGING = SCRIPTS.parents[3] / "staging"
HEADER = "| Agent file | Role | Provider | Model | Effort |"
P = r"(?:\S*/)?"  # optional leading folder of a rel path

MODELS = f"""# Models

Intro text with a | pipe that is not a table.

| Flag | State |
|---|---|
| x | off |

{HEADER}
|---|---|---|---|---|
| alpha | Alpha | Claude | claude-x-1 | high |
| beta, gamma | Scouts | Claude | claude-y-2 (Sonnet on escalation) | medium (high: cross-cutting, security) |
| rev | Reviewer | Codex | gpt-z-1 | medium |
| voice | Voice | OpenRouter | google/gem-4 | high |

Trailing text.
"""

# Clean files per provider folder: name -> content.
CLAUDE = {
    "alpha": "---\nname: alpha\ndescription: Does: things, with colons.\nmodel: claude-x-1\neffort: high\n"
             "tools: Read, Grep\n---\n\nBody text.\n",
    "beta": "---\nname: beta\ndescription: Scout.\nmodel: claude-y-2\neffort: medium\n---\n\nBody.\n",
    "gamma": "---\nname: gamma\ndescription: Scout.\nmodel: claude-y-2\neffort: medium\n---\n\nBody.\n",
}
CODEX = {
    "rev": '# comment\nname = "rev"\ndescription = "Reviewer"\nmodel = "gpt-z-1"\n'
           'model_reasoning_effort = "medium"\nsandbox_mode = "read-only"\n',
}
OPENCODE = {
    "voice": "---\ndescription: Third voice.\nmode: subagent\nmodel: openrouter/google/gem-4\n"
             "permission:\n  edit: deny\n  bash: deny\n---\n\nBody.\n",
}
CLEAN_COUNT = 5
PROBLEM_WORDS = ("!=", "no row", "unparsable", "name does not match", "has no file",
                 "not a regular file", "too large")


def printable_only(text):
    return all(c == "\n" or c.isprintable() for c in text)


class Base(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = Path(d.name).resolve()
        self.cwd = self.tmp / "cwd"
        self.cwd.mkdir()

    def env(self):
        e = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        e["PYTHONDONTWRITEBYTECODE"] = "1"
        e["HOME"] = str(self.tmp)
        return e

    def run_ca(self, *args, cwd=None):
        self.assertTrue(SCRIPT.is_file(), f"script missing: {SCRIPT}")
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=str(cwd or self.cwd),
                              env=self.env(), capture_output=True, text=True, errors="replace",
                              timeout=120)

    # -- fixtures
    def models(self, text=MODELS, name="models.md"):
        p = self.tmp / name
        p.write_text(text)
        return p

    def root(self, dot=False, claude=True, codex=True, opencode=True):
        r = self.tmp / "root"
        for prov, files, ext, on in (("claude", CLAUDE, ".md", claude), ("codex", CODEX, ".toml", codex),
                                     ("opencode", OPENCODE, ".md", opencode)):
            if not on:
                continue
            d = r / (("." if dot else "") + prov) / "agents"
            d.mkdir(parents=True)
            for stem, body in files.items():
                (d / (stem + ext)).write_text(body)
        r.mkdir(exist_ok=True)
        return r

    def check(self, root, models=None):
        return self.run_ca("--root", root, "--models", models or self.models())

    # -- assertions
    def usage_error(self, cp, label=""):
        self.assertEqual(cp.returncode, 2, f"{label}: exit {cp.returncode}, stdout={cp.stdout!r}, stderr={cp.stderr!r}")
        self.assertEqual(cp.stdout, "", f"{label}: stdout must be empty on exit 2")
        self.assertNotEqual(cp.stderr.strip(), "", f"{label}: message on stderr on exit 2")

    def clean(self, cp, n=CLEAN_COUNT):
        self.assertEqual(cp.returncode, 0, f"exit {cp.returncode}; stdout={cp.stdout!r}; stderr={cp.stderr!r}")
        self.assertEqual(cp.stdout.splitlines(), [f"agents: {n} files match models.md"])

    def problems(self, cp, *patterns, extra=()):
        """Exit 1; each pattern fullmatches exactly one stdout line; no other problem line, except
        lines that fullmatch one of `extra` (tolerated where the spec leaves a second line open)."""
        self.assertEqual(cp.returncode, 1, f"exit {cp.returncode}; stdout={cp.stdout!r}; stderr={cp.stderr!r}")
        lines = cp.stdout.splitlines()
        for pat in patterns:
            hits = [l for l in lines if re.fullmatch(pat, l)]
            self.assertEqual(len(hits), 1, f"want one line matching {pat!r}, stdout:\n{cp.stdout}")
        for l in lines:
            if any(re.fullmatch(p, l) for p in (*patterns, *extra)):
                continue
            self.assertFalse(any(w in l for w in PROBLEM_WORDS), f"unexpected problem line {l!r}")
        self.assertNotIn("files match models.md", cp.stdout)
        return lines


# ---------------------------------------------------------------- AC1

class TestAC1UsageErrors(Base):
    def test_root_argument_missing(self):
        self.usage_error(self.run_ca("--models", self.models()), "no --root")

    def test_root_dir_missing(self):
        self.usage_error(self.check(self.tmp / "nope"), "nonexistent --root")

    def test_models_missing(self):
        self.usage_error(self.run_ca("--root", self.root(), "--models", self.tmp / "nope.md"), "missing models")

    def test_models_unreadable(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root can read a mode-000 file")
        m = self.models()
        m.chmod(0)
        self.addCleanup(m.chmod, 0o644)
        self.usage_error(self.check(self.root(), m), "unreadable models")

    def test_models_is_a_directory(self):
        d = self.tmp / "models.d"
        d.mkdir()
        self.usage_error(self.check(self.root(), d), "models is a directory")

    def test_no_table(self):
        self.usage_error(self.check(self.root(), self.models("# Models\n\nNo table here.\n")), "no table")

    def test_no_table_with_exact_header(self):
        text = MODELS.replace(HEADER, "| Agent | Role | Provider | Model | Effort |")
        self.usage_error(self.check(self.root(), self.models(text)), "header differs")

    def test_row_with_too_few_cells(self):
        text = MODELS.replace("| rev | Reviewer | Codex | gpt-z-1 | medium |", "| rev | Reviewer | Codex | gpt-z-1 |")
        self.usage_error(self.check(self.root(), self.models(text)), "4 cells")

    def test_row_with_too_many_cells(self):
        text = MODELS.replace("| rev | Reviewer | Codex | gpt-z-1 | medium |",
                              "| rev | Reviewer | Codex | gpt-z-1 | medium | extra |")
        self.usage_error(self.check(self.root(), self.models(text)), "6 cells")

    def test_unknown_provider(self):
        text = MODELS.replace("| rev | Reviewer | Codex |", "| rev | Reviewer | Gemini |")
        self.usage_error(self.check(self.root(), self.models(text)), "unknown provider")

    def test_bad_model_token(self):
        text = MODELS.replace("| gpt-z-1 |", "| -gpt |")
        self.usage_error(self.check(self.root(), self.models(text)), "model token")

    def test_bad_effort_token(self):
        text = MODELS.replace("| gpt-z-1 | medium |", "| gpt-z-1 | (medium) |")
        self.usage_error(self.check(self.root(), self.models(text)), "effort token")

    def test_bad_agent_name(self):
        # AMBIGUITY: the spec states the name regex but not the outcome; a table rule -> exit 2.
        text = MODELS.replace("| alpha | Alpha |", "| Alpha | Alpha |")
        self.usage_error(self.check(self.root(), self.models(text)), "agent name")

    def test_dot_and_plain_folder_both_present(self):
        r = self.root()
        (r / ".claude" / "agents").mkdir(parents=True)
        self.usage_error(self.check(r), ".claude/agents and claude/agents")

    def test_no_provider_folder(self):
        r = self.tmp / "empty"
        r.mkdir()
        self.usage_error(self.check(r), "no provider folder")


# ---------------------------------------------------------------- AC2

class TestAC2TableFormats(Base):
    def test_clean_fixture_passes(self):
        """Comma cell (beta, gamma), `model (note)`, `effort (note)`, OpenRouter -> opencode with the
        openrouter/ prefix and no reasoningEffort, Codex toml; preceding table is not the one."""
        self.clean(self.check(self.root()))

    def test_dot_folders(self):
        self.clean(self.check(self.root(dot=True)))

    def test_comma_cell_gives_both_names(self):
        r = self.root()
        (r / "claude" / "agents" / "gamma.md").unlink()
        self.problems(self.check(r), rf"gamma: row in models\.md \(Claude\) has no file in {P}claude/agents/?")

    def test_note_is_dropped_from_the_table_only(self):
        """The first-token rule is for table cells; a file value with a note fails the token regex."""
        r = self.root()
        f = r / "claude" / "agents" / "beta.md"
        f.write_text(f.read_text().replace("model: claude-y-2", "model: claude-y-2 (Sonnet on escalation)"))
        self.problems(self.check(r), rf"{P}claude/agents/beta\.md: unparsable model value")

    def test_openrouter_prefix_required(self):
        r = self.root()
        f = r / "opencode" / "agents" / "voice.md"
        f.write_text(f.read_text().replace("model: openrouter/google/gem-4", "model: google/gem-4"))
        # AMBIGUITY: the table value printed may be `google/gem-4` or `openrouter/google/gem-4`.
        self.problems(self.check(r), rf"{P}opencode/agents/voice\.md: model google/gem-4 != (?:openrouter/)?google/gem-4")

    def test_only_first_matching_table_counts(self):
        r = self.root()
        (r / "claude" / "agents" / "stray.md").write_text(
            "---\nname: stray\nmodel: claude-x-1\neffort: high\n---\n")
        text = MODELS + f"\n{HEADER}\n|---|---|---|---|---|\n| stray | S | Claude | claude-x-1 | high |\n"
        self.problems(self.check(r, self.models(text)), rf"{P}claude/agents/stray\.md: no row in models\.md")


# ---------------------------------------------------------------- AC3

class TestAC3Problems(Base):
    def edit(self, r, rel, old, new):
        f = r / rel
        text = f.read_text()
        self.assertIn(old, text)
        f.write_text(text.replace(old, new))

    def test_claude_model_differs(self):
        r = self.root()
        self.edit(r, "claude/agents/alpha.md", "model: claude-x-1", "model: claude-wrong")
        self.problems(self.check(r), rf"{P}claude/agents/alpha\.md: model claude-wrong != claude-x-1")

    def test_claude_effort_differs(self):
        r = self.root()
        self.edit(r, "claude/agents/alpha.md", "effort: high", "effort: low")
        self.problems(self.check(r), rf"{P}claude/agents/alpha\.md: effort low != high")

    def test_codex_model_differs(self):
        r = self.root()
        self.edit(r, "codex/agents/rev.toml", 'model = "gpt-z-1"', 'model = "gpt-z-2"')
        self.problems(self.check(r), rf"{P}codex/agents/rev\.toml: model gpt-z-2 != gpt-z-1")

    def test_codex_effort_differs(self):
        r = self.root()
        self.edit(r, "codex/agents/rev.toml", 'model_reasoning_effort = "medium"', 'model_reasoning_effort = "high"')
        self.problems(self.check(r), rf"{P}codex/agents/rev\.toml: effort high != medium")

    def test_opencode_model_differs(self):
        r = self.root()
        self.edit(r, "opencode/agents/voice.md", "model: openrouter/google/gem-4", "model: openrouter/other/x")
        self.problems(self.check(r),
                      rf"{P}opencode/agents/voice\.md: model openrouter/other/x != (?:openrouter/)?google/gem-4")

    def test_opencode_effort_differs_when_present(self):
        r = self.root()
        self.edit(r, "opencode/agents/voice.md", "mode: subagent\n", "mode: subagent\nreasoningEffort: low\n")
        self.problems(self.check(r), rf"{P}opencode/agents/voice\.md: effort low != high")

    def test_opencode_effort_equal_when_present(self):
        r = self.root()
        self.edit(r, "opencode/agents/voice.md", "mode: subagent\n", "mode: subagent\nreasoningEffort: high\n")
        self.clean(self.check(r))

    def test_file_with_no_row(self):
        r = self.root()
        (r / "claude" / "agents" / "stray.md").write_text("---\nname: stray\nmodel: claude-x-1\neffort: high\n---\n")
        self.problems(self.check(r), rf"{P}claude/agents/stray\.md: no row in models\.md")

    def test_codex_file_with_no_row(self):
        r = self.root()
        (r / "codex" / "agents" / "other.toml").write_text(
            'name = "other"\nmodel = "gpt-z-1"\nmodel_reasoning_effort = "medium"\n')
        self.problems(self.check(r), rf"{P}codex/agents/other\.toml: no row in models\.md")

    def test_row_with_no_file(self):
        r = self.root()
        (r / "codex" / "agents" / "rev.toml").unlink()
        self.problems(self.check(r), rf"rev: row in models\.md \(Codex\) has no file in {P}codex/agents/?")

    def test_openrouter_row_with_no_file(self):
        r = self.root()
        (r / "opencode" / "agents" / "voice.md").unlink()
        self.problems(self.check(r), rf"voice: row in models\.md \(OpenRouter\) has no file in {P}opencode/agents/?")

    def test_claude_name_differs_from_stem(self):
        r = self.root()
        self.edit(r, "claude/agents/alpha.md", "name: alpha", "name: alfa")
        self.problems(self.check(r), rf"{P}claude/agents/alpha\.md: name does not match file name")

    def test_codex_name_differs_from_stem(self):
        r = self.root()
        self.edit(r, "codex/agents/rev.toml", 'name = "rev"', 'name = "rvw"')
        self.problems(self.check(r), rf"{P}codex/agents/rev\.toml: name does not match file name")

    def test_duplicate_key(self):
        r = self.root()
        self.edit(r, "claude/agents/alpha.md", "effort: high\n", "effort: high\neffort: high\n")
        self.problems(self.check(r), rf"{P}claude/agents/alpha\.md: unparsable \(.+\)")

    def test_duplicate_model_key_opencode(self):
        r = self.root()
        self.edit(r, "opencode/agents/voice.md", "mode: subagent\n", "mode: subagent\nmodel: openrouter/google/gem-4\n")
        self.problems(self.check(r), rf"{P}opencode/agents/voice\.md: unparsable \(.+\)")

    def test_no_front_matter(self):
        r = self.root()
        (r / "claude" / "agents" / "alpha.md").write_text("# Alpha\n\nname: alpha\nmodel: claude-x-1\neffort: high\n")
        self.problems(self.check(r), rf"{P}claude/agents/alpha\.md: unparsable \(.+\)")

    def test_front_matter_not_closed_within_100_lines(self):
        r = self.root()
        pad = "".join(f"k{i}: v\n" for i in range(150))
        (r / "claude" / "agents" / "alpha.md").write_text(
            "---\nname: alpha\nmodel: claude-x-1\neffort: high\n" + pad + "---\n")
        self.problems(self.check(r), rf"{P}claude/agents/alpha\.md: unparsable \(.+\)")

    def test_broken_toml(self):
        r = self.root()
        (r / "codex" / "agents" / "rev.toml").write_text('name = "rev\nmodel = gpt-z-1\n')
        self.problems(self.check(r), rf"{P}codex/agents/rev\.toml: unparsable \(.+\)")

    def test_symlinked_file(self):
        r = self.root()
        out = self.tmp / "outside"
        out.mkdir()
        (out / "beta.md").write_text(CLAUDE["beta"])
        f = r / "claude" / "agents" / "beta.md"
        f.unlink()
        f.symlink_to(out / "beta.md")
        # AMBIGUITY: whether the beta row is also reported as having no file is not fixed.
        self.problems(self.check(r), rf"{P}claude/agents/beta\.md: not a regular file",
                      extra=(rf"beta: row in models\.md \(Claude\) has no file in {P}claude/agents/?",))

    def test_symlinked_agents_folder_is_not_followed(self):
        r = self.root()
        real = self.tmp / "elsewhere" / "agents"
        real.parent.mkdir()
        shutil.move(str(r / "claude" / "agents"), str(real))
        (r / "claude" / "agents").symlink_to(real, target_is_directory=True)
        cp = self.check(r)
        self.assertEqual(cp.returncode, 1, f"symlinked folder is a problem: {cp.stdout!r} {cp.stderr!r}")
        self.assertNotIn("files match models.md", cp.stdout)

    def test_symlinked_provider_folder_is_not_followed(self):
        r = self.root()
        real = self.tmp / "elsewhere" / "claude"
        real.parent.mkdir()
        shutil.move(str(r / "claude"), str(real))
        (r / "claude").symlink_to(real, target_is_directory=True)
        cp = self.check(r)
        self.assertEqual(cp.returncode, 1, f"symlinked parent folder is a problem: {cp.stdout!r} {cp.stderr!r}")
        self.assertNotIn("files match models.md", cp.stdout)

    def test_file_over_64_kb(self):
        r = self.root()
        f = r / "claude" / "agents" / "alpha.md"
        f.write_text(CLAUDE["alpha"] + "x" * 70_000 + "\n")
        cp = self.check(r)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertTrue(any(re.fullmatch(r"too large: .+ over .+", l) for l in cp.stdout.splitlines()), cp.stdout)
        self.assertNotIn("files match models.md", cp.stdout)

    def test_more_than_200_files(self):
        r = self.root()
        d = r / "claude" / "agents"
        for i in range(201 - len(CLAUDE)):
            (d / f"x{i:03d}.md").write_text(f"---\nname: x{i:03d}\nmodel: claude-x-1\neffort: high\n---\n")
        self.assertEqual(len(list(d.glob("*.md"))), 201)
        cp = self.check(r)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertTrue(any(re.fullmatch(r"too large: .+ over .+", l) for l in cp.stdout.splitlines()), cp.stdout)

    def test_unparsable_model_value_is_not_echoed(self):
        r = self.root()
        self.edit(r, "claude/agents/alpha.md", "model: claude-x-1", "model: $(touch pwned);x")
        cp = self.check(r)
        self.problems(cp, rf"{P}claude/agents/alpha\.md: unparsable model value")
        self.assertNotIn("pwned", cp.stdout)

    def test_provider_without_folder_is_not_reported(self):
        self.clean(self.check(self.root(codex=False)), n=CLEAN_COUNT - 1)

    def test_only_one_provider_folder(self):
        self.clean(self.check(self.root(codex=False, opencode=False)), n=3)

    def test_other_files_are_ignored(self):
        r = self.root()
        d = r / "claude" / "agents"
        (d / "notes.txt").write_text("not an agent\n")
        # lead, spec 015 AC41: a folder inside an agents folder is now a problem, so none here
        (r / "codex" / "agents" / "readme.md").write_text("ignored in the codex folder\n")
        self.clean(self.check(r))

    def test_several_problems_one_line_each(self):
        r = self.root()
        self.edit(r, "claude/agents/alpha.md", "effort: high", "effort: low")
        self.edit(r, "codex/agents/rev.toml", 'model = "gpt-z-1"', 'model = "gpt-z-9"')
        (r / "claude" / "agents" / "gamma.md").unlink()
        self.problems(self.check(r),
                      rf"{P}claude/agents/alpha\.md: effort low != high",
                      rf"{P}codex/agents/rev\.toml: model gpt-z-9 != gpt-z-1",
                      rf"gamma: row in models\.md \(Claude\) has no file in {P}claude/agents/?")


# ---------------------------------------------------------------- AC4

@unittest.skipUnless(REAL_STAGING.is_dir() and REAL_MODELS.is_file(), "staging/ or models.md not found")
class TestAC4Real(Base):
    def real_count(self, root):
        return sum(len(list((root / p / "agents").glob(g)))
                   for p, g in (("claude", "*.md"), ("codex", "*.toml"), ("opencode", "*.md")))

    def test_real_staging_matches(self):
        # lead, spec 015 AC31: a `..` component in --models is rejected, so the real models.md is given absolute
        cp = self.run_ca("--root", "../../../../staging", "--models", str(REAL_MODELS), cwd=SCRIPTS)
        self.clean(cp, n=self.real_count(REAL_STAGING))

    def test_default_models_path_is_next_to_the_script(self):
        cp = self.run_ca("--root", REAL_STAGING)  # cwd is a temp dir: the default must not depend on it
        self.clean(cp, n=self.real_count(REAL_STAGING))

    def test_copy_with_one_effort_changed(self):
        copy = self.tmp / "staging"
        shutil.copytree(REAL_STAGING, copy, symlinks=True)
        f = copy / "claude" / "agents" / "implementer.md"
        text = f.read_text()
        self.assertIn("\neffort: medium\n", text)
        f.write_text(text.replace("\neffort: medium\n", "\neffort: high\n", 1))
        cp = self.run_ca("--root", copy, "--models", REAL_MODELS)
        self.problems(cp, rf"{P}claude/agents/implementer\.md: effort high != medium")


# ---------------------------------------------------------------- AC13

class TestAC13OutputSafety(Base):
    def stray(self, r, filename):
        (r / "claude" / "agents" / filename).write_text("---\nname: stray\nmodel: claude-x-1\neffort: high\n---\n")

    def test_escape_in_file_name(self):
        r = self.root()
        self.stray(r, "evil\x1b[31m.md")
        cp = self.check(r)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertTrue(printable_only(cp.stdout), repr(cp.stdout))
        self.assertIn("evil", cp.stdout, "the file is reported")

    def test_newline_in_file_name_cannot_forge_a_line(self):
        r = self.root()
        self.stray(r, "x\nagents: 9 files match models.md.md")
        cp = self.check(r)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertTrue(printable_only(cp.stdout), repr(cp.stdout))
        self.assertNotIn("agents: 9 files match models.md", cp.stdout.splitlines())
        self.assertFalse(any(l.startswith("agents: 9") for l in cp.stdout.splitlines()), cp.stdout)

    def test_long_file_name_is_cut(self):
        r = self.root()
        stem = "a" * 247  # 250 bytes with .md: within the 255-byte file name limit, over 200 characters
        self.stray(r, stem + ".md")
        cp = self.check(r)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertNotIn(stem, cp.stdout, "value over 200 characters printed whole")
        self.assertIn("...", cp.stdout)
        self.assertTrue(all(len(l) <= 300 for l in cp.stdout.splitlines()), cp.stdout)

    def test_control_bytes_in_model_value(self):
        r = self.root()
        f = r / "claude" / "agents" / "alpha.md"
        f.write_text(f.read_text().replace("model: claude-x-1", "model: claude\x1b[2J\x07x"))
        cp = self.check(r)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertTrue(printable_only(cp.stdout), repr(cp.stdout))
        self.assertNotIn("[2J", cp.stdout)

    def test_control_bytes_in_name_value(self):
        r = self.root()
        f = r / "claude" / "agents" / "alpha.md"
        f.write_text(f.read_text().replace("name: alpha", "name: al\x1b]0;t\x07pha"))
        cp = self.check(r)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertTrue(printable_only(cp.stdout), repr(cp.stdout))

    def test_file_contents_are_not_printed(self):
        r = self.root()
        f = r / "claude" / "agents" / "alpha.md"
        f.write_text(f.read_text().replace("effort: high", "effort: low") + "CANARY-c4f1 secret body\n")
        cp = self.check(r)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertNotIn("CANARY-c4f1", cp.stdout + cp.stderr)


# ---------------------------------------------------------------- AC14

FORBIDDEN_NAMES = ("socket", "urllib", "requests", "eval", "exec")
SUBPROCESS_FUNCS = {"Popen", "run", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}


def source_scan(test, path):
    """Shared AC14 scan: code tokens only (strings and comments are ignored, so a docstring may
    state the rules)."""
    test.assertTrue(path.is_file(), f"script missing: {path}")
    with open(path, "rb") as f:
        toks = [t for t in tokenize.tokenize(f.readline) if t.type in (tokenize.NAME, tokenize.OP)]
    seq = [t.string for t in toks]
    names = set(seq)
    for bad in FORBIDDEN_NAMES:
        test.assertNotIn(bad, names, f"{bad} used in {path.name}")
    for i in range(len(seq) - 2):
        three = seq[i:i + 3]
        test.assertNotEqual(three, ["shell", "=", "True"], "shell=True")
        test.assertNotEqual(three, ["os", ".", "system"], "os.system")
        test.assertNotEqual(three, ["http", ".", "client"], "http.client")
        test.assertNotEqual(three, ["os", ".", "popen"], "os.popen")
    tree = ast.parse(path.read_text(), str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else None
        is_sub = (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "subprocess") \
            or (isinstance(fn, ast.Name) and name in SUBPROCESS_FUNCS)
        if not is_sub:
            continue
        test.assertNotIn(name, ("getoutput", "getstatusoutput"), f"line {node.lineno}: {name} runs a shell")
        argv = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "args"), None)
        test.assertIsNotNone(argv, f"line {node.lineno}: subprocess call without argv")
        test.assertFalse(isinstance(argv, (ast.Constant, ast.JoinedStr)), f"line {node.lineno}: argv is a string")
        test.assertFalse(isinstance(argv, ast.BinOp) and isinstance(argv.op, ast.Mod), f"line {node.lineno}: % string")
        test.assertFalse(isinstance(argv, ast.Call) and isinstance(argv.func, ast.Attribute)
                         and argv.func.attr in ("format", "join"), f"line {node.lineno}: argv built as a string")
        for k in node.keywords:
            if k.arg == "shell":
                test.assertTrue(isinstance(k.value, ast.Constant) and k.value.value is False,
                                f"line {node.lineno}: shell= must be absent or False")
    return tree


class TestAC14SourceScan(unittest.TestCase):
    def test_no_shell_eval_network(self):
        source_scan(self, SCRIPT)

    def test_reads_files_only(self):
        # Spec AC20: check_agents reads files only and makes no git call.
        tree = source_scan(self, SCRIPT)
        mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertNotIn("subprocess", mods, "check_agents starts no process")


# ---------------------------------------------------------------- review round 1 (spec section 3)

class TestReviewRound1RootSymlink(Base):
    """Section 3: symlinks are never followed, and that includes --root itself."""

    def test_root_that_is_a_symlink_is_not_followed(self):
        real = self.root()  # a valid staging-shaped tree in the temp dir
        self.clean(self.check(real))  # fixture: the real folder is clean
        link = self.tmp / "linked-root"
        link.symlink_to(real, target_is_directory=True)
        cp = self.check(link)
        self.assertEqual(cp.returncode, 1, f"exit {cp.returncode}; stdout={cp.stdout!r}; stderr={cp.stderr!r}")
        lines = cp.stdout.splitlines()
        self.assertEqual(len(lines), 1, f"want one problem line, stdout:\n{cp.stdout}")
        self.assertRegex(lines[0], rf"^{P}{re.escape(link.name)}/?: not a regular folder$")
        self.assertNotIn("files match models.md", cp.stdout)

    def test_same_fixture_without_the_symlink_passes(self):
        self.clean(self.check(self.root()))


# ---------------------------------------------------------------- security round 1 (spec section 17)

import time  # noqa: E402  (used by the round 1 classes only)


def sized_models(size):
    """MODELS padded with plain text lines after the table to exactly `size` bytes."""
    body = MODELS.encode()
    fill = size - len(body)
    assert fill > 1, fill
    tail = ("p" * 99 + "\n") * ((fill - 1) // 100)
    tail += "r" * (fill - len(tail) - 1) + "\n"
    data = body + tail.encode()
    assert len(data) == size, len(data)
    return data


class TestSecurityRound1AC23ModelsFile(Base):
    """AC23: --models is opened like an agent file: O_NOFOLLOW | O_NONBLOCK, a regular file
    (fstat), at most MAX_MODELS_BYTES = 1_000_000 bytes."""

    def run_bounded(self, models, timeout=10):
        """The script with a short timeout; a hang is a failure, not an error."""
        self.assertTrue(SCRIPT.is_file(), f"script missing: {SCRIPT}")
        try:
            root = self.tmp / "root" if (self.tmp / "root").is_dir() else self.root()
            return subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), "--models", str(models)],
                                  cwd=str(self.cwd), env=self.env(), capture_output=True, text=True,
                                  errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            self.fail(f"check_agents hung for {timeout} s on --models {models.name}")

    def test_constant(self):
        import importlib.util
        sys.dont_write_bytecode = True  # loading the module must leave no __pycache__
        self.assertTrue(SCRIPT.is_file(), f"script missing: {SCRIPT}")
        spec = importlib.util.spec_from_file_location("check_agents_under_test", SCRIPT)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.assertEqual(getattr(m, "MAX_MODELS_BYTES", None), 1_000_000)

    def test_normal_file_passes(self):
        self.clean(self.run_bounded(self.models()))

    def test_symlinked_models_file_exit_2(self):
        target = self.models(name="real-models.md")
        self.clean(self.run_bounded(target))  # fixture: the target itself is a valid models file
        link = self.tmp / "models-link.md"
        link.symlink_to(target)
        self.usage_error(self.run_bounded(link), "symlinked models file")

    def test_symlink_to_models_in_a_folder_exit_2(self):
        # The default path form (<dir>/models.md) with the file itself a symlink.
        d = self.tmp / "refs"
        d.mkdir()
        target = self.models(name="elsewhere.md")
        (d / "models.md").symlink_to(target)
        self.usage_error(self.run_bounded(d / "models.md"), "symlinked models.md")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "no FIFOs on this platform")
    def test_fifo_models_file_exit_2_without_hang(self):
        fifo = self.tmp / "models.fifo"
        os.mkfifo(fifo)
        t0 = time.monotonic()
        cp = self.run_bounded(fifo)
        self.assertLess(time.monotonic() - t0, 5, "the FIFO was waited on")
        self.usage_error(cp, "FIFO models file")

    def test_one_byte_over_the_limit_is_too_large(self):
        p = self.tmp / "big-models.md"
        p.write_bytes(sized_models(1_000_001))
        cp = self.run_bounded(p)
        self.assertEqual(cp.returncode, 1, f"exit {cp.returncode}; stdout={cp.stdout[-500:]!r}; stderr={cp.stderr!r}")
        lines = cp.stdout.splitlines()
        self.assertTrue(any(l.startswith("too large: ") for l in lines), f"no `too large:` line: {cp.stdout!r}")
        self.assertNotIn("files match models.md", cp.stdout)

    def test_exactly_the_limit_passes(self):
        p = self.tmp / "full-models.md"
        p.write_bytes(sized_models(1_000_000))
        self.clean(self.run_bounded(p))


class TestSecurityRound1AC24FrontMatterKeys(Base):
    """AC24: a top-level front-matter key must match ^[A-Za-z_][A-Za-z0-9_-]*$; any other key
    makes the file unparsable (exit 1, one line). Duplicate detection is exact-match on the key."""

    KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
    BAD_KEYS = ['"model"', "'model'", '"x"', "my key", "model ", "ke\x01y", "ke\x00y", "ke\x1b[2Ky",
                "9lives", "-dash", "k.y", "kéy", "k/y"]

    def add_after(self, r, rel, anchor, line):
        f = r / rel
        text = f.read_text()
        self.assertIn(anchor, text)
        f.write_text(text.replace(anchor, anchor + line, 1))

    def test_bad_key_list_is_outside_the_key_regex(self):
        for k in self.BAD_KEYS:
            self.assertIsNone(self.KEY.fullmatch(k), k)

    def test_quoted_model_after_model_is_unparsable_claude(self):
        r = self.root()
        self.add_after(r, "claude/agents/alpha.md", "model: claude-x-1\n", '"model": evil\n')
        self.problems(self.check(r), rf"{P}claude/agents/alpha\.md: unparsable \(.+\)")

    def test_quoted_model_after_model_is_unparsable_opencode(self):
        r = self.root()
        self.add_after(r, "opencode/agents/voice.md", "model: openrouter/google/gem-4\n", '"model": evil\n')
        self.problems(self.check(r), rf"{P}opencode/agents/voice\.md: unparsable \(.+\)")

    def test_single_quoted_effort_is_unparsable(self):
        r = self.root()
        self.add_after(r, "claude/agents/alpha.md", "effort: high\n", "'effort': low\n")
        self.problems(self.check(r), rf"{P}claude/agents/alpha\.md: unparsable \(.+\)")

    def test_each_bad_key_is_unparsable(self):
        for i, key in enumerate(self.BAD_KEYS):
            for prov, rel, anchor in (("claude", "claude/agents/alpha.md", "effort: high\n"),
                                      ("opencode", "opencode/agents/voice.md", "mode: subagent\n")):
                with self.subTest(key=key, provider=prov):
                    shutil.rmtree(self.tmp / "root", ignore_errors=True)
                    r = self.root()
                    self.add_after(r, rel, anchor, f"{key}: value\n")
                    cp = self.check(r)
                    self.problems(cp, rf"{P}{re.escape(rel)}: unparsable \(.+\)")
                    self.assertTrue(printable_only(cp.stdout), repr(cp.stdout))

    def test_valid_key_forms_pass(self):
        r = self.root()
        self.add_after(r, "claude/agents/alpha.md", "effort: high\n",
                       "allowed-tools: Read\n_private: x\nKey2: y\nmodel_x: z\nA: b\n")
        self.add_after(r, "opencode/agents/voice.md", "mode: subagent\n", "temperature: 0\nx-y_z: 1\n")
        self.clean(self.check(r))

    def test_duplicate_detection_is_exact_match(self):
        # `Model` is another key, not a duplicate of `model`; the model check reads `model`.
        r = self.root()
        self.add_after(r, "claude/agents/alpha.md", "model: claude-x-1\n", "Model: claude-wrong\n")
        self.clean(self.check(r))

    def test_exact_duplicate_still_unparsable(self):
        r = self.root()
        self.add_after(r, "claude/agents/alpha.md", "model: claude-x-1\n", "model: claude-x-1\n")
        self.problems(self.check(r), rf"{P}claude/agents/alpha\.md: unparsable \(.+\)")


# ---------------------------------------------------------------- security round 2 (spec section 18)

class TestSecurityRound2AC27ModelsParents(Base):
    """AC27: a --models inside --root has no symlink in any component below --root (exit 2, empty
    stdout); a --models outside --root only has its leaf checked, so symlinked ancestors pass."""

    def elsewhere(self, name="elsewhere"):
        d = self.tmp / name
        d.mkdir()
        (d / "models.md").write_text(MODELS)
        return d

    def both_forms(self, root, models):
        """(label, cwd, --root, --models): relative to tmp, and absolute."""
        return (("relative", self.tmp, root.relative_to(self.tmp), models.relative_to(self.tmp)),
                ("absolute", self.cwd, root, models))

    def test_symlinked_folder_below_root_exit_2(self):
        root, target = self.root(), self.elsewhere()
        self.clean(self.check(root, target / "models.md"))  # fixture: the target is a valid models file
        (root / "refs").symlink_to(target, target_is_directory=True)
        for label, cwd, r, models in self.both_forms(root, root / "refs" / "models.md"):
            with self.subTest(form=label):
                self.usage_error(self.run_ca("--root", r, "--models", models, cwd=cwd), f"{label} root/refs link")

    def test_nested_symlinked_folder_below_root_exit_2(self):
        root, target = self.root(), self.elsewhere()
        (root / "docs").mkdir()
        (root / "docs" / "refs").symlink_to(target, target_is_directory=True)
        for label, cwd, r, models in self.both_forms(root, root / "docs" / "refs" / "models.md"):
            with self.subTest(form=label):
                self.usage_error(self.run_ca("--root", r, "--models", models, cwd=cwd),
                                 f"{label} root/docs/refs link")

    def test_symlink_inside_root_to_a_folder_inside_root_exit_2(self):
        root = self.root()
        (root / "real-refs").mkdir()
        (root / "real-refs" / "models.md").write_text(MODELS)
        (root / "refs").symlink_to(root / "real-refs", target_is_directory=True)
        for label, cwd, r, models in self.both_forms(root, root / "refs" / "models.md"):
            with self.subTest(form=label):
                self.usage_error(self.run_ca("--root", r, "--models", models, cwd=cwd), f"{label} in-root link")

    def test_models_file_inside_root_passes(self):
        root = self.root()
        (root / "refs" / "deep").mkdir(parents=True)
        for rel in ("models.md", "refs/models.md", "refs/deep/models.md"):
            (root / rel).write_text(MODELS)
            for label, cwd, r, models in self.both_forms(root, root / rel):
                with self.subTest(models=rel, form=label):
                    self.clean(self.run_ca("--root", r, "--models", models, cwd=cwd))

    def test_models_outside_root_under_symlinked_parent_passes(self):
        root, real = self.root(), self.elsewhere("real-parent")
        link = self.tmp / "linked-parent"
        link.symlink_to(real, target_is_directory=True)
        self.clean(self.check(root, link / "models.md"))
        self.clean(self.run_ca("--root", root.relative_to(self.tmp), "--models", "linked-parent/models.md",
                               cwd=self.tmp))

    def test_root_under_symlinked_parent_with_models_inside_passes(self):
        # Ancestors of --root are not components below it.
        real = self.tmp / "real-parent"
        real.mkdir()
        root = self.root()
        root.rename(real / "root")
        link = self.tmp / "linked-parent"
        link.symlink_to(real, target_is_directory=True)
        (real / "root" / "refs").mkdir()
        (real / "root" / "refs" / "models.md").write_text(MODELS)
        self.clean(self.run_ca("--root", link / "root", "--models", link / "root" / "refs" / "models.md"))

    def test_default_models_path_under_symlinked_ancestors_passes(self):
        # The default path (<script dir>/../references/models.md) lies outside --root; the copy of the
        # script is started through a symlinked ancestor, as on a runner with /tmp or /var links.
        real = self.tmp / "real-skill"
        (real / "scripts").mkdir(parents=True)
        (real / "references").mkdir()
        shutil.copy(SCRIPT, real / "scripts" / SCRIPT.name)
        (real / "references" / "models.md").write_text(MODELS)
        link = self.tmp / "linked-skill"
        link.symlink_to(real, target_is_directory=True)
        root = self.root()
        cp = subprocess.run([sys.executable, str(link / "scripts" / SCRIPT.name), "--root", str(root)],
                            cwd=str(self.cwd), env=self.env(), capture_output=True, text=True, errors="replace",
                            timeout=120)
        self.clean(cp)



# ---------------------------------------------------------------- security round 3 (spec section 19)

import contextlib  # noqa: E402
import importlib.util  # noqa: E402
import io  # noqa: E402
from unittest import mock  # noqa: E402


class TestSecurityRound3AC31ModelsPathForms(Base):
    """AC31: a --models value with a `..` component, an empty component (`//`, trailing `/`) or an
    empty value exits 2 with empty stdout; `.` components are removed. Inside --root is decided by
    directory identity (os.path.samefile on the lexical ancestors), so a second path to the root still
    gets the AC27 walk. Values are passed as raw strings: pathlib would normalise them."""

    def setUp(self):
        super().setUp()
        self.r = self.root()
        (self.r / "refs").mkdir()
        (self.r / "models.md").write_text(MODELS)
        (self.r / "refs" / "models.md").write_text(MODELS)
        self.models()  # tmp/models.md: a valid file outside --root
        self.elsewhere = self.tmp / "elsewhere"
        self.elsewhere.mkdir()
        (self.elsewhere / "models.md").write_text(MODELS)
        (self.r / "linked-refs").symlink_to(self.elsewhere, target_is_directory=True)

    def rejected(self, cases):
        for label, cwd, value in cases:
            with self.subTest(case=label, models=value):
                self.usage_error(self.run_ca("--root", self.r, "--models", value, cwd=cwd), label)

    def accepted(self, cases, root=None):
        for label, cwd, value in cases:
            with self.subTest(case=label, models=value):
                self.clean(self.run_ca("--root", root or self.r, "--models", value, cwd=cwd))

    def test_dotdot_component_exit_2(self):
        t, r = str(self.tmp), str(self.r)
        self.rejected([
            ("inside, absolute", self.cwd, f"{r}/refs/../models.md"),
            ("inside, relative", self.tmp, "root/refs/../models.md"),
            ("inside, leading ..", self.r / "refs", "../models.md"),
            ("outside, absolute", self.cwd, f"{t}/cwd/../models.md"),
            ("outside, relative", self.cwd, "../models.md"),
        ])

    def test_empty_component_exit_2(self):
        t, r = str(self.tmp), str(self.r)
        self.rejected([
            ("inside, absolute", self.cwd, f"{r}//models.md"),
            ("inside, nested", self.cwd, f"{r}/refs//models.md"),
            ("inside, relative", self.tmp, "root//refs/models.md"),
            ("outside, absolute", self.cwd, f"{t}//models.md"),
        ])

    def test_trailing_slash_exit_2(self):
        t, r = str(self.tmp), str(self.r)
        self.rejected([
            ("inside, file", self.cwd, f"{r}/models.md/"),
            ("inside, nested file", self.cwd, f"{r}/refs/models.md/"),
            ("inside, relative", self.tmp, "root/models.md/"),
            ("outside, file", self.cwd, f"{t}/models.md/"),
        ])

    def test_empty_value_exit_2(self):
        self.rejected([("cwd outside root", self.cwd, ""), ("cwd is root", self.r, "")])

    def test_dot_components_are_removed(self):
        t, r = str(self.tmp), str(self.r)
        self.accepted([
            ("inside, absolute", self.cwd, f"{r}/./models.md"),
            ("inside, nested", self.cwd, f"{r}/refs/./models.md"),
            ("inside, relative", self.tmp, "./root/./refs/models.md"),
            ("inside, from root", self.r, "./models.md"),
            ("outside, absolute", self.cwd, f"{t}/./models.md"),
        ])

    def test_dot_components_do_not_hide_a_symlink_below_root(self):
        r = str(self.r)
        self.rejected([
            ("absolute", self.cwd, f"{r}/./linked-refs/models.md"),
            ("relative", self.tmp, "./root/linked-refs/./models.md"),
        ])

    def test_root_reached_through_a_symlinked_parent_is_inside(self):
        # tmp/link -> tmp, so tmp/link/root is the same folder as --root by another path.
        (self.tmp / "link").symlink_to(self.tmp, target_is_directory=True)
        t = str(self.tmp)
        self.rejected([
            ("absolute", self.cwd, f"{t}/link/root/linked-refs/models.md"),
            ("relative", self.tmp, "link/root/linked-refs/models.md"),
        ])
        self.accepted([
            ("absolute", self.cwd, f"{t}/link/root/refs/models.md"),
            ("relative", self.tmp, "link/root/refs/models.md"),
        ])

    def test_symlink_to_root_below_root_exit_2(self):
        # root/self -> root: the ancestor root/self is the same folder as --root too; the walk must start
        # at the shallowest such ancestor, so the link below it is still seen.
        (self.r / "self").symlink_to(self.r, target_is_directory=True)
        self.rejected([
            ("absolute", self.cwd, f"{self.r}/self/models.md"),
            ("relative", self.tmp, "root/self/refs/models.md"),
        ])

    def test_case_alias_of_root_is_inside(self):
        alias = self.tmp / "ROOT"
        if not (alias.exists() and os.path.samefile(alias, self.r)):
            self.skipTest("case-sensitive file system: no case alias of --root exists")
        t = str(self.tmp)
        self.rejected([
            ("alias in models, absolute", self.cwd, f"{t}/ROOT/linked-refs/models.md"),
            ("alias in models, relative", self.tmp, "ROOT/linked-refs/models.md"),
        ])
        for label, cwd, root, value in (("alias as --root", self.cwd, alias, f"{t}/root/linked-refs/models.md"),
                                        ("alias in both", self.cwd, alias, f"{t}/ROOT/linked-refs/models.md")):
            with self.subTest(case=label):
                self.usage_error(self.run_ca("--root", root, "--models", value, cwd=cwd), label)
        self.accepted([
            ("alias in models, real folder", self.cwd, f"{t}/ROOT/refs/models.md"),
            ("alias in models, relative", self.tmp, "ROOT/models.md"),
        ])


def load_check_agents(test, name):
    test.assertTrue(SCRIPT.is_file(), f"script missing: {SCRIPT}")
    sys.dont_write_bytecode = True  # loading the module under test must leave no __pycache__
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestSecurityRound3AC33PinnedFolders(Base):
    """AC33: each agents folder is opened once (O_DIRECTORY|O_NOFOLLOW, walked from --root) and listed
    and read through that descriptor. A hook on os.open replaces claude/agents by a symlink to an
    external folder right after the folder's first open; the external files are never read."""

    def swap_case(self, inside, outside, name):
        root = self.root()
        agents = root / "claude" / "agents"
        for stem, body in inside.items():
            (agents / f"{stem}.md").write_text(body)
        external = self.tmp / "external-agents"
        external.mkdir()
        for stem, body in outside.items():
            (external / f"{stem}.md").write_text(body)
        models = self.models()
        st = os.stat(agents)
        folder = (st.st_dev, st.st_ino)
        external_ids = {(s.st_dev, s.st_ino) for s in (os.stat(p) for p in [external, *external.iterdir()])}
        m = load_check_agents(self, name)
        real_open, state = os.open, {"swapped": False, "external": []}

        def hook(path, flags, mode=0o777, *, dir_fd=None):
            fd = real_open(path, flags, mode, dir_fd=dir_fd)
            s = os.fstat(fd)
            if (s.st_dev, s.st_ino) in external_ids:
                state["external"].append(path)
            if not state["swapped"] and (s.st_dev, s.st_ino) == folder:
                state["swapped"] = True
                os.rename(agents, root / "claude" / "agents-pinned")
                os.symlink(external, agents, target_is_directory=True)
            return fd

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(m.os, "open", hook), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = m.main(["--root", str(root), "--models", str(models)])
        self.assertTrue(state["swapped"], "claude/agents was never opened with os.open: the folder is not pinned "
                                          "by a descriptor, so a swap after the check is followed")
        self.assertTrue(agents.is_symlink(), "fixture: the swap did not happen")
        self.assertEqual(state["external"], [], "a file or folder of the swapped-in external folder was opened")
        return subprocess.CompletedProcess([], code, out.getvalue(), err.getvalue())

    def test_swap_to_drifting_external_folder_is_not_followed(self):
        outside = {k: v.replace("model: claude-x-1", "model: claude-outside-9")
                   .replace("model: claude-y-2", "model: claude-outside-9") for k, v in CLAUDE.items()}
        self.assertNotEqual(outside, CLAUDE)
        self.clean(self.swap_case(CLAUDE, outside, "check_agents_round3_swap_drift"))

    def test_swap_to_matching_external_folder_is_not_followed(self):
        inside = dict(CLAUDE, alpha=CLAUDE["alpha"].replace("model: claude-x-1", "model: claude-inside-7"))
        cp = self.swap_case(inside, CLAUDE, "check_agents_round3_swap_match")
        self.problems(cp, rf"{P}claude/agents/alpha\.md: model claude-inside-7 != claude-x-1")


# ---------------------------------------------------------------- security round 4 (spec section 20)

import stat  # noqa: E402

# Loads check_agents and hooks os.open: the first time the agent file `target` is opened through a
# folder descriptor (after the folder scan), the regular file is replaced by a FIFO. With "writer" a
# thread writes `content_file`'s bytes into the FIFO as soon as a reader opens it; "none" swaps nothing.
FIFO_DRIVER = r"""
import importlib.util, os, sys, threading
path, target, mode, content_file = sys.argv[1:5]
spec = importlib.util.spec_from_file_location("check_agents", path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
content = open(content_file, "rb").read()
real_open, state = os.open, {"swapped": False}


def write():
    try:
        with open(target, "wb") as w:
            w.write(content)
    except OSError:
        pass


def hook(p, flags, mode_=0o777, *, dir_fd=None):
    if mode != "none" and not state["swapped"] and dir_fd is not None and p == os.path.basename(target):
        state["swapped"] = True
        os.unlink(target)
        os.mkfifo(target)
        if mode == "writer":
            threading.Thread(target=write, daemon=True).start()
    return real_open(p, flags, mode_, dir_fd=dir_fd)


m.os.open = hook
code = m.main(sys.argv[5:])
if mode != "none" and not state["swapped"]:
    print("driver: the agent file was never opened through its folder descriptor", file=sys.stderr)
    code = 3
sys.stdout.flush()
sys.stderr.flush()
os._exit(code)  # a writer thread may still wait in open()
"""


@unittest.skipUnless(hasattr(os, "mkfifo"), "no FIFOs on this platform")
class TestSecurityRound4AC35AgentFileNonBlocking(Base):
    """AC35: every agent file is opened O_RDONLY|O_NOFOLLOW|O_NONBLOCK through the pinned folder fd and
    must be S_ISREG by fstat before it is read. A regular file that becomes a FIFO after the folder scan
    gives `<rel path>: not a regular file` (exit 1) and never hangs, with or without a writer."""

    def run_case(self, mode):
        self.assertTrue(SCRIPT.is_file(), f"script missing: {SCRIPT}")
        root = self.root()
        target = root / "claude" / "agents" / "alpha.md"
        content = self.tmp / "fifo-content"
        content.write_text(CLAUDE["alpha"])  # valid front matter that matches its row
        try:
            cp = subprocess.run([sys.executable, "-c", FIFO_DRIVER, str(SCRIPT), str(target), mode, str(content),
                                 "--root", str(root), "--models", str(self.models())],
                                cwd=str(self.cwd), env=self.env(), capture_output=True, text=True, errors="replace",
                                timeout=5)
        except subprocess.TimeoutExpired:
            cp = None
        if cp is None:
            self.fail(f"check_agents did not return within 5 s ({mode}): the agent file is opened blocking, "
                      "so a FIFO that replaced it after the folder scan hangs the check")
        self.assertNotEqual(cp.returncode, 3, cp.stderr)
        if mode != "none":
            self.assertTrue(stat.S_ISFIFO(os.lstat(target).st_mode), "fixture: alpha.md was not replaced by a FIFO")
        return cp

    def assert_not_regular(self, cp):
        self.problems(cp, rf"{P}claude/agents/alpha\.md: not a regular file",
                      extra=(rf"alpha: row in models\.md \(Claude\) has no file in {P}claude/agents/?",))

    def test_fifo_without_writer_does_not_hang(self):
        self.assert_not_regular(self.run_case("nowriter"))

    def test_fifo_with_writer_of_valid_front_matter_is_rejected(self):
        self.assert_not_regular(self.run_case("writer"))

    def test_regular_file_still_passes(self):
        self.clean(self.run_case("none"))


# ---------------------------------------------------------------- second security pass (spec section 21)

class TestSecondPassAC40CommandMarker(Base):
    """AC40 (F6): plain() prints `##[` as `## [`."""

    def test_plain_breaks_the_marker(self):
        from test_check_trailers import marker_cases
        marker_cases(self, load_check_agents(self, "check_agents_ac40").plain)

    def test_file_name_with_marker(self):
        r = self.root()
        (r / "claude" / "agents" / "##[add-mask]x.md").write_text(
            "---\nname: ##[add-mask]x\nmodel: claude-x-1\neffort: high\n---\n")
        cp = self.check(r)
        self.problems(cp, rf"{P}claude/agents/## \[add-mask\]x\.md: no row in models\.md")
        self.assertNotIn("##[", cp.stdout)


class TestSecondPassAC41UnexpectedFolders(Base):
    """AC41 (F7): a folder inside an agents folder, and a singular `agent` folder for OpenCode, are problems."""

    def fresh(self, **kw):
        shutil.rmtree(self.tmp / "root", ignore_errors=True)
        return self.root(**kw)

    def test_folder_inside_agents_folder(self):
        for prov in ("claude", "codex", "opencode"):
            with self.subTest(provider=prov):
                r = self.fresh()
                (r / prov / "agents" / "sub").mkdir()
                (r / prov / "agents" / "sub" / "deep.md").write_text("---\nname: deep\n---\n")
                self.problems(self.check(r), rf"{P}{prov}/agents/sub/?: unexpected folder",
                              extra=[rf"{P}{prov}/agents/sub/deep\.md.*"])

    def test_folder_with_agent_extension(self):
        r = self.root()
        (r / "claude" / "agents" / "delta.md").mkdir()
        self.problems(self.check(r), rf"{P}claude/agents/delta\.md/?: unexpected folder")

    def test_empty_folder_dot_layout(self):
        r = self.root(dot=True)
        (r / ".codex" / "agents" / "x").mkdir()
        self.problems(self.check(r), rf"{P}\.codex/agents/x/?: unexpected folder")

    def test_singular_opencode_folder_next_to_agents(self):
        for dot in (False, True):
            with self.subTest(dot=dot):
                r = self.fresh(dot=dot)
                d = r / (".opencode" if dot else "opencode") / "agent"
                d.mkdir()
                (d / "voice2.md").write_text("---\nmodel: openrouter/google/gem-4\n---\n")
                self.problems(self.check(r), rf"{P}{re.escape(d.parent.name)}/agent/?: unexpected folder \(use agents\)")

    def test_singular_opencode_folder_instead_of_agents(self):
        for dot in (False, True):
            with self.subTest(dot=dot):
                r = self.fresh(dot=dot, opencode=False)
                d = r / (".opencode" if dot else "opencode") / "agent"
                d.mkdir(parents=True)
                (d / "voice.md").write_text(OPENCODE["voice"])
                cp = self.check(r)
                self.problems(cp, rf"{P}{re.escape(d.parent.name)}/agent/?: unexpected folder \(use agents\)")
                self.assertNotIn("has no file", cp.stdout, "a provider without an agents folder is still skipped")

    def test_other_files_still_ignored(self):
        r = self.root()
        (r / "claude" / "agents" / "notes.txt").write_text("not an agent\n")
        (r / "opencode" / "README.md").write_text("next to agents, not a folder\n")
        self.clean(self.check(r))


class TestSecondPassAC42FrontMatterLines(Base):
    """AC42 (F8): a control character (below U+0020 other than the line end, or U+007F) in a
    front-matter line, a bare CR included, makes the file unparsable. An indented line is accepted
    only after a top-level key whose value is empty (a nested mapping such as `permission:`)."""

    CONTROLS = ["\x00", "\x01", "\x07", "\x08", "\t", "\x0b", "\x0c", "\r", "\x1b", "\x1c", "\x1f", "\x7f"]
    FILES = (("claude", "claude/agents/alpha.md", "description: Does: things, with colons.\n"),
             ("opencode", "opencode/agents/voice.md", "description: Third voice.\n"))

    def edit_line(self, r, rel, anchor, new):
        f = r / rel
        text = f.read_text()
        self.assertIn(anchor, text)
        f.write_bytes(text.replace(anchor, new, 1).encode())

    def unparsable(self, cp, rel):
        self.problems(cp, rf"{P}{re.escape(rel)}: unparsable \(.+\)")
        self.assertTrue(printable_only(cp.stdout), repr(cp.stdout))

    def fresh(self):
        shutil.rmtree(self.tmp / "root", ignore_errors=True)
        return self.root()

    def test_control_character_in_a_value(self):
        for c in self.CONTROLS:
            for prov, rel, anchor in self.FILES:
                with self.subTest(char=repr(c), provider=prov):
                    r = self.fresh()
                    self.edit_line(r, rel, anchor, anchor[:-4] + c + anchor[-4:])
                    self.unparsable(self.check(r), rel)

    def test_bare_cr_hides_a_key(self):
        # `description: x\rmodel: evil` shows as `model: evil` on a terminal.
        for prov, rel, anchor in self.FILES:
            with self.subTest(provider=prov):
                r = self.fresh()
                self.edit_line(r, rel, anchor, "description: x\rmodel: evil\n")
                self.unparsable(self.check(r), rel)

    def test_line_of_only_a_control_character(self):
        # \x0b, \x0c and \x1c to \x1f are whitespace to str.strip: such a line must not pass as blank.
        for c in ("\x0b", "\x0c", "\x1c", "\x1f", "\x00"):  # "\r\n" would be a CRLF line end
            for prov, rel, anchor in self.FILES:
                with self.subTest(char=repr(c), provider=prov):
                    r = self.fresh()
                    self.edit_line(r, rel, anchor, anchor + c + "\n")
                    self.unparsable(self.check(r), rel)

    def test_control_character_in_an_indented_line(self):
        r = self.root()
        self.edit_line(r, "opencode/agents/voice.md", "  edit: deny\n", "  edit: de\x1bny\n")
        self.unparsable(self.check(r), "opencode/agents/voice.md")

    def test_tab_indented_line_is_unparsable(self):
        r = self.root()
        self.edit_line(r, "opencode/agents/voice.md", "  edit: deny\n", "\tedit: deny\n")
        self.unparsable(self.check(r), "opencode/agents/voice.md")

    def test_control_character_in_the_body_passes(self):
        r = self.root()
        f = r / "claude" / "agents" / "alpha.md"
        f.write_text(f.read_text() + "Body with \x1b[31m and \t and \x7f.\n")
        self.clean(self.check(r))

    def test_indented_line_after_a_key_with_a_value(self):
        for prov, rel, anchor in self.FILES:
            with self.subTest(provider=prov):
                r = self.fresh()
                self.edit_line(r, rel, anchor, anchor + "  extra: y\n")
                self.unparsable(self.check(r), rel)

    def test_indented_line_hides_a_second_model(self):
        r = self.root()
        self.edit_line(r, "claude/agents/alpha.md", "model: claude-x-1\n", "model: claude-x-1\n  model: evil\n")
        self.unparsable(self.check(r), "claude/agents/alpha.md")

    def test_indented_first_line(self):
        for prov, rel, _ in self.FILES:
            with self.subTest(provider=prov):
                r = self.fresh()
                f = r / rel
                text = f.read_text()
                self.assertTrue(text.startswith("---\n"))
                f.write_text("---\n  stray: x\n" + text[4:])
                self.unparsable(self.check(r), rel)

    def test_indented_line_after_nested_block_and_key_with_value(self):
        r = self.root()
        self.edit_line(r, "opencode/agents/voice.md", "  bash: deny\n", "  bash: deny\ntemperature: 0\n  extra: y\n")
        self.unparsable(self.check(r), "opencode/agents/voice.md")

    def test_nested_mapping_after_an_empty_key_passes(self):
        r = self.root()
        self.edit_line(r, "claude/agents/alpha.md", "effort: high\n", "effort: high\nhooks:\n  pre: none\n  post: none\n")
        self.edit_line(r, "opencode/agents/voice.md", "  bash: deny\n", "  bash: deny\ntools:\n  read: true\n")
        self.clean(self.check(r))

    @unittest.skipUnless(REAL_STAGING.is_dir() and REAL_MODELS.is_file(), "staging/ or models.md not found")
    def test_real_staging_still_passes(self):
        n = sum(len(list((REAL_STAGING / p / "agents").glob(g)))
                for p, g in (("claude", "*.md"), ("codex", "*.toml"), ("opencode", "*.md")))
        self.clean(self.run_ca("--root", REAL_STAGING, "--models", REAL_MODELS), n=n)


class TestSecondPassAC44RowsAndDeepToml(Base):
    """AC44 (F12, F13): a second row for the same (provider, agent name) exits 2; a RecursionError
    while parsing a Codex TOML file is `unparsable (invalid TOML)`, exit 1, no traceback."""

    ROW_ALPHA = "| alpha | Alpha | Claude | claude-x-1 | high |\n"
    ROW_REV = "| rev | Reviewer | Codex | gpt-z-1 | medium |\n"

    def test_duplicate_row(self):
        variants = {
            "same row twice": MODELS.replace(self.ROW_ALPHA, self.ROW_ALPHA * 2),
            "other values": MODELS.replace(self.ROW_ALPHA, self.ROW_ALPHA + "| alpha | Alt | Claude | claude-q-1 | low |\n"),
            "in a comma cell": MODELS.replace("| beta, gamma |", "| beta, alpha |"),
            "twice in one cell": MODELS.replace("| beta, gamma |", "| beta, gamma, beta |"),
            "codex twice": MODELS.replace(self.ROW_REV, self.ROW_REV * 2),
        }
        for label, text in variants.items():
            with self.subTest(case=label):
                self.assertNotEqual(text, MODELS, "fixture")
                shutil.rmtree(self.tmp / "root", ignore_errors=True)
                self.usage_error(self.check(self.root(), self.models(text)), label)

    def test_same_name_for_two_providers_is_not_a_duplicate(self):
        text = MODELS.replace(self.ROW_REV, self.ROW_REV + "| alpha | Alpha | Codex | gpt-z-1 | low |\n")
        r = self.root()
        (r / "codex" / "agents" / "alpha.toml").write_text(
            'name = "alpha"\nmodel = "gpt-z-1"\nmodel_reasoning_effort = "low"\n')
        self.clean(self.check(r, self.models(text)), n=CLEAN_COUNT + 1)

    def test_deeply_nested_toml(self):
        bodies = {"arrays": "x = " + "[" * 30000 + "]" * 30000 + "\n",
                  "inline tables": "x = " + "{a=" * 12000 + "}" * 12000 + "\n"}
        for label, body in bodies.items():
            with self.subTest(case=label):
                shutil.rmtree(self.tmp / "root", ignore_errors=True)
                r = self.root()
                f = r / "codex" / "agents" / "rev.toml"
                f.write_text(CODEX["rev"] + body)
                self.assertLessEqual(f.stat().st_size, 64 * 1024, "fixture within the 64 KB file limit")
                cp = self.check(r)
                self.problems(cp, rf"{P}codex/agents/rev\.toml: unparsable \(invalid TOML\)")
                self.assertNotIn("Traceback", cp.stderr)
                self.assertNotIn("RecursionError", cp.stdout + cp.stderr)


# ---------------------------------------------------------------- second security pass, round 2 (spec section 22)

class TestSecondPassR2AC48SymlinksAnyName(Base):
    """AC48 (N5): a symlink inside an agents folder is `not a regular file` (exit 1) whatever its name
    or target; a symlinked folder named `sub` is no exception. It is never followed."""

    def external(self):
        ext = self.tmp / "external"
        ext.mkdir(exist_ok=True)
        (ext / "evil.md").write_text("---\nname: evil\nmodel: claude-evil-9\neffort: low\n---\n")
        return ext

    def fresh(self):
        shutil.rmtree(self.tmp / "root", ignore_errors=True)
        return self.root()

    def assert_link_reported(self, cp, rel):
        self.problems(cp, rf"{P}{re.escape(rel)}/?: not a regular file")
        self.assertNotIn("evil", cp.stdout, "the symlink was followed")

    def test_symlinked_folder_named_sub(self):
        for prov in ("claude", "codex", "opencode"):
            with self.subTest(provider=prov):
                r = self.fresh()
                (r / prov / "agents" / "sub").symlink_to(self.external(), target_is_directory=True)
                self.assert_link_reported(self.check(r), f"{prov}/agents/sub")

    def test_symlink_named_x_md_to_a_file(self):
        r = self.root()
        (r / "claude" / "agents" / "x.md").symlink_to(self.external() / "evil.md")
        self.assert_link_reported(self.check(r), "claude/agents/x.md")

    def test_symlink_to_a_missing_target(self):
        for name in ("gone", "gone.md"):
            with self.subTest(name=name):
                r = self.fresh()
                (r / "claude" / "agents" / name).symlink_to(self.tmp / "does-not-exist")
                self.assert_link_reported(self.check(r), f"claude/agents/{name}")

    def test_symlink_with_another_extension(self):
        r = self.root()
        (r / "claude" / "agents" / "notes.txt").symlink_to(self.external() / "evil.md")
        self.assert_link_reported(self.check(r), "claude/agents/notes.txt")


class TestSecondPassR2AC49TomlValueErrorAndUnicodeControls(Base):
    """AC49 (N6, N7): a Codex TOML file that makes the parser raise ValueError (an integer of 5,000
    digits) or RecursionError is `unparsable (invalid TOML)`, exit 1, no traceback. The control
    characters of AC42 also include U+0080 to U+009F, U+2028 and U+2029."""

    CONTROLS = ["\x80", "\x85", "\x9f", " ", " "]
    FILES = TestSecondPassAC42FrontMatterLines.FILES

    def fresh(self):
        shutil.rmtree(self.tmp / "root", ignore_errors=True)
        return self.root()

    def test_toml_value_error_and_recursion_error(self):
        bodies = {"5,000-digit integer": "x = " + "1" * 5000 + "\n",
                  "5,000-digit integer in a table": "[t]\ny = " + "9" * 5000 + "\n",
                  "deep arrays (RecursionError)": "x = " + "[" * 30000 + "]" * 30000 + "\n"}
        for label, body in bodies.items():
            with self.subTest(case=label):
                r = self.fresh()
                f = r / "codex" / "agents" / "rev.toml"
                f.write_text(CODEX["rev"] + body)
                self.assertLessEqual(f.stat().st_size, 64 * 1024, "fixture within the 64 KB file limit")
                cp = self.check(r)
                self.problems(cp, rf"{P}codex/agents/rev\.toml: unparsable \(invalid TOML\)")
                self.assertNotIn("Traceback", cp.stderr)
                self.assertNotIn("Error", cp.stdout + cp.stderr)

    def test_unicode_control_character_in_a_value(self):
        for c in self.CONTROLS:
            for prov, rel, anchor in self.FILES:
                with self.subTest(char=repr(c), provider=prov):
                    r = self.fresh()
                    f = r / rel
                    text = f.read_text()
                    self.assertIn(anchor, text)
                    f.write_bytes(text.replace(anchor, anchor[:-4] + c + anchor[-4:], 1).encode())
                    cp = self.check(r)
                    self.problems(cp, rf"{P}{re.escape(rel)}: unparsable \(.+\)")
                    self.assertTrue(printable_only(cp.stdout), repr(cp.stdout))

    def test_line_separator_hides_a_key(self):
        # `description: x<U+2028>model: evil` may show as two lines in an editor.
        for c in (" ", "\x85"):
            with self.subTest(char=repr(c)):
                r = self.fresh()
                f = r / "claude" / "agents" / "alpha.md"
                f.write_bytes(f.read_text().replace("description: Does: things, with colons.\n",
                                                    f"description: x{c}model: evil\n", 1).encode())
                self.problems(self.check(r), rf"{P}claude/agents/alpha\.md: unparsable \(.+\)")

    def test_unicode_control_character_in_the_body_passes(self):
        r = self.root()
        f = r / "claude" / "agents" / "alpha.md"
        f.write_bytes((f.read_text() + "Body \x85     \x9f.\n").encode())
        self.clean(self.check(r))

    @unittest.skipUnless(REAL_STAGING.is_dir() and REAL_MODELS.is_file(), "staging/ or models.md not found")
    def test_real_staging_still_passes(self):
        TestSecondPassAC42FrontMatterLines.test_real_staging_still_passes(self)


class TestSecondPassR2AC51ExtensionCase(Base):
    """AC51 (N9): an agent file whose extension is `.md` or `.toml` in any letter case is checked
    like the lowercase one."""

    def fresh(self):
        shutil.rmtree(self.tmp / "root", ignore_errors=True)
        return self.root()

    def rename(self, r, rel, new_name):
        # Remove first: on a case-insensitive file system alpha.md and alpha.MD are one entry.
        f = r / rel
        text = f.read_text()
        f.unlink()
        (f.parent / new_name).write_text(text)
        return f.parent / new_name

    def test_drifting_model_with_upper_case_extension(self):
        cases = (("claude/agents/alpha.md", "alpha.MD", "claude-x-1"),
                 ("claude/agents/alpha.md", "alpha.Md", "claude-x-1"),
                 ("opencode/agents/voice.md", "voice.MD", "openrouter/google/gem-4"),
                 ("codex/agents/rev.toml", "rev.TOML", "gpt-z-1"),
                 ("codex/agents/rev.toml", "rev.Toml", "gpt-z-1"))
        for rel, new_name, model in cases:
            with self.subTest(file=new_name):
                r = self.fresh()
                f = self.rename(r, rel, new_name)
                f.write_text(f.read_text().replace(model, "evil-model-1", 1))
                folder = rel.rsplit("/", 1)[0]
                self.problems(self.check(r), rf"{P}{re.escape(folder)}/{re.escape(new_name)}: model "
                                             rf"evil-model-1 != {re.escape(model)}")

    def test_upper_case_extension_matching_its_row_passes(self):
        r = self.root()
        self.rename(r, "claude/agents/alpha.md", "alpha.MD")
        self.rename(r, "codex/agents/rev.toml", "rev.TOML")
        # FU16 AC1: now exit 1, exactly the extension problem per file.
        self.problems(self.check(r), rf"{P}claude/agents/alpha\.MD: unexpected extension case",
                      rf"{P}codex/agents/rev\.TOML: unexpected extension case")

    def test_fu16_ac1_each_upper_case_variant_reports_only_the_extension_problem(self):
        # FU16 AC1
        for rel, new_name in (("claude/agents/alpha.md", "alpha.MD"), ("claude/agents/alpha.md", "alpha.Md"),
                              ("opencode/agents/voice.md", "voice.MD"), ("codex/agents/rev.toml", "rev.TOML"),
                              ("codex/agents/rev.toml", "rev.Toml")):
            with self.subTest(file=new_name):
                r = self.fresh()
                self.rename(r, rel, new_name)
                folder = rel.rsplit("/", 1)[0]
                lines = self.problems(self.check(r), rf"{P}{re.escape(folder)}/{re.escape(new_name)}: "
                                                     r"unexpected extension case")
                self.assertEqual(len(lines), 1, lines)

    def test_fu16_ac2_drift_and_unknown_file_also_report_the_extension_problem(self):
        # FU16 AC2
        for rel, new_name, model in (("claude/agents/alpha.md", "alpha.MD", "claude-x-1"),
                                     ("opencode/agents/voice.md", "voice.MD", "openrouter/google/gem-4"),
                                     ("codex/agents/rev.toml", "rev.TOML", "gpt-z-1"),
                                     ("codex/agents/rev.toml", "rev.Toml", "gpt-z-1")):
            with self.subTest(file=new_name):
                r = self.fresh()
                f = self.rename(r, rel, new_name)
                f.write_text(f.read_text().replace(model, "evil-model-1", 1))
                folder = re.escape(rel.rsplit("/", 1)[0])
                self.problems(self.check(r), rf"{P}{folder}/{re.escape(new_name)}: unexpected extension case",
                              rf"{P}{folder}/{re.escape(new_name)}: model evil-model-1 != {re.escape(model)}")
        r = self.fresh()
        (r / "claude/agents/EVIL.MD").write_text("---\nname: EVIL\nmodel: claude-evil-9\neffort: low\n---\n")
        self.problems(self.check(r), rf"{P}claude/agents/EVIL\.MD: unexpected extension case",
                      rf"{P}claude/agents/EVIL\.MD: no row in models\.md")

    def test_new_upper_case_file_is_checked(self):
        for rel, body in (("claude/agents/EVIL.MD", "---\nname: EVIL\nmodel: claude-evil-9\neffort: low\n---\n"),
                          ("codex/agents/X.TOML", 'name = "X"\nmodel = "gpt-evil-9"\nmodel_reasoning_effort = "low"\n')):
            with self.subTest(file=rel):
                r = self.fresh()
                (r / rel).write_text(body)
                cp = self.check(r)
                self.assertEqual(cp.returncode, 1, f"stdout={cp.stdout!r} stderr={cp.stderr!r}")
                self.assertTrue(any(re.fullmatch(rf"{P}{re.escape(rel)}: .+", l) for l in cp.stdout.splitlines()),
                                f"{rel} not checked: {cp.stdout!r}")
                self.assertNotIn("files match models.md", cp.stdout)


if __name__ == "__main__":
    unittest.main()
