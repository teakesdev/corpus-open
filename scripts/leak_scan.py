#!/usr/bin/env python3
"""Scan a tree, a commit or a git range for things that must not be published.

Classes found:
  home-path           an absolute home directory: macOS, Linux or Windows
  email               an email address that is not reserved, noreply or file-like
  author-email        an author or committer address in a range (--check-authors)
  session-transcript  a file named like an assistant session transcript, or text that
                      only a session transcript carries (system reminders, a pasted
                      global instructions file)
  case-pattern        a match for a pattern in a file you keep outside the repository

Modes (any mix):
  --tree [DIR]     the working tree: tracked and untracked files that are not ignored
  --rev REV        every file in one commit, without a checkout
  --range SPEC     every commit message and every line a commit adds, merge
                   resolutions included. SPEC is anything `git log` takes, such as
                   `origin/main..HEAD` or `<sha> --not --remotes=origin`

Case patterns come from --patterns FILE or the LEAK_SCAN_PATTERNS environment variable
(the flag wins). One pattern a line: a case-insensitive regular expression, or
`lit:TEXT` for a literal. `#` starts a comment. A match is reported as the pattern's
number only, never its text and never the line, so the output can go in a public log.
--redact hides matched text for the other classes too.

Silence a line of the first four classes with the comment `leak-scan: allow`. Case
patterns cannot be silenced. Skip paths with --exclude GLOB and addresses with
--allow-email GLOB.

Exit status: 0 nothing found, 1 something found, 2 bad usage or configuration.
Standard library only; Python 3.10 or later.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass

PATTERNS_ENV = "LEAK_SCAN_PATTERNS"
ALLOW_MARKER = "leak-scan: allow"
MAX_BYTES = 10 * 1024 * 1024

HOME_RE = re.compile(
    r"(?<![A-Za-z0-9_.~-])"
    r"(?:/Users/|/home/|[A-Za-z]:\\Users\\)"
    r"(?P<user>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:[/\\][^\s'\"`<>)\]},;|]*)?"
)
GENERIC_USERS = {"runner", "runneradmin", "shared", "user", "username", "you", "yourname",
                 "name", "example", "ubuntu"}

EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])"
    r"(?P<local>[A-Za-z0-9][A-Za-z0-9._%+-]*)@"
    r"(?P<domain>[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?P<tld>[A-Za-z]{2,24}))"
    r"(?![A-Za-z0-9-])"
)
RESERVED_DOMAINS = {"example.com", "example.org", "example.net", "example.edu"}
RESERVED_TLDS = {"example", "invalid", "test", "localhost"}
FILE_TLDS = {"txt", "py", "md", "json", "jsonl", "pdf", "docx", "doc", "csv", "db", "log", "sh",
             "js", "ts", "html", "htm", "yml", "yaml", "toml", "png", "jpg", "jpeg", "gif", "svg",
             "zip", "tar", "gz", "ini", "cfg", "xml", "rtf", "sql", "lock"}
NOREPLY_LOCALS = {"noreply", "no-reply", "donotreply", "do-not-reply"}
GIT_HOSTS = {"github.com", "gitlab.com", "bitbucket.org"}

SESSION_MARKERS = (
    re.compile(r"</?system-reminder>"),
    re.compile(r"user's private global\s+instructions"),
    re.compile(r"#\s*userEmail\b"),
    re.compile(r"SessionStart:\w+ hook"),
)
_AGENTS = r"(?:swe-?2|astra|claude|codex|devin|gemini|grok|agent|session|chat|reviewer|llm|assistant)"
TRANSCRIPT_NAME_RES = (
    re.compile(_AGENTS + r"[-_. ]?(?:transcript|conversation|export|history)"),
    re.compile(r"(?:transcript|conversation)[-_. ]?" + _AGENTS),
    re.compile(r"\.jsonl$"),
)

_HUNK_RE = re.compile(r"^(@{2,}) (?:-\d+(?:,\d+)? )+\+(\d+)(?:,\d+)? @{2,}")


class ConfigError(Exception):
    """Bad usage or configuration; the message never contains scanned text or pattern text."""


@dataclass(frozen=True)
class CasePattern:
    number: int
    regex: "re.Pattern[str]"


@dataclass(frozen=True)
class Finding:
    where: str
    kind: str
    detail: str
    sensitive: bool = True

    def shown(self, redact: bool = False) -> str:
        return "<redacted>" if redact and self.sensitive else self.detail

    def render(self, redact: bool = False) -> str:
        return f"{self.where}: [{self.kind}] {self.shown(redact)}"

    def as_dict(self, redact: bool = False) -> dict:
        return {"where": self.where, "kind": self.kind, "detail": self.shown(redact)}


def load_patterns(path: str) -> list[CasePattern]:
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError as exc:
        raise ConfigError(f"cannot read the patterns file ({exc.strerror or type(exc).__name__}): {path}")
    except UnicodeDecodeError:
        raise ConfigError(f"the patterns file is not UTF-8 text: {path}")
    patterns: list[CasePattern] = []
    for lineno, raw in enumerate(lines, 1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        source = re.escape(raw.rstrip()[len("lit:"):]) if text.startswith("lit:") else text
        try:
            regex = re.compile(source, re.IGNORECASE)
        except re.error:
            raise ConfigError(f"patterns file line {lineno}: not a valid regular expression") from None
        if regex.search(""):
            raise ConfigError(f"patterns file line {lineno}: matches empty text, so it would match every line")
        patterns.append(CasePattern(len(patterns) + 1, regex))
    return patterns


def _email_allowed(address: str, local: str, domain: str, tld: str, allow_emails) -> bool:
    local, domain, tld = local.lower(), domain.lower(), tld.lower()
    if domain in RESERVED_DOMAINS or tld in RESERVED_TLDS or tld in FILE_TLDS:
        return True
    if local in NOREPLY_LOCALS or domain.endswith("users.noreply.github.com"):
        return True
    if local == "git" and domain in GIT_HOSTS:
        return True
    return any(fnmatch.fnmatch(address.lower(), g.lower()) for g in allow_emails)


def scan_line(line: str, where: str, patterns=(), allow_emails=()) -> list[Finding]:
    found: list[Finding] = []
    if ALLOW_MARKER not in line:
        for m in HOME_RE.finditer(line):
            if m.group("user").lower() not in GENERIC_USERS:
                found.append(Finding(where, "home-path", m.group(0)[:120]))
        for m in EMAIL_RE.finditer(line):
            if not _email_allowed(m.group(0), m.group("local"), m.group("domain"), m.group("tld"), allow_emails):
                found.append(Finding(where, "email", m.group(0)))
        for rx in SESSION_MARKERS:
            m = rx.search(line)
            if m:
                found.append(Finding(where, "session-transcript", m.group(0), sensitive=False))
    for p in patterns:
        if p.regex.search(line):
            found.append(Finding(where, "case-pattern", f"pattern #{p.number}", sensitive=False))
    return found


def scan_text(text: str, where: str, patterns=(), allow_emails=()) -> list[Finding]:
    found: list[Finding] = []
    for lineno, line in enumerate(text.split("\n"), 1):
        found.extend(scan_line(line, f"{where}:{lineno}", patterns, allow_emails))
    return found


def mask_path(path: str, patterns=()) -> str:
    """The path with every component that matches a case pattern replaced, so a location
    can be printed without printing the identifier it contains."""
    if not any(p.regex.search(path) for p in patterns):
        return path
    parts = path.replace("\\", "/").split("/")
    masked = ["<masked>" if any(p.regex.search(c) for p in patterns) else c for c in parts]
    if masked == parts:
        return "<masked path>"
    return "/".join(masked)


def scan_path_name(path: str, patterns=(), allow_emails=(), where: str | None = None) -> list[Finding]:
    where = mask_path(path, patterns) if where is None else where
    found: list[Finding] = []
    base = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if any(rx.search(base) for rx in TRANSCRIPT_NAME_RES):
        found.append(Finding(where, "session-transcript", "file name looks like an assistant session transcript",
                             sensitive=False))
    for p in patterns:
        if p.regex.search(path):
            found.append(Finding(where, "case-pattern", f"pattern #{p.number}", sensitive=False))
    return found


def _decode(data: bytes) -> str | None:
    """Text of a blob, or None for binary data."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def _excluded(path: str, excludes) -> bool:
    posix = path.replace(os.sep, "/")
    return any(fnmatch.fnmatch(posix, g) for g in excludes)


