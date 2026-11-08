"""Quote guard: an agent may not write a quotation or page cite into a draft that the record doesn't hold.

Runs as a pre_tool_call hook after the source guard. For write_file and patch aimed at a text file
(.md, .markdown, .txt, .text) inside a case folder, it runs the quote checker (matterkit.quotecheck)
on the text as it would read after the call, before anything is written. A patch call is read the
way Hermes's patch tool reads it: `mode` picks the branch ("replace" when absent), and V4A headers
and hunks follow Hermes's patch parser.

- write_file: the whole content.
- patch, replace mode: old_string -> new_string applied to the draft in memory (exactly, or with
  Hermes's own loose matcher when an exact match fails, as Hermes does). Only sentences the edit
  touches are checked, so a cite-only edit re-checks its quotation and problems elsewhere in the
  draft don't block it. When neither matches, Hermes writes nothing, so neither does the check.
- patch, V4A mode: each Update hunk applied to the draft the same way, checking the sentences its
  added lines touch, with the hunk's context lines as context; an Add File's whole text.

A "not found" or "wrong page" blocks the write, and the message tells the agent what is wrong and,
for a wrong page, where the words really are. "Exact" and "can't check" pass; `/matter check` still
lists what could not be checked. Paths are resolved by the source guard's own rules.
"""
from __future__ import annotations

import logging
import os
import re

from ..matterkit import quotecheck
from . import source_guard as _sg

TEXT_EXT = (".md", ".markdown", ".txt", ".text")
USE_PDFTOTEXT = True
MAX_CHARS = 2_000_000  # text to check; a bigger write passes unchecked
MAX_READ = MAX_CHARS  # a draft bigger than this is not read: a patch's new text is checked alone
MISSING, UNREADABLE = object(), object()  # never text: _hunk tells them apart from a draft

_log = logging.getLogger(__name__)
_warned_no_fuzzy = False

# Hermes's tools/patch_parser.py, as of Hermes 0.22: markers are matched at the start of a line and
# allow any whitespace inside them ("***Update File:" applies).
_BEGIN = re.compile(r"^\*\*\*\s*Begin\s+Patch\s*$")
_END = re.compile(r"^\*\*\*\s*End\s+Patch\s*$")
_HINT = re.compile(r"@@\s*(.+?)\s*@@")
_OPS = (("update", re.compile(r"\*\*\*\s*Update\s+File:\s*(.+)")),
        ("add", re.compile(r"\*\*\*\s*Add\s+File:\s*(.+)")),
        ("delete", re.compile(r"\*\*\*\s*Delete\s+File:\s*(.+)")),
        ("move", re.compile(r"\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+)")))


def check(tool_name, args, matter_dir: str | None = None, use_pdftotext: bool | None = None) -> dict | None:
    """The pre_tool_call directive for one call, or None to let it run."""
    if not isinstance(args, dict) or tool_name not in _sg.FILE_TOOLS:
        return None
    pdf = USE_PDFTOTEXT if use_pdftotext is None else use_pdftotext
    for raw, text, regions in _writes(tool_name, args, matter_dir):
        target = _target(raw, matter_dir)
        if target is None or not text or len(text) > MAX_CHARS:
            continue
        root, path = target
        problems = [r for r in quotecheck.check_text(root, text, regions=regions, use_pdftotext=pdf)
                    if r.verdict in quotecheck.PROBLEMS]
        if problems:
            return {"action": "block", "message": _message(root, path, problems)}
    return None


# -- what a call writes --------------------------------------------------------------------------
def _writes(tool_name, args, matter_dir):
    """(raw path, text as it would read, regions changed or None for all of it) per file written."""
    if tool_name == "write_file":
        if isinstance(args.get("path"), str) and isinstance(args.get("content"), str):
            yield args["path"].strip(), args["content"], None
        return
    mode = args.get("mode") or "replace"
    if mode == "replace":
        yield from _replace(args, matter_dir)
    elif mode == "patch" and isinstance(args.get("patch"), str) and args["patch"]:
        yield from _v4a(args["patch"], matter_dir)


