"""Amendments: a per-case queue of changes to make in the next version of a pleading.

Example item: "attribute all three ER injuries to the jail beating", paragraphs "¶¶ 37, 172".
The CLI, the MCP server and the web UI all call `add`, `list_`, `set_status` and `counts`, so a
change is refused or accepted the same way wherever it is entered. An item may point at facts
(assertion ids) and at registered documents (with a page or place), and it records why. A refusal
writes nothing; an add or a status change commits all its rows together. Nothing is ever deleted:
a change that is no longer wanted is marked dropped and keeps its history.

Additive tables only (store.SCHEMA); no existing table changes. `list_` and `counts` also work on
a store opened with `store.connect_readonly` that predates these tables: they report nothing.
"""
from collections.abc import Iterable

from . import subcase
from .store import new_id, now_iso

STATUSES = ("proposed", "approved", "incorporated", "dropped")
OPEN = ("proposed", "approved")  # still to do: proposed (someone suggested it) + approved (a person said yes)
LABELS = {"proposed": "Proposed", "approved": "Approved", "incorporated": "In the draft", "dropped": "Dropped"}
DEFAULT_TARGET = "complaint"
MAX_LINE = 200
TABLES = ("amendment_items", "amendment_item_links", "amendment_item_history")
_FILTERS = ", ".join(("open", "all") + STATUSES)


class AmendmentError(ValueError):
    """A refused amendment operation; nothing was written. `kind` is "not-found" for an unknown
    item id, so a front end can answer 404 instead of 400."""

    def __init__(self, msg, kind=None):
        super().__init__(msg)
        self.kind = kind


# -- checking input (no database access) --

def _one_line(value, what):
    """An optional short single-line field: None or blank -> None; anything but a string, a line
    break or more than MAX_LINE characters is refused."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise AmendmentError(f"{what} must be text")
    value = value.strip()
    if not value:
        return None
    if len(value) > MAX_LINE or "\n" in value or "\r" in value:
        raise AmendmentError(f"{what} must be one short line (at most {MAX_LINE} characters)")
    return value


def _optional_text(value, what):
    """An optional free-text field (may span lines): None or blank -> None."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise AmendmentError(f"{what} must be text")
    return value.strip() or None


def _name(value, what):
    if not isinstance(value, str) or not value.strip():
        raise AmendmentError(f"{what} is missing")
    return value


def _status(value, what="status"):
    """'Approved ' -> 'approved'. AmendmentError for anything that is not one of the four statuses."""
    if isinstance(value, str) and value.strip().lower() in STATUSES:
        return value.strip().lower()
    raise AmendmentError(f"invalid {what} '{value}' — use one of: {', '.join(STATUSES)}")


def _filter(value):
    if isinstance(value, str):
        v = value.strip().lower()
        if v == "open":
            return OPEN
        if v == "all":
            return STATUSES
        if v in STATUSES:
            return (v,)
    raise AmendmentError(f"invalid filter '{value}' — use one of: {_FILTERS}")


def _ids(values, what):
    if isinstance(values, (str, bytes, dict)) or not isinstance(values, Iterable):
        raise AmendmentError(f"{what} must be a list of ids")
    out = []
    for v in values:
        if not isinstance(v, str) or not v.strip():
            raise AmendmentError(f"each of {what} must be an id")
        if v not in out:
            out.append(v)
    return out


def _doc_links(documents):
    """[(document_id, locator)] with locators cleaned and repeats dropped. Each entry is a
    (document id, page or place) pair or a typed {"document_id", "locator"}, the form facts.add
    takes. SQLite lets NULLs repeat in a composite primary key, so a document cited twice without a
    page would be stored twice."""
    if isinstance(documents, (str, bytes, dict)) or not isinstance(documents, Iterable):
        raise AmendmentError("documents must be a list of (document id, page or place) pairs")
    out = []
    for entry in documents:
        if isinstance(entry, dict):
            doc_id, locator = entry.get("document_id"), entry.get("locator")
        elif isinstance(entry, (tuple, list)) and len(entry) == 2:
            doc_id, locator = entry
        else:
            raise AmendmentError("each document must be a (document id, page or place) pair "
                                 "or {\"document_id\", \"locator\"}")
        link = (_name(doc_id, "a document id"), _one_line(locator, "a document's page or place"))
        if link not in out:
            out.append(link)
    return out


# -- reading --

def _has_tables(conn):
    found = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN (?,?,?)",
                         TABLES).fetchall()[0][0]
    return found == len(TABLES)


def _status_of(conn, item_id):
    """The stored status of an item, or None if there is no such item."""
    rows = conn.execute("SELECT status FROM amendment_items WHERE id=?", (item_id,)).fetchall()
    return rows[0][0] if rows else None


