"""Calendar export: the case's open deadlines as an iCalendar file (RFC 5545), stdlib only.

Each open deadline with a readable date becomes an all-day event with reminders 7, 3 and 1 days
before. The title keeps the date's confidence and verification labels, so a working estimate
never looks confirmed in a calendar. Deadlines still waiting on an event have no date and are
left out; closed ones (met, superseded, lapsed, vacated) are left out. The UID is the deadline's
own id, so importing a newer export updates the events instead of adding copies.

`write` puts the file at `<case>/.matter/exports/deadlines.ics`: under `.matter/`, so ingest
never registers it as a case document.
"""
import os
from datetime import datetime, timedelta, timezone

from . import deadlines, store

ALARM_DAYS = (7, 3, 1)
PRODID = "-//Corpus Open//Matter Kit Deadline Watch//EN"


def _escape(text: str) -> str:
    return (str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def _fold(line: str) -> str:
    """Fold at 75 octets (RFC 5545 §3.1), never inside a UTF-8 character."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        limit = 75 if not out else 74  # continuation lines start with one space
        if size + n > limit:
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return "\r\n ".join(out)


def _labels(r) -> str:
    conf = (r["confidence"] or "working-estimate").replace("-", " ")
    ver = (r["verification_status"] or "unverified").replace("-", " ")
    return f"[{conf}, {ver}]"


def build(matter_dir: str, now: datetime | None = None) -> tuple[str, int]:
    """(calendar text with CRLF line ends, number of events). Reads the store read-only."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    case = os.path.basename(os.path.abspath(matter_dir))
    conn = store.connect_readonly(matter_dir)
    try:
        rows = deadlines.select(conn)
    finally:
        conn.close()
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{_escape(case + ' deadlines')}"]
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    count = 0
    for r in rows:
        due = deadlines.parse_date(r["due_date"])
        if due is None:
            continue
        count += 1
        owner = r["owner"] if "owner" in r.keys() else None
        desc = [f"Case: {case}", f"Deadline id: {r['id']}",
                f"Confidence: {r['confidence'] or 'working-estimate'}; "
                f"verification: {r['verification_status'] or 'unverified'}"]
        if r["rule_source"]:
            desc.append(f"Rule: {r['rule_source']}")
        if owner:
            desc.append(f"Who acts: {owner}")
        if r["verification_status"] != "verified-official":
            desc.append("Not confirmed against an official source. Check the rules and the court's orders.")
        desc.append("Exported by Corpus Open. A reminder, not legal advice.")
        summary = f"{r['label']} {_labels(r)}"
        lines += ["BEGIN:VEVENT", f"UID:{r['id']}@corpus-open", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{due.strftime('%Y%m%d')}",
                  f"DTEND;VALUE=DATE:{(due + timedelta(days=1)).strftime('%Y%m%d')}",
                  f"SUMMARY:{_escape(summary)}", f"DESCRIPTION:{_escape(chr(10).join(desc))}",
                  "TRANSP:TRANSPARENT"]
        for n in ALARM_DAYS:
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"TRIGGER:-P{n}D",
                      f"DESCRIPTION:{_escape(f'{n} day' + ('' if n == 1 else 's') + ' left: ' + summary)}",
                      "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "".join(_fold(line) + "\r\n" for line in lines), count


def export_path(matter_dir: str) -> str:
    return os.path.join(os.path.abspath(matter_dir), ".matter", "exports", "deadlines.ics")


def write(matter_dir: str, now: datetime | None = None) -> tuple[str, int]:
    """Write the calendar to `.matter/exports/deadlines.ics`. Returns (path, event count)."""
    text, count = build(matter_dir, now=now)
    path = export_path(matter_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)
    return path, count
