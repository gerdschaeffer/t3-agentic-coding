"""Static and executable checks of the CI workflow templates (spec 015-wp9-ci, AC16 to AC19,
sections 7 and 8).

Run from this folder:  python3 -m unittest test_ci_templates -v

Stdlib only, no PyYAML. The templates use the restricted YAML subset of section 7 (block style,
2-space indent, no tabs, anchors, aliases or flow mappings); `parse` accepts only that subset and
builds plain dicts, lists and strings from it, so the checks below work on structure, not on
loose text. Each check is a function `check_*(text, name) -> [problem]`. The real templates in
this folder must give no problem; every check also has mutation tests that insert a violation
into a copy of a real template and assert the check reports it.

The guard entry steps (AC19) and two ci.yml jobs (AC17) are also run: their `run:` text is taken
from the template and executed with `bash --noprofile --norc -eo pipefail` (GitHub's default for
`run:`), step by step, against temp git repos, with `${{ ... }}` in `env:` replaced by fixture
values. Only the expressions the spec allows are known to the emulator; step-level `if:`, another
`shell:` or another expression fails the check as unsupported.

This file is not copied to a product repo (section 8).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from urllib.parse import unquote

sys.dont_write_bytecode = True

CI = Path(__file__).resolve().parent
SKILL = CI.parent
README = CI / "README.md"
TRAILERS_DOC = SKILL / "references" / "commit-trailers.md"
TEMPLATES = ("ci", "diff-guard", "commit-trailers", "agents-drift")
GUARDS = {"diff-guard": "diff_guard.py", "commit-trailers": "check_trailers.py", "agents-drift": "check_agents.py"}
TARGETS = ["format", "lint", "types", "test", "secrets", "deps", "sast", "licence"]
ALLOWLIST = {"actions/checkout@v4"}
REQUIRED_CHECKS = ["ci", "diff-guard", "commit-trailers", "agents-drift", "risk-gate"]
NOTICE = "::notice::guards not installed on the base commit"
BASE_EXPR = "github.event.pull_request.base.sha"
HEAD_EXPR = "github.event.pull_request.head.sha"
MODELS_ARG = ".agents/skills/t3-build/references/models.md"
REAL_GIT = shutil.which("git")
BASH = shutil.which("bash")
MAKE = shutil.which("make")
MISSING_SHA = "0123456789abcdef0123456789abcdef01234567"
SHA40 = "a" * 40


# ================================================================ restricted YAML subset

class SubsetError(Exception):
    pass


Item = namedtuple("Item", "ln indent dash key kind value")
KEY = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*):(?: (.*))?")


def _scalar(s, ln):
    """One value on one line -> (kind, value); kind is empty, block, scalar or seq."""
    s = s.strip()
    if not s or s.startswith("#"):
        return "empty", None
    if s[0] in "|>":
        m = re.fullmatch(r"([|>][-+]?)(\s+#.*)?", s)
        if not m:
            raise SubsetError(f"line {ln}: bad block scalar header {s!r}")
        return "block", m.group(1)
    if s[0] in "\"'":
        m = re.match(r'"((?:[^"\\]|\\.)*)"' if s[0] == '"' else r"'((?:[^']|'')*)'", s)
        if not m:
            raise SubsetError(f"line {ln}: unbalanced or multi-line quoted scalar")
        rest = s[m.end():]
        if rest.strip() and not re.fullmatch(r"\s+#.*", rest):
            raise SubsetError(f"line {ln}: text after a quoted scalar")
        v = m.group(1)
        return "scalar", (re.sub(r"\\(.)", r"\1", v) if s[0] == '"' else v.replace("''", "'"))
    if s[0] == "[":
        m = re.match(r"\[([^\[\]{}\"']*)\]", s)
        if not m or (s[m.end():].strip() and not re.fullmatch(r"\s+#.*", s[m.end():])):
            raise SubsetError(f"line {ln}: flow sequence must be simple and on one line")
        return "seq", [_scalar(x, ln)[1] for x in m.group(1).split(",") if x.strip()]
    if s[0] in "{&*!%@`":
        raise SubsetError(f"line {ln}: flow mapping, anchor, alias, tag or reserved indicator {s[0]!r}")
    v = re.split(r"\s#", s, maxsplit=1)[0].rstrip()
    if ": " in v or v.endswith(":"):
        raise SubsetError(f"line {ln}: ': ' inside a plain scalar")
    return "scalar", v


def _fold(text):
    return re.sub(r"(?<=\S)\n(?=\S)", " ", text)


def _take_block(raw, i, key_indent, header, ln):
    body = []
    while i < len(raw):
        line = raw[i]
        if line.strip() and len(line) - len(line.lstrip(" ")) <= key_indent:
            break
        body.append(line if line.strip() else "")
        i += 1
    while body and body[-1] == "":
        body.pop()
    if not body:
        return "", i
    n = len(body[0]) - len(body[0].lstrip(" "))
    out = []
    for line in body:
        if line and len(line) - len(line.lstrip(" ")) < n:
            raise SubsetError(f"line {ln}: block scalar line less indented than its first line")
        out.append(line[n:])
    text = "\n".join(out) + "\n"
    return (_fold(text) if header[0] == ">" else text), i


def scan(text):
    """Lines of the subset -> (items, code). `code` is the text without YAML comments (block
    scalar contents kept verbatim), for token searches."""
    if "\t" in text:
        raise SubsetError("tab character")
    raw = text.split("\n")
    items, code, i = [], [], 0
    while i < len(raw):
        line, ln = raw[i], i + 1
        i += 1
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s in ("---", "..."):
            raise SubsetError(f"line {ln}: document marker")
        indent = len(line) - len(line.lstrip(" "))
        if indent % 2:
            raise SubsetError(f"line {ln}: indent {indent} is not a multiple of 2")
        body, dash = line[indent:].rstrip(), False
        if body == "-" or body.startswith("- "):
            dash, body = True, body[2:]
            if body.startswith(" ") or not body:
                raise SubsetError(f"line {ln}: list item must be '- ' and a value")
            if body == "-" or body.startswith("- "):
                raise SubsetError(f"line {ln}: nested '- - '")
        m = KEY.fullmatch(body)
        prefix = " " * indent + ("- " if dash else "")
        if m:
            kind, value = _scalar(m.group(2) or "", ln)
            if kind == "block":
                value, i = _take_block(raw, i, indent + (2 if dash else 0), value, ln)
                kind = "scalar"
                code.append(f"{prefix}{m.group(1)}: |")
                code.extend("  " + l for l in value.split("\n"))
            else:
                shown = ", ".join(map(str, value)) if kind == "seq" else (value or "")
                code.append(f"{prefix}{m.group(1)}: {shown}")
            items.append(Item(ln, indent, dash, m.group(1), kind, value))
        elif dash:
            kind, value = _scalar(body, ln)
            if kind == "block":
                raise SubsetError(f"line {ln}: block scalar as a bare list item")
            items.append(Item(ln, indent, True, None, kind, value))
            code.append(f"{prefix}{value if kind != 'seq' else ', '.join(value)}")
        else:
            raise SubsetError(f"line {ln}: not a 'key: value' or '- item' line: {body[:40]!r}")
    return items, "\n".join(code) + "\n"


def parse(text):
    """The subset -> nested dict/list/str (None for an empty value). Raises SubsetError."""
    items, _ = scan(text)
    pos = 0

    def value(it, key_indent):
        nonlocal pos
        if it.kind != "empty":
            return it.value
        if pos < len(items):
            nxt = items[pos]
            if nxt.indent > key_indent:
                return seq(nxt.indent) if nxt.dash else mapping(nxt.indent)
            if nxt.dash and nxt.indent == key_indent:
                return seq(key_indent)
        return None

    def mapping(indent):
        nonlocal pos
        d = {}
        while pos < len(items):
            it = items[pos]
            if it.indent < indent or (it.dash and it.indent == indent):
                break
            if it.indent > indent or it.dash or it.key is None:
                raise SubsetError(f"line {it.ln}: unexpected indentation")
            pos += 1
            if it.key in d:
                raise SubsetError(f"line {it.ln}: duplicate key {it.key!r}")
            d[it.key] = value(it, indent)
        return d

    def seq(indent):
        nonlocal pos
        out = []
        while pos < len(items) and items[pos].dash and items[pos].indent == indent:
            it = items[pos]
            pos += 1
            if it.key is None:
                out.append(it.value)
                continue
            d = {it.key: value(it, indent + 2)}
            for k, v in mapping(indent + 2).items():
                if k in d:
                    raise SubsetError(f"line {it.ln}: duplicate key {k!r}")
                d[k] = v
            out.append(d)
        return out

    doc = mapping(0)
    if pos != len(items):
        raise SubsetError(f"line {items[pos].ln}: unexpected indentation or list")
    return doc


def code_text(text):
    try:
        return scan(text)[1]
    except SubsetError:
        return text


def parsed(text):
    try:
        return parse(text), []
    except SubsetError as e:
        return None, [f"not in the YAML subset: {e}"]


def walk(node, path=()):
    yield path, node
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, path + (i,))


def jobs_of(doc):
    j = doc.get("jobs") if isinstance(doc, dict) else None
    return j if isinstance(j, dict) else {}


def steps_of(job):
    s = job.get("steps") if isinstance(job, dict) else None
    return [x for x in s if isinstance(x, dict)] if isinstance(s, list) else []


def run_texts(doc):
    return [v for p, v in walk(doc) if p and p[-1] == "run" and isinstance(v, str)]


def norm_expr(s):
    return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", lambda m: "${{ " + m.group(1) + " }}", s) if isinstance(s, str) else s


# ================================================================ static checks (AC16, AC17, AC19)

def check_subset(text, name):
    return parsed(text)[1]


def check_trigger(text, name):
    doc, p = parsed(text)
    if p:
        return p
    on = doc.get("on")
    events = set(on) if isinstance(on, (dict, list)) else {on} if isinstance(on, str) else set()
    if events != {"pull_request"}:
        p.append(f"on: {sorted(map(str, events))} is not exactly pull_request")
    code = code_text(text)
    for bad in ("pull_request_target", "workflow_run", "workflow_dispatch", "workflow_call",
                "repository_dispatch", "inputs"):
        if re.search(rf"(?<![\w-]){bad}(?![\w-])", code):
            p.append(f"{bad} named")
    return p


def check_permissions(text, name):
    doc, p = parsed(text)
    if p:
        return p
    if doc.get("permissions") != {"contents": "read"}:
        p.append(f"top-level permissions is {doc.get('permissions')!r}, not exactly contents: read")
    for jid, job in jobs_of(doc).items():
        if isinstance(job, dict) and "permissions" in job:
            p.append(f"job {jid} sets permissions")
    return p


def check_secrets(text, name):
    """No `secrets.` anywhere (comments included), in any spelling GitHub accepts."""
    p = []
    if re.search(r"(?i)\bsecrets\s*[.\[)}]", text):
        p.append("secrets context used")
    if re.search(r"(?im)^\s*(?:- )?secrets\s*:", text):
        p.append("secrets: key (inherit or pass-through)")
    if re.search(r"(?i)\bgithub\s*\.\s*token\b|\bgithub\s*\[\s*'token'\s*\]", text):
        p.append("github.token handed to a step")
    return p


def check_run_expr(text, name):
    doc, p = parsed(text)
    if p:
        return p
    for run in run_texts(doc):
        if "${{" in run:
            p.append(f"${{{{ inside run: {run.strip()[:60]!r}")
    return p


def check_fallbacks(text, name):
    code = code_text(text)
    rules = [(r"continue-on-error", "continue-on-error"), (r"\|\|\s*(?:true\b|:(?=\s|$|\)|;))", "|| true"),
             (r"\|\|\s*exit\s+0\b", "|| exit 0"), (r"\bset\s+\+[A-Za-z]*e", "set +e"),
             (r"\bset\s+\+o\s+errexit\b", "set +o errexit")]
    return [f"fallback {label}" for pat, label in rules if re.search(pat, code)]


def check_concurrency(text, name):
    doc, p = parsed(text)
    if p:
        return p
    wf = doc.get("name")
    if not isinstance(wf, str) or not wf:
        return ["no workflow name"]
    c = doc.get("concurrency")
    want = {f"{wf}-${{{{ github.event.pull_request.number }}}}",
            "${{ github.workflow }}-${{ github.event.pull_request.number }}"}
    if not isinstance(c, dict):
        return ["no top-level concurrency mapping"]
    if norm_expr(c.get("group")) not in want:
        p.append(f"concurrency group {c.get('group')!r} is not {wf}-${{{{ github.event.pull_request.number }}}}")
    if c.get("cancel-in-progress") != "true":
        p.append("cancel-in-progress is not true")
    return p


def check_jobs(text, name):
    doc, p = parsed(text)
    if p:
        return p
    js = jobs_of(doc)
    if not js:
        return ["no jobs"]
    for jid, job in js.items():
        if not isinstance(job, dict):
            p.append(f"job {jid} is not a mapping")
            continue
        t = job.get("timeout-minutes")
        if not (isinstance(t, str) and re.fullmatch(r"[1-9][0-9]*", t) and int(t) <= 30):
            p.append(f"job {jid}: timeout-minutes {t!r} is not 1 to 30")
        if job.get("runs-on") != "ubuntu-latest":
            p.append(f"job {jid}: runs-on {job.get('runs-on')!r} is not ubuntu-latest")
    return p


PINNED = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_./-]+)?@[0-9a-f]{40}")


def check_uses(text, name):
    doc, p = parsed(text)
    if p:
        return p
    found = [v for path, v in walk(doc) if path and path[-1] == "uses"]
    found += [m.group(1).strip().strip("'\"") for m in re.finditer(r"(?m)^\s*(?:- )?uses:\s*(\S+)", code_text(text))]
    for v in found:
        if not isinstance(v, str):
            p.append(f"uses: {v!r} is not a string")
            continue
        if v not in ALLOWLIST and not PINNED.fullmatch(v):
            p.append(f"uses: {v} is neither pinned to a 40-hex SHA nor on the allowlist")
        if re.search(r"(?:^|/)setup-|(?:^|/)cache(?:@|/)", v.lower()):
            p.append(f"uses: {v} is a setup or cache action")
    return p


def checkouts(doc):
    return [(jid, s) for jid, job in jobs_of(doc).items() for s in steps_of(job)
            if isinstance(s.get("uses"), str) and s["uses"].startswith("actions/checkout@")]


def check_checkout(text, name):
    doc, p = parsed(text)
    if p:
        return p
    for jid, s in checkouts(doc):
        w = s.get("with")
        if not isinstance(w, dict) or w.get("persist-credentials") != "false":
            p.append(f"job {jid}: checkout without persist-credentials: false")
    return p


def check_fetch_depth(text, name):
    doc, p = parsed(text)
    if p:
        return p
    cos = checkouts(doc)
    if name in GUARDS:
        if not cos:
            p.append("guard workflow has no checkout")
        for jid, s in cos:
            w = s.get("with") if isinstance(s.get("with"), dict) else {}
            if w.get("fetch-depth") != "0":
                p.append(f"job {jid}: checkout fetch-depth {w.get('fetch-depth')!r} is not 0")
    else:
        for jid, s in cos:
            if isinstance(s.get("with"), dict) and "fetch-depth" in s["with"]:
                p.append(f"job {jid}: ci.yml checkout sets fetch-depth; the default depth is required")
    return p


def _v(var):
    return '"\\$(?:' + var + r'|\{' + var + r'\})'


# AC39: `BASE_SHA="$(git rev-parse [options] HEAD^1)"` (quotes around HEAD^1 and a ^{commit} peel allowed).
DERIVE_BASE = re.compile(r"""BASE_SHA="?\$\(\s*git rev-parse(?: +-[-\w]+)* +["']?HEAD\^1(?:\^\{commit\})?["']?\s*\)"?""")
MERGE_MSG = "checkout is not the merge of the pull request head"
TRIGGER_TYPES = ["opened", "synchronize", "reopened", "edited"]