def _links(conn, item_id):
    fact_rows = conn.execute(
        "SELECT l.ref, a.text, a.status FROM amendment_item_links l LEFT JOIN assertions a ON a.id=l.ref"
        " WHERE l.item_id=? AND l.kind='fact' ORDER BY l.rowid", (item_id,)).fetchall()
    doc_rows = conn.execute(
        "SELECT l.ref, d.rel_path, l.locator FROM amendment_item_links l LEFT JOIN documents d ON d.id=l.ref"
        " WHERE l.item_id=? AND l.kind='document' ORDER BY l.rowid", (item_id,)).fetchall()
    return {"facts": [{"id": r[0], "text": r[1], "label": r[2]} for r in fact_rows],
            "documents": [{"id": r[0], "rel_path": r[1], "locator": r[2]} for r in doc_rows]}


def _history(conn, item_id):
    rows = conn.execute(
        "SELECT from_status, to_status, note, recorded_by, created_at FROM amendment_item_history"
        " WHERE item_id=? ORDER BY created_at, rowid", (item_id,)).fetchall()
    return [{"from_status": r[0], "to_status": r[1], "note": r[2], "recorded_by": r[3], "created_at": r[4]}
            for r in rows]


def _item(conn, row):
    return {"id": row["id"], "target": row["target"], "text": row["text"], "paragraphs": row["paragraphs"],
            "reason": row["reason"], "status": row["status"], "status_label": LABELS.get(row["status"], row["status"]),
            "created_by": row["created_by"], "created_at": row["created_at"], "updated_at": row["updated_at"],
            "links": _links(conn, row["id"]), "history": _history(conn, row["id"])}


def get(conn, item_id):
    """One item with its links and history, or None."""
    if not _has_tables(conn):
        return None
    row = conn.execute("SELECT * FROM amendment_items WHERE id=?", (item_id,)).fetchall()
    return _item(conn, row[0]) if row else None


def list_(conn, status="open", target=None):
    """Items with their links and history (oldest entry first). `status` is "open" (proposed and
    approved: the work still to do), "all", or one exact status. Approved items come before proposed
    ones, then by creation, with row order breaking ties inside one second; finished and dropped
    items follow when asked for. `target` limits the list to one pleading. Returns [] on a store
    that predates the tables, so a read-only caller never crashes."""
    wanted = _filter(status)
    if target is not None and not isinstance(target, str):
        raise AmendmentError("target must be text")
    if not _has_tables(conn):
        return []
    sql = ("SELECT * FROM amendment_items WHERE status IN (%s)" % ",".join("?" * len(wanted)))
    params = list(wanted)
    if target is not None:
        sql += " AND target=?"
        params.append(target.strip())
    sql += (" ORDER BY CASE status WHEN 'approved' THEN 0 WHEN 'proposed' THEN 1 WHEN 'incorporated' THEN 2"
            " ELSE 3 END, created_at, rowid")
    return [_item(conn, r) for r in conn.execute(sql, params).fetchall()]


def counts(conn):
    """{status: n} for the four statuses plus "open" (proposed + approved). All zero on a store that
    predates the tables."""
    out = dict.fromkeys(STATUSES, 0)
    if _has_tables(conn):
        for r in conn.execute("SELECT status, COUNT(*) FROM amendment_items GROUP BY status").fetchall():
            out[r[0]] = r[1]
    out["open"] = sum(out[s] for s in OPEN)
    return out


def resolve_source(conn, ref):
    """'rel_path@locator' -> (document id, locator) for a document registered in this case. The
    split is on the LAST '@', so a locator may not hold one, and a file name that has an '@' needs
    a trailing '@' when it is cited without a page ('Evidence/a@b.txt@'). A ref with no '@' is a
    whole rel_path. Refuses a document that is not registered here."""
    if not isinstance(ref, str) or not ref.strip():
        raise AmendmentError("a source needs a document: rel_path@page")
    rel, at, locator = ref.rpartition("@")
    if not at:
        rel, locator = ref, ""
    rows = conn.execute("SELECT id FROM documents WHERE rel_path=?", (rel,)).fetchall()
    if not rows:
        raise AmendmentError(f"'{rel}' is not a document registered in this case — add the file to the case "
                             "(ingest) or check the name; use rel_path@page, splitting on the last '@'")
    return rows[0][0], (locator.strip() or None)


# -- writing --

