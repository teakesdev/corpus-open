"""What the Hermes Desktop panel shows: the case at a glance, open deadlines with passed ones
first, the tamper check and how many facts have no source.

The same functions the local web UI reads (packet.status_data, deadlines, facts.list_,
ingest.verify_integrity), through a read-only connection that never creates or migrates a
store. Nothing returned holds an absolute path: the desktop sees folder-relative paths only.
"""
from __future__ import annotations

import datetime as _dt
import os

from . import deadlines, facts, ingest, packet, store, subcase, triggers

MAX_FAILURES = 20  # the badge lists at most this many files; the counts stay exact


class PanelError(ValueError):
    pass


def _open(matter_dir: str):
    try:
        return store.connect_readonly(matter_dir)
    except FileNotFoundError:
        raise PanelError("This folder is not a case folder yet (no .matter/ store).") from None


def _title(matter_dir: str) -> str:
    m = subcase.read_manifest(matter_dir)
    title = m.get("title") if m else None
    if not isinstance(title, str) or not title.strip():
        title = os.path.basename(os.path.abspath(matter_dir))
    return title.strip()


def _deadline(r, today) -> dict:
    due = deadlines.parse_date(r["due_date"])
    return {"id": r["id"], "label": r["label"], "due_date": r["due_date"],
            "rule_source": r["rule_source"], "confidence": r["confidence"],
            "verification": r["verification_status"], "owner": r["owner"],
            "passed": deadlines.is_overdue(r["due_date"], r["state"], today=today),
            "days": (due - today).days if due else None}


def _order(d) -> tuple:
    """Passed first (oldest on top), then upcoming by date, then dates the kit can't read."""
    return (not d["passed"], d["days"] is None, d["days"] if d["days"] is not None else 0, d["label"])


def panel_data(matter_dir: str, today: _dt.date | None = None) -> dict:
    today = today or _dt.date.today()
    conn = _open(matter_dir)
    try:
        st = packet.status_data(conn, matter_dir)
        rows = sorted((_deadline(r, today) for r in deadlines.select(conn)), key=_order)
        fact_rows = facts.list_(conn)
        waiting = len(triggers.select(conn))
    finally:
        conn.close()
    upcoming = [d for d in rows if not d["passed"] and d["days"] is not None]
    nxt = upcoming[0] if upcoming else None
    return {
        "as_of": today.isoformat(),
        "overview": {
            "title": _title(matter_dir),
            "documents": st["documents_total"],
            "documents_by_kind": st["documents_by_kind"],
            "facts": len(fact_rows),
            "unsupported_facts": sum(1 for f in fact_rows if f["unsupported"]),
            "open_deadlines": len(rows),
            "passed_deadlines": sum(1 for d in rows if d["passed"]),
            "waiting_triggers": waiting,
            "unverified_authorities": st["unverified_authorities"],
            "next_deadline": ({"label": nxt["label"], "due_date": nxt["due_date"], "days": nxt["days"]}
                              if nxt else None),
        },
        "deadlines": rows,
    }


def panel_verify(matter_dir: str) -> dict:
    """Re-hash every registered file. `tampered` (a file's bytes changed) outranks `missing`."""
    conn = _open(matter_dir)
    try:
        ok, changed, missing, failures = ingest.verify_integrity(conn, matter_dir)
    finally:
        conn.close()
    checked = ok + changed + missing
    state = ("empty" if checked == 0 else "tampered" if changed else "missing" if missing else "intact")
    return {"state": state, "checked": checked, "changed": changed, "missing": missing,
            "failures": [{"kind": k, "rel_path": p} for k, p in failures[:MAX_FAILURES]]}
