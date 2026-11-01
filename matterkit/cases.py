"""Cases: setting one up, and finding them in a workspace.

A workspace is a folder whose direct children are case folders; a case folder holds
`.matter/matter.db`. Subcases sit under their parent at `<case>/Subcases/<slug>/` (see subcase.py).
`discover` is the only thing that turns a case id into a path: ids are derived from the
workspace-relative path, so nothing a caller sends can name a folder outside the workspace.
"""
import hashlib
import os
import posixpath
import re
import shutil

from . import ingest, store, subcase

FOLDERS = ["Filings", "Evidence", "Correspondence", "Case_Tracking", "Research", "Media"]
SUBCASES = "Subcases"
MAX_SUBCASE_DEPTH = 3  # levels of subcases listed below a top-level case
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.,()&'-]*")


class CaseError(ValueError):
    """A refused case operation; nothing was written."""


def init(case_dir):
    """Create the case folders and the store (idempotent). Returns how many documents are registered."""
    os.makedirs(case_dir, exist_ok=True)
    for f in FOLDERS:
        os.makedirs(os.path.join(case_dir, f), exist_ok=True)
    conn = store.connect(case_dir)
    try:
        return conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
    finally:
        conn.close()


def is_case(path):
    return os.path.isfile(store.db_path(path))


def _entry(rel, path, parent_id):
    m = subcase.read_manifest(path)
    title = m.get("title") if m else None
    if not isinstance(title, str) or not title.strip():
        title = os.path.basename(path)
    return {"id": hashlib.sha256(rel.encode("utf-8")).hexdigest()[:16], "rel": rel, "path": path,
            "title": title, "parent_id": parent_id}


def _children(path, real_root):
    """(name, path) of the child folders worth looking at, sorted: no dot-folders, no symlinks,
    nothing that resolves outside the workspace."""
    try:
        names = os.listdir(path)
    except OSError:
        return []
    out = []
    for name in sorted(names, key=lambda n: (n.lower(), n)):
        d = os.path.join(path, name)
        if name.startswith(".") or os.path.islink(d) or not os.path.isdir(d):
            continue
        if not ingest.is_within_matter(os.path.realpath(d), real_root):
            continue
        out.append((name, d))
    return out


def _subcases(parent, real_root, depth=1):
    """Entries for the subcases under `parent`, depth-first, down to MAX_SUBCASE_DEPTH."""
    root = os.path.join(parent["path"], SUBCASES)
    if depth > MAX_SUBCASE_DEPTH or os.path.islink(root) or not os.path.isdir(root):
        return []
    out = []
    for name, d in _children(root, real_root):
        if is_case(d):
            e = _entry(posixpath.normpath(posixpath.join(parent["rel"], SUBCASES, name)), d, parent["id"])
            out += [e] + _subcases(e, real_root, depth + 1)
    return out


def discover(workspace):
    """Every case in a workspace, as {id, rel, path, title, parent_id}: top-level cases by name,
    each followed by its subcases. If the workspace is itself a case it is the only top-level
    case (rel "."); its other folders are not scanned. A missing workspace lists nothing."""
    ws = os.path.abspath(workspace)
    if not os.path.isdir(ws):
        return []
    real_root = os.path.realpath(ws)
    if is_case(ws):
        tops = [(".", ws)]
    else:
        tops = [(name, d) for name, d in _children(ws, real_root) if is_case(d)]
    out = []
    for rel, d in tops:
        e = _entry(rel, d, None)
        out += [e] + _subcases(e, real_root)
    return out


def resolve(workspace, case_id):
    """The discover entry with this id. CaseError if there is none; nothing else maps an id to a path."""
    for c in discover(workspace):
        if c["id"] == case_id:
            return c
    raise CaseError("unknown case")


def create(workspace, name):
    """Make a new case folder `name` directly inside the workspace and return its discover entry.
    Refuses a name that is not a plain folder name, a workspace that is itself a case, and a
    name already taken."""
    if (not isinstance(name, str) or not 1 <= len(name) <= 80 or not NAME_RE.fullmatch(name)
            or name.endswith((" ", "."))):
        raise CaseError("invalid case name — 1-80 characters: letters, digits, spaces and _ . , ( ) & ' -, "
                        "starting with a letter or digit, not ending in a space or '.'")
    ws = os.path.abspath(workspace)
    if not os.path.isdir(ws):
        raise CaseError("workspace is not a folder")
    if is_case(ws):
        raise CaseError("this folder is itself a case")
    target = os.path.join(ws, name)
    try:
        os.mkdir(target)  # exclusive: two creates of one name cannot both win
    except FileExistsError:
        raise CaseError(f"{name!r} already exists in this workspace") from None
    try:
        init(target)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)  # created here, so safe to remove again
        raise
    for c in discover(ws):
        if c["rel"] == name:
            return c
    raise CaseError(f"{name!r} was created but is not listed")