def _replace(args, matter_dir):
    path, old, new = args.get("path"), args.get("old_string"), args.get("new_string")
    if not isinstance(path, str) or not path.strip() or not isinstance(old, str) or not isinstance(new, str):
        return  # Hermes refuses the call
    path = path.strip()
    current = _read(path, matter_dir)
    if current is MISSING:
        return  # no file to patch: Hermes refuses the call
    if current is UNREADABLE:
        yield path, new, None  # Hermes may still patch it: check the new text on its own
        return
    if old and old in current:
        yield (path, *_replace_exact(current, old, new, bool(args.get("replace_all"))))
        return
    loose = _fuzzy_replace(current, old, new, bool(args.get("replace_all")))
    if loose is None:  # Hermes's matcher is not importable: check the new text on its own
        yield path, new, None
        return
    after, count, _strategy, error = loose
    if count and not error:
        yield path, after, [_diff_span(current, after)]


def _replace_exact(current, old, new, replace_all):
    parts, regions, i, length = [], [], 0, 0
    while True:
        j = current.find(old, i)
        if j < 0:
            break
        parts.append(current[i:j])
        length += j - i
        parts.append(new)
        regions.append((length, length + len(new)))
        length += len(new)
        i = j + len(old)
        if not replace_all:
            break
    parts.append(current[i:])
    return "".join(parts), regions


def _fuzzy_replace(content, old, new, replace_all=False):
    """Hermes's own loose matcher, or None outside Hermes (logged once: inside Hermes it means the
    matcher moved, and a loose patch then has only its new text checked)."""
    global _warned_no_fuzzy
    try:
        from tools.fuzzy_match import fuzzy_find_and_replace  # Hermes's package, on its sys.path
    except Exception:  # noqa: BLE001
        if not _warned_no_fuzzy:
            _warned_no_fuzzy = True
            _log.warning("corpus-open quote guard: Hermes's tools.fuzzy_match is not importable; "
                         "a patch whose old_string matches only loosely has only its new text checked")
        return None
    return fuzzy_find_and_replace(content, old, new, replace_all)


def _diff_span(a: str, b: str) -> tuple:
    p = 0
    while p < min(len(a), len(b)) and a[p] == b[p]:
        p += 1
    s = 0
    while s < min(len(a), len(b)) - p and a[-1 - s] == b[-1 - s]:
        s += 1
    return p, len(b) - s


def _parse_v4a(patch: str):
    """[(kind, path, hunks)], each hunk (context hint or None, [(prefix, text)]), following Hermes's
    parser."""
    lines = [ln.removesuffix("\r") for ln in patch.split("\n")]
    start, end = -1, len(lines)
    for i, ln in enumerate(lines):
        if _BEGIN.match(ln):
            start = i
        elif _END.match(ln):
            end = i
            break
    ops, op, hunk = [], None, None
    for ln in lines[start + 1:end]:
        hit = next(((kind, m) for kind, rx in _OPS if (m := rx.match(ln))), None)
        if hit:
            if op and hunk and hunk[1]:
                op[2].append(hunk)
            op, hunk = (hit[0], hit[1].group(1).strip(), []), (None, []) if hit[0] == "add" else None
            ops.append(op)
        elif ln.startswith("@@"):
            if op:
                if hunk and hunk[1]:
                    op[2].append(hunk)
                hint = _HINT.match(ln)
                hunk = (hint.group(1) if hint else None, [])
        elif op and ln:
            if hunk is None:
                hunk = (None, [])
            if ln[0] in "+- ":
                hunk[1].append((ln[0], ln[1:]))
            elif ln[0] != "\\":
                hunk[1].append((" ", ln))
    if op and hunk and hunk[1]:
        op[2].append(hunk)
    return ops


def _v4a(patch, matter_dir):
    # path -> [text | MISSING | UNREADABLE, regions]: later ops on a file build on earlier ones, as
    # Hermes's overlay does (an Update after an Add in the same patch edits the added text)
    drafts = {}
    for kind, path, hunks in _parse_v4a(patch):
        if kind == "add":
            added = [t for _hint, lines in hunks for p, t in lines if p == "+"]
            text = "\n".join(added) + "\n" if added else ""
            drafts[path] = [text, [(0, len(text))] if added else []]
        elif kind == "update":
            if path not in drafts:
                drafts[path] = [_read(path, matter_dir), []]
            state = drafts[path]
            for hint, lines in hunks:
                yield from _hunk(path, state, hint, lines)
    for path, (text, regions) in drafts.items():
        if isinstance(text, str) and regions:
            yield path, text, regions


