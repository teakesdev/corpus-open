"""A case law research log kept in the case: where you looked, what to look at again, and where else to look.

Looking for case law happens in many places: CourtListener through the kit, but also Google
Scholar, a court's own website, a law library. This module keeps three things in the case's
.matter/caselaw/research.jsonl (append-only; one JSON object a line):

  searches  what was searched, where, and what came of it: found, nothing, blocked, not-online,
            paywalled or error. The searches the kit itself sent to CourtListener are already kept
            by matterkit.saved_lookups; list_searches() reads them back in beside the hand-logged ones.
  leads     cases that may matter later, each with a reason and, if you like, a date to look again.
  where     for a state (or DC, or US for the federal courts), the places a court's opinions can be
            read for free and from what year, from a dated survey packaged with the kit
            (matterkit/data/caselaw_coverage.json).

Changing a record never rewrites the file: the change is appended with the same id, and the
newest record for an id is the one read. A line cut short by a crash is skipped, and the next
write starts on a fresh line. A leads entry is a note to self, not good law, and the survey is a
dated snapshot: court websites change. Every refusal raises ValueError and writes nothing.
Network-free; standard library only.
"""
import datetime as _dt
import json
import os
import re
import secrets

from . import saved_lookups
from .citations import now_iso

LOG = "research.jsonl"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "caselaw_coverage.json")

OUTCOMES = ("found", "nothing", "blocked", "not-online", "paywalled", "error")
MISSES = ("nothing", "blocked", "not-online", "paywalled")  # after these, "where else to look" helps
LEAD_STATUSES = ("saved", "revisited", "dropped", "used")
OPEN = "saved"  # the only status that still waits for a second look
LEVELS = ("supreme", "appellate", "trial", "federal-appellate", "federal-district", "bankruptcy", "other")
# Sources that cover a whole jurisdiction's published opinions, whatever the court level the row names.
BROAD_SOURCES = ("cap_static", "google_scholar_case_law")
SEARCH_ID, LEAD_ID = "rs_", "rl_"
YEARS_HELP = "years must be one year (2003) or a range (1990-2005), earliest first"
DATED = "Survey checked {date}; court sites change, so check each page yourself."

_OFFICIAL_WORDS = {"official", "official site", "official website", "court website", "courts website", "court s website",
                   "court site", "court s site", "courts site", "court web site", "state court website",
                   "state courts website"}  # how a person names a court's own site (articles dropped first)
_GENERIC_WORDS = {"the", "a", "search", "web", "website", "site", "online", "com", "org", "www", "case", "law", "bulk",
                  "free", "opinions", "cases", "caselaw"}
_COVER_RANK = {"covers": 0, "partly": 1, "unknown": 2, "does-not-cover": 3, None: 0}
_STATUS_RANK = {"accessible": 0, "partial": 1, "unknown": 2, "blocked": 3}
_FULLTEXT_RANK = {"y": 0, "unknown": 1, "n": 2}
_MISS_WORDS = {"nothing": "Nothing came of that search", "blocked": "That site blocked you",
               "not-online": "That was not online there", "paywalled": "That was behind a paywall"}


# ---------------------------------------------------------------- the log file

def _path(matter_dir: str) -> str:
    return os.path.join(matter_dir, ".matter", "caselaw", LOG)


def _require_case(matter_dir: str) -> None:
    if not os.path.isdir(os.path.join(matter_dir, ".matter")):
        raise ValueError(f"{matter_dir} is not a case folder (no .matter/)")


