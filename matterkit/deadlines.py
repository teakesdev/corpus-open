"""Deadline lifecycle: add/update with an events trail, and OVERDUE.

No schema change: `deadlines.state` and the `events` table already exist. Every
add/update appends one `events` row (the deadlines table has no created_by) in
the same transaction as the change, so the trail and the row cannot disagree.
"""
import json
import re
from datetime import date

from .ingest import PARENT_PREFIX
from .store import new_id

STATES = ("open", "met", "superseded", "lapsed", "vacated")
VERIFICATIONS = ("verified-official", "secondary-source", "unverified")
CONFIDENCES = ("hard", "working-estimate")
OVERDUE_MARK = "⚠ OVERDUE"
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class DeadlineError(ValueError):
    """A refused deadline operation; nothing was written."""


def parse_date(s):
    """date for a strict YYYY-MM-DD string, else None."""
    if isinstance(s, str) and _ISO_DATE.fullmatch(s):
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None
    return None


def is_overdue(due_date, state, today=None):
    """An open deadline whose date is before today. Malformed stored dates never flag."""
    d = parse_date(due_date)
    return state == "open" and d is not None and d < (today or date.today())


def add(conn, label, due, by, rule=None, confidence="working-estimate", verification="unverified"):
    """Insert a deadline plus its 'added' event atomically. Returns the deadline id."""
    if not (label or "").strip():
        raise DeadlineError("deadline add requires a non-empty --label")
    if parse_date(due) is None:
        raise DeadlineError(f"--due must be a valid date, YYYY-MM-DD (got {due!r})")
    did = new_id("d_")
    text = f"deadline {did} added: {label!r} due {due} ({confidence}/{verification})"
    if rule:
        text += f"; rule: {rule}"
    with conn:
        conn.execute("INSERT INTO deadlines (id,label,due_date,rule_source,confidence,verification_status)"
                     " VALUES (?,?,?,?,?,?)", (did, label, due, rule, confidence, verification))
        _event(conn, text, by, [])
    return did


def update(conn, case_dir, deadline_id, reason, by, state=None, verification=None,
           confidence=None, due=None, sources=()):
    """Change a deadline's state/verification/confidence/date with a reason, a trail and sources.

    Validates everything before writing; the update and its event commit together or not
    at all. Sources are `rel_path@locator` refs (same lookup as `fact add`) that must name
    a document registered in this matter, or in a subcase a `parent:<rel_path>` reference to a
    document the parent registers (re-hashed first; see subcase.py). `case_dir` is the matter root.
    Returns the event text.
    """
    reason = (reason or "").strip()
    sources = list(sources or [])
    if not reason:
        raise DeadlineError("--reason is required and must not be blank")
    for name, val, allowed in (("state", state, STATES), ("verification", verification, VERIFICATIONS),
                               ("confidence", confidence, CONFIDENCES)):
        if val is not None and val not in allowed:
            raise DeadlineError(f"invalid {name} {val!r} — use one of: {', '.join(allowed)}")
    if due is not None and parse_date(due) is None:
        raise DeadlineError(f"--due must be a valid date, YYYY-MM-DD (got {due!r})")
    if state is None and verification is None and confidence is None and due is None:
        raise DeadlineError("nothing to update — give at least one of --state, --verification, "
                            "--confidence, --due")
    row = conn.execute("SELECT * FROM deadlines WHERE id=?", (deadline_id,)).fetchone()
    if row is None:
        raise DeadlineError(f"unknown deadline id {deadline_id!r}")
    changes = []  # (field, column, old, new)
    for field, col, new in (("state", "state", state), ("verification", "verification_status", verification),
                            ("confidence", "confidence", confidence), ("due", "due_date", due)):
        if new is not None and new != row[col]:
            changes.append((field, col, row[col], new))
    if not changes:
        raise DeadlineError(f"no change — deadline {deadline_id} already has those values")

    from . import subcase  # lazy: subcase imports this module
    try:  # in a subcase, parent: sources are re-hashed against the parent before anything is written
        parents = subcase.resolve_parent_sources(case_dir, sources)
    except subcase.SubcaseError as e:
        raise DeadlineError(str(e)) from None
    refs, unregistered = [], []
    for src in sources:
        ref, _, loc = src.partition("@")
        if ref in parents:
            refs.append(PARENT_PREFIX + parents[ref].rel_path + (f"@{loc}" if loc else ""))
            continue
        doc = conn.execute("SELECT rel_path FROM documents WHERE rel_path=? OR id=?", (ref, ref)).fetchone()
        if doc is None:
            unregistered.append(ref)
        else:
            refs.append(doc["rel_path"] + (f"@{loc}" if loc else ""))
    if unregistered:
        raise DeadlineError(f"source(s) not registered in this matter: {unregistered}")

    text = (f"deadline {deadline_id} {row['label']!r}: "
            + "; ".join(f"{f} {o}→{n}" for f, _, o, n in changes) + f"; reason: {reason}")
    with conn:
        for pdoc in parents.values():
            subcase.register_parent_ref(conn, pdoc)
        conn.execute("UPDATE deadlines SET " + ", ".join(f"{c}=?" for _, c, _, _ in changes) + " WHERE id=?",
                     [n for _, _, _, n in changes] + [deadline_id])
        _event(conn, text, by, refs)
    return text


def _event(conn, text, by, sources):
    conn.execute("INSERT INTO events (id,occurred_on,text,actors,sources) VALUES (?,?,?,?,?)",
                 (new_id("ev_"), date.today().isoformat(), text, by, json.dumps(sources)))


def list_line(r, show_state=False, today=None):
    """One `deadline list` line; the OVERDUE marker sits last so the leading columns stay stable."""
    flag = "" if r["verification_status"] == "verified-official" else f" ({r['confidence']}/{r['verification_status']})"
    line = f"{r['due_date']}  {r['label']}{flag}  [{r['rule_source'] or '—'}]"
    if show_state:
        line += f"  state={r['state']}"
    if is_overdue(r["due_date"], r["state"], today):
        line += f"  {OVERDUE_MARK}"
    return line
