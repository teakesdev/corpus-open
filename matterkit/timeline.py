"""Timeline: the case's dated facts, oldest first, each source's quoted words checked against the record.

A fact (matterkit.facts) may carry a date in `fact_dates` and, for any of its sources, the exact
words that source holds for it in `fact_quotes`. The timeline lists dated facts in order and puts
undated ones after. Every time it is built, each quote is looked for in the registered document,
on the page the source cites, the same way brief check looks for a quotation:

  ✓ exact        the words are on the cited page (or anywhere in the document when the locator
                 names no page)
  ⚠ wrong page   the words are in the document, but not on the cited page
  ✗ not found    the words are on no page of the document
  · can't check  no readable text, the file is missing or changed since it was registered
  – no quote     the source is named, but no words were given to check

Nothing about a check is stored, so a file that changes after a fact was saved can never keep an
old ✓. "Exact" means the words are in the registered document there. It does not mean the fact is
true, that the document is authentic, or that the date is right.

Dates are as precise as a source is: YYYY-MM-DD, YYYY-MM or YYYY, optionally a range, optionally
"on or about" (approximate) and optionally inferred (worked out, not stated by any source).
"""
from __future__ import annotations

import calendar
import re
from datetime import date

from . import briefcheck, store
from .quotecheck import CANT_CHECK, EXACT, NOT_FOUND, WRONG_PAGE
from .store import now_iso

NO_QUOTE = "no quote"
MARKS = {EXACT: "✓", WRONG_PAGE: "⚠", NOT_FOUND: "✗", CANT_CHECK: "·", NO_QUOTE: "–"}
_PRECISION_RANK = {"year": 0, "month": 1, "day": 2}
_DATE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")
_PAGE_LOC = re.compile(r"^\s*(?:pp?\.?|pages?)?\s*\d+(?:\s*[-–—,]\s*\d+)*\s*$", re.I)
_PARA_LOC = re.compile(r"^\s*(?:¶+|paras?\.?|paragraphs?)\s*(\d+(?:\s*[-–—,]\s*\d+)*)\s*$", re.I)
_PROBLEMS = {"not-registered": "not registered in this case", "missing": "the file is missing",
             "changed": "the file changed after it was registered"}


class TimelineError(ValueError):
    """A refused timeline operation; nothing was written."""


# -- dates -------------------------------------------------------------------------------------
def parse_date(text) -> tuple[str, str]:
    """('2026-03-14', 'day') for '2026-03-14'; also 'YYYY-MM' (month) and 'YYYY' (year).
    TimelineError for anything else, including a day that doesn't exist."""
    m = _DATE.match(text.strip()) if isinstance(text, str) else None
    if not m:
        raise TimelineError(f"date {text!r} is not YYYY-MM-DD, YYYY-MM or YYYY")
    y, mo, d = m.group(1), m.group(2), m.group(3)
    try:
        date(int(y), int(mo or 1), int(d or 1))
    except ValueError:
        raise TimelineError(f"date {text!r} does not exist") from None
    return text.strip(), ("day" if d else "month" if mo else "year")


def _first_day(iso: str) -> str:
    parts = iso.split("-")
    return "-".join(parts + ["01"] * (3 - len(parts)))


def _last_day(iso: str) -> str:
    parts = iso.split("-")
    if len(parts) == 3:
        return iso
    if len(parts) == 2:
        y, m = int(parts[0]), int(parts[1])
        return f"{iso}-{calendar.monthrange(y, m)[1]:02d}"
    return f"{iso}-12-31"


def check_date(start, end=None) -> tuple[str, str, str | None]:
    """(start, precision, end) validated; the precision is the start's. TimelineError when the range
    ends before it starts."""
    start, precision = parse_date(start)
    if end in (None, ""):
        return start, precision, None
    end, _ = parse_date(end)
    if _last_day(end) < _first_day(start):
        raise TimelineError(f"the range ends ({end}) before it starts ({start})")
    if end == start:
        return start, precision, None
    return start, precision, end


def write_date(conn, aid, start, end=None, approximate=False, inferred=False, by="human"):
    """Insert or replace the date of fact `aid`. Does not commit; callers hold the transaction."""
    start, precision, end = check_date(start, end)
    conn.execute("INSERT OR REPLACE INTO fact_dates (assertion_id,date_start,date_end,precision,approximate,"
                 "date_basis,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (aid, start, end, precision, 1 if approximate else 0, "inferred" if inferred else "stated",
                  by, now_iso()))
    return {"date_start": start, "date_end": end, "precision": precision,
            "approximate": bool(approximate), "inferred": bool(inferred)}


def set_date(conn, aid, start=None, end=None, approximate=False, inferred=False, by="human", clear=False):
    """Date an existing fact, change its date, or (clear=True) take the date off. TimelineError for
    an unknown fact or a bad date; a refusal writes nothing."""
    if conn.execute("SELECT 1 FROM assertions WHERE id=?", (aid,)).fetchone() is None:
        raise TimelineError(f"no fact {aid!r} in this case")
    if clear:
        with conn:
            conn.execute("DELETE FROM fact_dates WHERE assertion_id=?", (aid,))
        return None
    if not start:
        raise TimelineError("give a date (YYYY-MM-DD, YYYY-MM or YYYY), or clear the date")
    check_date(start, end)  # refuse before the transaction opens
    with conn:
        return write_date(conn, aid, start, end, approximate, inferred, by)


