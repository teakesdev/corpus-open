"""Facts (assertions): the one place the label and source rules live.

The CLI, the MCP server and the web UI all call `add` and `list_`, so a fact is refused or
accepted the same way wherever it is entered. No schema change: this is the `assertions` and
`assertion_sources` code that used to be written out in both matter.py and matter-mcp.py.
"""
from . import store, subcase, timeline
from .store import new_id, now_iso


class FactError(ValueError):
    """A refused fact operation; nothing was written."""


def normalize_status(s):
    """'documented-fact' / 'Documented Fact' -> 'DOCUMENTED FACT'. FactError for anything else."""
    if isinstance(s, str):
        label = s.replace("-", " ").upper().strip()
        if label in store.VALID_STATUS:
            return label
    valid = ", ".join(sorted(v.lower().replace(" ", "-") for v in store.VALID_STATUS))
    raise FactError(f"invalid status '{s}' — use one of: {valid}")


def add(conn, matter_dir, text, status, sources=(), by="human", date=None, date_end=None,
        approximate=False, inferred=False, quotes=None):
    """Register an assertion with its sources. A source is a 'ref@locator' string (ref is a
    rel_path, a document id, or 'parent:rel_path' in a subcase) or a typed
    {"document_id": id, "locator": text} that names one document by its exact id, as the web UI
    sends. The caller composes `by` (store.attribution).

    `date` (YYYY-MM-DD, YYYY-MM or YYYY), `date_end`, `approximate` and `inferred` put the fact on
    the timeline (matterkit.timeline). `quotes` runs alongside `sources`: quotes[i] is the exact
    words sources[i] holds for the fact, or None. The timeline checks each quote against the
    record every time it is shown; nothing about the check is stored.

    Refuses an invalid label (FactError), empty text (FactError), a bad date, a quote with no
    source or on a source that is not a registered document (FactError), and a parent document
    that cannot be cited (subcase.SubcaseError, propagated), however it is named: a subcase's
    reference row picked by id is re-hashed against the parent just as a 'parent:' ref is.
    Parents are re-hashed before anything is written and the insert plus its sources, date and
    quotes commit together, so a refusal writes nothing. A source with no quote that is not a
    registered document is reported in "unrecognized", not an error."""
    status = normalize_status(status)
    if not isinstance(text, str) or not text.strip():
        raise FactError("fact text is empty — say what the fact is")
    sources = list(sources or ())
    for s in sources:
        if not (isinstance(s, str) or (isinstance(s, dict) and isinstance(s.get("document_id"), str)
                                       and isinstance(s.get("locator") or "", str))):
            raise FactError(f"a source must be 'path@locator' or a document id, not {s!r}")
    quotes = list(quotes or ())
    if len(quotes) > len(sources):
        raise FactError(f"{len(quotes)} quote(s) for {len(sources)} source(s): each quote goes with the "
                        "source in the same position")
    for q in quotes:
        if q is not None and not isinstance(q, str):
            raise FactError(f"a quote must be text, not {q!r}")
    if date not in (None, ""):
        try:
            timeline.check_date(date, date_end)
        except timeline.TimelineError as e:
            raise FactError(str(e)) from None
    elif date_end not in (None, "") or approximate or inferred:
        raise FactError("a date range, 'approximate' or 'inferred' needs a date")
    parents = subcase.resolve_parent_sources(matter_dir, sources, conn)
    aid = new_id("a_")
    resolved: dict = {}
    with conn:
        conn.execute("INSERT INTO assertions (id,text,status,created_by,created_at) VALUES (?,?,?,?,?)",
                     (aid, text, status, by, now_iso()))
        unrecognized, notes = subcase.attach_sources(conn, aid, sources, parents, resolved)
        for i, q in enumerate(quotes):
            if q is None or not q.strip():
                continue
            if i not in resolved:
                raise FactError(f"the quote {q!r} goes with a source that is not a registered document "
                                f"({sources[i]!r}); register the file first")
            doc_id, loc = resolved[i]
            conn.execute("INSERT OR REPLACE INTO fact_quotes (assertion_id,document_id,locator,quote) VALUES (?,?,?,?)",
                         (aid, doc_id, loc or "", q.strip()))
        dated = timeline.write_date(conn, aid, date, date_end, approximate, inferred, by) if date else None
    n = conn.execute("SELECT COUNT(*) c FROM assertion_sources WHERE assertion_id=?", (aid,)).fetchone()["c"]
    return {"id": aid, "status": status, "created_by": by, "sources_attached": n,
            "unsupported": n == 0, "unrecognized": unrecognized, "notes": notes, "date": dated,
            "quotes": sum(1 for q in quotes if q and q.strip())}


def list_(conn, status=None):
    """Assertions ordered by label then creation, each with its sources. `status` is an exact
    stored label (callers normalize it); a falsy value lists everything. "unsupported" is true
    when no document backs the fact; "date" is the fact's timeline date or None. rowid breaks
    ties inside one second."""
    q = "SELECT * FROM assertions" + (" WHERE status=?" if status else "") + " ORDER BY status, created_at, rowid"
    out = []
    dated = timeline._has_tables(conn)  # a read-only connection to an old store has no fact_dates
    for r in conn.execute(q, (status,) if status else ()).fetchall():
        srcs = conn.execute(
            "SELECT d.rel_path, s.locator FROM assertion_sources s JOIN documents d ON d.id=s.document_id"
            " WHERE s.assertion_id=?", (r["id"],)).fetchall()
        d = conn.execute("SELECT * FROM fact_dates WHERE assertion_id=?", (r["id"],)).fetchone() if dated else None
        out.append({"id": r["id"], "text": r["text"], "status": r["status"], "created_by": r["created_by"],
                    "created_at": r["created_at"],
                    "sources": [{"rel_path": x["rel_path"], "locator": x["locator"]} for x in srcs],
                    "unsupported": not srcs,
                    "date": None if d is None else {
                        "date_start": d["date_start"], "date_end": d["date_end"], "precision": d["precision"],
                        "approximate": bool(d["approximate"]), "inferred": d["date_basis"] == "inferred"}})
    return out
