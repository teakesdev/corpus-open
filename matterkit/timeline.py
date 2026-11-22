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

A text or Markdown file is cited by line ("L12", "L12-L14", "lines 12-14"), counted in the file as
registered; a changed file can't be checked, so a line cite can't drift. A fact may name its event
("notice served"): facts that share an event and whose dates don't overlap are listed as a date
conflict, each side with its label and sources. The timeline never picks a side. Each row says how
many documents back the fact. Hypotheses are left out unless asked for.
"""
from __future__ import annotations

import calendar
import os
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
_LINE_LOC = re.compile(r"^\s*(?:L|lines?\s*)(\d+)(?:\s*[-–—]\s*L?(\d+))?\s*$", re.I)
_TEXT_EXTS = (".md", ".txt", ".markdown", ".text")
_EVENT_MAX = 80
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


def normalize_event(text) -> str | None:
    """An event name as stored: lower case, single spaces. None for no name. TimelineError for one
    that isn't text or is longer than a short name."""
    if text is None:
        return None
    if not isinstance(text, str):
        raise TimelineError(f"an event name must be text, not {text!r}")
    key = " ".join(text.split()).lower()
    if len(key) > _EVENT_MAX:
        raise TimelineError(f"an event name is a short name for the happening (at most {_EVENT_MAX} characters)")
    return key or None


def write_date(conn, aid, start, end=None, approximate=False, inferred=False, by="human", event=None):
    """Insert or replace the date of fact `aid`. Does not commit; callers hold the transaction."""
    start, precision, end = check_date(start, end)
    event = normalize_event(event)
    conn.execute("INSERT OR REPLACE INTO fact_dates (assertion_id,date_start,date_end,precision,approximate,"
                 "date_basis,event_key,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                 (aid, start, end, precision, 1 if approximate else 0, "inferred" if inferred else "stated",
                  event, by, now_iso()))
    return {"date_start": start, "date_end": end, "precision": precision,
            "approximate": bool(approximate), "inferred": bool(inferred), "event": event}


def set_date(conn, aid, start=None, end=None, approximate=False, inferred=False, by="human", clear=False,
             event=None):
    """Date an existing fact, change its date, or (clear=True) take the date off. With no date but an
    event name, renames the event of a fact that already has a date ("" takes the name off).
    TimelineError for an unknown fact, a bad date or a bad name; a refusal writes nothing."""
    if conn.execute("SELECT 1 FROM assertions WHERE id=?", (aid,)).fetchone() is None:
        raise TimelineError(f"no fact {aid!r} in this case")
    if clear:
        with conn:
            conn.execute("DELETE FROM fact_dates WHERE assertion_id=?", (aid,))
        return None
    key = normalize_event(event)
    if not start:
        row = conn.execute("SELECT * FROM fact_dates WHERE assertion_id=?", (aid,)).fetchone()
        if event is None or row is None:
            raise TimelineError("give a date (YYYY-MM-DD, YYYY-MM or YYYY), or clear the date"
                                + ("; an event name needs the fact to have a date" if event is not None else ""))
        with conn:
            conn.execute("UPDATE fact_dates SET event_key=?, created_by=?, created_at=? WHERE assertion_id=?",
                         (key, by, now_iso(), aid))
        return _date_dict(conn.execute("SELECT * FROM fact_dates WHERE assertion_id=?", (aid,)).fetchone())
    check_date(start, end)  # refuse before the transaction opens
    with conn:
        return write_date(conn, aid, start, end, approximate, inferred, by, key)


def _date_dict(d) -> dict:
    return {"date_start": d["date_start"], "date_end": d["date_end"], "precision": d["precision"],
            "approximate": bool(d["approximate"]), "inferred": d["date_basis"] == "inferred",
            "event": d["event_key"] if "event_key" in d.keys() else None}


def _span(d: dict) -> tuple[str, str]:
    return _first_day(d["date_start"]), _last_day(d.get("date_end") or d["date_start"])


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
    line = _LINE_LOC.match(loc)
    if line:
        lo, hi = sorted((int(line.group(1)), int(line.group(2) or line.group(1))))
        return _check_lines(docs, rel, frags, loc, lo, hi, pages, label)
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


def _fmt_lines(a: int, b: int) -> str:
    return f"L{a}" if a == b else f"L{a}-L{b}"


