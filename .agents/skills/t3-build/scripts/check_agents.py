#!/usr/bin/env python3
"""Check agent files against the agent table in references/models.md (spec 015-wp9-ci, section 4).

Usage: check_agents.py --root DIR [--models FILE]

Folders: for each of claude, codex, opencode, <root>/.<p>/agents or else <root>/<p>/agents.
Exit 0: every agent file matches its row. Exit 1: drift, or input over a bound. Exit 2: bad
arguments, an unreadable or malformed models.md, an unknown provider, both folder forms present,
or no provider folder at all (stdout empty, message on stderr).
Reads files only. Symlinks are problems, never followed, --root included. Each agents folder is opened
once (walked from --root, O_NOFOLLOW) and listed and read only through that descriptor.
--models: empty, a `..` component or an empty component (`//`, a trailing /) is exit 2; `.` components
are dropped. It is inside --root when a lexical ancestor is the same folder as --root (os.path.samefile,
the shallowest one counts); then no component below that ancestor may be a symlink. One outside has only
its leaf checked. File contents are never printed; every printed value goes through plain().
"""

import argparse
import errno
import os
import re
import stat
import sys
import tomllib
from pathlib import Path

MAX_AGENT_FILES = 200  # per folder
MAX_AGENT_BYTES = 64 * 1024  # per file
MAX_MODELS_BYTES = 1_000_000
FRONT_LINES = 100  # the closing --- must come within this many lines
HEADER = "| Agent file | Role | Provider | Model | Effort |"
KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")  # a top-level front-matter key
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:-]{0,127}")
SEPARATOR = re.compile(r":?-+:?")
PROVIDERS = {"Claude": "claude", "Codex": "codex", "OpenRouter": "opencode"}
EXT = {"claude": ".md", "codex": ".toml", "opencode": ".md"}
EFFORT_KEY = {"claude": "effort", "codex": "model_reasoning_effort", "opencode": "reasoningEffort"}
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class Usage(Exception):
    """Exit 2."""


def plain(value):
    """One line of plain text: every non-printable character becomes a space; cut at 200."""
    s = "".join(c if c.isprintable() else " " for c in str(value))
    return s if len(s) <= 200 else s[:200] + "..."


def open_models(path, root):
    """fd of the models file. Inside root, no component below root may be a symlink: each one is opened
    O_NOFOLLOW from its parent's fd. Outside root only the leaf is checked (runner ancestors may be links)."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    parts = path.split("/")
    absolute = path.startswith("/")
    if absolute:
        parts = parts[1:]
    if not path or "" in parts or ".." in parts:
        raise Usage("--models: empty, `..` or empty path component")
    parts = [p for p in parts if p != "."]
    if not absolute:
        parts = [p for p in os.getcwd().split("/") if p] + parts
    inside = None
    for i in range(len(parts)):  # the shallowest lexical ancestor that is the root folder
        try:
            if os.path.samefile("/" + "/".join(parts[:i]), root):
                inside = i
                break
        except OSError:
            pass
    if inside is None:
        return os.open("/" + "/".join(parts), flags)
    fd = os.open(root, DIR_FLAGS)
    try:
        *folders, leaf = parts[inside:]
        for part in folders:
            below = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = below
        return os.open(leaf, flags, dir_fd=fd)
    finally:
        os.close(fd)


def load_rows(path, root):
    """{(folder, agent): (provider, model, effort)} from the first table with HEADER; None when too large.

    Opened like an agent file: no symlink, no wait on a FIFO, a regular file only, a bounded read.
    """
    try:
        fd = open_models(path, root)
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise Usage("models file is not a regular file")
            data = f.read(MAX_MODELS_BYTES + 1)
        if len(data) > MAX_MODELS_BYTES:
            return None
        lines = data.decode("utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        raise Usage("cannot read models file")
    starts = [i for i, l in enumerate(lines) if l.strip() == HEADER]
    if not starts:
        raise Usage("models file has no agent table")
    rows = {}
    for n, line in enumerate(lines[starts[0] + 1:], starts[0] + 2):
        line = line.strip()
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line[1:-1].split("|")] if len(line) > 1 and line.endswith("|") else []
        if len(cells) != 5:
            raise Usage(f"models file line {n}: wrong number of cells")
        if all(SEPARATOR.fullmatch(c) for c in cells):
            continue
        names, _, provider, model, effort = cells
        if provider not in PROVIDERS:
            raise Usage(f"models file line {n}: unknown provider")
        model, effort = (model.split() or [""])[0], (effort.split() or [""])[0]
        if not (TOKEN.fullmatch(model) and TOKEN.fullmatch(effort)):
            raise Usage(f"models file line {n}: bad model or effort value")
        for name in names.split(","):
            name = name.strip()
            if not NAME.fullmatch(name):
                raise Usage(f"models file line {n}: bad agent name")
            rows[(PROVIDERS[provider], name)] = (provider, model, effort)
    return rows


def front_matter(text):
    """(keys, None) or (None, reason). Top-level `key: value` lines; indented lines are nested values."""
    lines = text.split("\n")
    if lines[0].rstrip("\r") != "---":
        return None, "no front matter"
    end = next((i for i in range(1, min(len(lines), FRONT_LINES + 1)) if lines[i].rstrip("\r") == "---"), None)
    if end is None:
        return None, "front matter not closed"
    keys = {}
    for line in lines[1:end]:
        line = line.rstrip("\r")
        if not line.strip() or line[0] in " \t":
            continue
        key, sep, value = line.partition(":")
        if not sep:
            return None, "line without key"
        if not KEY.fullmatch(key):
            return None, "bad key"
        if key in keys:
            return None, "duplicate key"
        keys[key] = value.strip()
    return keys, None


def parse(folder, data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, "not UTF-8"
    if folder != "codex":
        return front_matter(text)
    try:
        return tomllib.loads(text), None
    except tomllib.TOMLDecodeError:
        return None, "invalid TOML"


def find_folders(root):
    """{folder: name of the folder below root that holds agents/}; Usage when both forms exist or none at all."""
    found = {}
    for p in EXT:
        present = [d for d in ("." + p, p) if os.path.lexists(root / d / "agents")]
        if len(present) > 1:
            raise Usage(f"both .{p}/agents and {p}/agents exist")
        if present:
            found[p] = present[0]
    if not found:
        raise Usage("no agent folder under root")
    return found


def check_file(folder, dir_fd, name, rel, stem, rows, problems):
    """Checks one regular agent file, opened through its folder's fd; True when it was read and checked."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
        with os.fdopen(fd, "rb") as f:
            data = f.read(MAX_AGENT_BYTES + 1)
    except OSError:
        problems.append(f"{plain(rel)}: unparsable (unreadable)")
        return False
    if len(data) > MAX_AGENT_BYTES:
        problems.append(f"too large: {plain(rel)} over {MAX_AGENT_BYTES} bytes")
        return False
    fields, reason = parse(folder, data)
    if fields is None:
        problems.append(f"{plain(rel)}: unparsable ({reason})")
        return True
    if folder != "opencode" and fields.get("name") != stem:
        problems.append(f"{plain(rel)}: name does not match file name")
    row = rows.get((folder, stem))
    if row is None:
        problems.append(f"{plain(rel)}: no row in models.md")
        return True
    _, model, effort = row
    want = "openrouter/" + model if folder == "opencode" else model
    have = fields.get("model")
    if not (isinstance(have, str) and TOKEN.fullmatch(have)):
        problems.append(f"{plain(rel)}: unparsable model value")
    elif have != want:
        problems.append(f"{plain(rel)}: model {plain(have)} != {plain(want)}")
    have = fields.get(EFFORT_KEY[folder])
    if have is None and folder == "opencode":
        pass  # OpenCode files carry no effort unless reasoningEffort is set (spec Q1)
    elif not (isinstance(have, str) and TOKEN.fullmatch(have)):
        problems.append(f"{plain(rel)}: unparsable effort value")
    elif have != effort:
        problems.append(f"{plain(rel)}: effort {plain(have)} != {plain(effort)}")
    return True


