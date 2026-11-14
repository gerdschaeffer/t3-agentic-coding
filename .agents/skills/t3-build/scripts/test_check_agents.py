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
        (d / "sub").mkdir()
        (d / "sub" / "deep.md").write_text("---\nname: deep\n---\n")
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
        cp = self.run_ca("--root", "../../../../staging", "--models", "../references/models.md", cwd=SCRIPTS)
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


if __name__ == "__main__":
    unittest.main()
