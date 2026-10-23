"""Subcases: a matter of its own, nested at <parent>/Subcases/<slug>/.

A subcase has its own `.matter/` store and a `matter.json` naming its parent. The
parent never registers a subcase's files (a nested `.matter/` is a boundary, see
ingest.walk_files) and a subcase never writes to the parent store: anything it reads
from the parent goes through store.connect_readonly.
"""
import json
import os
import re
import shutil
import sqlite3
from collections import namedtuple

from . import deadlines, ingest, store
from .ingest import PARENT_PREFIX
from .store import new_id, now_iso

MANIFEST = "matter.json"
SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")


class SubcaseError(ValueError):
    """A refused subcase operation; nothing was written. `kind` (ESCAPED | MISSING | CHANGED)
    says how a parent reference failed, for verify's three counts."""

    def __init__(self, msg, kind=None):
        super().__init__(msg)
        self.kind = kind


ParentDoc = namedtuple("ParentDoc", "rel_path doc_id sha256 kind bytes mtime path")


def validate_slug(slug):
    # the regex already rules out separators; ".." is refused as well, as a belt-and-braces rule
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug) or ".." in slug:
        raise SubcaseError(f"invalid slug {slug!r} — 1-81 chars of A-Z a-z 0-9 . _ -, starting with a "
                           "letter or digit, no path separators or '..'")


def init(parent_dir, slug, title, by, folders):
    """Create <parent>/Subcases/<slug>/ as its own matter. Returns the subcase path."""
    validate_slug(slug)
    if not (title or "").strip():
        raise SubcaseError("--title is required and must not be blank")
    try:
        store.connect_readonly(parent_dir).close()  # a parent must already have a store; never create one
    except FileNotFoundError as e:
        raise SubcaseError(f"{e} — run `matter.py init` on the parent first") from None
    subcases = os.path.join(parent_dir, "Subcases")
    if os.path.islink(subcases):
        raise SubcaseError(f"{subcases} is a symlink — refusing to create a matter through it")
    if os.path.lexists(subcases) and not os.path.isdir(subcases):
        raise SubcaseError(f"{subcases} exists and is not a folder")
    path = os.path.join(subcases, slug)
    if os.path.lexists(path):
        raise SubcaseError(f"{path} already exists")
    os.makedirs(path)  # created here, so safe to remove again if setup fails below
    try:
        for f in folders:
            os.makedirs(os.path.join(path, f), exist_ok=True)
        store.connect(path).close()
        manifest = {"parent": "../..", "relation": "subcase", "title": title.strip(),
                    "created_at": now_iso(), "created_by": by}
        with open(os.path.join(path, MANIFEST), "w") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except BaseException:
        shutil.rmtree(path, ignore_errors=True)
        raise
    return path