def _line_span(frags, lines: list) -> tuple[int, int] | None:
    """(first, last) 1-based lines of the first place the quote's words occur, or None."""
    def found(a, b):
        return briefcheck._find(frags, "\n".join(lines[a:b])) is not None
    if not lines or not found(0, len(lines)):
        return None
    lo, hi = 1, len(lines)  # the fewest leading lines that hold the words
    while lo < hi:
        mid = (lo + hi) // 2
        lo, hi = (lo, mid) if found(0, mid) else (mid + 1, hi)
    end, lo, hi = lo, 0, lo - 1  # then the latest line they can start on
    while lo < hi:
        mid = (lo + hi + 1) // 2
        lo, hi = (mid, hi) if found(mid, end) else (lo, mid - 1)
    return lo + 1, end


def _check_lines(docs, rel, frags, loc, lo, hi, pages, label) -> dict:
    """A line cite on a text file: the words must be within those lines of the file as registered."""
    if os.path.splitext(rel)[1].lower() not in _TEXT_EXTS:
        if briefcheck._find(frags, "\n".join(pages)) is None:
            return {"verdict": NOT_FOUND, "where": "", "note": f"not in {label}"}
        return {"verdict": EXACT, "where": "", "note": "line cites are for text files, so the whole document was searched"}
    lines = briefcheck._read_text(docs.paths[rel]).splitlines()
    if hi <= len(lines) and briefcheck._find(frags, "\n".join(lines[lo - 1:hi])) is not None:
        return {"verdict": EXACT, "where": _fmt_lines(lo, hi), "note": ""}
    span = _line_span(frags, lines)
    out = ({"verdict": NOT_FOUND, "where": "", "note": f"not in {rel}"} if span is None else
           {"verdict": WRONG_PAGE, "where": _fmt_lines(*span), "note": f"cited at {loc}"})
    if hi > len(lines):  # the cite itself points past the end of the file
        out["warning"] = f"the file has {len(lines)} line{'s' if len(lines) != 1 else ''}"
    return out


# -- the timeline ------------------------------------------------------------------------------
def _has_tables(conn) -> bool:
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('fact_dates','fact_quotes')")}
    return names == {"fact_dates", "fact_quotes"}


def _sort_key(d: dict):
    return (_first_day(d["date_start"]), _PRECISION_RANK[d["precision"]], d["created_at"], d["rowid"])


def _overlap(a: dict, b: dict) -> bool:
    (a0, a1), (b0, b1) = _span(a), _span(b)
    return a0 <= b1 and b0 <= a1


def _conflicts(dated: list) -> list:
    """[{"event", "facts"}] for each event name whose facts' dates don't all overlap, oldest first."""
    groups: dict = {}
    for r in dated:
        if r.get("event"):
            groups.setdefault(r["event"], []).append(r)
    out = []
    for key, rows in groups.items():
        if any(not _overlap(a, b) for i, a in enumerate(rows) for b in rows[i + 1:]):
            out.append({"event": key, "facts": sorted(rows, key=_sort_key)})
    return sorted(out, key=lambda c: _sort_key(c["facts"][0]))