def display_date(d: dict) -> str:
    """'2026-03-14', 'about 2026-03', '2026-03-01 to 2026-03-09', '2026 (inferred)'."""
    s = d["date_start"] + (f" to {d['date_end']}" if d.get("date_end") else "")
    if d.get("approximate"):
        s = "about " + s
    if d.get("inferred"):
        s += " (inferred)"
    return s


# -- quotes ------------------------------------------------------------------------------------
def check_quote(docs, rel: str, locator: str | None, quote: str | None) -> dict:
    """{verdict, where, note} for one source's quote, read against the registered document now.
    `docs` is a briefcheck.Docs; it re-hashes the file before reading it."""
    if not quote or not quote.strip():
        return {"verdict": NO_QUOTE, "where": "", "note": "no words given for this source"}
    frags = briefcheck._fragments(quote)
    if not frags:
        return {"verdict": CANT_CHECK, "where": "", "note": "the quote has no words to look for"}
    _row, problem = docs.verify(rel)
    if problem:
        return {"verdict": CANT_CHECK, "where": "", "note": _PROBLEMS.get(problem, problem)}
    pages, count, label = docs.text(rel)
    if pages is None:
        return {"verdict": CANT_CHECK, "where": "", "note": f"no readable text for {rel}"}
    loc = (locator or "").strip()
    para = _PARA_LOC.match(loc)
    if para:
        paras = briefcheck._paragraphs("\n".join(pages))
        wanted = [n for lo, hi in briefcheck._pin_ranges(para.group(1)) for n in range(lo, hi + 1)]
        if briefcheck._find(frags, "\n".join(paras.get(n, "") for n in wanted)) is not None:
            return {"verdict": EXACT, "where": loc, "note": ""}
        if briefcheck._find(frags, "\n".join(pages)) is not None:
            return {"verdict": WRONG_PAGE, "where": "another paragraph", "note": f"not in {loc}"}
        return {"verdict": NOT_FOUND, "where": "", "note": f"not in {label}"}
    pins = briefcheck._pin_ranges(loc) if _PAGE_LOC.match(loc) and count else []
    hits = [i + 1 for i, p in enumerate(pages) if briefcheck._find(frags, p) is not None]
    where = f"p. {', '.join(map(str, hits))}" if hits and count else ""
    if not hits:  # words across one page break count for both pages
        pairs = [i + 1 for i in range(len(pages) - 1)
                 if briefcheck._find(frags, pages[i] + "\n" + pages[i + 1]) is not None]
        if pairs:
            hits, where = [pairs[0], pairs[0] + 1], f"pp. {pairs[0]}–{pairs[0] + 1}"
        elif briefcheck._find(frags, "\n".join(pages)) is not None:
            return {"verdict": EXACT if not pins else WRONG_PAGE, "where": "across several pages",
                    "note": "" if not pins else f"spans pages, not only {loc}"}
    if not hits:
        return {"verdict": NOT_FOUND, "where": "", "note": f"not in {label}"}
    if pins and not any(lo <= p <= hi for p in hits for lo, hi in pins):
        return {"verdict": WRONG_PAGE, "where": where, "note": f"cited at {loc}"}
    note = "" if pins or not loc or not count else f"the locator {loc!r} is not a page, so the whole document was searched"
    return {"verdict": EXACT, "where": where, "note": note}


# -- the timeline ------------------------------------------------------------------------------
def _has_tables(conn) -> bool:
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('fact_dates','fact_quotes')")}
    return names == {"fact_dates", "fact_quotes"}


def _sort_key(d: dict):
    return (_first_day(d["date_start"]), _PRECISION_RANK[d["precision"]], d["created_at"], d["rowid"])


