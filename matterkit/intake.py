"""Intake checklist: what a case folder already holds, one step at a time.

`/matter intake` (the Hermes plugin) prints it, and the case-intake skill runs it at the end of an
intake interview. `steps` reads the case folder and nothing else: the store is opened read-only,
nothing is written and nothing is sent. Each step is done or not done from what the folder really
holds. Nothing here judges the case, and no date is computed: a deadline is shown as it was entered.

The rendered text is meant to be read on screen: at most MAX_LINES lines of at most WIDTH
characters, plain text, with the marks the citation block uses (✓ done, · not yet).
"""
from __future__ import annotations

import datetime as _dt
import os
import sqlite3
from typing import NamedTuple

from . import caselaw, citelookup, facts, statutelookup, store
from . import deadlines as dl
from . import triggers

WIDTH = 78
MAX_LINES = 14
DONE, NOT_DONE = "✓", "·"
DRAFTS = "Drafts"
NOT_A_LAWYER = "Corpus Open is not a lawyer. This organizes your case; it is not legal advice."
START = 'say "help me start my case"'
# The Next line for the first step not done. The draft step is not needed for intake.
NEXT = {
    "facts": f"Next: what happened. {START[0].upper() + START[1:]} to answer a few questions.",
    "dates": "Next: dates. Say each hearing date and any date on a paper you received.",
    "papers": 'Next: papers. Copy them into Filings or Evidence; say "register my files".',
    "cites": "Next: cite checks. /matter grant shows what is sent and how to turn them on.",
}
COMPLETE = "Next: nothing is missing from intake. Type /matter to see the case."


class Step(NamedTuple):
    key: str          # folder, facts, dates, papers, cites, draft
    title: str
    done: bool
    detail: str
    sub: str = ""     # a second line under the step (the next deadline), or ""
    needed: bool = True  # False: shown, but intake is complete without it


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _count(conn, sql: str) -> int:
    try:
        return conn.execute(sql).fetchone()[0]
    except sqlite3.OperationalError:  # a store older than the table: nothing of that kind yet
        return 0


def _facts(conn) -> Step:
    rows = facts.list_(conn)
    if not rows:
        return Step("facts", "What happened", False, "no facts recorded yet")
    bare = sum(1 for r in rows if r["unsupported"])
    tail = f"{bare} with no source yet" if bare else "each with a source"
    return Step("facts", "What happened", True, f"{_plural(len(rows), 'fact')} recorded, {tail}")


def _dates(conn, today: _dt.date) -> Step:
    rows = list(dl.select(conn))
    try:
        waiting = len(triggers.select(conn))
    except sqlite3.OperationalError:
        waiting = 0
    if not rows and not waiting:
        return Step("dates", "Deadlines", False, "none entered yet")
    parts = [f"{len(rows)} open"]
    overdue = sum(1 for r in rows if dl.is_overdue(r["due_date"], r["state"], today=today))
    if overdue:
        parts.append(f"{overdue} overdue")
    if waiting:
        parts.append(f"{waiting} waiting on an event")
    ahead = sorted((d, r["label"]) for r in rows
                   if (d := dl.parse_date(r["due_date"])) is not None and d >= today)
    sub = f"next: {ahead[0][0].isoformat()} {ahead[0][1]}" if ahead else ""
    return Step("dates", "Deadlines", True, ", ".join(parts), sub)


def _papers(conn) -> Step:
    n = _count(conn, "SELECT COUNT(*) FROM documents")
    if not n:
        return Step("papers", "Papers", False, "none registered yet")
    return Step("papers", "Papers", True, f"{n} registered, each with a fingerprint")


_CITE_WORDS = {"on": "on", "default": "on", "declined": "turned off by a person", "off": "not on"}