def _read(matter_dir: str) -> list[dict]:
    try:
        f = open(_path(matter_dir), encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    with f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue  # a line cut short by a crash; the rest still count
            if isinstance(rec, dict):
                out.append(rec)
    return out


def _append(matter_dir: str, record: dict) -> None:
    _require_case(matter_dir)
    path = _path(matter_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
    with open(path, "a+b") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        if size:  # a torn last line has no newline: start this record on a line of its own
            f.seek(size - 1)
            if f.read(1) != b"\n":
                line = b"\n" + line
        f.write(line)


def _latest(matter_dir: str, kind: str) -> list[dict]:
    """The newest record of each id of this kind, in the order the ids first appeared."""
    order, newest = [], {}
    for rec in _read(matter_dir):
        rid = rec.get("id")
        if not isinstance(rid, str) or rec.get("kind") not in ("search", "lead"):
            continue
        if rid in newest and newest[rid]["kind"] != rec["kind"]:
            continue
        if rid not in newest:
            order.append(rid)
        newest[rid] = rec
    return [newest[i] for i in order if newest[i]["kind"] == kind]


def _new_id(matter_dir: str, prefix: str) -> str:
    taken = {r.get("id") for r in _read(matter_dir)}
    while True:
        rid = prefix + secrets.token_hex(4)
        if rid not in taken:
            return rid


# ---------------------------------------------------------------- checking what a person typed

def _line(value, label: str, required: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if required and not text:
        raise ValueError(f"{label} is required")
    return text


def _para(value) -> str:
    return str(value or "").strip()


def _by(value) -> str:
    text = _line(value, "by", required=True)
    if ";" in text and not text.startswith("agent:"):
        raise ValueError("by must not contain ';'")
    return text


def _jurisdiction(value) -> str | None:
    text = _line(value, "jurisdiction")
    if not text:
        return None
    if not re.fullmatch(r"[A-Za-z][A-Za-z-]{1,15}", text):
        raise ValueError(f"jurisdiction {text!r} should be a two-letter state code such as MD, DC, or US for the federal courts")
    return text.upper()


def _url(value) -> str | None:
    text = _line(value, "url")
    if text and not re.fullmatch(r"https?://\S+", text):
        raise ValueError("url must be a web address starting with http:// or https://")
    return text or None


def _date(value, label: str) -> str | None:
    text = _line(value, label)
    if not text:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    try:
        if not m:
            raise ValueError
        _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        raise ValueError(f"{label} must be a real date written YYYY-MM-DD, not {text!r}") from None
    return text


def parse_years(value) -> tuple[int, int] | None:
    """'2003' -> (2003, 2003); '1990-2005' -> (1990, 2005); an int is one year. None for blank."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    m = re.fullmatch(r"\s*(\d{4})\s*(?:-\s*(\d{4}))?\s*", str(value))
    if not m:
        raise ValueError(f"{YEARS_HELP}, not {str(value).strip()!r}")
    first = int(m.group(1))
    last = int(m.group(2)) if m.group(2) else first
    if first > last:
        raise ValueError(f"{YEARS_HELP}, not {str(value).strip()!r}")
    if first < 1600 or last > 2100:
        raise ValueError(f"years should fall between 1600 and 2100, not {str(value).strip()!r}")
    return first, last


def _years_text(span: tuple[int, int] | None) -> str | None:
    if not span:
        return None
    return str(span[0]) if span[0] == span[1] else f"{span[0]}-{span[1]}"


# ---------------------------------------------------------------- searches

def log_search(matter_dir: str, by: str, source: str, query: str, outcome: str, jurisdiction: str | None = None,
               court: str | None = None, years: str | None = None, url: str | None = None, note: str = "") -> dict:
    """Record one search done by hand, anywhere. `source` is any words ("google scholar", "the
    county law library"); the ids that `where` lists work best, so a later search can tell what
    was already tried. Returns the saved record. Raises ValueError, writing nothing, for an
    empty source or query, an outcome not in OUTCOMES, or years that are not a year or a range."""
    _require_case(matter_dir)
    outcome = _line(outcome, "outcome").lower()
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}, not {outcome!r}")
    rec = {"kind": "search", "recorded_at": now_iso(), "by": _by(by),
           "source": _line(source, "source", required=True), "query": _line(query, "query", required=True),
           "outcome": outcome, "jurisdiction": _jurisdiction(jurisdiction), "court": _line(court, "court") or None,
           "years": _years_text(parse_years(years)), "url": _url(url), "note": _para(note)}
    rec["id"] = _new_id(matter_dir, SEARCH_ID)
    _append(matter_dir, rec)
    return rec


def _kit_searches(matter_dir: str) -> list[dict]:
    """The CourtListener searches the kit sent (or answered from the case), in the same shape."""
    out = []
    try:
        saved = saved_lookups._read(matter_dir, saved_lookups.SEARCHES)
    except ValueError:  # a line cut off inside a multi-byte character: those saved searches cannot be read
        saved = []
    for rec in saved:
        results, query = rec.get("results"), rec.get("query")
        if not isinstance(results, list) or not isinstance(query, str) or not query.strip():
            continue
        try:
            state = _jurisdiction(rec.get("jurisdiction"))
        except ValueError:
            state = None
        n = len(results)
        out.append({"id": "cl_" + str(rec.get("key") or "")[:8], "kind": "search",
                    "recorded_at": str(rec.get("retrieved_at") or ""), "by": "kit", "source": "courtlistener",
                    "query": query, "outcome": "found" if n else "nothing",
                    "jurisdiction": state, "court": None, "years": None, "url": None,
                    "note": f"{n} result{'s' if n != 1 else ''}; sent by the kit and saved in the case", "origin": "kit"})
    return out


def list_searches(matter_dir: str, outcome: str | None = None, jurisdiction: str | None = None) -> list[dict]:
    """Searches logged by hand plus the kit's CourtListener searches, oldest first. `outcome` and
    `jurisdiction` narrow the list. The kit's searches carry no state, so a jurisdiction filter
    leaves them out unless they were scoped to it."""
    if outcome is not None and outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}, not {outcome!r}")
    want = _jurisdiction(jurisdiction)
    rows = [dict(r, origin="log") for r in _latest(matter_dir, "search")] + _kit_searches(matter_dir)
    rows = [r for r in rows if (outcome is None or r.get("outcome") == outcome)
            and (want is None or r.get("jurisdiction") == want)]
    return sorted(rows, key=lambda r: str(r.get("recorded_at") or ""))


# ---------------------------------------------------------------- leads

def save_lead(matter_dir: str, by: str, title: str, why: str, revisit: str | None = None, citation: str | None = None,
              url: str | None = None, jurisdiction: str | None = None) -> dict:
    """Keep a case that may matter later, with the reason you kept it and, optionally, the day to
    look again (YYYY-MM-DD). A lead is a note to self: it is not checked and not good law.
    Raises ValueError, writing nothing, for an empty title or reason or a bad date."""
    _require_case(matter_dir)
    now = now_iso()
    rec = {"kind": "lead", "recorded_at": now, "created_at": now, "by": _by(by), "created_by": _by(by),
           "title": _line(title, "title", required=True), "why": _para(why), "status": OPEN,
           "revisit": _date(revisit, "revisit"), "citation": _line(citation, "citation") or None, "url": _url(url),
           "jurisdiction": _jurisdiction(jurisdiction), "note": ""}
    if not rec["why"]:
        raise ValueError("why is required: say why this case may matter, so you can tell later")
    rec["id"] = _new_id(matter_dir, LEAD_ID)
    _append(matter_dir, rec)
    return rec


def _sorted_leads(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (r.get("revisit") or "9999-99-99", str(r.get("created_at") or "")))


def leads(matter_dir: str, status: str = "open") -> list[dict]:
    """Leads, soonest revisit date first and undated last. status: 'open' (still saved, the
    default), 'all', or one of LEAD_STATUSES."""
    if status not in ("open", "all") + LEAD_STATUSES:
        raise ValueError(f"status must be open, all, or one of {', '.join(LEAD_STATUSES)}, not {status!r}")
    rows = _latest(matter_dir, "lead")
    if status == "open":
        rows = [r for r in rows if r.get("status") == OPEN]
    elif status != "all":
        rows = [r for r in rows if r.get("status") == status]
    return _sorted_leads(rows)


def update_lead(matter_dir: str, by: str, lead_id: str, status: str | None = None, revisit: str | None = None,
                note: str = "") -> dict:
    """Change a lead's status, its revisit date, or add a note. Appends a new record with the same
    id (the old one stays in the file). Raises ValueError, writing nothing, for an id that is not a
    lead in this case, an unknown status, a bad date, or a change that changes nothing."""
    _require_case(matter_dir)
    old = next((r for r in _latest(matter_dir, "lead") if r["id"] == lead_id), None)
    if old is None:
        raise ValueError(f"no lead {lead_id!r} in this case; `matter.py research leads <case> --status all` lists them")
    if status is not None and status not in LEAD_STATUSES:
        raise ValueError(f"status must be one of {', '.join(LEAD_STATUSES)}, not {status!r}")
    revisit, note = _date(revisit, "revisit"), _para(note)
    changes = {}
    if status is not None and status != old.get("status"):
        changes["status"] = status
    if revisit is not None and revisit != old.get("revisit"):
        changes["revisit"] = revisit
    if not changes and not note:
        raise ValueError(f"nothing to change: lead {lead_id} is already that way (give a new status, a new revisit "
                         "date or a note)")
    rec = dict(old, **changes, recorded_at=now_iso(), by=_by(by), note=note or old.get("note") or "")
    _append(matter_dir, rec)
    return rec


def _as_date(value) -> _dt.date:
    if value is None:
        return _dt.date.today()
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    text = _date(value, "as-of")
    return _dt.date.fromisoformat(text) if text else _dt.date.today()


def due(matter_dir: str, as_of=None) -> dict:
    """Open leads whose revisit date has come (on or before `as_of`, default today), oldest first,
    each with days_over; and, apart, the open leads that have no date at all."""
    day = _as_date(as_of)
    opened = leads(matter_dir, "open")
    ready = [dict(r, days_over=(day - _dt.date.fromisoformat(r["revisit"])).days)
             for r in opened if r.get("revisit") and r["revisit"] <= day.isoformat()]
    return {"as_of": day.isoformat(), "due": ready, "undated": [r for r in opened if not r.get("revisit")]}


# ---------------------------------------------------------------- where else to look

_loaded: dict = {}


def load_coverage(path: str | None = None) -> dict:
    """The survey: rows as dicts (with source name and link filled in), the sources by id, and
    the gap sentences by state. Read once per file."""
    path = path or DATA
    if path in _loaded:
        return _loaded[path]
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError(f"the case law coverage survey could not be read ({path}): {e}") from None
    if raw.get("schema_version") != 1:
        raise ValueError(f"unknown coverage file version {raw.get('schema_version')!r}; this kit reads version 1")
    sources = {s["id"]: s for s in raw["sources"]}
    rows = []
    for values in raw["rows"]:
        row = dict(zip(raw["columns"], values))
        src = sources.get(row["source"]) or {}
        rows.append({"source_id": row["source"], "source": src.get("name") or row["source"],
                     "url": row.get("url") or src.get("url"), "court": row["court"], "level": row["level"],
                     "jurisdiction": row["jurisdiction"], "year_from": row["year_from"], "year_to": row["year_to"],
                     "fulltext": row["fulltext"], "status": row["status"], "basis": row["basis"],
                     "checked_at": row["checked_at"], "note": row["note"]})
    data = {"checked_at": raw["checked_at"], "legend": raw.get("legend", {}), "sources": sources, "rows": rows,
            "gaps": raw.get("gaps", {}), "general_gaps": raw.get("general_gaps", []),
            "jurisdictions": sorted({r["jurisdiction"] for r in rows})}
    _loaded[path] = data
    return data


def _covers(row: dict, span: tuple[int, int] | None) -> str | None:
    """Does this place hold opinions from these years? covers / partly / does-not-cover / unknown."""
    if span is None:
        return None
    first, last = span
    start, end = row["year_from"], row["year_to"]
    if start is None and end is None:
        return "unknown"
    if (start is not None and last < start) or (end is not None and first > end):
        return "does-not-cover"
    if (start is not None and first < start) or (end is not None and last > end):
        return "partly"
    if start is None:
        return "unknown"
    return "covers"


def _kind_rank(source_id: str) -> int:
    if source_id.startswith("official_") or source_id == "scotus_official":
        return 0
    return {"google_scholar_case_law": 1, "courtlistener_bulk": 2, "govinfo_uscourts": 3, "cap_static": 4}.get(source_id, 5)


def where(jurisdiction: str, court_level: str | None = None, year=None, court: str | None = None,
          limit: int | None = None, data: dict | None = None) -> dict:
    """Where a jurisdiction's opinions can be read for free, from a dated survey.

    jurisdiction: a two-letter state code, DC, or US for the federal courts. court_level narrows to
    one of LEVELS (Google Scholar and the Harvard bulk copy cover every level but trial, so they
    stay). year is one year or a range such as 1990-2005: each place is marked covers, partly,
    does-not-cover or unknown for it, and covering places come first. court keeps courts whose name
    holds those words. Raises ValueError for a jurisdiction the survey has no rows for. The answer
    carries the survey's checked_at: court websites change."""
    data = data or load_coverage()
    j = _line(jurisdiction, "jurisdiction", required=True).upper()
    if j not in data["jurisdictions"]:
        raise ValueError(f"the survey has nothing for {j!r}; use a two-letter state code, DC, or US for the federal "
                         "courts")
    if court_level is not None and court_level not in LEVELS:
        raise ValueError(f"level must be one of {', '.join(LEVELS)}, not {court_level!r}")
    span = parse_years(year)
    words = _line(court, "court").casefold()
    rows = []
    for r in data["rows"]:
        if r["jurisdiction"] != j:
            continue
        broad = r["source_id"] in BROAD_SOURCES
        if court_level and r["level"] != court_level and not (broad and court_level != "trial"):
            continue
        if words and words not in r["court"].casefold() and not broad:
            continue
        rows.append(dict(r, covers=_covers(r, span)))
    rows.sort(key=lambda r: (_COVER_RANK[r["covers"]], _STATUS_RANK.get(r["status"], 2), _FULLTEXT_RANK.get(r["fulltext"], 1),
                             _kind_rank(r["source_id"]), r["court"].casefold(), r["source_id"]))
    total = len(rows)
    if limit is not None:
        rows = rows[:max(0, limit)]
    return {"jurisdiction": j, "level": court_level, "year": _years_text(span), "court": words or None,
            "checked_at": data["checked_at"], "note": DATED.format(date=data["checked_at"]), "total": total,
            "rows": rows, "gaps": list(data["gaps"].get(j, [])), "general_gaps": list(data["general_gaps"]),
            "legend": data["legend"]}


def _norm(text) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").casefold()).strip()


def tried(row: dict, said: str) -> bool:
    """Is this row the place a person said they just searched? 'google scholar', 'scholar',
    'courtlistener', 'the court website' and the ids that `where` prints all name a place."""
    wanted = " ".join(w for w in _norm(said).split() if w not in ("the", "a", "an", "my", "our", "its"))
    if wanted in _OFFICIAL_WORDS:
        return row["source_id"].startswith("official_") or row["source_id"] == "scotus_official"
    words = set(wanted.split()) - _GENERIC_WORDS
    have = set(_norm(row["source_id"]).split()) | set(_norm(row["source"]).split())
    return bool(words) and words <= have


def hints(jurisdiction: str, tried_source: str | None = None, court_level: str | None = None, years=None,
          court: str | None = None, limit: int = 5, data: dict | None = None) -> dict:
    """The best few places to try next for a jurisdiction (and years), leaving out the place just
    tried and any place that cannot hold those years. Same shape as where(), plus `message` when
    the survey has nothing. A court name that matches nothing is dropped rather than leave the person empty-handed."""
    try:
        res = where(jurisdiction, court_level, years, court, data=data)
        if court and all(r["source_id"] in BROAD_SOURCES for r in res["rows"]):  # no court has that name
            res = where(jurisdiction, court_level, years, None, data=data)
    except ValueError as e:
        return {"jurisdiction": str(jurisdiction or "").strip().upper(), "rows": [], "total": 0, "gaps": [],
                "general_gaps": [], "checked_at": None, "message": str(e)}
    rows = [r for r in res["rows"] if r["covers"] != "does-not-cover" and not (tried_source and tried(r, tried_source))]
    res["total"], res["rows"] = len(rows), rows[:max(0, limit)]
    return res


# ---------------------------------------------------------------- plain text for the CLI and MCP

def _where_row(r: dict) -> str:
    start, end = r["year_from"], r["year_to"]
    years = ("years " + (f"{start}-{end}" if end else f"{start} on") + f" ({r['basis']})") if start else \
        (f"through {end}" if end else "years not known")
    mark = f"[{r['covers']}] " if r.get("covers") else ""
    lines = [f"- {mark}{r['source']}: {r['court']} [{r['level']}]  <{r['source_id']}>"]
    if r["url"]:
        lines.append(f"    {r['url']}")
    lines.append(f"    {years}; full-text search: {({'y': 'yes', 'n': 'no'}).get(r['fulltext'], 'not known')}; "
                 f"status: {r['status']}")
    if r["note"]:
        lines.append(f"    {r['note']}")
    return "\n".join(lines)


def _legend_line(res: dict) -> str:
    legend = res.get("legend") or {}
    words = [f"{k} = {legend[k]}" for k in ("CAP", "OFF", "CL") if k in legend]
    return "In these notes: " + "; ".join(words) + "." if words else ""


def render_where(res: dict) -> str:
    """The where() answer as lines a person can read."""
    if res.get("message"):
        return res["message"]
    quals = ([f"{res['level']} courts"] if res.get("level") else []) + ([f"year {res['year']}"] if res.get("year") else []) \
        + ([f"courts named like {res['court']!r}"] if res.get("court") else [])
    lines = [f"Where to find case law for {res['jurisdiction']}" + (f" ({', '.join(quals)})" if quals else "")
             + f": {res['total']} place{'s' if res['total'] != 1 else ''}. " + res["note"]]
    lines += [_where_row(r) for r in res["rows"]]
    if res["total"] > len(res["rows"]):
        lines.append(f"... {res['total'] - len(res['rows'])} more; narrow it with --level, --court or --year, "
                     "or raise --limit.")
    if not res["rows"] and res.get("level") == "trial":
        lines.append("The survey found no statewide free source for trial court opinions.")
    if res["gaps"]:
        lines.append(f"Known gaps in {res['jurisdiction']}:")
        lines += [f"  {g}" for g in res["gaps"]]
        if _legend_line(res):
            lines.append("  " + _legend_line(res))
    if res.get("general_gaps") and res.get("level") in (None, "trial") and res["jurisdiction"] != "US":
        lines += [f"  {g}" for g in res["general_gaps"]]
    lines.append("'blocked' means the survey's automated check was refused; the page may still open in your own "
                 "browser. Anything found here is a lead, not good law: read the opinion itself.")
    return "\n".join(lines)


def render_hints(res: dict, outcome: str) -> str:
    """After a search that came to nothing: a few places to try next, from the survey."""
    head = f"{_MISS_WORDS.get(outcome, 'That did not work out')}. "
    if res.get("message"):
        return head + res["message"]
    if not res["rows"]:
        return head + f"The survey lists no other place for {res['jurisdiction']} to try."
    lines = [head + f"Other places to look in {res['jurisdiction']}"
             + (f" for {res['year']}" if res.get("year") else "") + f" (survey checked {res['checked_at']}; court sites change):"]
    lines += [_where_row(r) for r in res["rows"]]
    if res["gaps"]:
        lines.append(f"Known gap: {res['gaps'][0]}")
    lines.append(f"More: python3 matter.py research where {res['jurisdiction']}")
    return "\n".join(lines)


def render_searches(rows: list[dict]) -> str:
    if not rows:
        return "No searches logged."
    out = []
    for r in rows:
        where_ = " ".join(x for x in (r.get("jurisdiction"), r.get("court"), r.get("years")) if x)
        out.append(f"{r['id']}  {str(r.get('recorded_at') or '')[:10]}  {r.get('outcome', ''):<10}  "
                   f"{r.get('source', '')}: {r.get('query', '')}" + (f"  ({where_})" if where_ else "")
                   + f"  by {r.get('by') or 'unrecorded'}")
        for extra in (r.get("url"), r.get("note")):
            if extra:
                out.append(f"    {extra}")
    return "\n".join(out)


def render_leads(rows: list[dict]) -> str:
    if not rows:
        return "No leads."
    out = []
    for r in rows:
        head = f"{r['id']}  [{r.get('status')}]  {r.get('title')}"
        if r.get("citation"):
            head += f"  ({r['citation']})"
        if r.get("revisit"):
            head += f"  look again {r['revisit']}"
        if r.get("days_over"):
            head += f" ({r['days_over']} day{'s' if r['days_over'] != 1 else ''} ago)"
        out.append(head)
        out.append(f"    why: {r.get('why')}")
        for extra in (f"jurisdiction: {r['jurisdiction']}" if r.get("jurisdiction") else None, r.get("url"),
                      f"note: {r['note']}" if r.get("note") else None):
            if extra:
                out.append(f"    {extra}")
        out.append(f"    saved by {r.get('created_by') or 'unrecorded'}, {str(r.get('created_at') or '')[:10]}"
                   + (f"; last changed by {r['by']}, {str(r.get('recorded_at'))[:10]}" if r.get("recorded_at") != r.get("created_at") else ""))
    return "\n".join(out)


def render_due(res: dict) -> str:
    lines = [f"Due on or before {res['as_of']}: {len(res['due'])}"]
    lines.append(render_leads(res["due"]) if res["due"] else "  Nothing is due.")
    lines.append(f"No revisit date: {len(res['undated'])}")
    if res["undated"]:
        lines.append(render_leads(res["undated"]))
    return "\n".join(lines)
