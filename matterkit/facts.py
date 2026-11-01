"""Facts (assertions): the one place the label and source rules live.

The CLI, the MCP server and the web UI all call `add` and `list_`, so a fact is refused or
accepted the same way wherever it is entered. No schema change: this is the `assertions` and
`assertion_sources` code that used to be written out in both matter.py and matter-mcp.py.
"""
from . import store, subcase
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


def add(conn, matter_dir, text, status, sources=(), by="human"):
    """Register an assertion with its sources. `sources` are 'rel_path@locator' strings, or
    'parent:rel_path@locator' in a subcase. The caller composes `by` (store.attribution).

    Refuses an invalid label (FactError), empty text (FactError) and a parent reference that
    cannot be cited (subcase.SubcaseError, propagated); parents are re-hashed before anything
    is written and the insert plus its sources commit together, so a refusal writes nothing.
    A source that is not a registered document is reported in "unrecognized", not an error."""
    status = normalize_status(status)
    if not isinstance(text, str) or not text.strip():
        raise FactError("fact text is empty — say what the fact is")
    sources = list(sources or ())
    parents = subcase.resolve_parent_sources(matter_dir, sources)
    aid = new_id("a_")
    with conn:
        conn.execute("INSERT INTO assertions (id,text,status,created_by,created_at) VALUES (?,?,?,?,?)",
                     (aid, text, status, by, now_iso()))
        unrecognized, notes = subcase.attach_sources(conn, aid, sources, parents)
    n = conn.execute("SELECT COUNT(*) c FROM assertion_sources WHERE assertion_id=?", (aid,)).fetchone()["c"]
    return {"id": aid, "status": status, "created_by": by, "sources_attached": n,
            "unsupported": n == 0, "unrecognized": unrecognized, "notes": notes}


def list_(conn, status=None):
    """Assertions ordered by label then creation, each with its sources. `status` is an exact
    stored label (callers normalize it); a falsy value lists everything. "unsupported" is true
    when no document backs the fact. rowid breaks ties inside one second."""
    q = "SELECT * FROM assertions" + (" WHERE status=?" if status else "") + " ORDER BY status, created_at, rowid"
    out = []
    for r in conn.execute(q, (status,) if status else ()).fetchall():
        srcs = conn.execute(
            "SELECT d.rel_path, s.locator FROM assertion_sources s JOIN documents d ON d.id=s.document_id"
            " WHERE s.assertion_id=?", (r["id"],)).fetchall()
        out.append({"id": r["id"], "text": r["text"], "status": r["status"], "created_by": r["created_by"],
                    "created_at": r["created_at"],
                    "sources": [{"rel_path": x["rel_path"], "locator": x["locator"]} for x in srcs],
                    "unsupported": not srcs})
    return out