def check_guard(text, name):
    """AC19 static part: BASE_SHA/HEAD_SHA (and BRANCH) only from the PR event, verify both
    commits, ls-tree with the notice branch, copy from the base commit to $RUNNER_TEMP, run that
    copy; nothing runs .github/ci/<script> from the checkout."""
    doc, p = parsed(text)
    if p:
        return p
    script = GUARDS[name]
    if name not in jobs_of(doc):
        p.append(f"no job named {name} (the required check name)")
    # AC39 supersedes: BASE_SHA is no longer an env: value from the event; the run step derives it from HEAD^1.
    want = {"HEAD_SHA": f"${{{{ {HEAD_EXPR} }}}}", "BRANCH": "${{ github.head_ref }}"}
    defs = {k: [] for k in want}
    for path, node in walk(doc):
        if path and path[-1] == "env" and isinstance(node, dict):
            if "BASE_SHA" in node:
                p.append(f"BASE_SHA is set in env: ({node['BASE_SHA']!r}); AC39 derives it from HEAD^1")
            for k in want:
                if k in node:
                    defs[k].append(norm_expr(node[k]))
    for k in ["HEAD_SHA"] + (["BRANCH"] if name == "commit-trailers" else []):
        if not defs[k]:
            p.append(f"{k} is not set in env:")
    for k, vals in defs.items():
        for v in vals:
            if v != want[k]:
                p.append(f"{k} comes from {v!r}, not {want[k]}")
    runs = "\n".join(run_texts(doc))
    if re.search(r"GITHUB_ENV|GITHUB_PATH", runs):
        p.append("a run step writes GITHUB_ENV or GITHUB_PATH")
    if re.search(r"\b(?:HEAD_SHA|BRANCH|RUNNER_TEMP)\s*=", runs):
        p.append("a run step assigns HEAD_SHA, BRANCH or RUNNER_TEMP")
    for m in re.finditer(r"\bBASE_SHA\s*=", runs):  # AC39: the only assignment is the HEAD^1 derivation
        if not DERIVE_BASE.match(runs, m.start()):
            p.append(f"a run step assigns BASE_SHA other than from git rev-parse HEAD^1: {runs[m.start():m.start() + 50]!r}")
    for m in re.finditer(r"\.github/ci/", runs):
        if not re.search(r"(?::|--\s+[\"']?)$", runs[max(0, m.start() - 4):m.start()]):
            p.append(f"run step uses the checked-out {runs[m.start():m.start() + 40].split()[0]} directly")
    s = re.escape(script)
    pats = {"verify base": "git cat-file -e " + _v("BASE_SHA") + r'\^\{commit\}"',
            "verify head": "git cat-file -e " + _v("HEAD_SHA") + r'\^\{commit\}"',
            "ls-tree": "git ls-tree --name-only " + _v("BASE_SHA") + r'" -- ["\']?\.github/ci/' + s + r"(?![\w.])",
            "notice": re.escape(NOTICE),
            "copy": "git show " + _v("BASE_SHA") + r":\.github/ci/" + s + '" > ' + _v("RUNNER_TEMP") + "/" + s + '"',
            "run copy": "python3 " + _v("RUNNER_TEMP") + "/" + s + '"'}
    at = {}
    for label, pat in pats.items():
        m = re.search(pat, runs)
        if not m:
            p.append(f"guard entry step missing: {label}")
        else:
            at[label] = m.start()
    for a, b in (("verify base", "ls-tree"), ("verify head", "ls-tree"), ("ls-tree", "notice"),
                 ("ls-tree", "copy"), ("copy", "run copy")):
        if a in at and b in at and at[a] > at[b]:
            p.append(f"{a} comes after {b}")
    return p


def check_ci(text, name):
    """AC17 static part of ci.yml."""
    doc, p = parsed(text)
    if p:
        return p
    js = jobs_of(doc)
    for j in ("check", "guard-tests", "ci"):
        if not isinstance(js.get(j), dict):
            p.append(f"no job {j}")
    if p:
        return p
    strat = js["check"].get("strategy")
    matrix = strat.get("matrix") if isinstance(strat, dict) else None
    targets = matrix.get("target") if isinstance(matrix, dict) else None
    if not isinstance(matrix, dict) or set(matrix) != {"target"}:
        p.append(f"matrix keys {sorted(matrix) if isinstance(matrix, dict) else matrix!r} are not exactly [target]")
    if not isinstance(targets, list) or sorted(map(str, targets)) != sorted(TARGETS) or len(targets) != 8:
        p.append(f"matrix target {targets!r} is not exactly {TARGETS}")
    if not isinstance(strat, dict) or strat.get("fail-fast") != "false":
        p.append("check: fail-fast is not false")
    check_steps = steps_of(js["check"])
    make = [s for s in check_steps if isinstance(s.get("run"), str)
            and re.fullmatch(r'make "\$(?:TARGET|\{TARGET\})"', s["run"].strip())]
    if not make:
        p.append('check: no step make "$TARGET"')
    target_defs = [norm_expr(e.get("TARGET")) for e in [js["check"].get("env")] + [s.get("env") for s in make]
                   if isinstance(e, dict) and "TARGET" in e]
    if target_defs != ["${{ matrix.target }}"]:
        p.append(f"check: TARGET env {target_defs!r} is not ${{{{ matrix.target }}}}")
    gt = "\n".join(run_texts(js["guard-tests"]))
    for d in ("risk-gate", "ci"):
        if not re.search(r'(?m)^\s*python3 -m unittest discover -s ["\']?\.github/' + d + r'["\']?(?:\s+-\S+(?:\s+\S+)?)*\s*$', gt):
            p.append(f"guard-tests: no python3 -m unittest discover -s .github/{d}")
    needs = js["ci"].get("needs")
    if not isinstance(needs, list) or sorted(needs) != ["check", "guard-tests"]:
        p.append(f"ci: needs {needs!r} is not [check, guard-tests]")
    if norm_expr(js["ci"].get("if")) not in ("always()", "${{ always() }}"):
        p.append(f"ci: if {js['ci'].get('if')!r} is not always()")
    if not run_texts(js["ci"]):
        p.append("ci: no step checks the results")
    return p


# ================================================================ step emulator (AC17, AC19)

class Unsupported(Exception):
    pass


def expand(value, ctx):
    def sub(m):
        key = re.sub(r"\[\s*'([\w-]+)'\s*\]", r".\1", m.group(1).strip())
        if key not in ctx:
            raise Unsupported(f"expression {m.group(0)!r} is not known to the step emulator")
        return ctx[key]
    return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", sub, value)


def run_job(doc, job_id, ctx, cwd, env0, scratch):
    """Run the run: steps of one job in order; stop at the first failure -> (ok, output)."""
    job = jobs_of(doc).get(job_id)
    if not isinstance(job, dict):
        raise Unsupported(f"no job {job_id}")
    shells = [((x.get("defaults") or {}).get("run") or {}).get("shell") if isinstance(x.get("defaults"), dict)
              and isinstance(x["defaults"].get("run"), dict) else None for x in (doc, job)]
    out = []
    for n, step in enumerate(steps_of(job)):
        if "uses" in step:
            continue
        if not isinstance(step.get("run"), str):
            raise Unsupported(f"{job_id}: step {n} has neither uses nor run")
        if "if" in step:
            raise Unsupported(f"{job_id}: step-level if: (section 7: the branches are shell if/else)")
        shell = step.get("shell") or shells[1] or shells[0]
        if shell not in (None, "bash"):
            raise Unsupported(f"{job_id}: shell {shell!r}")
        env = dict(env0)
        for layer in (doc.get("env"), job.get("env"), step.get("env")):
            if layer is None:
                continue
            if not isinstance(layer, dict):
                raise Unsupported(f"{job_id}: env is not a mapping")
            for k, v in layer.items():
                env[k] = expand("" if v is None else str(v), ctx)
        wd = Path(cwd) / step.get("working-directory", "")
        script = Path(scratch) / f"{job_id}-step-{n}.sh"
        script.write_text(step["run"])
        cp = subprocess.run([BASH, "--noprofile", "--norc", "-eo", "pipefail", str(script)], cwd=str(wd), env=env,
                            capture_output=True, text=True, errors="replace", timeout=120)
        out.append(cp.stdout + cp.stderr)
        if cp.returncode:
            return False, "".join(out)
    return True, "".join(out)


BASE_STUB = """import json, os, sys
with open(os.environ["STUB_LOG"], "w") as f:
    json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(), "file": os.path.abspath(__file__)}, f)
sys.exit(int(os.environ.get("STUB_EXIT", "0")))
"""
PR_STUB = """import os
open(os.environ["STUB_PR_MARKER"], "w").write("the PR copy ran")
"""
GUARD_CASES = ("present", "present-fail", "absent", "missing-base", "missing-head", "ls-tree-error", "show-error")


