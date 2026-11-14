"""Static and behaviour checks of risk-gate.yml (spec 017 FU10, AC1 to AC6).

Run from this folder:  python3 -m unittest test_risk_gate_workflow -v

Stdlib only, no PyYAML. `parse` reads the small YAML subset the workflow uses (block mappings and
sequences, one-line flow lists, `>`/`|` block scalars, comments) into dicts, lists and strings.
Each check is `check_*(text) -> [problem]`: the real file gives none, and every check has
mutation tests that change a copy of the real text and assert the check reports it. AC4 runs a
small evaluator for the expression subset the job `if:` uses over payload fixtures, on the `if:`
text read from the file.

AC6: the workflow and README checks are skipped only in the product layout, where this file sits in a
folder named `risk-gate` (.github/risk-gate/; the workflow lives in .github/workflows/). Anywhere else
(this repository's gate/ folder) a missing risk-gate.yml or README.md is a failure.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
WORKFLOW = HERE / "risk-gate.yml"
README = HERE / "README.md"


def product_layout(folder):
    """AC6: True only in a product repo, where the gate files are copied to .github/risk-gate/."""
    return folder.name == "risk-gate" and folder.parent.name == ".github"


skip_no_workflow = unittest.skipIf(
    product_layout(HERE), "product layout (.github/risk-gate/): workflow and README live elsewhere; skipped")

PR_TYPES = ["opened", "synchronize", "reopened", "ready_for_review", "converted_to_draft", "edited"]
COMMENT_TYPES = ["created", "edited", "deleted"]
RETARGET = ("(github.event_name == 'pull_request_target' && (github.event.action != 'edited' || "
            "github.event.changes.base.ref.from != ''))")
# The spec-017 round-0 form: `!= null` coerces a branch named "0" to the number 0 and equals null.
RETARGET_NULL = RETARGET.replace("from != ''", "from != null")
ISSUE_CLAUSE = ("(github.event.issue.pull_request && github.event.comment.user.login == github.repository_owner && "
                "(startsWith(github.event.comment.body, '<!-- risk-rating sha=') || "
                "startsWith(github.event.changes.body.from || '', '<!-- risk-rating sha=')))")
WANT_IF = f"{RETARGET} || {ISSUE_CLAUSE}"
RETARGET_RE = (r"\(\s*github\.event_name\s*==\s*'pull_request_target'\s*&&\s*\(\s*github\.event\.action\s*!=\s*"
               r"'edited'\s*\|\|\s*github\.event\.changes\.base\.ref\.from\s*!=\s*''\s*\)\s*\)")
PERMISSIONS = {"contents": "read", "pull-requests": "write", "statuses": "write"}
GROUP = "risk-gate-${{ github.event.pull_request.number || github.event.issue.number }}"
RUN = "python3 .github/risk-gate/risk_gate.py"
MARK = "<!-- risk-rating sha=" + "a" * 40 + " -->\nverdict: APPROVE"


# ================================================================ reader

class ReaderError(Exception):
    pass


KEY = re.compile(r"([A-Za-z_][\w-]*):(?:\s+(.*))?")


def _value(v, ln):
    v = v.strip()
    if v[:1] in "'\"":
        if len(v) < 2 or v[-1] != v[0]:
            raise ReaderError(f"line {ln}: unsupported quoted scalar")
        return v[1:-1]
    v = re.split(r"\s#", v, maxsplit=1)[0].rstrip()
    if v.startswith("["):
        if not v.endswith("]") or re.search(r"[\[\]{}'\"]", v[1:-1]):
            raise ReaderError(f"line {ln}: flow list must be simple and on one line")
        return [x.strip() for x in v[1:-1].split(",") if x.strip()]
    if v[:1] in "{&*!|>%@`":
        raise ReaderError(f"line {ln}: unsupported value {v!r}")
    return v


def scan(text):
    """-> [(ln, dash, key_indent, key, value)]; value None when the key opens a block."""
    if "\t" in text:
        raise ReaderError("tab character")
    raw, items, i = text.split("\n"), [], 0
    while i < len(raw):
        line, ln = raw[i], i + 1
        i += 1
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        dash = s.startswith("- ")
        m = KEY.fullmatch(s[2:] if dash else s)
        if not m:
            raise ReaderError(f"line {ln}: not a 'key: value' line: {s[:40]!r}")
        kind = indent + (2 if dash else 0)
        v = (m.group(2) or "").strip()
        header = re.fullmatch(r"([|>])[-+]?(?:\s+#.*)?", v)
        if header:
            body = []
            while i < len(raw) and (not raw[i].strip() or len(raw[i]) - len(raw[i].lstrip(" ")) > kind):
                body.append(raw[i])
                i += 1
            lines = [b.strip() for b in body if b.strip()]
            # shortcut: folding joins all lines with spaces; fine for expressions, not for text with indented lines.
            value = " ".join(lines) if header.group(1) == ">" else "\n".join(lines)
        else:
            value = _value(v, ln) if v and not v.startswith("#") else None
        items.append((ln, dash, kind, m.group(1), value))
    return items


def parse(text):
    items, pos = scan(text), 0

    def child(it):
        if it[4] is not None:
            return it[4]
        if pos < len(items):
            nxt = items[pos]
            if nxt[1] and nxt[2] - 2 >= it[2]:
                return seq(nxt[2] - 2)
            if not nxt[1] and nxt[2] > it[2]:
                return mapping(nxt[2])
        return None

    def mapping(indent, d=None):
        nonlocal pos
        d = {} if d is None else d
        while pos < len(items) and items[pos][2] == indent and not items[pos][1]:
            it = items[pos]
            pos += 1
            if it[3] in d:
                raise ReaderError(f"line {it[0]}: duplicate key {it[3]!r}")
            d[it[3]] = child(it)
        return d

    def seq(dash_indent):
        nonlocal pos
        out = []
        while pos < len(items) and items[pos][1] and items[pos][2] - 2 == dash_indent:
            it = items[pos]
            pos += 1
            out.append(mapping(it[2], {it[3]: child(it)}))
        return out

    doc = mapping(0)
    if pos != len(items):
        raise ReaderError(f"line {items[pos][0]}: unexpected indentation")
    return doc


def load(text):
    try:
        return parse(text), []
    except ReaderError as e:
        return None, [f"workflow not readable: {e}"]


def walk(node, path=()):
    yield path, node
    items = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
    for k, v in items:
        yield from walk(v, path + (k,))


def gate_job(doc):
    jobs = doc.get("jobs")
    return jobs.get("gate") if isinstance(jobs, dict) and isinstance(jobs.get("gate"), dict) else {}


def steps(doc):
    return [s for s in gate_job(doc).get("steps") or [] if isinstance(s, dict)]


def norm(expr):
    s = re.sub(r"\s+", " ", expr or "").strip()
    return re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", s))


# ================================================================ expression evaluator (AC4)

class ExprError(Exception):
    pass


TOKEN = re.compile(r"\s*(?:(?P<str>'(?:[^']|'')*')|(?P<op>==|!=|&&|\|\||!|\(|\)|,)"
                   r"|(?P<word>[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)*))")


def truthy(v):
    if v is None or v is False or v == "":
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v == v and v != 0
    return True


def _num(v):
    if v is None or isinstance(v, bool):
        return float(bool(v))
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v) if v.strip() else 0.0
        except ValueError:
            return float("nan")
    return float("nan")


def equal(a, b):
    """Actions rules: two strings compare as strings, case-insensitively; otherwise null, booleans and
    strings coerce to numbers (null 0, false 0, true 1, '' 0, numeric text its value, other text NaN),
    so null == '' and null == '0'; objects and arrays equal only themselves."""
    if isinstance(a, str) and isinstance(b, str):
        return a.casefold() == b.casefold()
    if isinstance(a, (dict, list)) or isinstance(b, (dict, list)):
        return a is b
    return _num(a) == _num(b)


def _text(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return v
    raise ExprError(f"cannot convert {type(v).__name__} to a string")


class Expr:
    """`||`, `&&`, `==`/`!=`, `!`, parentheses, dotted context lookups, string literals, null,
    true/false, and startsWith(a, b). Anything else raises ExprError."""

    def __init__(self, src, ctx):
        src = src.strip()
        m = re.fullmatch(r"\$\{\{(.*)\}\}", src, re.S)
        src = (m.group(1) if m else src).strip()
        self.toks, pos = [], 0
        while pos < len(src):
            t = TOKEN.match(src, pos)
            if not t:
                raise ExprError(f"unsupported text {src[pos:pos + 20]!r}")
            self.toks.append((t.lastgroup, t.group(t.lastgroup)))
            pos = t.end()
            while pos < len(src) and src[pos].isspace():
                pos += 1
        self.i, self.ctx = 0, ctx

    def peek(self):
        return self.toks[self.i][1] if self.i < len(self.toks) else None

    def take(self, want=None):
        if self.i >= len(self.toks) or (want is not None and self.toks[self.i][1] != want):
            raise ExprError(f"expected {want or 'a token'} at token {self.i}")
        self.i += 1
        return self.toks[self.i - 1]

    def run(self):
        v = self.or_()
        if self.i != len(self.toks):
            raise ExprError(f"trailing token {self.peek()!r}")
        return v

    def or_(self):
        v = self.and_()
        while self.peek() == "||":
            self.take()
            r = self.and_()
            v = v if truthy(v) else r
        return v

    def and_(self):
        v = self.cmp()
        while self.peek() == "&&":
            self.take()
            r = self.cmp()
            v = r if truthy(v) else v
        return v

    def cmp(self):
        v = self.unary()
        while self.peek() in ("==", "!="):
            op = self.take()[1]
            v = equal(v, self.unary()) == (op == "==")
        return v

    def unary(self):
        if self.peek() == "!":
            self.take()
            return not truthy(self.unary())
        return self.atom()

    def atom(self):
        kind, val = self.take()
        if kind == "str":
            return val[1:-1].replace("''", "'")
        if val == "(":
            v = self.or_()
            self.take(")")
            return v
        if kind != "word":
            raise ExprError(f"unexpected {val!r}")
        if val == "null":
            return None
        if val in ("true", "false"):
            return val == "true"
        if self.peek() == "(":
            self.take("(")
            args = [self.or_()]
            while self.peek() == ",":
                self.take()
                args.append(self.or_())
            self.take(")")
            if val != "startsWith" or len(args) != 2:
                raise ExprError(f"unsupported function {val}/{len(args)}")
            return _text(args[0]).casefold().startswith(_text(args[1]).casefold())
        root, *rest = val.split(".")
        if root not in self.ctx:
            raise ExprError(f"unknown context {root!r}")
        node = self.ctx[root]
        for part in rest:
            node = node.get(part) if isinstance(node, dict) else None
        return node


def evaluate(src, ctx):
    return Expr(src, ctx).run()


# ---------------------------------------------------------------- payload fixtures

def ctx(event_name, payload):
    return {"github": {"event_name": event_name, "event": payload, "repository_owner": "owner",
                       "repository": "owner/repo"}}


def pr(action, changes=None):
    p = {"action": action, "number": 7, "pull_request": {"number": 7, "draft": False}}
    if changes is not None:
        p["changes"] = changes
    return p


def comment(action, login="owner", body=MARK, old=None, on_pr=True):
    issue = {"number": 7, **({"pull_request": {"url": "https://api.github.com/x/pulls/7"}} if on_pr else {})}
    p = {"action": action, "issue": issue, "comment": {"user": {"login": login}, "body": body}}
    if old is not None:
        p["changes"] = {"body": {"from": old}}
    return p


def base_change(ref):
    return {"base": {"ref": {"from": ref}, "sha": {"from": "b" * 40}}}


BASE_CHANGE = base_change("main")
# (label, event, payload, job starts, condition alone; None = no expectation for the condition alone)
FIXTURES = [(f"lifecycle:{a}", "pull_request_target", pr(a), True, True)
            for a in ("opened", "synchronize", "reopened", "ready_for_review", "converted_to_draft")] + [
    ("retarget:base changed from main", "pull_request_target", pr("edited", BASE_CHANGE), True, True),
    ("retarget:base changed from a branch named 0", "pull_request_target", pr("edited", base_change("0")), True, True),
    ("retarget:base changed with title", "pull_request_target",
     pr("edited", {**BASE_CHANGE, "title": {"from": "old"}}), True, True),
    ("edit:title only", "pull_request_target", pr("edited", {"title": {"from": "old"}}), False, False),
    ("edit:body only", "pull_request_target", pr("edited", {"body": {"from": "old"}}), False, False),
    ("edit:title and body", "pull_request_target",
     pr("edited", {"title": {"from": "a"}, "body": {"from": "b"}}), False, False),
    ("edit:no changes", "pull_request_target", pr("edited"), False, False),
    ("other:labeled", "pull_request_target", pr("labeled"), False, None),
    ("comment:owner marker created", "issue_comment", comment("created"), True, True),
    ("comment:owner marker deleted", "issue_comment", comment("deleted"), True, True),
    ("comment:owner edited away from marker", "issue_comment", comment("edited", body="hi", old=MARK), True, True),
    ("comment:stranger marker", "issue_comment", comment("created", login="stranger"), False, False),
    ("comment:owner no marker", "issue_comment", comment("created", body="looks good"), False, False),
    ("comment:owner marker not first", "issue_comment", comment("created", body="x\n" + MARK), False, False),
    ("comment:owner marker on an issue", "issue_comment", comment("created", on_pr=False), False, False),
]


def behaviour(doc, event, payload):
    """-> (job starts, condition value). The event must be listed and its action in `types`."""
    on = doc.get("on") if isinstance(doc.get("on"), dict) else {}
    trig = on.get(event)
    types = trig.get("types") if isinstance(trig, dict) else None
    if event in on and not isinstance(types, list):
        raise ExprError(f"on.{event} has no types list")
    cond = truthy(evaluate(gate_job(doc).get("if") or "", ctx(event, payload)))
    return event in on and payload["action"] in types and cond, cond


# ================================================================ checks

def check_triggers(text):  # AC1
    doc, p = load(text)
    if p:
        return p
    on = doc.get("on")
    if not isinstance(on, dict) or set(on) != {"pull_request_target", "issue_comment"}:
        return [f"on: is {on!r}, not exactly pull_request_target and issue_comment"]
    for event, want in (("pull_request_target", PR_TYPES), ("issue_comment", COMMENT_TYPES)):
        trig = on[event]
        if not isinstance(trig, dict) or set(trig) != {"types"}:
            p.append(f"on.{event} is {trig!r}, not only a types list (no branch or path filter)")
            continue
        types = trig["types"]
        if not isinstance(types, list) or sorted(types) != sorted(want):
            p.append(f"on.{event}.types is {types!r}, not exactly {want}")
    return p


def check_condition(text):  # AC2
    doc, p = load(text)
    if p:
        return p
    got = norm(gate_job(doc).get("if"))
    if got != norm(WANT_IF):
        if norm(RETARGET) not in got:
            p.append("job if: lacks the retarget condition (edited only when the base branch changed)")
        if norm(ISSUE_CLAUSE) not in got:
            p.append("job if: lacks the unchanged issue_comment owner-and-marker clause")
        p.append(f"job if: is {got!r}, not exactly the retarget condition || the issue_comment clause")
    return p


def check_permissions(text):  # AC3
    doc, p = load(text)
    if p:
        return p
    if doc.get("permissions") != PERMISSIONS:
        p.append(f"top-level permissions is {doc.get('permissions')!r}, not exactly {PERMISSIONS}")
    for jid, job in (doc.get("jobs") or {}).items():
        if isinstance(job, dict) and "permissions" in job:
            p.append(f"job {jid} sets permissions")
    return p


def check_concurrency(text):  # AC3
    doc, p = load(text)
    if p:
        return p
    if "concurrency" in doc:
        p.append("top-level concurrency: unrelated comments could cancel a running gate")
    c = gate_job(doc).get("concurrency")
    if not isinstance(c, dict):
        return p + ["job gate has no concurrency mapping"]
    if norm(c.get("group")) != GROUP:
        p.append(f"concurrency group {c.get('group')!r} is not {GROUP}")
    if c.get("cancel-in-progress") != "true":
        p.append("cancel-in-progress is not true")
    return p


def check_checkout(text):  # AC3
    doc, p = load(text)
    if p:
        return p
    cos = [s for s in steps(doc) if str(s.get("uses", "")).startswith("actions/checkout@")]
    if not cos:
        return ["no actions/checkout step"]
    for s in cos:
        w = s.get("with") if isinstance(s.get("with"), dict) else {}
        if w.get("persist-credentials") != "false":
            p.append("checkout without persist-credentials: false")
        for bad in ("ref", "repository"):
            if bad in w:
                p.append(f"checkout sets {bad}: (must stay on the base branch, never the PR's code)")
    return p


def check_run(text):  # AC3, superseded by spec 021 AC5: two run: steps, the gate command first
    doc, p = load(text)
    if p:
        return p
    runs = [v for path, v in walk(doc) if path and path[-1] == "run"]
    if len(runs) != 2 or runs[0] != RUN:
        p.append(f"run: steps are {runs!r}, not exactly two with {RUN!r} first")
    return p


def check_expressions(text):  # AC3
    doc, p = load(text)
    if p:
        return p
    for path, v in walk(doc):
        allowed = "env" in path or "concurrency" in path or (path and path[-1] == "if")
        if isinstance(v, str) and "${{" in v and not allowed:
            p.append(f"${{{{ at {'.'.join(map(str, path))}: only env:, concurrency and if: may hold expressions")
    return p


def check_secrets(text):  # AC3
    doc, p = load(text)
    if p:
        return p
    for m in re.finditer(r"(?i)\bsecrets\b(?!\.RISK_GATE_TOKEN\b)", text):
        p.append(f"secrets used other than secrets.RISK_GATE_TOKEN: {text[m.start():m.start() + 30]!r}")
    return p


def check_behaviour(text):  # AC4
    doc, p = load(text)
    if p:
        return p
    for label, event, payload, want_start, want_cond in FIXTURES:
        try:
            start, cond = behaviour(doc, event, payload)
        except ExprError as e:
            return [f"job if: cannot be evaluated: {e}"]
        if start != want_start:
            p.append(f"{label}: job {'starts' if start else 'does not start'}")
        if want_cond is not None and cond != want_cond:
            p.append(f"{label}: condition alone is {cond}, want {want_cond}")
    return p


def readme_problems(text):  # AC5
    paras = [re.sub(r"\s+", " ", x) for x in re.split(r"\n\s*\n", text)]
    p = []
    if not any(re.search(r"(?i)\bretarget|\bbase branch", x) and re.search(r"(?i)\bre-?runs?\b|\bruns? again\b", x)
               for x in paras):
        p.append("README does not say the gate re-runs when the base branch of a PR changes (retarget)")
    if not any(re.search(r"(?i)\btitle\b", x) and re.search(r"(?i)\bbody\b", x)
               and re.search(r"(?i)\b(?:not|no|never)\b|n't\b", x) for x in paras):
        p.append("README does not say that title or body edits do not start the gate")
    if not any(re.search(r"(?i)\bauto-?merge", x) and re.search(r"(?i)\bretarget", x)
               and re.search(r"(?i)\b(?:disable|turn off|disarm)", x) and re.search(r"(?i)\bbefore\b", x)
               for x in paras):
        p.append("README does not warn to disable auto-merge before retargeting a PR")
    return p


# ================================================================ tests

def sub1(text, pattern, repl, flags=re.M):
    new, n = re.subn(pattern, repl, text, count=1, flags=flags)
    if not n:
        raise AssertionError(f"mutation anchor {pattern!r} not found in risk-gate.yml")
    return new


def types_list(event, fn):
    """Mutation: rewrite the flow list `types: [...]` of `event`."""
    def mutate(t):
        pat = rf"(^  {event}:[^\n]*\n(?:[ ]*#[^\n]*\n)*[ ]+types:[ ]*\[)([^\]]*)(\])"
        return sub1(t, pat, lambda m: m.group(1) + ", ".join(fn([x.strip() for x in m.group(2).split(",")]))
                    + m.group(3))
    return mutate


def after(anchor, *lines):
    """Mutation: insert lines (at the anchor line's indent plus their own) after the first anchor line."""
    def mutate(t):
        m = re.search(rf"(?m)^( *)(?:- )?{anchor}[^\n]*\n", t)
        if not m:
            raise AssertionError(f"mutation anchor {anchor!r} not found in risk-gate.yml")
        pad = m.group(1) + ("  " if t[m.start():].lstrip(" ").startswith("- ") else "")
        return t[:m.end()] + "".join(pad + l + "\n" for l in lines) + t[m.end():]
    return mutate


def sub(pattern, repl):
    return lambda t: sub1(t, pattern, repl)


class MutationCase(unittest.TestCase):
    def assert_catches(self, check, mutations):
        text = WORKFLOW.read_text()
        self.assertEqual(check(text), [], f"{check.__name__} fails on the real risk-gate.yml")
        for label, fn in mutations:
            with self.subTest(mutation=label):
                bad = fn(text)
                self.assertNotEqual(bad, text, "the mutation changed nothing")
                self.assertEqual(load(bad)[1], [], "the mutation left the readable subset")
                self.assertNotEqual(check(bad), [], f"{check.__name__} missed: {label}")


class TestReader(unittest.TestCase):
    def test_reads_the_subset(self):
        text = ("# c\non:\n  a:\n    types: [x, y]  # t\njobs:\n  j:\n    if: >-\n      p ||\n       (q &&\n"
                "       # not a comment\n        r)\n    steps:\n      - uses: u@v1   # c\n        with:\n"
                "          k: 'v'\n      - run: |\n          one\n          two\n")
        self.assertEqual(parse(text), {
            "on": {"a": {"types": ["x", "y"]}},
            "jobs": {"j": {"if": "p || (q && # not a comment r)",
                           "steps": [{"uses": "u@v1", "with": {"k": "v"}}, {"run": "one\ntwo"}]}}})

    def test_rejects_unsupported(self):
        for text in ("a:\n\tb: c\n", "a: {b: c}\n", "a: &x b\n", "a:\n  - b\n", "a: 1\na: 2\n",
                     "a:\n    b: 1\n  c: 2\n", "a: [b, [c]]\n"):
            with self.subTest(text=text):
                self.assertNotEqual(load(text)[1], [])


class TestEvaluator(unittest.TestCase):
    C = {"github": {"s": "Main", "e": "", "n": None, "o": {"k": "v"}, "t": True, "f": False, "z": "0", "one": "1"}}

    def test_coercion(self):
        """Actions == / != rules (AC4)."""
        for src, want in [
            ("github.z == null", True), ("github.z != null", False), ("github.z != ''", True),
            ("github.z == '0'", True), ("github.z == '00'", False), ("github.n == ''", True),
            ("github.n != ''", False), ("github.missing != ''", False), ("github.s != ''", True),
            ("github.s == null", False), ("github.f == null", True), ("github.f == ''", True),
            ("github.t == null", False), ("github.t == github.one", True), ("github.f == github.z", True),
            ("'MAIN' == github.s", True), ("github.o == github.o", True), ("github.o == null", False),
        ]:
            with self.subTest(src=src):
                self.assertEqual(evaluate(src, self.C), want)

    def test_null_form_misses_branch_named_0(self):
        """Proof the fix matters: the old `!= null` form does not start the gate for a retarget away from `0`."""
        c = ctx("pull_request_target", pr("edited", base_change("0")))
        self.assertFalse(truthy(evaluate(RETARGET_NULL, c)))
        self.assertTrue(truthy(evaluate(RETARGET, c)))
        main = ctx("pull_request_target", pr("edited", BASE_CHANGE))
        self.assertTrue(truthy(evaluate(RETARGET_NULL, main)))

    def test_operators(self):
        for src, want in [
            ("github.s == 'main'", True), ("github.s != 'main'", False), ("'it''s' == 'it''s'", True),
            ("github.missing.deep == null", True), ("github.n == null", True), ("github.s != null", True),
            ("github.e == null", True), ("github.o != null", True), ("github.t == true", True),
            ("github.n || 'x'", "x"), ("github.s || 'x'", "Main"), ("github.e && 'x'", ""),
            ("github.s && 'x'", "x"), ("'a' == 'b' || 'c' == 'c' && 'd' == 'd'", True),
            ("('a' == 'b' || 'c' == 'c') && 'd' == 'e'", False), ("!github.n", True), ("!github.o", False),
            ("startsWith(github.s, 'MA')", True), ("startsWith(github.n || '', 'x')", False),
            ("${{ github.s == 'main' }}", True),
        ]:
            with self.subTest(src=src):
                self.assertEqual(evaluate(src, self.C), want)

    def test_rejects_unsupported(self):
        for src in ("github.s > 'a'", "contains(github.s, 'a')", "other.x", "(github.s", "github.s ==",
                    "github.s 'a'", "github['s']", "1 == 1", '"a" == "a"'):
            with self.subTest(src=src):
                with self.assertRaises(ExprError):
                    evaluate(src, self.C)


@skip_no_workflow
class TestAC1Triggers(MutationCase):
    def test_types_exact(self):
        self.assertEqual(check_triggers(WORKFLOW.read_text()), [])

    def test_types_mutations(self):
        self.assert_catches(check_triggers, [
            ("edited dropped", types_list("pull_request_target", lambda xs: [x for x in xs if x != "edited"])),
            ("seventh type labeled", types_list("pull_request_target", lambda xs: xs + ["labeled"])),
            ("synchronize dropped", types_list("pull_request_target", lambda xs: [x for x in xs if x != "synchronize"])),
            ("issue_comment deleted dropped", types_list("issue_comment", lambda xs: [x for x in xs if x != "deleted"])),
            ("branch filter", after("pull_request_target:", "  branches: [main]")),
            ("extra event", after("on:", "  workflow_dispatch:")),
        ])


@skip_no_workflow
class TestAC2Condition(MutationCase):
    def test_condition_exact(self):
        self.assertEqual(check_condition(WORKFLOW.read_text()), [])

    def test_condition_mutations(self):
        self.assert_catches(check_condition, [
            ("plain match without the edited guard", sub(RETARGET_RE, "github.event_name == 'pull_request_target'")),
            ("plain match or'd beside the guard",
             sub(RETARGET_RE, lambda m: m.group(0) + " || github.event_name == 'pull_request_target'")),
            ("guard on title", sub(r"changes\.base\.ref\.from", "changes.title.from")),
            ("null instead of ''", sub(r"from\s*!=\s*''", "from != null")),
            ("guard on another action", sub(r"action\s*!=\s*'edited'", "action != 'labeled'")),
            ("owner check dropped", sub(r"github\.event\.comment\.user\.login == github\.repository_owner &&", "")),
            ("old-body marker clause dropped", sub(r"github\.event\.changes\.body\.from \|\| ''", "github.event.comment.body")),
            ("marker shortened", sub(r"'<!-- risk-rating sha='", "'<!-- risk-rating'")),
        ])


@skip_no_workflow
class TestAC3Unchanged(MutationCase):
    def test_permissions(self):
        self.assert_catches(check_permissions, [
            ("contents write", sub(r"^  contents: read", "  contents: write")),
            ("extra permission", after("statuses: write", "actions: write")),
            ("write-all", sub(r"^permissions:\n(?:  [^\n]*\n)+", "permissions: write-all\n")),
            ("job-level permissions", after("runs-on:", "permissions:", "  contents: write")),
        ])

    def test_concurrency(self):
        self.assert_catches(check_concurrency, [
            ("group not per PR", sub(r"group: risk-gate-[^\n]*", "group: risk-gate")),
            ("group on issue number only", sub(r"github\.event\.pull_request\.number \|\| (?=github\.event\.issue)", "")),
            ("cancel false", sub(r"cancel-in-progress: true", "cancel-in-progress: false")),
            ("cancel dropped", sub(r"^ *cancel-in-progress: true\n", "")),
            ("top-level concurrency", sub(r"^jobs:", "concurrency:\n  group: x\njobs:")),
        ])

    def test_checkout_base_only(self):
        self.assert_catches(check_checkout, [
            ("persist-credentials true", sub(r"persist-credentials: false", "persist-credentials: true")),
            ("with dropped", sub(r"^ *with:\n *persist-credentials: false\n", "")),
            ("ref to the PR head", after("persist-credentials:", "ref: ${{ github.event.pull_request.head.sha }}")),
            ("repository of the fork",
             after("persist-credentials:", "repository: ${{ github.event.pull_request.head.repo.full_name }}")),
            ("second checkout of the PR",
             sub(r"^( *)steps:\n", r"\g<0>\1  - uses: actions/checkout@v4\n\1    with:\n\1      persist-credentials: false\n"
                 r"\1      ref: refs/pull/7/head\n")),
        ])

    def test_single_run(self):
        self.assert_catches(check_run, [
            ("other command", sub(r"run: python3 \.github/risk-gate/risk_gate\.py", "run: python3 risk_gate.py")),
            ("chained command", sub(r"risk_gate\.py$", "risk_gate.py && ./ci.sh")),
            ("literal block", sub(r"^( *)run: python3 \.github/risk-gate/risk_gate\.py\n",
                                  r"\1run: |\n\1  python3 .github/risk-gate/risk_gate.py\n\1  make test\n")),
            ("second run step", sub(r"^( *)steps:\n", r"\g<0>\1  - run: make test\n")),
        ])

    def test_expressions_only_in_env_concurrency_if(self):
        self.assert_catches(check_expressions, [
            ("in run", sub(r"risk_gate\.py$", "risk_gate.py ${{ github.event.pull_request.title }}")),
            ("in step name", sub(r"name: Rate and route the PR", "name: Rate ${{ github.event.pull_request.title }}")),
            ("in runs-on", sub(r"runs-on: ubuntu-latest", "runs-on: ${{ github.event.pull_request.title }}")),
            ("in with", after("persist-credentials:", "path: ${{ github.event.pull_request.head.ref }}")),
        ])

    def test_secrets_only_risk_gate_token(self):
        self.assert_catches(check_secrets, [
            ("other secret", after("GH_TOKEN:", "OTHER: ${{ secrets.OTHER_TOKEN }}")),
            ("GITHUB_TOKEN secret", sub(r"secrets\.RISK_GATE_TOKEN", "secrets.GITHUB_TOKEN")),
            ("all secrets", after("GH_TOKEN:", "ALL: ${{ toJSON(secrets) }}")),
            ("index syntax", after("GH_TOKEN:", "X: ${{ secrets['DEPLOY_KEY'] }}")),
        ])


@skip_no_workflow
class TestAC4Behaviour(MutationCase):
    def rows(self, prefix):
        doc = parse(WORKFLOW.read_text())
        for label, event, payload, want_start, want_cond in FIXTURES:
            if label.startswith(prefix):
                with self.subTest(fixture=label):
                    start, cond = behaviour(doc, event, payload)
                    self.assertEqual(start, want_start, f"{label}: job starts")
                    if want_cond is not None:
                        self.assertEqual(cond, want_cond, f"{label}: condition alone")

    def test_lifecycle_types_start(self):
        self.rows("lifecycle:")

    def test_retarget_starts(self):
        self.rows("retarget:")

    def test_title_or_body_edit_does_not_start(self):
        self.rows("edit:")

    def test_other_types_do_not_start(self):
        self.rows("other:")

    def test_issue_comment_owner_marker(self):
        self.rows("comment:")

    def test_behaviour_mutations(self):
        self.assert_catches(check_behaviour, [
            ("plain match without the edited guard", sub(RETARGET_RE, "github.event_name == 'pull_request_target'")),
            ("guard on title", sub(r"changes\.base\.ref\.from", "changes.title.from")),
            ("guard && turned into ||", sub(r"'pull_request_target'\s*&&", "'pull_request_target' ||")),
            ("from != '' turned into == ''", sub(r"from\s*!=\s*''", "from == ''")),
            ("null instead of ''", sub(r"from\s*!=\s*''", "from != null")),
            ("owner check dropped", sub(r"github\.event\.comment\.user\.login == github\.repository_owner &&", "")),
            ("edited dropped from types", types_list("pull_request_target", lambda xs: [x for x in xs if x != "edited"])),
        ])


@skip_no_workflow
class TestAC5Readme(unittest.TestCase):
    def test_readme_states_retarget_rerun(self):
        self.assertEqual(readme_problems(README.read_text()), [])

    def test_readme_check_self_test(self):
        good = ("The gate re-runs when a PR is retargeted to another base branch.\n\n"
                "An `edited` event that only changes the title or body does not start the gate.\n\n"
                "A retarget keeps the old success until the new run writes pending, so an armed auto-merge can "
                "race it: disable auto-merge before you retarget.\n")
        paras = good.split("\n\n")
        self.assertEqual(readme_problems(good), [])
        self.assertEqual(len(readme_problems("")), 3)
        for i in range(3):
            with self.subTest(only_paragraph=i):
                self.assertEqual(len(readme_problems(paras[i])), 2)
        # each statement must sit in one paragraph
        self.assertEqual(len(readme_problems(
            "Retargeted.\n\nIt re-runs.\n\nTitle.\n\nBody, not.\n\nAuto-merge.\n\nRetarget.\n\nDisable it before.\n")), 3)
        for word in ("auto-merge", "retarget", "disable", "before"):
            with self.subTest(warning_without=word):
                warning = re.sub(re.escape(word), "x", paras[2], flags=re.I)
                self.assertEqual(readme_problems("\n\n".join(paras[:2] + [warning])),
                                 ["README does not warn to disable auto-merge before retargeting a PR"])


class TestAC6Layout(unittest.TestCase):
    def test_skip_decision(self):
        self.assertTrue(product_layout(Path("/repo/.github/risk-gate")))
        for folder in ("/repo/.agents/skills/t3-build/gate", "/repo/.github/workflows", "/repo/risk-gate-old",
                       "/repo/risk-gate/sub", "/repo/Risk-Gate", "/repo/risk-gate", "/repo/x/risk-gate"):
            with self.subTest(folder=folder):
                self.assertFalse(product_layout(Path(folder)))

    @skip_no_workflow
    def test_files_present_outside_product_layout(self):
        self.assertTrue(WORKFLOW.is_file(), f"{WORKFLOW} missing: not the product layout, so this is a failure")
        self.assertTrue(README.is_file(), f"{README} missing: not the product layout, so this is a failure")


# ================================================================ spec 021 FU14 (AC5, AC6, AC7)

HEAD = "${{ github.event.pull_request.head.sha }}"
FAILURE_ENV = {"GH_TOKEN": "${{ secrets.RISK_GATE_TOKEN }}", "REPO": "${{ github.repository }}", "HEAD_SHA": HEAD}
FU14_HEX = ("0123456789abcdef" * 3)[:40]


def gate_and_rest(doc):
    """-> (the gate step or None, the steps after it)."""
    st = steps(doc)
    gate = [s for s in st if s.get("run") == RUN]
    return (gate[0], st[st.index(gate[0]) + 1:]) if len(gate) == 1 else (None, [])


def check_failure_step(text):  # spec 021 AC5
    doc, p = load(text)
    if p:
        return p
    gate, rest = gate_and_rest(doc)
    if gate is None:
        return [f"not exactly one step runs {RUN!r}"]
    genv = gate.get("env") if isinstance(gate.get("env"), dict) else {}
    if genv.get("HEAD_SHA") != HEAD:
        p.append(f"gate step env HEAD_SHA is {genv.get('HEAD_SHA')!r}, not {HEAD!r}")
    if len(rest) != 1:
        return p + [f"{len(rest)} steps after the gate step, not exactly one (the failure step)"]
    s = rest[0]
    if norm(s.get("if")) != "failure()":
        p.append(f"failure step if: is {s.get('if')!r}, not failure()")
    if s.get("env") != FAILURE_ENV:
        p.append(f"failure step env is {s.get('env')!r}, not exactly {FAILURE_ENV}")
    run = s.get("run")
    if not isinstance(run, str) or not run.strip():
        p.append("failure step has no run:")
    elif "${{" in run:
        p.append("failure step run: holds ${{ (values must come through env:)")
    return p


def fu14_readme_problems(text):  # spec 021 AC7
    paras = [re.sub(r"\s+", " ", x) for x in re.split(r"\n\s*\n", text)]

    def has(*pats):
        return any(all(re.search(pat, x) for pat in pats) for x in paras)

    p = []
    if not has(r"HEAD_SHA", r"(?i)\bpending\b", r"(?i)\bbefore\b"):
        p.append("README does not say the gate writes pending for HEAD_SHA before its first gh call")
    if not has(r"failure\(\)", r"(?i)\bpending\b", r"HEAD_SHA"):
        p.append("README does not describe the if: failure() step that writes pending for HEAD_SHA")
    if not has(r"issue_comment", r"(?i)head SHA|HEAD_SHA", r"(?i)\b(?:no|not|empty|without|nothing)\b"):
        p.append("README does not say issue_comment runs have no payload head SHA")
    if not has(r"(?i)\bAPI\b", r"(?i)\b(?:unavailable|down|outage|unreachable)\b",
               r"(?i)\b(?:neither|either|no status|cannot|can't)\b"):
        p.append("README does not say an unavailable GitHub API leaves no status from either mechanism")
    if not has(r"(?i)\bearly\b|\bbefore\b", r"(?i)\b(?:stays|remains|is left) pending\b",
               r"(?i)\b(?:intended|by design|on purpose|deliberate)"):
        p.append("README does not say an early pending can precede a run that stops, and intentionally stays pending")
    return p


def last_step_start(t):
    """Offset of the last item of the gate job's steps list in the workflow text."""
    m = re.search(r"(?m)^( *)steps:\n(?:[ ]*#[^\n]*\n)*( *)- ", t)
    if not m:
        raise AssertionError("mutation anchor steps: not found in risk-gate.yml")
    return list(re.finditer(rf"(?m)^{m.group(2)}- ", t))[-1].start()


def in_last_step(pattern, repl):
    """Mutation: substitute once inside the last step only."""
    def mutate(t):
        i = last_step_start(t)
        return t[:i] + sub1(t[i:], pattern, repl)
    return mutate


def extra_step(t):
    pad = re.search(r"(?m)^( *)steps:\n(?:[ ]*#[^\n]*\n)*( *)- ", t).group(2)
    return t.rstrip("\n") + f"\n{pad}- uses: actions/cache@v4\n"


HEAD_RE = r"HEAD_SHA: \$\{\{ github\.event\.pull_request\.head\.sha \}\}"


@skip_no_workflow
class TestFU14AC5FailureStep(MutationCase):
    def test_failure_step(self):
        self.assertEqual(check_failure_step(WORKFLOW.read_text()), [])

    def test_failure_step_mutations(self):
        self.assert_catches(check_failure_step, [
            ("if: failure() removed", in_last_step(r"if: failure\(\)", "continue-on-error: false")),
            ("if: always()", in_last_step(r"failure\(\)", "always()")),
            ("if: success()", in_last_step(r"failure\(\)", "success()")),
            ("token missing", in_last_step(r"^ *GH_TOKEN:[^\n]*\n", "")),
            ("token is github.token", in_last_step(r"secrets\.RISK_GATE_TOKEN", "github.token")),
            ("REPO missing", in_last_step(r"^ *REPO:[^\n]*\n", "")),
            ("HEAD_SHA missing in the failure step", in_last_step(r"^ *HEAD_SHA:[^\n]*\n", "")),
            ("${{ in run for HEAD_SHA", in_last_step(r"\$\{?HEAD_SHA\}?", HEAD)),
            ("${{ in run for REPO", in_last_step(r"\$\{?REPO\}?", "${{ github.repository }}")),
            ("gate HEAD_SHA from github.sha", sub(HEAD_RE, "HEAD_SHA: ${{ github.sha }}")),
            ("gate HEAD_SHA from the head ref", sub(HEAD_RE, "HEAD_SHA: ${{ github.event.pull_request.head.ref }}")),
            ("gate HEAD_SHA dropped", sub(r"^ *HEAD_SHA:[^\n]*\n", "")),
            ("failure HEAD_SHA from the head ref",
             in_last_step(HEAD_RE, "HEAD_SHA: ${{ github.event.pull_request.head.ref }}")),
            ("failure step dropped", lambda t: t[:last_step_start(t)]),
            ("extra step after it", extra_step),
        ])

    def test_017_checks_hold_with_the_failure_step(self):
        text = WORKFLOW.read_text()
        for check in (check_run, check_expressions, check_secrets, check_permissions, check_concurrency,
                      check_checkout, check_triggers, check_condition, check_behaviour):
            with self.subTest(check=check.__name__):
                self.assertEqual(check(text), [])


@skip_no_workflow
@unittest.skipIf(shutil.which("bash") is None, "bash not installed")
class TestFU14AC6FailureStepRuns(unittest.TestCase):
    """The failure step's run: text, read from the real workflow, under bash with a fake gh."""

    def run_step(self, head_sha):
        _, rest = gate_and_rest(parse(WORKFLOW.read_text()))
        self.assertEqual(len(rest), 1, "no single step after the gate step")
        script = rest[0].get("run")
        self.assertIsInstance(script, str)
        with tempfile.TemporaryDirectory() as d:
            log, fake, step = Path(d, "calls"), Path(d, "gh"), Path(d, "step.sh")
            fake.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                            "with open(os.environ['GH_CALLS'], 'a') as f:\n"
                            "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n")
            fake.chmod(0o755)
            step.write_text(script + "\n")
            env = {"PATH": d + os.pathsep + os.environ.get("PATH", ""), "GH_CALLS": str(log),
                   "GH_TOKEN": "fake-token", "REPO": "owner/repo", "HEAD_SHA": head_sha}
            # GitHub's default bash for run: steps on ubuntu runners
            r = subprocess.run([shutil.which("bash"), "--noprofile", "--norc", "-eo", "pipefail", str(step)],
                               env=env, capture_output=True, text=True, timeout=30)
            calls = [json.loads(ln) for ln in log.read_text().splitlines()] if log.exists() else []
        return r, calls

    def test_valid_head_sha_writes_one_pending(self):
        for sha in (FU14_HEX, "a" * 40, "0" * 40):
            with self.subTest(head_sha=sha):
                r, calls = self.run_step(sha)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(len(calls), 1, calls)
                [args] = calls
                self.assertEqual(args[:7], ["api", f"repos/owner/repo/statuses/{sha}", "-f", "state=pending",
                                            "-f", "context=risk-gate", "-f"], args)
                self.assertEqual(len(args), 8, args)
                self.assertTrue(args[7].startswith("description="), args)

    def test_ignored_head_sha_makes_no_call(self):
        for label, value in {
            "empty": "", "39 characters": FU14_HEX[:39], "41 characters": FU14_HEX + "0",
            "upper case": FU14_HEX.upper(), "g characters": FU14_HEX[:39] + "g", "path ../x": "../x",
            "path, 40 characters": "../" + FU14_HEX[:37], "trailing newline": FU14_HEX + "\n",
            "leading newline": "\n" + FU14_HEX, "newline then text": FU14_HEX + "\nx",
            "text then newline": "x\n" + FU14_HEX, "space inside": FU14_HEX[:20] + " " + FU14_HEX[21:],
        }.items():
            with self.subTest(head_sha=label):
                r, calls = self.run_step(value)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(calls, [])


@skip_no_workflow
class TestFU14AC7Readme(unittest.TestCase):
    def test_readme_documents_early_pending_and_limits(self):
        self.assertEqual(fu14_readme_problems(README.read_text()), [])

    def test_readme_check_self_test(self):
        good = ("Before the first `gh pr view` call the gate writes `pending` for `HEAD_SHA`.\n\n"
                "A step with `if: failure()` writes `pending` for `HEAD_SHA` when the job fails.\n\n"
                "`issue_comment` runs have no head SHA in the payload.\n\n"
                "If the GitHub API itself is unavailable, no status can be written by either mechanism.\n\n"
                "The early pending can precede a run that stops for another reason; that is intended: "
                "the status stays pending.\n")
        self.assertEqual(fu14_readme_problems(good), [])
        self.assertEqual(len(fu14_readme_problems("")), 5)
        for i, para in enumerate(good.split("\n\n")):
            with self.subTest(without_paragraph=i):
                rest = good.replace(para, "Unrelated.")
                self.assertGreaterEqual(len(fu14_readme_problems(rest)), 1)


if __name__ == "__main__":
    unittest.main()