def read_manifest(matter_dir):
    """matter.json as a dict, or None if absent, unreadable or not an object."""
    try:
        with open(os.path.join(matter_dir, MANIFEST), encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return None
    return m if isinstance(m, dict) else None


def parent_dir_of(matter_dir):
    """Absolute parent matter dir named by matter.json "parent" (relative to the subcase)."""
    m = read_manifest(matter_dir)
    rel = m.get("parent") if m else None
    if not isinstance(rel, str) or not rel.strip():
        raise SubcaseError(f"not a subcase: no {MANIFEST} with a \"parent\" in {os.path.abspath(matter_dir)}")
    me = os.path.abspath(matter_dir)
    parent = os.path.normpath(os.path.join(me, rel))
    if os.path.realpath(parent) == os.path.realpath(me):
        raise SubcaseError(f"{MANIFEST} names the subcase itself as its parent")
    return parent


class ParentView:
    """Read-only view of a subcase's parent. Opens the parent store with connect_readonly only
    (never connect(): that runs schema and migrations), lazily, and never writes to it."""

    def __init__(self, matter_dir):
        self.matter_dir = matter_dir
        self._conn = None
        self._err = None
        self.parent = None
        try:
            self.parent = parent_dir_of(matter_dir)
        except SubcaseError as e:
            self._err = e

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _open(self):
        if self._err:
            raise self._err
        if self._conn is None:
            try:
                self._conn = store.connect_readonly(self.parent)
            except FileNotFoundError:
                self._err = SubcaseError(f"parent matter has no store at {store.db_path(self.parent)}", "MISSING")
                raise self._err from None
        return self._conn

    def locate(self, rel):
        """(parent documents row, absolute path) for a document the parent registers and still has on disk."""
        if ingest._invalid_rel(rel):
            raise SubcaseError(f"parent reference {rel!r} escapes the parent matter (absolute path or '..')",
                               "ESCAPED")
        rel = os.path.normpath(rel)
        try:
            row = self._open().execute(
                "SELECT id, sha256, rel_path, kind, bytes, mtime FROM documents WHERE rel_path=?", (rel,)).fetchone()
        except sqlite3.DatabaseError as e:
            raise SubcaseError(f"parent store unreadable: {e}", "MISSING") from None
        if row is None:
            raise SubcaseError(f"{rel} is not registered in the parent matter", "MISSING")
        path = ingest.resolve_document_path(self.parent, row["rel_path"])
        if path is None:
            raise SubcaseError(f"parent reference {rel!r} escapes the parent matter", "ESCAPED")
        if not os.path.isfile(path):
            raise SubcaseError(f"{rel} is missing in parent: registered there but not on disk", "MISSING")
        return row, path

    def document(self, rel):
        """A parent document that may be cited now: registered, on disk, and still hashing to the
        sha256 the parent registered. Re-hashed on every call; never cached."""
        row, path = self.locate(rel)
        if ingest.sha256_file(path) != row["sha256"]:
            raise SubcaseError(f"{row['rel_path']} changed in parent: its bytes no longer match the "
                               "sha256 the parent registered", "CHANGED")
        return ParentDoc(row["rel_path"], row["id"], row["sha256"], row["kind"], row["bytes"], row["mtime"], path)

    def check(self, rel, recorded_sha):
        """verify's verdict on a stored reference: None if ok, else CHANGED | MISSING | ESCAPED.
        Ok means the parent still registers it, and file, parent record and our record all agree."""
        try:
            row, path = self.locate(rel)
        except SubcaseError as e:
            return e.kind or "MISSING"
        return None if ingest.sha256_file(path) == recorded_sha == row["sha256"] else "CHANGED"


def resolve_parent_sources(case_dir, sources):
    """{raw ref: ParentDoc} for every `parent:<rel_path>[@loc]` in sources, each re-hashed against
    the parent now. Raises SubcaseError for the first that cannot be cited; read-only."""
    refs = []
    for s in sources or []:
        ref = s.partition("@")[0]
        if ref.startswith(PARENT_PREFIX) and ref not in refs:
            refs.append(ref)
    if not refs:
        return {}
    view = ParentView(case_dir)
    try:
        out = {}
        for ref in refs:
            try:
                out[ref] = view.document(ref[len(PARENT_PREFIX):])
            except SubcaseError as e:
                raise SubcaseError(f"{ref}: {e}", e.kind) from None
        return out
    finally:
        view.close()


def register_parent_ref(conn, pdoc):
    """Record a parent document in the subcase store without copying bytes. Returns
    (local document id, note or None). Inserts a row once; a row already holding the same bytes
    (a local copy, or an earlier reference) is reused. Does not commit."""
    local = PARENT_PREFIX + pdoc.rel_path
    row = conn.execute("SELECT id, sha256 FROM documents WHERE rel_path=?", (local,)).fetchone()
    if row:
        if row["sha256"] != pdoc.sha256:
            raise SubcaseError(f"{local} is already registered here at a different sha256 than the parent "
                               "registers now; run verify before citing it again", "CHANGED")
        return row["id"], None
    row = conn.execute("SELECT id, rel_path FROM documents WHERE sha256=?", (pdoc.sha256,)).fetchone()
    if row:  # documents.sha256 is UNIQUE: the same bytes are already here under another path
        return row["id"], (f"reused existing document {row['rel_path']} (same sha256 as parent "
                           f"{pdoc.rel_path}); no parent reference row added")
    doc_id = new_id()
    conn.execute("INSERT INTO documents (id, sha256, rel_path, bytes, mtime, kind, notes) VALUES (?,?,?,?,?,?,?)",
                 (doc_id, pdoc.sha256, local, pdoc.bytes, pdoc.mtime, pdoc.kind,
                  f"reference to parent matter document {pdoc.doc_id}; bytes live in the parent"))
    return doc_id, f"referenced parent document {pdoc.rel_path} ({pdoc.doc_id}); bytes stay in the parent"


def attach_sources(conn, aid, sources, parents):
    """Attach `rel_path@locator` sources to assertion `aid`. `parents` is resolve_parent_sources'
    result, so parent refs were re-hashed before anything was written. Returns (unrecognized
    refs, notes). Does not commit; a SubcaseError here means roll back."""
    unrecognized, notes = [], []
    for s in sources or []:
        ref, _, loc = s.partition("@")
        if ref in parents:
            doc_id, note = register_parent_ref(conn, parents[ref])
            if note:
                notes.append(note)
        else:
            row = conn.execute("SELECT id FROM documents WHERE rel_path=? OR id=?", (ref, ref)).fetchone()
            if not row:
                unrecognized.append(ref)
                continue
            doc_id = row["id"]
        conn.execute("INSERT OR IGNORE INTO assertion_sources VALUES (?,?,?)", (aid, doc_id, loc or None))
    return unrecognized, notes


def parent_line(matter_dir):
    """For `status` on a subcase: one line naming its parent. None for an ordinary matter."""
    m = read_manifest(matter_dir)
    if not m or not isinstance(m.get("parent"), str) or not m["parent"].strip():
        return None
    title = f" · title: {m['title']}" if m.get("title") else ""
    try:
        parent = parent_dir_of(matter_dir)
    except SubcaseError as e:
        return f"Subcase{title} — ⚠ {e}"
    return f"Subcase of: {os.path.basename(parent) or parent} ({parent}){title}"


def children_lines(matter_dir):
    """For `status` on a parent: its subcases (immediate children of Subcases/ holding both a store
    and a matter.json) with open and OVERDUE deadline counts. Read-only: connect_readonly, so
    listing never creates, migrates or writes a child store. Empty list when there are none."""
    root = os.path.join(matter_dir, "Subcases")
    if not os.path.isdir(root) or os.path.islink(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if os.path.islink(d) or not os.path.isdir(d):
            continue
        m = read_manifest(d)
        if m is None or not os.path.isfile(store.db_path(d)):
            continue
        title = str(m.get("title") or name)
        try:
            conn = store.connect_readonly(d)
            try:
                due = conn.execute("SELECT due_date, state FROM deadlines WHERE state='open'").fetchall()
            finally:
                conn.close()
            late = sum(deadlines.is_overdue(r["due_date"], r["state"]) for r in due)
            detail = f"Open deadlines: {len(due)} · {'⚠ ' if late else ''}OVERDUE: {late}"
        except (sqlite3.DatabaseError, FileNotFoundError):
            detail = "⚠ store unreadable"
        out.append(f"- {name} — {title} · {detail}")
    return [f"Subcases: {len(out)}"] + out if out else []