def git_env(root):
    e = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    e.update(HOME=str(root), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_CEILING_DIRECTORIES=str(root),
             GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid", GIT_COMMITTER_NAME="Fixture",
             GIT_COMMITTER_EMAIL="fixture@example.invalid", GIT_AUTHOR_DATE="2026-01-05T12:00:00+0000",
             GIT_COMMITTER_DATE="2026-01-05T12:00:00+0000", PYTHONDONTWRITEBYTECODE="1")
    return e


def pr_repo(root, script, on_base):
    """Work tree of the PR as actions/checkout gives it on pull_request (AC39): the merge commit is
    checked out, HEAD^1 = the base tip, HEAD^2 = the PR head. `old` is an older base commit without
    the guard script (a stale event base); the branch point after it has the script (unless not
    on_base), the PR replaces it with a copy that must never run, and the base moved on after the
    branch point. -> (ws, base tip, PR head, old)."""
    ws = root / "workspace"
    ws.mkdir(parents=True)
    env = git_env(root)

    def git(*a):
        cp = subprocess.run([REAL_GIT, "-C", str(ws), *a], env=env, capture_output=True, text=True)
        if cp.returncode:
            raise AssertionError(f"fixture git {a}: {cp.stderr}")
        return cp.stdout.strip()

    git("init", "-q")
    git("symbolic-ref", "HEAD", "refs/heads/main")
    (ws / "README.md").write_text("old\n")
    git("add", "-A")
    git("commit", "-q", "-m", "old base, guard not installed")
    old = git("rev-parse", "HEAD")
    (ws / "README.md").write_text("base\n")
    (ws / ".github" / "ci").mkdir(parents=True)
    if on_base:
        (ws / ".github" / "ci" / script).write_text(BASE_STUB)
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    git("checkout", "-q", "-b", "pr")
    (ws / "README.md").write_text("pr\n")
    (ws / ".github" / "ci" / script).write_text(PR_STUB)
    git("add", "-A")
    git("commit", "-q", "-m", "pr")
    head = git("rev-parse", "HEAD")
    git("checkout", "-q", "main")
    (ws / "NEWS").write_text("the base moved on\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base moves on")
    base = git("rev-parse", "HEAD")
    git("checkout", "-q", "--detach", base)
    git("merge", "-q", "--no-ff", "--no-edit", "pr")
    if git("rev-parse", "HEAD^1") != base or git("rev-parse", "HEAD^2") != head:
        raise AssertionError("fixture: the checkout is not the merge of the PR head into the base tip")
    git("update-ref", "refs/remotes/origin/main", base)  # AC46: the target branch as the fetch-depth 0 checkout has it
    return ws, base, head, old


def runner_env(root, ws, extra_bin=None):
    bin_ = root / "bin"
    bin_.mkdir(exist_ok=True)
    py = bin_ / "python3"
    if not py.exists():
        py.symlink_to(sys.executable)
    temp = root / "runner-temp"
    temp.mkdir(exist_ok=True)
    for f in ("output", "env", "summary"):
        (root / f).touch()
    e = git_env(root)
    e.pop("GIT_AUTHOR_DATE"), e.pop("GIT_COMMITTER_DATE")
    e.update(PATH=f"{bin_}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}", RUNNER_TEMP=str(temp),
             GITHUB_WORKSPACE=str(ws), GITHUB_EVENT_NAME="pull_request", GITHUB_HEAD_REF="agent/x",
             GITHUB_BASE_REF="main", GITHUB_ACTIONS="true", CI="true", GITHUB_OUTPUT=str(root / "output"),
             GITHUB_ENV=str(root / "env"), GITHUB_STEP_SUMMARY=str(root / "summary"))
    return e, bin_, temp


def want_args(name, base, head):
    return {"diff-guard": ["--base", base, "--head", head],
            "commit-trailers": ["--branch", "agent/x", "--base", base, "--head", head],
            "agents-drift": ["--root", ".", "--models", MODELS_ARG]}[name]


def guard_case(doc, job_id, name, case, root):
    script = GUARDS[name]
    ws, base, head, old = pr_repo(root, script, on_base=case != "absent")
    if case == "not-merge":  # AC39: the PR head itself is checked out, not the merge commit
        subprocess.run([REAL_GIT, "-C", str(ws), "checkout", "-q", "--detach", head], env=git_env(root), check=True)
    if case == "missing-base":  # AC39: the base is HEAD^1 now, so its object is removed from the checkout
        (ws / ".git" / "objects" / base[:2] / base[2:]).unlink()
    env, bin_, temp = runner_env(root, ws)
    if case in ("ls-tree-error", "show-error"):
        sub = case.split("-error")[0]
        g = bin_ / "git"
        g.write_text(f'#!/bin/sh\nfor a in "$@"; do\n  if [ "$a" = "{sub}" ]; then echo "fatal: injected" >&2; exit 128; fi\n'
                     f'done\nexec "{REAL_GIT}" "$@"\n')
        g.chmod(0o755)
    log, marker = root / "stub.json", root / "pr-ran"
    env.update(STUB_LOG=str(log), STUB_PR_MARKER=str(marker), STUB_EXIT="1" if case == "present-fail" else "0")
    # AC39: the event's base sha is stale in "stale-base" (the branch point) and must be ignored.
    ctx = {BASE_EXPR: old if case == "stale-base" else base,
           HEAD_EXPR: {"missing-head": MISSING_SHA, "head-mismatch": old}.get(case, head), "github.head_ref": "agent/x",
           "github.base_ref": "main"}  # AC46: BASE_REF in the guard step's env:
    ok, out = run_job(doc, job_id, ctx, ws, env, root)
    label = f"{name}/{job_id} [{case}]"
    p = []
    if marker.exists():
        p.append(f"{label}: the PR's own .github/ci/{script} ran")
    notice = any(l.strip() == NOTICE for l in out.splitlines())
    if case in ("not-merge", "head-mismatch") and MERGE_MSG not in out:
        p.append(f"{label}: no {MERGE_MSG!r} in the output: {out[-300:]!r}")
    if case in ("present", "stale-base"):
        if not ok:
            p.append(f"{label}: job failed although the base script passed: {out[-300:]!r}")
        if not log.exists():
            p.append(f"{label}: the base commit's script did not run")
        else:
            rec = json.loads(log.read_text())
            if rec["argv"] != want_args(name, base, head):
                p.append(f"{label}: script arguments {rec['argv']} != {want_args(name, base, head)}")
            if Path(rec["cwd"]).resolve() != ws.resolve():
                p.append(f"{label}: script ran in {rec['cwd']}, not the workspace")
            if Path(rec["file"]).resolve().parent != temp.resolve():
                p.append(f"{label}: script ran from {rec['file']}, not from $RUNNER_TEMP")
        if notice:
            p.append(f"{label}: notice printed although the guard is installed")
    elif case == "present-fail":
        if ok:
            p.append(f"{label}: job passed although the base script exited 1 (result not used)")
        if not log.exists():
            p.append(f"{label}: the base commit's script did not run")
    elif case == "absent":
        if not ok:
            p.append(f"{label}: job failed although the guard is not on the base: {out[-300:]!r}")
        if not notice:
            p.append(f"{label}: no line {NOTICE!r}")
        if log.exists():
            p.append(f"{label}: a script ran although none is on the base")
    else:
        if ok:
            p.append(f"{label}: job passed on a git error or missing object")
        if log.exists():
            p.append(f"{label}: the script ran after a git error or missing object")
    return p


def guard_exec_problems(doc, name, tmp, cases=GUARD_CASES):
    script = GUARDS[name]
    job_ids = [jid for jid, job in jobs_of(doc).items()
               if any(f".github/ci/{script}" in (s.get("run") or "") for s in steps_of(job) if isinstance(s.get("run"), str))]
    if not job_ids:
        return [f"no job runs the guard entry steps for .github/ci/{script}"]
    p = []
    for jid in job_ids:
        for case in cases:
            root = Path(tempfile.mkdtemp(dir=tmp)).resolve()
            try:
                p += guard_case(doc, jid, name, case, root)
            except Unsupported as e:
                return [f"{name}/{jid}: unsupported by the step emulator: {e}"]
    return p


def ci_aggregate_problems(doc, tmp):
    combos = [("success", "success", True), ("failure", "success", False), ("success", "failure", False),
              ("cancelled", "success", False), ("success", "cancelled", False), ("skipped", "success", False),
              ("success", "skipped", False), ("skipped", "skipped", False)]
    p = []
    for c, g, want in combos:
        root = Path(tempfile.mkdtemp(dir=tmp)).resolve()
        env, _, _ = runner_env(root, root)
        try:
            ok, out = run_job(doc, "ci", {"needs.check.result": c, "needs.guard-tests.result": g}, root, env, root)
        except Unsupported as e:
            return [f"ci: unsupported by the step emulator: {e}"]
        if ok != want:
            p.append(f"ci: check={c} guard-tests={g} -> {'pass' if ok else 'fail'}, want {'pass' if want else 'fail'}")
    return p


def ci_make_problems(doc, tmp):
    cases = [("no Makefile", None, False), ("target missing", "other:\n\t@true\n", False),
             ("target fails", "lint:\n\t@false\n", False), ("target passes", "lint:\n\t@true\n", True)]
    p = []
    for label, makefile, want in cases:
        root = Path(tempfile.mkdtemp(dir=tmp)).resolve()
        ws = root / "workspace"
        ws.mkdir()
        if makefile is not None:
            (ws / "Makefile").write_text(makefile)
        env, _, _ = runner_env(root, ws)
        try:
            ok, out = run_job(doc, "check", {"matrix.target": "lint"}, ws, env, root)
        except Unsupported as e:
            return [f"check: unsupported by the step emulator: {e}"]
        if ok != want:
            p.append(f"check [{label}]: {'pass' if ok else 'fail'}, want {'pass' if want else 'fail'}")
    return p


# ================================================================ README and links (AC17, AC18)