def _begin(conn):
    """Take the write lock before reading what the write depends on, so two people changing the same
    case at once wait for each other instead of deadlocking or acting on a stale read."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def add(conn, text, paragraphs=None, reason=None, target=DEFAULT_TARGET, facts=(), documents=(), by="human",
        matter_dir=None):
    """Queue a change. `text` is what to add or change, `paragraphs` where ("¶¶ 37, 172"), `reason`
    why, `target` which pleading. `facts` are assertion ids; `documents` are (document id, page or
    place) pairs or typed {"document_id", "locator"} (as facts.add takes). `by` is the caller's
    store.attribution value. `matter_dir` is the case folder: given, a document that is a subcase's
    reference to a parent document is re-hashed against the parent before anything is written, as
    a fact's source is, and a parent that changed or went missing raises subcase.SubcaseError
    (propagated). The CLI, the MCP server and the web UI all pass it.

    Refuses empty text, an empty target, an unknown fact id, an unknown document id and malformed
    fields (AmendmentError). The item, its links and its first history row (None -> proposed) commit
    together, so a refusal or a failure writes nothing. The text is stored as given, like a fact's.
    Returns {"id", "status", "status_label", "target", "created_by", "links"}."""
    if not isinstance(text, str) or not text.strip():
        raise AmendmentError("change text is empty — say what to add or change")
    paragraphs = _one_line(paragraphs, "paragraphs")
    reason = _optional_text(reason, "reason")
    target = _one_line(target, "target")
    if target is None:
        raise AmendmentError("target is empty — say which pleading this change is for (for example: complaint)")
    fact_ids = _ids(facts, "facts")
    doc_links = _doc_links(documents)
    by = _name(by, "who is making this change")
    if matter_dir is not None and doc_links:  # parents are re-hashed before the write lock is taken
        subcase.resolve_parent_sources(matter_dir, [{"document_id": d} for d, _ in doc_links], conn)
    item_id, when = new_id("am_"), now_iso()
    with conn:
        _begin(conn)
        for fid in fact_ids:
            if not conn.execute("SELECT 1 FROM assertions WHERE id=?", (fid,)).fetchall():
                raise AmendmentError(f"unknown fact {fid} — link only facts recorded in this case")
        for doc_id, _ in doc_links:
            if not conn.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchall():
                raise AmendmentError(f"unknown document {doc_id} — link only documents registered in this case")
        conn.execute("INSERT INTO amendment_items (id,target,text,paragraphs,reason,status,created_by,created_at)"
                     " VALUES (?,?,?,?,?,'proposed',?,?)", (item_id, target, text, paragraphs, reason, by, when))
        for fid in fact_ids:
            conn.execute("INSERT INTO amendment_item_links (item_id,kind,ref,locator) VALUES (?,'fact',?,NULL)",
                         (item_id, fid))
        for doc_id, locator in doc_links:
            conn.execute("INSERT INTO amendment_item_links (item_id,kind,ref,locator) VALUES (?,'document',?,?)",
                         (item_id, doc_id, locator))
        conn.execute("INSERT INTO amendment_item_history (id,item_id,from_status,to_status,note,recorded_by,created_at)"
                     " VALUES (?,?,NULL,'proposed',NULL,?,?)", (new_id("ah_"), item_id, by, when))
    return {"id": item_id, "status": "proposed", "status_label": LABELS["proposed"], "target": target,
            "created_by": by, "links": _links(conn, item_id)}


def set_status(conn, item_id, status, note=None, by="human"):
    """Move an item to another status and record who, when and why. Any move between the four
    statuses is allowed (a dropped change can be reopened as proposed). Refuses an unknown id
    (kind "not-found"), an invalid status and a change to the status it already has. The update and
    its history row commit together; an item that someone else changed in the meantime is refused
    rather than overwritten. Returns {"id", "from_status", "status", "status_label", "note",
    "recorded_by", "created_at"}."""
    status = _status(status)
    note = _optional_text(note, "note")
    by = _name(by, "who is making this change")
    if not isinstance(item_id, str) or not item_id:
        raise AmendmentError(f"no change with id '{item_id}'", kind="not-found")
    with conn:
        _begin(conn)
        current = _status_of(conn, item_id) if _has_tables(conn) else None
        if current is None:
            raise AmendmentError(f"no change with id '{item_id}' in this case", kind="not-found")
        if current == status:
            raise AmendmentError(f"{item_id} is already {LABELS[status]}")
        when = now_iso()
        moved = conn.execute("UPDATE amendment_items SET status=?, updated_at=? WHERE id=? AND status=?",
                             (status, when, item_id, current))
        if moved.rowcount != 1:
            raise AmendmentError(f"{item_id} was changed by someone else just now — look at the list again")
        conn.execute("INSERT INTO amendment_item_history (id,item_id,from_status,to_status,note,recorded_by,created_at)"
                     " VALUES (?,?,?,?,?,?,?)", (new_id("ah_"), item_id, current, status, note, by, when))
    return {"id": item_id, "from_status": current, "status": status, "status_label": LABELS[status],
            "note": note, "recorded_by": by, "created_at": when}
