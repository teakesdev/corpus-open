"""Deadline Watch: the morning check a scheduler runs over one case folder.

`report` reads the case store read-only: open deadlines that are overdue or fall on a reminder
day, deadlines still waiting on an event, and the tamper check (how many registered files
changed or went missing). `render` turns it into plain text, or "" when there is nothing new, so
a scheduler that sends only non-empty output stays quiet on an ordinary day.

`cron_main` is what Hermes's script-only cron job runs (`/matter watch on` writes the wrapper):
it prints the text for Hermes to deliver and, when it printed anything, appends a hash-only line
to `.matter/egress.jsonl`, as every other path that sends case content out does. Changed files
are reported as counts, never by name, because a file name can say more than a date.
"""
import contextlib
import io
import os
import sqlite3
from datetime import date

from . import deadlines, store

REMIND_DAYS = (0, 1, 3, 7)
EGRESS_PROVIDER = "hermes-cron"
EGRESS_ENDPOINT = "hermes cron delivery (the job's --deliver target)"
EGRESS_PAYLOAD = "deadline_watch_summary"


def _labels(r) -> str:
    conf = (r["confidence"] or "working-estimate").replace("-", " ")
    ver = (r["verification_status"] or "unverified").replace("-", " ")
    return f"[{conf}, {ver}]"


def _confirmed(r) -> bool:
    return r["confidence"] == "hard" and r["verification_status"] == "verified-official"


def report(matter_dir: str, today: date | None = None, remind_days=REMIND_DAYS) -> dict:
    """What the morning check found. Opens the store read-only; never writes it."""
    from . import ingest as ing
    today = today or date.today()
    conn = store.connect_readonly(matter_dir)
    try:
        rows = deadlines.select(conn)
        try:
            waiting = conn.execute("SELECT COUNT(*) FROM deadline_triggers WHERE state='waiting'").fetchone()[0]
        except sqlite3.OperationalError:  # a store from before trigger deadlines
            waiting = 0
        with contextlib.redirect_stdout(io.StringIO()):
            _ok, changed, missing, _failures = ing.verify_integrity(conn, matter_dir)
    finally:
        conn.close()
    overdue, upcoming, unreadable = [], [], 0
    for r in rows:
        due = deadlines.parse_date(r["due_date"])
        if due is None:
            unreadable += 1
            continue
        days = (due - today).days
        item = {"label": r["label"], "due": due.isoformat(), "days": days, "labels": _labels(r),
                "confirmed": _confirmed(r)}
        if days < 0:
            overdue.append(item)
        elif days in remind_days:
            upcoming.append(item)
    return {"case": os.path.basename(os.path.abspath(matter_dir)), "today": today.isoformat(),
            "overdue": overdue, "upcoming": upcoming, "waiting": waiting, "unreadable": unreadable,
            "changed": changed, "missing": missing}


def has_news(r: dict) -> bool:
    """Waiting triggers alone are standing state, not news: they ride along with real news only."""
    return bool(r["overdue"] or r["upcoming"] or r["unreadable"] or r["changed"] or r["missing"])


def _when(days: int) -> str:
    if days < 0:
        return "1 day ago" if days == -1 else f"{-days} days ago"
    return {0: "today", 1: "tomorrow"}.get(days, f"in {days} days")


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def render(r: dict) -> str:
    """Plain text for a chat message, or "" when there is nothing new."""
    if not has_news(r):
        return ""
    lines = [f"Corpus Open · {r['case']} · {r['today']}"]
    if r["overdue"]:
        lines.append("OVERDUE (still open):")
        lines += [f"- {i['label']}: due {i['due']}, {_when(i['days'])} {i['labels']}" for i in r["overdue"]]
    if r["upcoming"]:
        lines.append("Coming up:")
        lines += [f"- {i['label']}: due {i['due']}, {_when(i['days'])} {i['labels']}" for i in r["upcoming"]]
    if r["unreadable"]:
        lines.append(_n(r["unreadable"], "open deadline has", "open deadlines have")
                     + " a date that can't be read. Check them with /matter deadlines.")
    if r["changed"] or r["missing"]:
        parts = []
        if r["changed"]:
            parts.append(_n(r["changed"], "registered file changed", "registered files changed"))
        if r["missing"]:
            parts.append(f"{r['missing']} {'is' if r['missing'] == 1 else 'are'} missing")
        lines.append("Files: " + " and ".join(parts) + " since they were registered. "
                     "Run matter.py verify on the case to see which.")
    if r["waiting"]:
        lines.append(_n(r["waiting"], "deadline waits", "deadlines wait")
                     + " on an event that hasn't been recorded yet.")
    if any(not i["confirmed"] for i in r["overdue"] + r["upcoming"]):
        lines.append("Dates marked working estimate or unverified are not confirmed. "
                     "Check them against the rules and the court's orders.")
    return "\n".join(lines)


def cron_main(matter_dir: str, today: date | None = None) -> None:
    """The scheduled job's body: print the summary (or nothing) and log it as egress."""
    from . import search
    matter_dir = os.path.abspath(os.path.expanduser(matter_dir))
    if not os.path.isfile(store.db_path(matter_dir)):
        print(f"Corpus Open Deadline Watch: {matter_dir} isn't a case folder any more (no .matter store). "
              "Point the job at the right folder with /matter watch on, or remove it with hermes cron.")
        return
    text = render(report(matter_dir, today=today))
    if not text:
        return
    search.log_egress(matter_dir, EGRESS_PROVIDER, EGRESS_ENDPOINT, EGRESS_PAYLOAD, text)
    print(text)