def build(conn, matter_dir: str, since: str | None = None, until: str | None = None,
          use_pdftotext: bool = True) -> dict:
    """{"dated": [...], "undated": [...], "counts": {...}}: every fact, dated ones in order with each
    source's quote checked now. since/until (YYYY[-MM[-DD]]) keep the dated facts whose dates touch
    that span; undated facts are left out when either is given. Reads only."""
    lo = _first_day(parse_date(since)[0]) if since else None
    hi = _last_day(parse_date(until)[0]) if until else None
    tables = _has_tables(conn)
    facts = conn.execute("SELECT rowid, * FROM assertions ORDER BY created_at, rowid").fetchall()
    docket, _ = briefcheck.load_docket(matter_dir)
    docs = briefcheck.Docs(conn, matter_dir, docket, use_pdftotext)
    dated, undated = [], []
    counts = {EXACT: 0, WRONG_PAGE: 0, NOT_FOUND: 0, CANT_CHECK: 0, NO_QUOTE: 0}
    try:
        for f in facts:
            d = conn.execute("SELECT * FROM fact_dates WHERE assertion_id=?", (f["id"],)).fetchone() if tables else None
            if d is not None:
                span = (_first_day(d["date_start"]), _last_day(d["date_end"] or d["date_start"]))
                if (lo and span[1] < lo) or (hi and span[0] > hi):
                    continue
            elif lo or hi:
                continue
            q = ("SELECT d.rel_path, s.locator, q.quote FROM assertion_sources s JOIN documents d ON d.id=s.document_id"
                 " LEFT JOIN fact_quotes q ON q.assertion_id=s.assertion_id AND q.document_id=s.document_id"
                 " AND q.locator=COALESCE(s.locator,'') WHERE s.assertion_id=? ORDER BY d.rel_path, s.locator") if tables else \
                ("SELECT d.rel_path, s.locator, NULL AS quote FROM assertion_sources s JOIN documents d"
                 " ON d.id=s.document_id WHERE s.assertion_id=? ORDER BY d.rel_path, s.locator")
            sources = []
            for s in conn.execute(q, (f["id"],)).fetchall():
                chk = check_quote(docs, s["rel_path"], s["locator"], s["quote"])
                counts[chk["verdict"]] += 1
                sources.append({"rel_path": s["rel_path"], "locator": s["locator"] or "", "quote": s["quote"], **chk})
            row = {"id": f["id"], "text": f["text"], "status": f["status"], "created_by": f["created_by"],
                   "created_at": f["created_at"], "rowid": f["rowid"], "sources": sources}
            if d is None:
                undated.append(row)
            else:
                row.update(date_start=d["date_start"], date_end=d["date_end"], precision=d["precision"],
                           approximate=bool(d["approximate"]), inferred=d["date_basis"] == "inferred")
                dated.append(row)
    finally:
        docs.close()
    dated.sort(key=_sort_key)
    for r in dated + undated:
        r.pop("rowid", None)
    return {"dated": dated, "undated": undated, "counts": counts}


def _cell(s: str) -> str:
    return " ".join((s or "").split()).replace("|", "\\|")


def _source_cell(sources: list) -> tuple[str, str]:
    if not sources:
        return "**no source**", "–"
    where, marks = [], []
    for s in sources:
        where.append(_cell(s["rel_path"] + (f" {s['locator']}" if s["locator"] else "")))
        mark = MARKS[s["verdict"]]
        if s["verdict"] == WRONG_PAGE and s["where"]:
            mark += f" words on {s['where']}"
        elif s["verdict"] == NOT_FOUND:
            mark += " not found"
        elif s["verdict"] == CANT_CHECK:
            mark += f" can't check ({s['note']})" if s["note"] else " can't check"
        marks.append(mark)
    return "; ".join(where), "; ".join(marks)


def render(data: dict, heading: str = "Timeline") -> str:
    """Markdown: a table of dated facts, then undated facts, then a key and the check counts."""
    dated, undated, c = data["dated"], data["undated"], data["counts"]
    lines = [f"## {heading}", ""]
    if not dated and not undated:
        lines.append("No facts yet. Add one with a date: `matter.py fact add <case> --text ... --status ... "
                     "--date YYYY-MM-DD --source <file>@p. N --quote \"<the words on that page>\"`.")
        return "\n".join(lines)
    if dated:
        lines += ["| Date | What happened | Label | Source | Check |", "|---|---|---|---|---|"]
        for r in dated:
            where, marks = _source_cell(r["sources"])
            lines.append(f"| {_cell(display_date(r))} | {_cell(r['text'])} | {r['status']} | {where} | {marks} |")
    else:
        lines.append("No dated facts yet. Give a fact a date with `matter.py fact date <case> <fact id> --date YYYY-MM-DD`.")
    if undated:
        lines += ["", f"**Undated facts ({len(undated)})**"]
        for r in undated:
            where, marks = _source_cell(r["sources"])
            lines.append(f"- [{r['status']}] {_cell(r['text'])} ({where}; {marks}) `{r['id']}`")
    checked = c[EXACT] + c[WRONG_PAGE] + c[NOT_FOUND] + c[CANT_CHECK]
    lines += ["", f"Quotes checked against the record just now: {c[EXACT]} exact, {c[WRONG_PAGE]} wrong page, "
              f"{c[NOT_FOUND]} not found, {c[CANT_CHECK]} can't check"
              + (f"; {c[NO_QUOTE]} source(s) with no quote to check." if c[NO_QUOTE] else ".")
              if checked or c[NO_QUOTE] else "No sources to check."]
    lines += ["", "_✓ the words are on the cited page · ⚠ in the document, on another page · ✗ not in the document "
              "· · can't check · – no quote given. A ✓ means the words are there, not that the fact is true or the "
              "date is right. Labels are the person's: an ALLEGATION is what someone says, not a finding._"]
    return "\n".join(lines)


def summary_line(conn) -> str:
    """One line for status: how many facts are dated. Cheap: checks no quotes."""
    n = conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    if not n:
        return ""
    dated = conn.execute("SELECT COUNT(*) FROM fact_dates").fetchone()[0] if _has_tables(conn) else 0
    return f"Timeline: {dated} of {n} facts dated."