def open_folder(root_fd, d):
    """fd of <root>/<d>/agents, each component opened O_NOFOLLOW from its parent's fd (the folder is pinned)."""
    fd = os.open(d, DIR_FLAGS, dir_fd=root_fd)
    try:
        return os.open("agents", DIR_FLAGS, dir_fd=fd)
    finally:
        os.close(fd)


def check(root, rows):
    problems, checked = [], 0
    found = find_folders(root)
    try:
        root_fd = os.open(root, DIR_FLAGS)
    except OSError:
        raise Usage("cannot read --root")
    try:
        for folder, d in found.items():
            rel_folder = f"{d}/agents"
            try:
                fd = open_folder(root_fd, d)
            except OSError as e:
                if e.errno not in (errno.ELOOP, errno.ENOTDIR):
                    raise Usage(f"cannot read {plain(rel_folder)}")
                problems.append(f"{plain(rel_folder)}: not a regular folder")
                continue
            try:
                checked += check_folder(folder, fd, rel_folder, rows, problems)
            finally:
                os.close(fd)
    finally:
        os.close(root_fd)
    return problems, checked


def check_folder(folder, fd, rel_folder, rows, problems):
    """Lists and reads the agents folder only through fd; returns the number of files checked."""
    ext, entries, checked = EXT[folder], [], 0
    try:
        with os.scandir(fd) as it:
            for e in it:
                if e.name.endswith(ext) and not e.is_dir(follow_symlinks=False):
                    entries.append(e)
                    if len(entries) > MAX_AGENT_FILES:
                        break
    except OSError:
        raise Usage(f"cannot read {plain(rel_folder)}")
    if len(entries) > MAX_AGENT_FILES:
        problems.append(f"too large: {plain(rel_folder)} over {MAX_AGENT_FILES} files")
        return 0
    present = set()
    for e in sorted(entries, key=lambda e: e.name):
        rel, stem = f"{rel_folder}/{e.name}", e.name[:-len(ext)]
        if not e.is_file(follow_symlinks=False):
            problems.append(f"{plain(rel)}: not a regular file")
            continue
        present.add(stem)
        checked += check_file(folder, fd, e.name, rel, stem, rows, problems)
    for (f, name), (provider, _, _) in rows.items():
        if f == folder and name not in present:
            problems.append(f"{name}: row in models.md ({provider}) has no file in {plain(rel_folder)}")
    return checked


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check agent files against models.md.")
    ap.add_argument("--root", required=True)
    ap.add_argument("--models", default=str(Path(__file__).resolve().parent.parent / "references" / "models.md"))
    args = ap.parse_args(argv)
    try:
        root = Path(args.root)
        if root.is_symlink():  # never followed, like every other symlink
            print(f"{plain(root)}: not a regular folder")
            return 1
        if not root.is_dir():
            raise Usage("--root is not a folder")
        rows = load_rows(args.models, root)
        if rows is None:
            print(f"too large: models file over {MAX_MODELS_BYTES}")
            return 1
        problems, checked = check(root, rows)
    except Usage as e:
        print(f"check_agents: {e}", file=sys.stderr)
        return 2
    for p in problems:
        print(p)
    if problems:
        return 1
    print(f"agents: {checked} files match models.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