LINK = re.compile(r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
REF_LINK = re.compile(r"(?m)^\s{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s|$)")


def strip_code(text):
    text = re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1[^\n]*$", "", text)
    return re.sub(r"`[^`\n]*`", "", text)


def relative_links(text):
    out = []
    for t in LINK.findall(strip_code(text)) + REF_LINK.findall(strip_code(text)):
        if re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", t) or t.startswith("#") or t.startswith("//"):
            continue
        t = unquote(t.split("#", 1)[0].split("?", 1)[0])
        if t:
            out.append(t)
    return out


def link_problems(text, folder):
    return [f"broken link {t}" for t in relative_links(text) if not (folder / t).exists()]


def token(name):
    return re.compile(r"(?<![\w/.@-])" + re.escape(name) + r"(?![\w/.@-])")


def readme_problems(readme, templates):
    p = []
    paths = set()
    for t in templates.values():
        paths |= set(re.findall(r"\.github/(?:ci|risk-gate)/[A-Za-z0-9_.-]+", t))
    sources = {"ci": SKILL / "scripts", "risk-gate": SKILL / "gate"}
    for path in sorted(paths):
        _, sub, fname = path.split("/")
        if not (sources[sub] / fname).is_file():
            p.append(f"{path}: source {sources[sub] / fname} does not exist")
        if fname.rsplit(".", 1)[0] not in readme or f".github/{sub}/" not in readme:
            p.append(f"{path}: not in the README copy table")
    rows = [l for l in readme.splitlines() if l.lstrip().startswith("|")]
    for dest, src in ((".github/ci/", "scripts/"), (".github/risk-gate/", "gate/"), (".github/workflows/", "ci/"),
                      (".github/workflows/", "risk-gate.yml")):
        if not any(dest in r and src in r for r in rows):
            p.append(f"copy table has no row from {src} to {dest}")
    if "actions/checkout@v4" not in readme:
        p.append("README does not name the allowlist entry actions/checkout@v4")
    sections = re.split(r"(?m)^#{1,6}\s", readme)
    if not any(re.search(r"(?i)required", s) and all(token(c).search(s) for c in REQUIRED_CHECKS) for s in sections):
        p.append(f"no README section names the required checks {REQUIRED_CHECKS}")
    if not (re.search(r"(?i)\bno secrets?\b", readme) and re.search(r"(?i)release job", readme)):
        p.append("README does not state the secrets rule (no secrets; deployment secrets only in the release job)")
    return p


# ================================================================ tests

def read(test, name):
    path = CI / f"{name}.yml"
    test.assertTrue(path.is_file(), f"template missing: {path}")
    return path.read_text()


def read_all(test):
    return {n: read(test, n) for n in TEMPLATES}


class TempCase(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = Path(d.name).resolve()


# -- mutation helpers: each asserts its anchor exists, so a mutation never silently does nothing

def sub1(text, pattern, repl, flags=re.M):
    new, n = re.subn(pattern, repl, text, count=1, flags=flags)
    if not n:
        raise AssertionError(f"mutation anchor {pattern!r} not found in the template")
    return new


def del_line(text, pattern):
    return sub1(text, r"^[^\n]*" + pattern + r"[^\n]*\n", "")


def add_step(text, *lines):
    m = re.search(r"(?m)^( *)steps:[ ]*(?:#[^\n]*)?\n(?:[ ]*(?:#[^\n]*)?\n)*( *)- ", text)
    if not m:
        raise AssertionError("mutation anchor: no steps: list")
    return text[:m.start(2)] + "".join(m.group(2) + l + "\n" for l in lines) + text[m.start(2):]


def add_job_key(text, *lines):
    m = re.search(r"(?m)^( *)runs-on:[^\n]*\n", text)
    if not m:
        raise AssertionError("mutation anchor: no runs-on:")
    return text[:m.end()] + "".join(m.group(1) + l + "\n" for l in lines) + text[m.end():]


def add_trigger(text, event):
    m = re.search(r"(?m)^on:([^\n]*)$", text)
    if not m:
        raise AssertionError("mutation anchor: no on:")
    rest = re.split(r"\s#", m.group(1), maxsplit=1)[0].strip()
    if not rest:
        return text[:m.end()] + f"\n  {event}:" + text[m.end():]
    if rest.startswith("["):
        return text[:m.start()] + "on: " + rest[:-1] + f", {event}]" + text[m.end():]
    return text[:m.start()] + f"on: [{rest}, {event}]" + text[m.end():]


def replace_on(text, event):
    m = re.search(r"(?m)^on:", text)
    if not m:
        raise AssertionError("mutation anchor: no on:")
    return text[:m.start()] + sub1(text[m.start():], r"\bpull_request\b(?!_)", event)


def drop_top_block(text, key):
    return sub1(text, rf"^{key}:[^\n]*\n(?:(?:[ ]+[^\n]*|[ ]*)\n)*", "")


class MutationCase(unittest.TestCase):
    def assert_catches(self, check, mutations, names=TEMPLATES, keep_subset=True):
        """mutations: [(label, fn(text, name) -> text)]. Each mutated copy must stay in the subset
        (unless the check is the subset check) and make `check` report a problem."""
        texts = {n: read(self, n) for n in names}
        for label, fn in mutations:
            for n in names:
                with self.subTest(mutation=label, template=n):
                    self.assertEqual(check(texts[n], n), [], f"{check.__name__} fails on the real {n}.yml")
                    bad = fn(texts[n], n)
                    self.assertNotEqual(bad, texts[n], "the mutation changed nothing")
                    if keep_subset:
                        self.assertEqual(check_subset(bad, n), [], "mutation left the YAML subset")
                    self.assertNotEqual(check(bad, n), [], f"{check.__name__} missed: {label}")


# ---------------------------------------------------------------- AC16

class TestAC16Static(unittest.TestCase):
    def each(self, check, names=TEMPLATES):
        for n in names:
            with self.subTest(template=n):
                self.assertEqual(check(read(self, n), n), [])

    def test_yaml_subset(self):
        self.each(check_subset)

    def test_trigger_pull_request_only(self):
        self.each(check_trigger)

    def test_permissions_contents_read_only(self):
        self.each(check_permissions)

    def test_no_secrets(self):
        self.each(check_secrets)

    def test_no_expression_in_run(self):
        self.each(check_run_expr)

    def test_no_fallbacks(self):
        self.each(check_fallbacks)

    def test_concurrency(self):
        self.each(check_concurrency)

    def test_timeouts_and_runner(self):
        self.each(check_jobs)

    def test_uses_pinned_or_allowlisted(self):
        self.each(check_uses)

    def test_checkout_persist_credentials_false(self):
        self.each(check_checkout)

    def test_fetch_depth(self):
        self.each(check_fetch_depth)

    def test_workflow_names_distinct(self):
        """Concurrency groups start with the workflow name: equal names would cancel each other."""
        names = [parse(read(self, n)).get("name") for n in TEMPLATES]
        self.assertEqual(len(set(names)), len(names), names)


class TestAC16Mutations(MutationCase):
    def test_subset(self):
        self.assert_catches(check_subset, [
            ("tab", lambda t, n: t.replace("\n", "\t\n", 1)),
            ("anchor", lambda t, n: sub1(t, r"^(name:\s*)", r"\1&wf ")),
            ("alias", lambda t, n: add_job_key(t, "env2: *wf")),
            ("merge key", lambda t, n: add_job_key(t, "<<: *wf")),
            ("flow mapping", lambda t, n: add_step(t, "- uses: actions/checkout@v4", "  with: {persist-credentials: false}")),
            ("odd indent", lambda t, n: add_job_key(t, " timeout-minutes2: 5")),
            ("duplicate key", lambda t, n: t + "permissions:\n  contents: read\n"),
            ("multi-line plain scalar", lambda t, n: add_step(t, "- run: echo one", "    two")),
            ("multi-line quoted scalar", lambda t, n: add_step(t, '- run: "echo one', '    two"')),
            ("document marker", lambda t, n: "---\n" + t),
            ("multi-line flow sequence", lambda t, n: add_job_key(t, "needs2: [a,", "  b]")),
        ], keep_subset=False)

    def test_trigger(self):
        self.assert_catches(check_trigger, [
            ("pull_request_target", lambda t, n: replace_on(t, "pull_request_target")),
            ("push added", lambda t, n: add_trigger(t, "push")),
            ("workflow_run added", lambda t, n: add_trigger(t, "workflow_run")),
            ("workflow_dispatch added", lambda t, n: add_trigger(t, "workflow_dispatch")),
            ("pull_request_target added", lambda t, n: add_trigger(t, "pull_request_target")),
        ])

    def test_permissions(self):
        self.assert_catches(check_permissions, [
            ("contents: write", lambda t, n: sub1(t, r"^(permissions:\s*\n\s+contents:\s*)read", r"\1write")),
            ("extra permission", lambda t, n: sub1(t, r"^(permissions:[^\n]*\n)", r"\1  pull-requests: write\n")),
            ("job-level permissions", lambda t, n: add_job_key(t, "permissions:", "  contents: read")),
            ("read-all", lambda t, n: drop_top_block(t, "permissions") + "permissions: read-all\n"),
            ("missing", lambda t, n: drop_top_block(t, "permissions")),
        ])

    def test_secrets(self):
        self.assert_catches(check_secrets, [
            ("secrets.X in env", lambda t, n: add_step(t, '- run: echo "$T"', "  env:", "    T: ${{ secrets.X }}")),
            ("SECRETS.X", lambda t, n: add_step(t, '- run: echo "$T"', "  env:", "    T: ${{ SECRETS.X }}")),
            ("secrets['X']", lambda t, n: add_step(t, '- run: echo "$T"', "  env:", "    T: ${{ secrets['X'] }}")),
            ("toJSON(secrets)", lambda t, n: add_step(t, '- run: echo "$T"', "  env:", "    T: ${{ toJSON(secrets) }}")),
            ("secrets in a comment", lambda t, n: t + "# token: secrets.X\n"),
            ("secrets: inherit", lambda t, n: add_job_key(t, "secrets: inherit")),
            ("github.token", lambda t, n: add_step(t, '- run: echo "$T"', "  env:", "    T: ${{ github.token }}")),
        ])

    def test_expression_in_run(self):
        self.assert_catches(check_run_expr, [
            ("inline", lambda t, n: add_step(t, "- run: echo ${{ github.head_ref }}")),
            ("quoted", lambda t, n: add_step(t, '- run: "echo ${{ github.head_ref }}"')),
            ("block", lambda t, n: add_step(t, "- run: |", "    echo ok", '    echo "${{ github.event.pull_request.title }}"')),
            ("folded", lambda t, n: add_step(t, "- run: >-", "    echo", "    ${{github.head_ref}}")),
            ("in a shell comment", lambda t, n: add_step(t, "- run: |", "    # ${{ github.head_ref }}", "    true")),
        ])

    def test_fallbacks(self):
        self.assert_catches(check_fallbacks, [
            ("job continue-on-error", lambda t, n: add_job_key(t, "continue-on-error: true")),
            ("step continue-on-error", lambda t, n: add_step(t, "- run: make x", "  continue-on-error: true")),
            ("|| true", lambda t, n: add_step(t, "- run: make lint || true")),
            ("||true in block", lambda t, n: add_step(t, "- run: |", "    make lint ||true")),
            ("|| :", lambda t, n: add_step(t, "- run: |", "    make lint || :")),
            ("set +e", lambda t, n: add_step(t, "- run: |", "    set +e", "    make lint")),
            ("set +o errexit", lambda t, n: add_step(t, "- run: |", "    set +o errexit", "    make lint")),
            ("|| exit 0", lambda t, n: add_step(t, "- run: make lint || exit 0")),
        ])

    def test_concurrency(self):
        self.assert_catches(check_concurrency, [
            ("shared group", lambda t, n: sub1(t, r"^(\s+group:\s*).*$", r"\1shared")),
            ("group without PR number", lambda t, n: sub1(t, r"^(\s+group:\s*).*$", r"\1" + n + "-${{ github.ref }}")),
            ("cancel-in-progress false", lambda t, n: sub1(t, r"(cancel-in-progress:\s*)true", r"\1false")),
            ("missing", lambda t, n: drop_top_block(t, "concurrency")),
        ])

    def test_jobs(self):
        self.assert_catches(check_jobs, [
            ("timeout missing", lambda t, n: del_line(t, r"timeout-minutes:")),
            ("timeout 45", lambda t, n: sub1(t, r"(timeout-minutes:\s*)\d+", r"\g<1>45")),
            ("timeout 0", lambda t, n: sub1(t, r"(timeout-minutes:\s*)\d+", r"\g<1>0")),
            ("self-hosted", lambda t, n: sub1(t, r"(runs-on:\s*)ubuntu-latest", r"\1self-hosted")),
        ])

    def test_uses(self):
        self.assert_catches(check_uses, [
            ("tag", lambda t, n: add_step(t, "- uses: someone/action@v5")),
            ("branch", lambda t, n: add_step(t, "- uses: someone/action@main")),
            ("short sha", lambda t, n: add_step(t, "- uses: someone/action@1234567")),
            ("checkout v3", lambda t, n: sub1(t, r"actions/checkout@v4", "actions/checkout@v3")),
            ("pinned setup action", lambda t, n: add_step(t, f"- uses: actions/setup-python@{SHA40}")),
            ("pinned cache action", lambda t, n: add_step(t, f"- uses: actions/cache@{SHA40}")),
            ("docker", lambda t, n: add_step(t, "- uses: docker://alpine:3")),
            ("local action", lambda t, n: add_step(t, "- uses: ./.github/actions/x")),
            ("reusable workflow", lambda t, n: add_job_key(t, "uses: someone/repo/.github/workflows/x.yml@main")),
        ])

    def test_checkout(self):
        self.assert_catches(check_checkout, [
            ("persist-credentials removed", lambda t, n: del_line(t, r"persist-credentials:")),
            ("persist-credentials true", lambda t, n: sub1(t, r"(persist-credentials:\s*)false", r"\1true")),
            ("second checkout without with", lambda t, n: add_step(t, "- uses: actions/checkout@v4")),
        ])

    def test_fetch_depth_guards(self):
        self.assert_catches(check_fetch_depth, [
            ("fetch-depth removed", lambda t, n: del_line(t, r"fetch-depth:")),
            ("fetch-depth 1", lambda t, n: sub1(t, r"(fetch-depth:\s*)0\b", r"\g<1>1")),
            ("second shallow checkout", lambda t, n: add_step(t, "- uses: actions/checkout@v4", "  with:",
                                                              "    persist-credentials: false")),
        ], names=tuple(GUARDS))

    def test_fetch_depth_ci_default(self):
        self.assert_catches(check_fetch_depth, [
            ("fetch-depth set in ci.yml", lambda t, n: sub1(t, r"^( *)(persist-credentials:[^\n]*\n)",
                                                            r"\1\2\1fetch-depth: 0\n")),
        ], names=("ci",))


# ---------------------------------------------------------------- AC17

class TestAC17CiYml(TempCase):
    def test_static(self):
        self.assertEqual(check_ci(read(self, "ci"), "ci"), [])

    @unittest.skipUnless(BASH, "bash not found: the step logic cannot run here")
    def test_ci_job_fails_unless_both_succeed(self):
        doc = parse(read(self, "ci"))
        self.assertEqual(ci_aggregate_problems(doc, self.tmp), [])

    @unittest.skipUnless(BASH and MAKE, "bash or make not found: the make step cannot run here")
    def test_make_step_fails_closed(self):
        doc = parse(read(self, "ci"))
        self.assertEqual(ci_make_problems(doc, self.tmp), [])

    def test_static_mutations(self):
        t = read(self, "ci")
        self.assertEqual(check_ci(t, "ci"), [])

        def matrix_drop(t, _):
            return re.sub(r",\s*licence\b|\blicence\s*,\s*", "", t, count=1) if re.search(r"target:\s*\[", t) \
                else del_line(t, r"^\s*- licence\s*$")

        def matrix_add(t, _):
            return sub1(t, r"(target:\s*\[)", r"\1docs, ") if re.search(r"target:\s*\[", t) \
                else sub1(t, r"^(\s*)- licence\s*$", r"\1- licence\n\1- docs")

        def needs_drop(t, _):
            return sub1(t, r"(needs:\s*\[\s*check)\s*,\s*guard-tests\s*\]", r"\1]") if re.search(r"needs:\s*\[", t) \
                else del_line(t, r"^\s*- guard-tests\s*$")

        mutations = [
            ("matrix target dropped", matrix_drop),
            ("matrix target added", matrix_add),
            ("matrix include", lambda t, _: sub1(t, r"^( *)(target:[^\n]*\n)", r"\1\2\1include:\n\1  - target: docs\n")),
            ("fail-fast true", lambda t, _: sub1(t, r"(fail-fast:\s*)false", r"\1true")),
            ("discover .github/ci removed", lambda t, _: del_line(t, r"discover -s \S*\.github/ci\b")),
            ("discover .github/risk-gate removed", lambda t, _: del_line(t, r"discover -s \S*\.github/risk-gate")),
            ("needs without guard-tests", needs_drop),
            ("if success()", lambda t, _: sub1(t, r"(if:\s*(?:\$\{\{\s*)?)always\(\)", r"\1success()")),
            ("make target hard-coded", lambda t, _: sub1(t, r'make "\$\{?TARGET\}?"', "make lint")),
            ("TARGET from elsewhere", lambda t, _: sub1(t, r"(TARGET:\s*)\$\{\{\s*matrix\.target\s*\}\}", r"\1lint")),
        ]
        for label, fn in mutations:
            with self.subTest(mutation=label):
                bad = fn(t, "ci")
                self.assertNotEqual(bad, t)
                self.assertEqual(check_subset(bad, "ci"), [], "mutation left the YAML subset")
                self.assertNotEqual(check_ci(bad, "ci"), [], f"check_ci missed: {label}")

    @unittest.skipUnless(BASH and MAKE, "bash or make not found: the step logic cannot run here")
    def test_exec_mutations(self):
        doc = parse(read(self, "ci"))
        lax = parse(read(self, "ci"))
        lax["jobs"]["ci"]["steps"] = [{"run": "echo ok\n"}]
        self.assertNotEqual(ci_aggregate_problems(lax, self.tmp), [], "aggregation that always passes not caught")
        lax = parse(read(self, "ci"))
        for s in steps_of(lax["jobs"]["check"]):
            if isinstance(s.get("run"), str) and "make" in s["run"]:
                s["run"] = 'if [ -f Makefile ]; then make "$TARGET"; fi\n'
        self.assertNotEqual(ci_make_problems(lax, self.tmp), [], "make step that skips a missing Makefile not caught")
        self.assertEqual(ci_aggregate_problems(doc, self.tmp), [])

    def test_readme_copy_table_and_sources(self):
        self.assertTrue(README.is_file(), f"README missing: {README}")
        self.assertEqual(readme_problems(README.read_text(), read_all(self)), [])

    def test_readme_problems_detects_gaps(self):
        """Checker self-test on the real README: removing what AC17 asks for is reported."""
        self.assertTrue(README.is_file(), f"README missing: {README}")
        text, tpl = README.read_text(), read_all(self)
        for label, bad in [("allowlist", text.replace("actions/checkout@v4", "actions/checkout")),
                           ("required checks", text.replace("agents-drift", "agents drift")),
                           ("secrets rule", re.sub(r"(?i)release job", "job", text)),
                           ("copy rows", "\n".join(l for l in text.splitlines() if ".github/ci/" not in l))]:
            with self.subTest(gap=label):
                self.assertNotEqual(readme_problems(bad, tpl), [])


# ---------------------------------------------------------------- AC18

POINTER_TARGETS = {SKILL / "ci" / "README.md", SKILL / "ci" / "commit-trailers.yml", SKILL / "scripts" / "check_trailers.py"}


class TestAC18Docs(unittest.TestCase):
    def test_commit_trailers_pointer_line(self):
        self.assertTrue(TRAILERS_DOC.is_file(), f"missing: {TRAILERS_DOC}")
        text = TRAILERS_DOC.read_text()
        named = relative_links(text) + re.findall(r"`([^`\s]+\.(?:md|yml|py))`", text)
        hits = set()
        for t in named:
            for folder in (TRAILERS_DOC.parent, SKILL):
                q = (folder / t).resolve()
                if q in POINTER_TARGETS and q.is_file():
                    hits.add(q)
        self.assertTrue(hits, f"{TRAILERS_DOC.name} has no pointer to an existing one of "
                              f"{sorted(str(p.relative_to(SKILL)) for p in POINTER_TARGETS)}")

    def test_commit_trailers_links_resolve(self):
        self.assertTrue(TRAILERS_DOC.is_file(), f"missing: {TRAILERS_DOC}")
        self.assertEqual(link_problems(TRAILERS_DOC.read_text(), TRAILERS_DOC.parent), [])

    def test_readme_links_resolve(self):
        self.assertTrue(README.is_file(), f"README missing: {README}")
        self.assertEqual(link_problems(README.read_text(), README.parent), [])

    def test_link_checker_self_test(self):
        text = ("[ok](test_ci_templates.py) [anchor](#x) [web](https://example.invalid/x) `[code](nope.md)`\n"
                "[bad](no/such/file.md) ![img](missing.png)\n[ref]: ../nope.md\n")
        self.assertEqual(sorted(link_problems(text, CI)),
                         ["broken link ../nope.md", "broken link missing.png", "broken link no/such/file.md"])


# ---------------------------------------------------------------- AC19

class TestAC19GuardSteps(TempCase):
    def test_static(self):
        for n in GUARDS:
            with self.subTest(template=n):
                self.assertEqual(check_guard(read(self, n), n), [])

    @unittest.skipUnless(BASH, "bash not found: the guard entry steps cannot run here")
    def test_exec_present_absent_error(self):
        for n in GUARDS:
            with self.subTest(template=n):
                self.assertEqual(guard_exec_problems(parse(read(self, n)), n, self.tmp), [])

    def test_static_mutations(self):
        def script(n):
            return re.escape(GUARDS[n])

        self.assert_mutations(check_guard, [
            ("runs the checked-out script", lambda t, n: sub1(t, r'python3 "\$\{?RUNNER_TEMP\}?/' + script(n) + '"',
                                                               f"python3 .github/ci/{GUARDS[n]}")),
            ("extra direct run", lambda t, n: add_step(t, f"- run: python3 .github/ci/{GUARDS[n]} --help")),
            ("direct exec", lambda t, n: add_step(t, f"- run: ./.github/ci/{GUARDS[n]}")),
            ("copy from the checkout", lambda t, n: add_step(t, f'- run: cp .github/ci/{GUARDS[n]} "$RUNNER_TEMP/"')),
            # AC39 supersedes the event-field anchors: BASE_SHA is derived from HEAD^1 in the run step.
            ("BASE_SHA from head", lambda t, n: sub1(t, r"(BASE_SHA=\"?\$\(\s*git rev-parse[^)\n]*HEAD)\^1", r"\1^2")),
            ("BASE_SHA from base_ref", lambda t, n: sub1(t, r"\$\(\s*git rev-parse[^)\n]*HEAD\^1[^)\n]*\)",
                                                         "$(git rev-parse origin/main)")),
            ("HEAD_SHA from github.sha", lambda t, n: sub1(t, r"\$\{\{\s*github\.event\.pull_request\.head\.sha\s*\}\}",
                                                           "${{ github.sha }}")),
            ("BASE_SHA overridden in GITHUB_ENV", lambda t, n: add_step(t, '- run: echo "BASE_SHA=x" >> "$GITHUB_ENV"')),
            ("BASE_SHA reassigned", lambda t, n: add_step(t, "- run: |", "    BASE_SHA=main", "    true")),
            ("no base verification", lambda t, n: sub1(t, r'git cat-file -e "\$\{?BASE_SHA\}?\^\{commit\}"', "true")),
            ("no head verification", lambda t, n: sub1(t, r'git cat-file -e "\$\{?HEAD_SHA\}?\^\{commit\}"', "true")),
            ("no ls-tree", lambda t, n: sub1(t, r"git ls-tree --name-only", "git ls-files --")),
            ("notice text changed", lambda t, n: sub1(t, re.escape(NOTICE), "::notice::skipped")),
            ("copy not from the base commit", lambda t, n: sub1(t, r'git show "\$\{?BASE_SHA\}?:', 'git show "$HEAD_SHA:')),
            ("job renamed", lambda t, n: sub1(t, r"^(  )" + re.escape(n) + ":", r"\1guard:")),
        ])

    @unittest.skipUnless(BASH, "bash not found: the guard entry steps cannot run here")
    def test_exec_mutations(self):
        for n, s in GUARDS.items():
            t = read(self, n)
            muts = [("runs the checked-out script",
                     sub1(t, r'python3 "\$\{?RUNNER_TEMP\}?/' + re.escape(s) + '"', f'python3 ".github/ci/{s}"')),
                    ("ls-tree error swallowed",
                     sub1(t, r'(git ls-tree --name-only "\$\{?BASE_SHA\}?" -- ["\']?\.github/ci/' + re.escape(s) + r'["\']?)',
                          r"\1 2>/dev/null || true"))]
            for label, bad in muts:
                with self.subTest(template=n, mutation=label):
                    self.assertNotEqual(bad, t)
                    self.assertNotEqual(guard_exec_problems(parse(bad), n, self.tmp), [],
                                        f"the executable step test missed: {label}")

    def assert_mutations(self, check, mutations):
        for n in GUARDS:
            t = read(self, n)
            self.assertEqual(check(t, n), [], f"{check.__name__} fails on the real {n}.yml")
            for label, fn in mutations:
                with self.subTest(template=n, mutation=label):
                    bad = fn(t, n)
                    self.assertNotEqual(bad, t)
                    self.assertEqual(check_subset(bad, n), [], "mutation left the YAML subset")
                    self.assertNotEqual(check(bad, n), [], f"{check.__name__} missed: {label}")


# ---------------------------------------------------------------- checker self-tests (no template needed)

class TestParserSelfTest(unittest.TestCase):
    SNIPPET = ("name: demo  # comment\n"
               "on:\n  pull_request:\n    types: [opened, synchronize]\n"
               "permissions:\n  contents: read\n"
               "jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n"
               "      - uses: actions/checkout@v4\n        with:\n          persist-credentials: false\n"
               "      - name: x\n        env:\n          V: \"${{ github.head_ref }}\"\n"
               "        run: |\n          # shell comment\n          echo \"$V\"\n\n          true\n"
               "  b:\n    needs: [a]\n    steps:\n    - run: echo 'it''s'\n")

    def test_parse(self):
        doc = parse(self.SNIPPET)
        self.assertEqual(doc["name"], "demo")
        self.assertEqual(doc["on"], {"pull_request": {"types": ["opened", "synchronize"]}})
        steps = doc["jobs"]["a"]["steps"]
        self.assertEqual(steps[0], {"uses": "actions/checkout@v4", "with": {"persist-credentials": "false"}})
        self.assertEqual(steps[1]["env"], {"V": "${{ github.head_ref }}"})
        self.assertEqual(steps[1]["run"], "# shell comment\necho \"$V\"\n\ntrue\n")
        self.assertEqual(doc["jobs"]["b"], {"needs": ["a"], "steps": [{"run": "echo 'it''s'"}]})
        self.assertIn("# shell comment", code_text(self.SNIPPET))
        self.assertNotIn("# comment", code_text(self.SNIPPET).split("\n")[0])

    def test_rejects(self):
        for bad in ("a:\n\tb: 1\n", "a: &x 1\n", "a: *x\n", "a: {b: 1}\n", "a:\n   b: 1\n", "a: 1\na: 2\n",
                    "a: one\n  two\n", "a: 'x\n", "- a\n", "a: b: c\n"):
            with self.subTest(text=bad):
                self.assertRaises(SubsetError, parse, bad)


# ---------------------------------------------------------------- security round 1 (spec section 17, AC21)

def check_no_lazy_fetch(text, name):
    """AC21 template part: the guard step (the one that runs the base copy of the script) has
    GIT_NO_LAZY_FETCH: "1" in its own env:, since its preflight, ls-tree and show run git in a PR checkout."""
    doc, p = parsed(text)
    if p:
        return p
    run_copy = re.compile(r"python3 " + _v("RUNNER_TEMP") + "/" + re.escape(GUARDS[name]) + '"')
    steps = [s for j in jobs_of(doc).values() for s in steps_of(j)
             if isinstance(s.get("run"), str) and run_copy.search(s["run"])]
    if len(steps) != 1:
        return [f"want one guard step that runs the base copy, found {len(steps)}"]
    env = steps[0].get("env")
    value = env.get("GIT_NO_LAZY_FETCH") if isinstance(env, dict) else None
    if value != "1":
        p.append(f"guard step env GIT_NO_LAZY_FETCH is {value!r}, not \"1\"")
    return p


class TestSecurityRound1AC21Templates(TempCase):
    def test_static(self):
        for n in GUARDS:
            with self.subTest(template=n):
                self.assertEqual(check_no_lazy_fetch(read(self, n), n), [])

    def test_static_mutations(self):
        def env_line(t):
            m = re.search(r'(?m)^( *)GIT_NO_LAZY_FETCH:[^\n]*\n', t)
            if not m:
                raise AssertionError("mutation anchor: no GIT_NO_LAZY_FETCH line")
            return m

        def moved_to_other_step(t, n):
            m = env_line(t)
            t = t[:m.start()] + t[m.end():]
            return add_step(t, "- env:", '    GIT_NO_LAZY_FETCH: "1"', "  run: true")

        TestAC19GuardSteps.assert_mutations(self, check_no_lazy_fetch, [
            ("deleted", lambda t, n: del_line(t, r"GIT_NO_LAZY_FETCH:")),
            ("set to 0", lambda t, n: sub1(t, r'(GIT_NO_LAZY_FETCH:) *"?1"?', r'\1 "0"')),
            ("set to empty", lambda t, n: sub1(t, r'(GIT_NO_LAZY_FETCH:) *"?1"?', r'\1 ""')),
            ("key renamed", lambda t, n: sub1(t, r"GIT_NO_LAZY_FETCH:", "GIT_NO_LAZY_FETCHX:")),
            ("moved to another step", moved_to_other_step),
        ])

    def test_checker_self_test(self):
        # Independent of the real templates: a minimal guard step with and without the variable.
        def doc(env_lines):
            return ("name: diff-guard\non:\n  pull_request:\npermissions:\n  contents: read\njobs:\n  diff-guard:\n"
                    "    runs-on: ubuntu-latest\n    steps:\n      - env:\n          BASE_SHA: x\n" + env_lines +
                    '        run: |\n          python3 "$RUNNER_TEMP/diff_guard.py" --base "$BASE_SHA"\n')
        self.assertEqual(check_no_lazy_fetch(doc('          GIT_NO_LAZY_FETCH: "1"\n'), "diff-guard"), [])
        self.assertNotEqual(check_no_lazy_fetch(doc(""), "diff-guard"), [])
        self.assertNotEqual(check_no_lazy_fetch(doc('          GIT_NO_LAZY_FETCH: "0"\n'), "diff-guard"), [])


# ---------------------------------------------------------------- second security pass (spec section 21)

AC39_CASES = ("stale-base", "not-merge", "head-mismatch")


def check_trigger_types(text, name):
    """AC39 (F3): the guard templates trigger on pull_request with exactly these four types.
    The other pull_request keys are checked by check_branch_filter (spec 016 FU9 supersedes the
    former "no filter other than types" rule)."""
    doc, p = parsed(text)
    if p:
        return p
    on = doc.get("on")
    pr = on.get("pull_request") if isinstance(on, dict) else None
    types = pr.get("types") if isinstance(pr, dict) else None
    if not isinstance(types, list) or len(types) != len(TRIGGER_TYPES) or sorted(map(str, types)) != sorted(TRIGGER_TYPES):
        p.append(f"pull_request types {types!r} are not exactly {TRIGGER_TYPES}")
    return p


def check_merge_base(text, name):
    """AC39 (F2): the guard step does not use the event's base sha. It verifies that HEAD^2 is the
    PR head (else MERGE_MSG), then derives BASE_SHA from HEAD^1, before verifying, listing and copying."""
    doc, p = parsed(text)
    if p:
        return p
    if re.search(r"pull_request\s*(?:\.\s*base\s*\.|\[\s*'base'\s*\])", code_text(text)):
        p.append("the event's pull_request.base is used")
    run_copy = re.compile(r"python3 " + _v("RUNNER_TEMP") + "/" + re.escape(GUARDS[name]) + '"')
    steps = [s for j in jobs_of(doc).values() for s in steps_of(j)
             if isinstance(s.get("run"), str) and run_copy.search(s["run"])]
    if len(steps) != 1:
        return p + [f"want one guard step that runs the base copy, found {len(steps)}"]
    run = steps[0]["run"]
    pats = {"merge check": r"git rev-parse\b[^\n]*\bHEAD\^2\b", "message": re.escape(MERGE_MSG),
            "derive": DERIVE_BASE.pattern, "verify base": "git cat-file -e " + _v("BASE_SHA"),
            "ls-tree": "git ls-tree --name-only " + _v("BASE_SHA")}
    at = {}
    for label, pat in pats.items():
        hits = [m.start() for m in re.finditer(pat, run)]
        if not hits:
            p.append(f"guard step missing: {label}")
        else:
            at[label] = hits[0]
    if len(DERIVE_BASE.findall(run)) > 1:
        p.append("BASE_SHA is derived more than once")
    for a, b in (("merge check", "derive"), ("message", "derive"), ("derive", "verify base"), ("derive", "ls-tree")):
        if a in at and b in at and at[a] > at[b]:
            p.append(f"{a} comes after {b}")
    return p


def move_derive_first(t, n):
    m = re.search(r"(?m)^ *[^\n]*BASE_SHA=[^\n]*\n", t)
    if not m:
        raise AssertionError("mutation anchor: no BASE_SHA= line")
    line, t = m.group(0), t[:m.start()] + t[m.end():]
    r = re.search(r"(?m)^ *run: \|\n( *)", t)
    if not r:
        raise AssertionError("mutation anchor: no run: |")
    return t[:r.end(1) - len(r.group(1))] + r.group(1) + line.lstrip(" ") + t[r.end(1) - len(r.group(1)):]


def types_drop(t, n):
    if re.search(r"types:\s*\[", t):
        return sub1(t, r"\bedited\s*,\s*|,\s*edited\b", "")
    return del_line(t, r"^\s*- edited\s*$")


def types_add(t, n):
    if re.search(r"types:\s*\[", t):
        return sub1(t, r"(types:\s*\[)", r"\1labeled, ")
    return sub1(t, r"^(\s*)- edited\s*$", r"\1- edited\n\1- labeled")


def event_base_in_env(t, n):
    return sub1(t, r"^( *)(HEAD_SHA:[^\n]*\n)", r"\1\2\1EVENT_BASE: ${{ github.event.pull_request.base.sha }}\n")


class TestSecondPassAC39MergeCommitBase(TempCase):
    """AC39 (F2, F3): trigger types, merge-commit verification, BASE_SHA from HEAD^1."""

    def test_static_trigger_types(self):
        for n in GUARDS:
            with self.subTest(template=n):
                self.assertEqual(check_trigger_types(read(self, n), n), [])

    def test_static_merge_base(self):
        for n in GUARDS:
            with self.subTest(template=n):
                self.assertEqual(check_merge_base(read(self, n), n), [])

    def test_static_trigger_types_mutations(self):
        TestAC19GuardSteps.assert_mutations(self, check_trigger_types, [
            ("edited dropped", types_drop),
            ("labeled added", types_add),
            ("types removed", lambda t, n: del_line(t, r"^\s*types:")),
        ])

    def test_static_merge_base_mutations(self):
        TestAC19GuardSteps.assert_mutations(self, check_merge_base, [
            ("message changed", lambda t, n: sub1(t, re.escape(MERGE_MSG), "checkout mismatch")),
            ("no HEAD^2 check", lambda t, n: re.sub(r"HEAD\^2", "HEAD", sub1(t, r"HEAD\^2", "HEAD"))),
            ("BASE_SHA from HEAD~1", lambda t, n: sub1(t, r"(BASE_SHA=\"?\$\(\s*git rev-parse[^)\n]*HEAD)\^1", r"\1~1")),
            ("BASE_SHA from HEAD^2", lambda t, n: sub1(t, r"(BASE_SHA=\"?\$\(\s*git rev-parse[^)\n]*HEAD)\^1", r"\1^2")),
            ("event base in env", event_base_in_env),
            ("event base read in another step", lambda t, n: add_step(t, "- env:", "    B: ${{ github.event.pull_request['base']['sha'] }}",
                                                                       '  run: echo "$B"')),
            ("derived before the merge check", move_derive_first),
        ])

    def test_check_guard_rejects_base_sha_from_the_event(self):
        """The superseded AC19 form (BASE_SHA from the event in env:) now fails check_guard."""
        TestAC19GuardSteps.assert_mutations(self, check_guard, [
            ("BASE_SHA from the event in env:", lambda t, n: sub1(t, r"^( *)(HEAD_SHA:[^\n]*\n)",
                                                                  r"\1\2\1BASE_SHA: ${{ github.event.pull_request.base.sha }}\n")),
        ])

    @unittest.skipUnless(BASH, "bash not found: the guard entry steps cannot run here")
    def test_exec_stale_base_non_merge_and_head_mismatch(self):
        for n in GUARDS:
            with self.subTest(template=n):
                self.assertEqual(guard_exec_problems(parse(read(self, n)), n, self.tmp, AC39_CASES), [])

    @unittest.skipUnless(BASH, "bash not found: the guard entry steps cannot run here")
    def test_exec_mutations(self):
        for n in GUARDS:
            t = read(self, n)
            muts = [("BASE_SHA from the event", sub1(event_base_in_env(t, n), r"\$\(\s*git rev-parse[^)\n]*HEAD\^1[^)\n]*\)",
                                                     '${EVENT_BASE}')),
                    ("merge check made vacuous", re.sub(r"HEAD\^2", "$HEAD_SHA", sub1(t, r"HEAD\^2", "$HEAD_SHA")))]
            for label, bad in muts:
                with self.subTest(template=n, mutation=label):
                    self.assertNotEqual(bad, t)
                    self.assertNotEqual(guard_exec_problems(parse(bad), n, self.tmp, AC39_CASES), [],
                                        f"the executable step test missed: {label}")


class TestSecondPassAC43ReadmeMakefile(unittest.TestCase):
    """AC43 (F9): the README says the Makefile is the trust root of ci.yml and is rated by the policy."""

    @staticmethod
    def problems(text):
        paras = re.split(r"\n\s*\n", text)
        if any(re.search(r"\bMakefile\b", s) and re.search(r"(?i)\btrust root\b", s)
               and re.search(r"(?i)\bci\.yml\b", s) and re.search(r"(?i)\bpolic(?:y|ies)\b|risk-policy", s) for s in paras):
            return []
        return ["no README paragraph says the Makefile is the trust root of ci.yml and is rated by the policy"]

    def test_readme_names_the_makefile_as_trust_root(self):
        self.assertTrue(README.is_file(), f"README missing: {README}")
        self.assertEqual(self.problems(README.read_text()), [])

    def test_checker_self_test(self):
        ok = "The `Makefile` is the trust root of `ci.yml`; risk-policy.toml rates it (review lane).\n"
        self.assertEqual(self.problems(ok), [])
        self.assertNotEqual(self.problems(ok.replace("trust root", "entry point")), [])
        self.assertNotEqual(self.problems("The Makefile is the trust root of ci.yml.\n\nThe policy rates it.\n"), [])


# ================================================================ second security pass, round 2 (spec section 22)

# AC46: `git merge-base --is-ancestor "$BASE_SHA" "refs/remotes/origin/$BASE_REF"` in a bash if.
IS_ANCESTOR = re.compile(r"git merge-base --is-ancestor " + _v("BASE_SHA") + r'" "refs/remotes/origin/\$(?:BASE_REF|\{BASE_REF\})"')
BASE_REF_EXPR = "${{ github.base_ref }}"
BASE_MSG = "the merge base is not on the target branch"
# AC50: `git rev-parse --verify -q HEAD^3` (options in any order, HEAD^3 may be quoted).
OCTOPUS = re.compile(r"""git rev-parse(?: +-[-\w]+)* +["']?HEAD\^3\b""")


def guard_step(doc, name):
    """The one step that runs the base copy of the guard script, or None."""
    run_copy = re.compile(r"python3 " + _v("RUNNER_TEMP") + "/" + re.escape(GUARDS[name]) + '"')
    steps = [s for j in jobs_of(doc).values() for s in steps_of(j)
             if isinstance(s.get("run"), str) and run_copy.search(s["run"])]
    return steps[0] if len(steps) == 1 else None


def check_target_branch(text, name):
    """AC46 (N3): BASE_REF comes from github.base_ref in the guard step's env: only, and is used only as
    the quoted ref argument of the is-ancestor check, which runs in a bash if after BASE_SHA is derived
    and before anything is listed or run from the base, and prints BASE_MSG."""
    doc, p = parsed(text)
    if p:
        return p
    step = guard_step(doc, name)
    if step is None:
        return ["want one guard step that runs the base copy"]
    defs = [(node is step.get("env"), norm_expr(node["BASE_REF"])) for path, node in walk(doc)
            if path and path[-1] == "env" and isinstance(node, dict) and "BASE_REF" in node]
    if defs != [(True, BASE_REF_EXPR)]:
        p.append(f"BASE_REF definitions {defs!r}: want exactly one, {BASE_REF_EXPR}, in the guard step's env:")
    run = step["run"]
    code = "\n".join(l for l in "\n".join(run_texts(doc)).splitlines() if not l.lstrip().startswith("#"))
    uses = re.findall(r"\bBASE_REF\b", code)
    if len(uses) != 1:
        p.append(f"BASE_REF appears {len(uses)} times in run text; want only the is-ancestor argument")
    m = re.search(r"\bif\s+(?:!\s+)?" + IS_ANCESTOR.pattern, run)
    if not m:
        return p + ['guard step missing: if ... git merge-base --is-ancestor "$BASE_SHA" "refs/remotes/origin/$BASE_REF"']
    if BASE_MSG not in run[m.end():]:
        p.append(f"no {BASE_MSG!r} after the is-ancestor check")
    derive = DERIVE_BASE.search(run)
    listing = re.search("git ls-tree --name-only " + _v("BASE_SHA"), run)
    if not derive or derive.start() > m.start():
        p.append("the is-ancestor check does not come after BASE_SHA is derived")
    if not listing or listing.start() < m.start():
        p.append("the is-ancestor check does not come before git ls-tree")
    return p


def check_octopus(text, name):
    """AC50 (N8): the guard step checks for a third parent (git rev-parse --verify -q HEAD^3) before
    anything is listed or run from the base."""
    doc, p = parsed(text)
    if p:
        return p
    step = guard_step(doc, name)
    if step is None:
        return ["want one guard step that runs the base copy"]
    run = step["run"]
    m = OCTOPUS.search(run)
    if not m:
        return ["guard step missing: git rev-parse --verify -q HEAD^3"]
    listing = re.search("git ls-tree --name-only " + _v("BASE_SHA"), run)
    if not listing or listing.start() < m.start():
        p.append("the HEAD^3 check does not come before git ls-tree")
    if MERGE_MSG not in run:
        p.append(f"no {MERGE_MSG!r} in the guard step")
    return p


R2_PASS = ("present", "origin-ahead")
R2_CASES = ("present", "origin-ahead", "old-branch-merge", "orphan-merge", "missing-origin", "octopus")


def r2_case(doc, job_id, name, case, root):
    """AC46/AC50 executable cases on top of pr_repo (refs/remotes/origin/main = the base tip):
    present: the normal merge passes. origin-ahead: the target branch moved on after the merge commit
    was made (the base is an ancestor, not the tip) and still passes. old-branch-merge / orphan-merge:
    the checkout merges the head into a commit that is not on the target branch (an old soft branch off
    an earlier main, or an unrelated root) and fails with BASE_MSG. missing-origin: no origin/main, fails.
    octopus: HEAD^1 = base, HEAD^2 = head, HEAD^3 = a third parent; fails with MERGE_MSG."""
    script = GUARDS[name]
    ws, base, head, old = pr_repo(root, script, on_base=True)
    genv = git_env(root)

    def git(*a):
        cp = subprocess.run([REAL_GIT, "-C", str(ws), *a], env=genv, capture_output=True, text=True)
        if cp.returncode:
            raise AssertionError(f"fixture git {a}: {cp.stderr}")
        return cp.stdout.strip()

    base_tree = git("rev-parse", f"{base}^{{tree}}")
    if case == "origin-ahead":
        git("update-ref", "refs/remotes/origin/main", git("commit-tree", base_tree, "-p", base, "-m", "main moves on"))
    elif case in ("old-branch-merge", "orphan-merge"):
        # The soft base holds the base's guard stub: if the check is missing, that stub runs.
        soft = git("commit-tree", base_tree, *(("-p", old) if case == "old-branch-merge" else ()), "-m", "soft base")
        merge = git("commit-tree", git("rev-parse", "HEAD^{tree}"), "-p", soft, "-p", head, "-m", "merge into soft")
        git("checkout", "-q", "--detach", merge)
        if git("rev-parse", "HEAD^1") != soft or git("rev-parse", "HEAD^2") != head:
            raise AssertionError("fixture: not a merge of the head into the soft base")
    elif case == "missing-origin":
        git("update-ref", "-d", "refs/remotes/origin/main")
    elif case == "octopus":
        extra = git("commit-tree", base_tree, "-p", base, "-m", "third parent")
        merge = git("commit-tree", git("rev-parse", "HEAD^{tree}"), "-p", base, "-p", head, "-p", extra, "-m", "octopus")
        git("checkout", "-q", "--detach", merge)
        if [git("rev-parse", f"HEAD^{i}") for i in (1, 2, 3)] != [base, head, extra]:
            raise AssertionError("fixture: not an octopus merge")
    env, bin_, temp = runner_env(root, ws)
    log, marker = root / "stub.json", root / "pr-ran"
    env.update(STUB_LOG=str(log), STUB_PR_MARKER=str(marker), STUB_EXIT="0")
    ctx = {BASE_EXPR: base, HEAD_EXPR: head, "github.head_ref": "agent/x", "github.base_ref": "main"}
    ok, out = run_job(doc, job_id, ctx, ws, env, root)
    label = f"{name}/{job_id} [{case}]"
    p = []
    if marker.exists():
        p.append(f"{label}: the PR's own .github/ci/{script} ran")
    if case in R2_PASS:
        if not ok:
            p.append(f"{label}: job failed on a merge into the target branch: {out[-300:]!r}")
        if not log.exists():
            p.append(f"{label}: the base commit's script did not run")
        elif json.loads(log.read_text())["argv"] != want_args(name, base, head):
            p.append(f"{label}: script arguments {json.loads(log.read_text())['argv']} != {want_args(name, base, head)}")
        return p
    if ok:
        p.append(f"{label}: job passed")
    if log.exists():
        p.append(f"{label}: the guard script ran")
    want = MERGE_MSG if case == "octopus" else BASE_MSG
    if want not in out:
        p.append(f"{label}: no {want!r} in the output: {out[-300:]!r}")
    return p


def r2_exec_problems(doc, name, tmp, cases=R2_CASES):
    script = GUARDS[name]
    job_ids = [jid for jid, job in jobs_of(doc).items()
               if any(f".github/ci/{script}" in (s.get("run") or "") for s in steps_of(job) if isinstance(s.get("run"), str))]
    if not job_ids:
        return [f"no job runs the guard entry steps for .github/ci/{script}"]
    p = []
    for jid in job_ids:
        for case in cases:
            root = Path(tempfile.mkdtemp(dir=tmp)).resolve()
            try:
                p += r2_case(doc, jid, name, case, root)
            except Unsupported as e:
                return [f"{name}/{jid}: unsupported by the step emulator: {e}"]
    return p


class TestSecondPassR2AC45CiTriggerTypes(MutationCase):
    """AC45 (N1): ci.yml triggers on pull_request with exactly the four types of the guards."""

    def test_static(self):
        t = read(self, "ci")
        self.assertEqual(check_trigger_types(t, "ci"), [])
        self.assertEqual(check_trigger(t, "ci"), [])

    def test_static_mutations(self):
        self.assert_catches(check_trigger_types, [
            ("edited dropped", types_drop),
            ("labeled added", types_add),
            ("types removed", lambda t, n: del_line(t, r"^\s*types:")),
        ], names=("ci",))


class TestSecondPassR2AC46TargetBranch(TempCase):
    """AC46 (N3): the merge base (HEAD^1) must be on refs/remotes/origin/$BASE_REF."""

    def test_static(self):
        for n in GUARDS:
            with self.subTest(template=n):
                self.assertEqual(check_target_branch(read(self, n), n), [])

    def test_static_mutations(self):
        TestAC19GuardSteps.assert_mutations(self, check_target_branch, [
            ("BASE_REF env dropped", lambda t, n: del_line(t, r"^\s*BASE_REF:")),
            ("BASE_REF from head_ref", lambda t, n: sub1(t, r"(BASE_REF:\s*)\$\{\{\s*github\.base_ref\s*\}\}",
                                                         r"\1${{ github.head_ref }}")),
            ("BASE_REF in another step", lambda t, n: add_step(t, "- env:", "    BASE_REF: ${{ github.base_ref }}",
                                                               "  run: echo ok")),
            ("BASE_REF used elsewhere", lambda t, n: sub1(t, r"^( *)(BASE_SHA=\"?\$\([^\n]*\n)",
                                                          r'\1\2\1echo "$BASE_REF"\n')),
            ("is-ancestor removed", lambda t, n: sub1(t, IS_ANCESTOR.pattern, "true")),
            ("unquoted ref", lambda t, n: sub1(t, r'"(refs/remotes/origin/\$\{?BASE_REF\}?)"', r"\1")),
            ("local branch ref", lambda t, n: sub1(t, r"refs/remotes/origin/(\$\{?BASE_REF\}?)", r"refs/heads/\1")),
            ("checks HEAD_SHA", lambda t, n: sub1(t, r"(--is-ancestor )" + _v("BASE_SHA") + '"', r'\1"$HEAD_SHA"')),
            ("message changed", lambda t, n: sub1(t, re.escape(BASE_MSG), "not on main")),
        ])

    @unittest.skipUnless(BASH, "bash not found: the guard entry steps cannot run here")
    def test_exec(self):
        for n in GUARDS:
            with self.subTest(template=n):
                self.assertEqual(r2_exec_problems(parse(read(self, n)), n, self.tmp,
                                                  ("present", "origin-ahead", "old-branch-merge", "orphan-merge",
                                                   "missing-origin")), [])

    @unittest.skipUnless(BASH, "bash not found: the guard entry steps cannot run here")
    def test_exec_mutations(self):
        for n in GUARDS:
            t = read(self, n)
            muts = [("is-ancestor removed", sub1(t, IS_ANCESTOR.pattern, "true")),
                    ("compared with itself", sub1(t, r'"refs/remotes/origin/\$\{?BASE_REF\}?"', '"$BASE_SHA"'))]
            for label, bad in muts:
                with self.subTest(template=n, mutation=label):
                    self.assertNotEqual(r2_exec_problems(parse(bad), n, self.tmp, ("old-branch-merge", "missing-origin")), [],
                                        f"the executable step test missed: {label}")


class TestSecondPassR2AC50Octopus(TempCase):
    """AC50 (N8): an octopus merge (HEAD^3 exists) fails with MERGE_MSG."""

    def test_static(self):
        for n in GUARDS:
            with self.subTest(template=n):
                self.assertEqual(check_octopus(read(self, n), n), [])

    def test_static_mutations(self):
        TestAC19GuardSteps.assert_mutations(self, check_octopus, [
            ("HEAD^3 check removed", lambda t, n: sub1(t, OCTOPUS.pattern, "true")),
            ("HEAD^4 instead", lambda t, n: sub1(t, r"HEAD\^3\b", "HEAD^4")),
        ])

    @unittest.skipUnless(BASH, "bash not found: the guard entry steps cannot run here")
    def test_exec(self):
        for n in GUARDS:
            with self.subTest(template=n):
                self.assertEqual(r2_exec_problems(parse(read(self, n)), n, self.tmp, ("present", "octopus")), [])

    @unittest.skipUnless(BASH, "bash not found: the guard entry steps cannot run here")
    def test_exec_mutation(self):
        for n in GUARDS:
            with self.subTest(template=n):
                bad = sub1(read(self, n), r"HEAD\^3\b", "HEAD^4")
                self.assertNotEqual(r2_exec_problems(parse(bad), n, self.tmp, ("octopus",)), [],
                                    "the executable step test missed: HEAD^4 instead of HEAD^3")


class TestSecondPassR2AC47ReadmeTrustRoot(unittest.TestCase):
    """AC47 (N4): the README says the trust root is the Makefile and everything it calls, and that a
    script it runs is rated by the ordinary rules."""

    @staticmethod
    def problems(text):
        paras = re.split(r"\n\s*\n", text)
        if any(re.search(r"\bMakefile\b", s) and re.search(r"(?i)\btrust root\b", s)
               and re.search(r"(?i)\beverything (?:it|the Makefile) (?:calls|runs)\b", s)
               and re.search(r"(?i)\bordinary (?:policy )?rules\b", s) for s in paras):
            return []
        return ["no README paragraph says the trust root is the Makefile and everything it calls "
                "(scripts it runs are rated by the ordinary rules)"]

    def test_readme(self):
        self.assertTrue(README.is_file(), f"README missing: {README}")
        self.assertEqual(self.problems(README.read_text()), [])

    def test_checker_self_test(self):
        ok = ("The trust root is the Makefile and everything it calls: a script it runs is rated by the "
              "ordinary rules, so read those too.\n")
        self.assertEqual(self.problems(ok), [])
        self.assertNotEqual(self.problems(ok.replace("everything it calls", "the targets")), [])
        self.assertNotEqual(self.problems(ok.replace("ordinary rules", "policy")), [])
        self.assertNotEqual(self.problems("The trust root is the Makefile and everything it calls.\n\n"
                                          "Scripts follow the ordinary rules.\n"), [])



# ================================================================ FU9: branch filter (spec 016)

BRANCHES = ["main"]


def check_branch_filter(text, name):
    """FU9 AC1: on: pull_request: has exactly the keys types and branches, types are the four of
    AC39/AC45 and branches is exactly [main] (a PR against another base starts no run: fail closed)."""
    doc, p = parsed(text)
    if p:
        return p
    on = doc.get("on")
    pr = on.get("pull_request") if isinstance(on, dict) else None
    if not isinstance(pr, dict) or set(pr) != {"types", "branches"}:
        p.append(f"pull_request keys {sorted(pr) if isinstance(pr, dict) else pr!r} are not exactly types and branches")
    elif pr["branches"] != BRANCHES:
        p.append(f"pull_request branches {pr['branches']!r} are not exactly {BRANCHES}")
    return p + check_trigger_types(text, name)


# The branches entry: a flow line or a key followed by block list items.
BRANCHES_ENTRY = r"^(    )branches:[^\n]*\n(?:\1(?:  )?- [^\n]*\n)*"


def set_branches(t, *lines):
    return sub1(t, BRANCHES_ENTRY, "".join(r"\g<1>" + l.replace("\\", "\\\\") + "\n" for l in lines))


def pr_key_add(line):
    return lambda t, n: sub1(t, r"^(  pull_request:[^\n]*\n)", r"\1    " + line + r"\n")


class TestFU9AC1BranchFilter(unittest.TestCase):
    """FU9 AC1: each of the four templates filters pull_request on branches: [main] next to types."""

    def test_static(self):
        for n in TEMPLATES:
            with self.subTest(template=n):
                t = read(self, n)
                self.assertEqual(check_branch_filter(t, n), [])
                self.assertEqual(check_trigger(t, n), [])
                self.assertEqual(check_trigger_types(t, n), [])

    def test_checker_self_test(self):
        head = "name: x\non:\n  pull_request:\n    types: [opened, synchronize, reopened, edited]\n"
        tail = "permissions:\n  contents: read\n"
        self.assertEqual(check_branch_filter(head + "    branches: [main]\n" + tail, "x"), [])
        self.assertEqual(check_branch_filter(head + "    branches:\n      - main\n" + tail, "x"), [])
        for bad in ("", "    branches: main\n", "    branches: []\n", "    branches: [main, main]\n",
                    "    branches: [Main]\n", "    branches:\n" ):
            with self.subTest(branches=bad):
                self.assertNotEqual(check_branch_filter(head + bad + tail, "x"), [])


class TestFU9AC2BranchFilterMutations(MutationCase):
    """FU9 AC2: mutations of the filter are caught; the existing trigger checks keep working."""

    def test_mutations(self):
        self.assert_catches(check_branch_filter, [
            ("branches missing", lambda t, n: set_branches(t)),
            ("branches [main, release]", lambda t, n: set_branches(t, "branches: [main, release]")),
            ("branches ['**']", lambda t, n: set_branches(t, "branches:", "  - '**'")),
            ("branches [develop]", lambda t, n: set_branches(t, "branches: [develop]")),
            ("branches-ignore instead", lambda t, n: set_branches(t, "branches-ignore: [main]")),
            ("branches-ignore added", pr_key_add("branches-ignore: [develop]")),
            ("paths added", pr_key_add("paths: [src/**]")),
            ("paths-ignore added", pr_key_add("paths-ignore: [docs/**]")),
            ("edited dropped", types_drop),
            ("labeled added", types_add),
            ("types removed", lambda t, n: del_line(t, r"^\s*types:")),
        ])

    def test_trigger_checks_still_work(self):
        self.assert_catches(check_trigger, [
            ("pull_request_target", lambda t, n: replace_on(t, "pull_request_target")),
            ("push added", lambda t, n: add_trigger(t, "push")),
            ("inputs named", pr_key_add("inputs: [x]")),
        ])


class TestFU9AC4ReadmeSharedCheckRuns(unittest.TestCase):
    """FU9 AC4: the README section on shared check runs explains the branch filter."""

    HEADING = "Known design limit: shared check runs"

    @classmethod
    def problems(cls, text):
        m = re.search(rf"(?ms)^## {re.escape(cls.HEADING)}[ \t]*\n(.*?)(?=^## |\Z)", text)
        if not m:
            return [f"no README section '## {cls.HEADING}'"]
        sec, p = m.group(1), []
        for tok in ("branches", "main", "ruleset"):
            if not re.search(rf"(?<![\w-]){tok}(?![\w-])", sec):
                p.append(f"the section does not name {tok}")
        paras = re.split(r"\n\s*\n", sec)
        if not any(re.search(r"(?i)\breplace\b", s) and re.search(r"\bmain\b", s)
                   and re.search(r"(?i)\bdefault branch\b", s) and re.search(r"(?i)\ball four\b", s) for s in paras):
            p.append("no paragraph says to replace main by the default branch in all four files")
        if not any(re.search(r"(?i)\brequired checks?\b", s) and re.search(r"(?i)\bpending\b", s)
                   and re.search(r"(?i)\b(?:other|another)\b[^.]*\bbranch|\bnot in the list\b", s) for s in paras):
            p.append("no paragraph says a required check stays pending for a PR against another base branch")
        if not any(re.search(r"(?i)\bpush(?:es)? (?:a |the )?base branch", s) and re.search(r"\bruleset\b", s)
                   for s in paras):
            p.append("no paragraph says the base-branch-pusher variant stays and a ruleset is the full fix")
        if re.search(r"(?i)forbid a filter", sec):
            p.append("the section still says the template tests forbid a filter")
        return p

    def test_readme(self):
        self.assertTrue(README.is_file(), f"README missing: {README}")
        self.assertEqual(self.problems(README.read_text()), [])

    def test_checker_self_test(self):
        replace = ("The four workflows have `branches: [main]` under `pull_request:`. Replace `main` by the "
                   "repo's default branch in all four files.")
        pending = ("A pull request against a branch not in the list starts no run, so a required check stays "
                   "pending and it cannot merge (fail closed).")
        pusher = ("This closes the variant any author can use. Someone who can push a base branch can still "
                  "do it; a ruleset with required workflows pinned to a ref is the full fix.")
        ok = f"# CI\n\n## {self.HEADING}\n\nShared runs.\n\n{replace}\n\n{pending}\n\n{pusher}\n\n## Secrets\n\nNone.\n"
        self.assertEqual(self.problems(ok), [])
        for label, bad in [
            ("heading", ok.replace(f"## {self.HEADING}", "## Shared runs")),
            ("ruleset", ok.replace("a ruleset with", "a setting with")),
            ("branches", ok.replace("`branches: [main]`", "a filter")),
            ("replace statement", ok.replace(replace, "The four workflows have `branches: [main]` under `pull_request:`.")),
            ("all four", ok.replace("in all four files", "in ci.yml")),
            ("default branch", ok.replace("repo's default branch", "release branch")),
            ("pending statement", ok.replace(pending, "")),
            ("pending", ok.replace("stays pending", "fails")),
            ("other base branch", ok.replace("a branch not in the list", "main")),
            ("pusher variant", ok.replace("Someone who can push a base branch can still do it; ", "")),
            ("stale forbid note", ok.replace("Shared runs.", "Not in the templates, whose tests forbid a filter.")),
            ("statement outside the section", ok.replace(f"\n\n{replace}", "").replace("None.", replace)),
        ]:
            with self.subTest(removed=label):
                self.assertNotEqual(bad, ok)
                self.assertNotEqual(self.problems(bad), [], f"the checker missed: {label}")


class TestFU15ReadmeSpecRates2(unittest.TestCase):
    """Spec 022 AC8: the README says a spec change rates 2 in the gate."""

    def test_readme_says_a_spec_change_rates_2(self):
        paras = re.split(r"\n\s*\n|\n(?=\s*- )", README.read_text())
        self.assertTrue(
            any(re.search(r"(?i)\bspecs?\b", s) and re.search(r"(?i)\b(rates?|regression) 2\b", s)
                and re.search(r"(?i)\bgate\b", s) for s in paras),
            "no README paragraph or bullet says a spec change rates 2 in the gate",
        )


class TestFU16AC4ReadmeExtensionCase(unittest.TestCase):
    """FU16 AC4: the README says an agent file with a non-lowercase extension is a problem."""

    def test_readme_agents_drift_bullet_names_extension_case(self):
        bullets = re.split(r"\n(?=- )", README.read_text())
        self.assertTrue(
            any(re.match(r"- `agents-drift`", b) and re.search(r"(?i)\bextension\b", b)
                and re.search(r"(?i)\b(?:non-lowercase|upper[- ]?case|not lowercase)\b", b)
                and re.search(r"(?i)\bproblem\b", b) for b in bullets),
            "the agents-drift bullet does not say a non-lowercase extension is a problem",
        )


if __name__ == "__main__":
    unittest.main()