def build(conn, matter_dir: str, since: str | None = None, until: str | None = None,
          use_pdftotext: bool = True, label: str | None = None, doc: str | None = None,
          hypotheses: bool = False) -> dict:
    """{"dated", "undated", "conflicts", "counts", "hidden_hypotheses"}: dated facts in order and
    undated ones after, each source's quote checked now. Reads only.

    since/until (YYYY[-MM[-DD]]) keep the dated facts whose dates touch that span and leave undated
    facts out. `label` keeps one label (HYPOTHESIS included when asked for by name); `doc` keeps
    facts with a source in that registered file. Hypotheses are left out unless `hypotheses` is true
    or `label` names them; "hidden_hypotheses" counts what was left out for that reason alone.
    "conflicts" is worked out over every dated fact, whatever the filters, because the other side
    of a conflict may fall outside them."""
    lo = _first_day(parse_date(since)[0]) if since else None
    hi = _last_day(parse_date(until)[0]) if until else None
    if label:
        from .facts import FactError, normalize_status
        try:
            label = normalize_status(label)
        except FactError as e:
            raise TimelineError(str(e)) from None
    tables = _has_tables(conn)
    facts = conn.execute("SELECT rowid, * FROM assertions ORDER BY created_at, rowid").fetchall()
    if doc and conn.execute("SELECT 1 FROM documents WHERE rel_path=?", (doc,)).fetchone() is None:
        raise TimelineError(f"{doc!r} is not a registered document in this case")
    docket, _ = briefcheck.load_docket(matter_dir)
    docs = briefcheck.Docs(conn, matter_dir, docket, use_pdftotext)
    q = ("SELECT d.rel_path, s.locator, q.quote FROM assertion_sources s JOIN documents d ON d.id=s.document_id"
         " LEFT JOIN fact_quotes q ON q.assertion_id=s.assertion_id AND q.document_id=s.document_id"
         " AND q.locator=COALESCE(s.locator,'') WHERE s.assertion_id=? ORDER BY d.rel_path, s.locator") if tables else \
        ("SELECT d.rel_path, s.locator, NULL AS quote FROM assertion_sources s JOIN documents d"
         " ON d.id=s.document_id WHERE s.assertion_id=? ORDER BY d.rel_path, s.locator")
    checked: dict = {}
    rows = []
    try:
        for f in facts:
            d = conn.execute("SELECT * FROM fact_dates WHERE assertion_id=?", (f["id"],)).fetchone() if tables else None
            sources = []
            for s in conn.execute(q, (f["id"],)).fetchall():
                key = (s["rel_path"], s["locator"] or "", s["quote"])
                if key not in checked:
                    checked[key] = check_quote(docs, s["rel_path"], s["locator"], s["quote"])
                sources.append({"rel_path": s["rel_path"], "locator": s["locator"] or "", "quote": s["quote"],
                                **checked[key]})
            row = {"id": f["id"], "text": f["text"], "status": f["status"], "created_by": f["created_by"],
                   "created_at": f["created_at"], "rowid": f["rowid"], "sources": sources,
                   "documents": len({x["rel_path"] for x in sources})}
            if d is not None:
                row.update(_date_dict(d))
            rows.append(row)
    finally:
        docs.close()
    conflicts = _conflicts([r for r in rows if "date_start" in r])
    in_conflict = {r["id"]: c["event"] for c in conflicts for r in c["facts"]}
    dated, undated, hidden = [], [], 0
    counts = {EXACT: 0, WRONG_PAGE: 0, NOT_FOUND: 0, CANT_CHECK: 0, NO_QUOTE: 0}
    for r in rows:
        r["conflict"] = r["id"] in in_conflict
        if "date_start" in r:
            if (lo and _span(r)[1] < lo) or (hi and _span(r)[0] > hi):
                continue
        elif lo or hi:
            continue
        if label and r["status"] != label:
            continue
        if doc and doc not in {x["rel_path"] for x in r["sources"]}:
            continue
        if r["status"] == "HYPOTHESIS" and not (hypotheses or label):
            hidden += 1
            continue
        for x in r["sources"]:
            counts[x["verdict"]] += 1
        (dated if "date_start" in r else undated).append(r)
    dated.sort(key=_sort_key)
    for r in rows:
        r.pop("rowid", None)
    return {"dated": dated, "undated": undated, "conflicts": conflicts, "counts": counts,
            "hidden_hypotheses": hidden}


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
        if s.get("warning"):
            mark += f" ({s['warning']})"
        marks.append(mark)
    n = len({s["rel_path"] for s in sources})
    return (f"{n} documents: " if n > 1 else "") + "; ".join(where), "; ".join(marks)


def _what(r: dict) -> str:
    text = _cell(r["text"])
    if r.get("event"):
        text += f" _(event: {_cell(r['event'])}{'; dates disagree' if r.get('conflict') else ''})_"
    return text


def _conflict_lines(conflicts: list) -> list:
    lines = ["", f"**Dates that disagree ({len(conflicts)})**"]
    for c in conflicts:
        sides = []
        for r in c["facts"]:
            where, marks = _source_cell(r["sources"])
            sides.append(f"{display_date(r)} ({r['status']}; {where}; {marks})")
        lines.append(f"- **{_cell(c['event'])}**: " + " vs ".join(sides))
    lines.append("_The timeline lists both sides and does not pick one._")
    return lines


