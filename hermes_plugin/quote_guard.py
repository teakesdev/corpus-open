"""Quote guard: an agent may not write a quotation or page cite into a draft that the record doesn't hold.

Runs as a pre_tool_call hook after the source guard. For write_file and patch aimed at a text file
(.md, .markdown, .txt, .text) inside a case folder, it runs the quote checker (matterkit.quotecheck)
on the new text before anything is written:

- write_file: the whole content.
- patch, replace mode: the file as it would read after the edit, so the quotation's sentence keeps
  its cite, keeping only results from the new text. Problems already in the draft do not block an
  unrelated edit.
- patch, V4A mode: each file's added (+) lines.

A "not found" or "wrong page" blocks the write, and the message tells the agent what is wrong and,
for a wrong page, where the words really are. "Exact" and "can't check" pass; `/matter check` still
lists what could not be checked. Paths are resolved by the source guard's own rules.
"""
from __future__ import annotations

import os
import re

from ..matterkit import quotecheck
from . import source_guard as _sg

TEXT_EXT = (".md", ".markdown", ".txt", ".text")
USE_PDFTOTEXT = True
MAX_CHARS = 2_000_000

_V4A_SECTION = re.compile(r"^\*\*\* (?:Update|Add) File:[ \t]*(.+?)[ \t]*$", re.M)


def check(tool_name, args, matter_dir: str | None = None, use_pdftotext: bool | None = None) -> dict | None:
    """The pre_tool_call directive for one call, or None to let it run."""
    if not isinstance(args, dict) or tool_name not in _sg.FILE_TOOLS:
        return None
    pdf = USE_PDFTOTEXT if use_pdftotext is None else use_pdftotext
    for raw, text, only in _writes(tool_name, args, matter_dir):
        target = _target(raw, matter_dir)
        if target is None or not text or len(text) > MAX_CHARS:
            continue
        root, path = target
        problems = [r for r in quotecheck.check_text(root, text, only=only, use_pdftotext=pdf)
                    if r.verdict in quotecheck.PROBLEMS]
        if problems:
            return {"action": "block", "message": _message(root, path, problems)}
    return None


# -- what a call writes --------------------------------------------------------------------------
def _writes(tool_name, args, matter_dir):
    """(raw path, text to check, only) for each file the call writes."""
    if tool_name == "write_file":
        if isinstance(args.get("path"), str) and isinstance(args.get("content"), str):
            yield args["path"].strip(), args["content"], None
        return
    patch = args.get("patch")
    if isinstance(patch, str) and patch.strip():
        sections = list(_V4A_SECTION.finditer(patch))
        for i, m in enumerate(sections):
            body = patch[m.end(): sections[i + 1].start() if i + 1 < len(sections) else len(patch)]
            added = [ln[1:] for ln in body.splitlines() if ln.startswith("+")]
            if added:
                yield m.group(1), "\n".join(added) + "\n", None
        return
    path, new = args.get("path"), args.get("new_string")
    if not isinstance(path, str) or not isinstance(new, str) or not new.strip():
        return
    old = args.get("old_string") if isinstance(args.get("old_string"), str) else ""
    current = _read(path.strip(), matter_dir)
    if current is not None and old and old in current:
        after = current.replace(old, new) if args.get("replace_all") else current.replace(old, new, 1)
        yield path.strip(), after, [new]
    else:
        yield path.strip(), new, None


def _read(raw, matter_dir):
    for cand in _sg._candidates(raw, matter_dir):
        if os.path.isfile(cand):
            try:
                if os.path.getsize(cand) > MAX_CHARS:
                    return None
                with open(cand, encoding="utf-8", errors="replace") as f:
                    return f.read()
            except OSError:
                return None
    return None


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