def _hunk(path, state, hint, lines):
    """Apply one Update hunk to state ([text, regions]) the way Hermes's _apply_update does; a hunk
    that can't be placed is yielded on its own lines."""
    search_lines = [t for p, t in lines if p != "+"]
    repl_lines = [(p, t) for p, t in lines if p != "-"]
    if search_lines and search_lines == [t for _, t in repl_lines]:
        return  # inert: context only, or identical -/+ lines; Hermes skips it
    repl = "\n".join(t for _, t in repl_lines)
    added, off = [], 0
    for p, t in repl_lines:
        if p == "+":
            added.append((off, off + len(t)))
        off += len(t) + 1
    added = added or [(0, 0)]  # a deletion re-checks the sentence it sits in
    text = state[0]
    if not isinstance(text, str):
        if any(p == "+" for p, _ in lines):
            yield path, repl + "\n", added
        return
    if not search_lines:  # addition-only: after its context hint's line when unique, else at the end
        if hint and text.count(hint) > 1:
            yield path, repl + "\n", added  # Hermes refuses an ambiguous hint
            return
        if hint and hint in text:
            eol = text.find("\n", text.find(hint))
            new, at = ((text + "\n" + repl, len(text) + 1) if eol < 0
                       else (text[:eol + 1] + repl + "\n" + text[eol + 1:], eol + 1))
        else:
            base = text.rstrip("\n")
            new, at = base + "\n" + repl + "\n", len(base) + 1
        _splice(state, new, at, at, added)
        return
    search = "\n".join(search_lines)
    at = text.find(search)
    if at >= 0:
        _splice(state, text[:at] + repl + text[at + len(search):], at, at + len(search), added)
        return
    loose = _fuzzy_replace(text, search, repl)
    if loose is None:
        yield path, repl + "\n", added
        return
    after, count, _strategy, error = loose
    if count and not error:  # else Hermes fails the hunk, or it is already applied: no new text
        lo, hi = _diff_span(text, after)
        state[1] = [(a + len(after) - len(text), b + len(after) - len(text)) if a >= lo else (a, b)
                    for a, b in state[1]] + [(lo, hi)]
        state[0] = after


def _splice(state, new, at, old_end, added):
    """state's text becomes new, in which text[at:old_end] was replaced by a block at `at`; earlier
    regions after the block shift, and the block's added lines become regions."""
    delta = len(new) - len(state[0])
    state[1] = [(a + delta, b + delta) if a >= old_end else (a, b) for a, b in state[1]]
    state[1] += [(at + a, at + b) for a, b in added]
    state[0] = new


def _read(raw, matter_dir):
    """The draft's text as Hermes's patch reads it (BOM stripped, undecodable bytes kept as
    surrogates; line endings normalized, which Hermes's matcher does too), MISSING when there is
    no such file, or UNREADABLE when it is too big or can't be read."""
    for cand in _sg._candidates(raw, matter_dir):
        if os.path.isfile(cand):
            try:
                if os.path.getsize(cand) > MAX_READ:
                    return UNREADABLE
                with open(cand, encoding="utf-8", errors="surrogateescape") as f:
                    return f.read().removeprefix("\ufeff")
            except (OSError, ValueError):
                return UNREADABLE
    return MISSING


def _target(raw, matter_dir):
    """(case root, path) when raw is a text file in a case that is not a source or the store."""
    if not raw.lower().endswith(TEXT_EXT):
        return None
    for cand in _sg._candidates(raw, matter_dir):
        for root in _sg._cases_above(cand):
            try:
                hit = _sg._classify(cand)
            except _sg.Unreadable:
                return None
            if hit and hit[0] in ("store", "source"):
                return None  # the source guard's call
            return root, cand
    return None


def _message(root, path, problems) -> str:
    rel = os.path.relpath(path, root)
    lines = [f"corpus-open quote check: nothing was written to {rel}. It would put "
             f"{len(problems)} quotation(s) or page cite(s) in the draft that the case record at {root} "
             "does not support:"]
    lines += [f"- {quotecheck.describe(r)}" for r in problems]
    lines.append("Quote the document's exact words, cite the page where they really are, or take the "
                 "quotation out, then write again. Matching ignores case, curly quotes and dashes; ellipses "
                 "and [brackets] mark changes.")
    return "\n".join(lines)