def render(data: dict, heading: str = "Timeline", conflicts_only: bool = False) -> str:
    """Markdown: a table of dated facts, then date conflicts, then undated facts, then the check
    counts and a key. conflicts_only gives the conflicts alone."""
    dated, undated, c = data["dated"], data["undated"], data["counts"]
    conflicts = data.get("conflicts") or []
    if conflicts_only:
        heading = f"Dates that disagree ({len(conflicts)})"
    lines = [f"## {heading}", ""]
    if conflicts_only:
        if not conflicts:
            lines.append("No dates disagree. Facts are compared when they share an event name "
                         "(`--event \"notice served\"`).")
            return "\n".join(lines)
        body = _conflict_lines(conflicts)
        return "\n".join(lines + body[2:-1] + ["", body[-1]])
    if not dated and not undated:
        lines.append("No facts yet. Add one with a date: `matter.py fact add <case> --text ... --status ... "
                     "--date YYYY-MM-DD --source <file>@p. N --quote \"<the words on that page>\"`."
                     if not data.get("hidden_hypotheses") else "No facts match.")
    else:
        if dated:
            lines += ["| Date | What happened | Label | Source | Check |", "|---|---|---|---|---|"]
            for r in dated:
                where, marks = _source_cell(r["sources"])
                lines.append(f"| {_cell(display_date(r))} | {_what(r)} | {r['status']} | {where} | {marks} |")
        else:
            lines.append("No dated facts yet. Give a fact a date with `matter.py fact date <case> <fact id> "
                         "--date YYYY-MM-DD`.")
        if conflicts:
            lines += _conflict_lines(conflicts)
        if undated:
            lines += ["", f"**Undated facts ({len(undated)})**"]
            for r in undated:
                where, marks = _source_cell(r["sources"])
                lines.append(f"- [{r['status']}] {_cell(r['text'])} ({where}; {marks}) `{r['id']}`")
    if data.get("hidden_hypotheses"):
        n = data["hidden_hypotheses"]
        lines += ["", f"{n} hypothes{'is' if n == 1 else 'es'} not shown; ask for hypotheses to include "
                  f"{'it' if n == 1 else 'them'}."]
    if not dated and not undated:
        return "\n".join(lines)
    checked = c[EXACT] + c[WRONG_PAGE] + c[NOT_FOUND] + c[CANT_CHECK]
    lines += ["", f"Quotes checked against the record just now: {c[EXACT]} exact, {c[WRONG_PAGE]} in the wrong place, "
              f"{c[NOT_FOUND]} not found, {c[CANT_CHECK]} can't check"
              + (f"; {c[NO_QUOTE]} source(s) with no quote to check." if c[NO_QUOTE] else ".")
              if checked or c[NO_QUOTE] else "No sources to check."]
    lines += ["", "_✓ the words are on the cited page or lines · ⚠ in the document, somewhere else · ✗ not in the "
              "document · · can't check · – no quote given. A ✓ means the words are there, not that the fact is "
              "true or the date is right. Labels are the person's: an ALLEGATION is what someone says, not a "
              "finding._"]
    return "\n".join(lines)


def summary_line(conn) -> str:
    """One line for status: facts dated, date conflicts, sources with no quote. Reads no files."""
    n = conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    if not n:
        return ""
    if not _has_tables(conn):
        return f"Timeline: 0 of {n} facts dated."
    rows = [dict(_date_dict(d), id=d["assertion_id"], created_at=d["created_at"], rowid=i)
            for i, d in enumerate(conn.execute("SELECT * FROM fact_dates").fetchall())]
    parts = [f"Timeline: {len(rows)} of {n} facts dated"]
    k = len(_conflicts(rows))
    if k:
        parts.append(f"{k} date conflict{'s' if k > 1 else ''}")
    bare = conn.execute(
        "SELECT COUNT(*) FROM assertion_sources s WHERE NOT EXISTS (SELECT 1 FROM fact_quotes q WHERE "
        "q.assertion_id=s.assertion_id AND q.document_id=s.document_id AND q.locator=COALESCE(s.locator,''))"
    ).fetchone()[0]
    if bare:
        parts.append(f"{bare} source{'s' if bare > 1 else ''} with no quote")
    return " · ".join(parts) + "."