def _scan_blob(data: bytes, path: str, prefix: str, patterns, allow_emails, notes) -> list[Finding]:
    where = prefix + mask_path(path, patterns)
    if len(data) > MAX_BYTES:
        if notes is not None:
            notes.append(f"skipped content of {where}: larger than {MAX_BYTES // (1024 * 1024)} MB")
        return []
    text = _decode(data)
    if text is None:
        return []
    return scan_text(text, where, patterns, allow_emails)


def _git(repo: str, *args: str, input: bytes | None = None) -> bytes:
    try:
        r = subprocess.run(["git", "-C", repo, "-c", "core.quotepath=false", *args],
                           input=input, capture_output=True)
    except FileNotFoundError:
        raise ConfigError("git is not installed or not on PATH") from None
    if r.returncode:
        err = r.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise ConfigError(f"git {args[0]} failed: {err[-1] if err else 'no message'}")
    return r.stdout


def _tree_files(root: str) -> list[str]:
    try:
        inside = subprocess.run(["git", "-C", root, "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True).stdout.strip() == "true"
    except FileNotFoundError:
        inside = False
    if inside:
        out = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        return sorted(set(p for p in out.decode("utf-8", errors="replace").split("\0") if p))
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        files.extend(os.path.relpath(os.path.join(dirpath, f), root) for f in sorted(filenames))
    return files


def scan_tree(root: str, patterns=(), allow_emails=(), excludes=(), notes=None) -> list[Finding]:
    if not os.path.isdir(root):
        raise ConfigError(f"not a directory: {root}")
    found: list[Finding] = []
    for rel in _tree_files(root):
        full = os.path.join(root, rel)
        if _excluded(rel, excludes) or os.path.islink(full) or not os.path.isfile(full):
            continue
        found.extend(scan_path_name(rel, patterns, allow_emails))
        with open(full, "rb") as fh:
            data = fh.read(MAX_BYTES + 1)
        found.extend(_scan_blob(data, rel, "", patterns, allow_emails, notes))
    return found


def _resolve(repo: str, rev: str) -> str:
    return _git(repo, "rev-parse", "--verify", "--quiet", rev + "^{commit}").decode().strip()


def scan_rev(repo: str, rev: str, patterns=(), allow_emails=(), excludes=(), notes=None) -> list[Finding]:
    try:
        sha = _resolve(repo, rev)
    except ConfigError:
        raise ConfigError(f"not a commit: {rev}") from None
    prefix = sha[:10] + ":"
    entries = []
    for rec in _git(repo, "ls-tree", "-r", "-z", "--full-tree", sha).split(b"\0"):
        if not rec:
            continue
        meta, _, path = rec.partition(b"\t")
        mode, kind, obj = meta.decode().split()
        if kind == "blob" and mode != "120000":
            entries.append((path.decode("utf-8", errors="replace"), obj))
    found: list[Finding] = []
    entries = [(p, o) for p, o in entries if not _excluded(p, excludes)]
    for p, _ in entries:
        found.extend(scan_path_name(p, patterns, allow_emails, where=prefix + mask_path(p, patterns)))
    stream = _git(repo, "cat-file", "--batch", input="".join(o + "\n" for _, o in entries).encode())
    pos = 0
    for path, _ in entries:
        eol = stream.index(b"\n", pos)
        header = stream[pos:eol].split()
        if len(header) != 3:
            raise ConfigError(f"git cat-file could not read a file in {rev}")
        size = int(header[2])
        data = stream[eol + 1:eol + 1 + size]
        pos = eol + 1 + size + 1
        found.extend(_scan_blob(data, path, prefix, patterns, allow_emails, notes))
    return found


def scan_range(repo: str, spec: str, patterns=(), allow_emails=(), excludes=(), check_authors=False) -> list[Finding]:
    try:
        revs = shlex.split(spec)
    except ValueError:
        raise ConfigError("the range is not valid shell-style text") from None
    if not revs:
        raise ConfigError("the range is empty")
    found: list[Finding] = []

    log = _git(repo, "log", "--no-color", "--format=%H%x1f%ae%x1f%ce%x1f%B%x1e", *revs).decode("utf-8", errors="replace")
    for rec in log.split("\x1e"):
        rec = rec.lstrip("\n")
        if not rec:
            continue
        sha, author, committer, message = rec.split("\x1f", 3)
        short = sha[:10]
        found.extend(scan_text(message, f"{short}:message", patterns, allow_emails))
        if check_authors:
            for addr in dict.fromkeys((author, committer)):
                m = EMAIL_RE.fullmatch(addr)
                if m and not _email_allowed(addr, m.group("local"), m.group("domain"), m.group("tld"), allow_emails):
                    found.append(Finding(f"{short}:author", "author-email", addr))

    diff = _git(repo, "log", "--no-color", "--no-ext-diff", "--no-renames", "--cc", "-p",
                "--format=COMMIT-MARK:%H", *revs).decode("utf-8", errors="replace")
    short = path = None
    parents = 1
    new_line = 0
    in_hunk = deleted = named = False
    skip_file = False

    def finish_file():
        if path is not None and not named and not deleted and not skip_file:
            found.extend(scan_path_name(path, patterns, allow_emails, where=f"{short}:{mask_path(path, patterns)}"))

    for line in diff.split("\n"):
        if line.startswith("COMMIT-MARK:"):
            finish_file()
            short, path, in_hunk, deleted, named, skip_file = line[12:22], None, False, False, False, False
            continue
        if line.startswith("diff "):
            finish_file()
            rest = line.split(" ", 2)[2]
            if line.startswith("diff --git "):
                half = (len(rest) - 5) // 2
                path = rest[2:2 + half] if rest.startswith("a/") and half > 0 else rest.split(" b/", 1)[-1]
            else:
                path = rest
            in_hunk, deleted, named = False, False, False
            skip_file = _excluded(path, excludes)
            continue
        if not in_hunk:
            if line.startswith("deleted file mode"):
                deleted = True
            m = _HUNK_RE.match(line)
            if m:
                if path is not None and not named and not deleted and not skip_file:
                    found.extend(scan_path_name(path, patterns, allow_emails,
                                                where=f"{short}:{mask_path(path, patterns)}"))
                named = True
                in_hunk, parents, new_line = True, len(m.group(1)) - 1, int(m.group(2))
            continue
        m = _HUNK_RE.match(line)
        if m:
            parents, new_line = len(m.group(1)) - 1, int(m.group(2))
            continue
        if line.startswith("\\"):
            continue
        prefix = line[:parents]
        if skip_file or deleted:
            continue
        if "+" in prefix:
            found.extend(scan_line(line[parents:], f"{short}:{mask_path(path, patterns)}:{new_line}",
                                   patterns, allow_emails))
            new_line += 1
        elif "-" in prefix:
            continue
        else:
            new_line += 1
    finish_file()
    return found


def _split_args(argv):
    p = argparse.ArgumentParser(
        prog="leak_scan.py",
        description="Find home paths, personal emails, session transcripts and case patterns.",
        epilog="Exit status: 0 clean, 1 found something, 2 bad usage or configuration.")
    p.add_argument("--tree", nargs="?", const=".", metavar="DIR", help="scan the working tree (default DIR: .)")
    p.add_argument("--rev", metavar="REV", help="scan every file in one commit")
    p.add_argument("--range", metavar="SPEC", help="scan commit messages and added lines for a git log range")
    p.add_argument("--repo", default=".", metavar="DIR", help="repository for --rev and --range (default: .)")
    p.add_argument("--patterns", metavar="FILE", help=f"case patterns file (else ${PATTERNS_ENV})")
    p.add_argument("--allow-email", action="append", default=[], metavar="GLOB", help="never report matching addresses")
    p.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="skip matching paths")
    p.add_argument("--check-authors", action="store_true", help="also report author and committer addresses in --range")
    p.add_argument("--redact", action="store_true", help="hide matched text (use in public CI logs)")
    p.add_argument("--format", choices=("text", "json"), default="text")
    args = p.parse_args(argv)
    if args.tree is None and args.rev is None and args.range is None:
        p.error("give at least one of --tree, --rev, --range")
    return args


def main(argv=None) -> int:
    args = _split_args(sys.argv[1:] if argv is None else argv)
    notes: list[str] = []
    try:
        patterns_path = args.patterns or os.environ.get(PATTERNS_ENV)
        patterns = load_patterns(patterns_path) if patterns_path else []
        common = dict(patterns=patterns, allow_emails=tuple(args.allow_email), excludes=tuple(args.exclude))
        found: list[Finding] = []
        if args.tree is not None:
            found += scan_tree(args.tree, notes=notes, **common)
        if args.rev is not None:
            found += scan_rev(args.repo, args.rev, notes=notes, **common)
        if args.range is not None:
            found += scan_range(args.repo, args.range, check_authors=args.check_authors, **common)
    except ConfigError as exc:
        print(f"leak_scan: error: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps([f.as_dict(args.redact) for f in found], indent=1))
    else:
        for f in found:
            print(f.render(args.redact))
    for n in notes:
        print(f"leak_scan: note: {n}", file=sys.stderr)
    if patterns_path:
        print(f"leak_scan: {len(patterns)} case pattern(s) loaded", file=sys.stderr)
    else:
        print(f"leak_scan: no case patterns configured (use --patterns or ${PATTERNS_ENV}); "
              "case identifiers were not checked", file=sys.stderr)
    print(f"leak_scan: {len(found)} finding(s)", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