def _cites(matter_dir: str, guard_mode: str) -> Step:
    if guard_mode == "local":
        return Step("cites", "Cite checks", True, "kept on this computer (citation_guard local)")
    if guard_mode == "off":
        return Step("cites", "Cite checks", True, "off in this plugin's settings")
    case = caselaw.check_state(matter_dir, citelookup.PROVIDER)
    statute = caselaw.check_state(matter_dir, statutelookup.PROVIDER)
    done = "off" not in (case, statute)
    if _CITE_WORDS[case] == _CITE_WORDS[statute]:
        detail = f"{_CITE_WORDS[case]} for case cites and statutes"
    else:
        detail = f"case cites {_CITE_WORDS[case]}, statutes {_CITE_WORDS[statute]}"
    return Step("cites", "Cite checks", done, detail)


def _draft(matter_dir: str) -> Step:
    folder = os.path.join(matter_dir, DRAFTS)
    try:
        names = sorted(n for n in os.listdir(folder)
                       if not n.startswith(".") and os.path.isfile(os.path.join(folder, n)))
    except OSError:
        names = []
    if not names:
        return Step("draft", "Draft (optional)", False, "none in Drafts yet", needed=False)
    if len(names) == 1:
        return Step("draft", "Draft (optional)", True, f"/matter check {DRAFTS}/{names[0]}", needed=False)
    return Step("draft", "Draft (optional)", True,
                f"{len(names)} in {DRAFTS}; /matter check {DRAFTS}/<file>", needed=False)


def steps(matter_dir: str, today: _dt.date | None = None, guard_mode: str = "lookup") -> list[Step]:
    """The intake steps for a case folder (one with `.matter/`), read without writing anything.
    guard_mode is the plugin's citation_guard setting, which wins over the case's own checks."""
    today = today or _dt.date.today()
    conn = store.connect_readonly(matter_dir)
    try:
        out = [Step("folder", "Case folder", True, matter_dir), _facts(conn), _dates(conn, today),
               _papers(conn)]
    finally:
        conn.close()
    return out + [_cites(matter_dir, guard_mode), _draft(matter_dir)]


def next_line(items: list[Step]) -> str:
    for s in items:
        if s.needed and not s.done:
            return NEXT.get(s.key, COMPLETE)
    return COMPLETE


def _fit(line: str) -> str:
    return line if len(line) <= WIDTH else line[:WIDTH - 1] + "…"


def _fit_path(prefix: str, path: str) -> str:
    """prefix + path, with the path's start cut (it is the end that names the case) to fit."""
    room = WIDTH - len(prefix)
    return prefix + (path if len(path) <= room else "…" + path[-(room - 1):])


def render(items: list[Step], shown: str | None = None, today: _dt.date | None = None) -> str:
    """The checklist as plain text. shown is the folder as a person reads it (~/cases/x)."""
    today = today or _dt.date.today()
    folder = items[0].detail if items else ""
    shown = shown or folder
    lines = [_fit(f"Intake checklist · {os.path.basename(folder.rstrip(os.sep))} · {today.isoformat()}"), ""]
    for s in items:
        prefix = f"- {DONE if s.done else NOT_DONE} {s.title}: "
        lines.append(_fit_path(prefix, shown) if s.key == "folder" else _fit(prefix + s.detail))
        if s.sub:
            lines.append(_fit(f"  - {s.sub}"))
    lines += ["", next_line(items), "", NOT_A_LAWYER]
    return "\n".join(lines)


def no_case(folder: str | None = None) -> str:
    """What `/matter intake` says before there is a case. folder: a configured folder that is not a
    case yet, as a person reads it; the command that names it is never cut."""
    if folder:
        head = [f"Intake checklist: {folder} is not a case folder yet.", "",
                "- Set it up (files already there are kept):", f"  /matter init {folder}"]
    else:
        head = ["Intake checklist: there is no case folder yet.", "",
                "- Start your own: /matter init ~/cases/my-case"]
    return "\n".join(head + ["- Or try a made-up case first: /matter demo", "",
                             f"Then type /matter intake again, or {START}.", "", NOT_A_LAWYER])
