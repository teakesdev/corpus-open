"""Cases: setting one up, and finding them in a workspace.

A workspace is a folder whose direct children are case folders; a case folder holds
`.matter/matter.db`. Subcases sit under their parent at `<case>/Subcases/<slug>/` (see subcase.py).
`discover` is the only thing that turns a case id into a path: ids are derived from the
workspace-relative path, so nothing a caller sends can name a folder outside the workspace.
`pinned` opens a discovered case for one request without following a link, so a folder renamed
or swapped part way through makes the request fail instead of writing somewhere else.
"""
import hashlib
import os
import posixpath
import re
import shutil
import stat
from contextlib import contextmanager

from . import ingest, store, subcase

FOLDERS = ["Filings", "Evidence", "Correspondence", "Case_Tracking", "Research", "Media"]
SUBCASES = "Subcases"
MAX_SUBCASE_DEPTH = 3  # levels of subcases listed below a top-level case
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.,()&'-]*")


class CaseError(ValueError):
    """A refused case operation; nothing was written."""


class CaseChanged(CaseError):
    """A case folder, its `.matter/` or a folder in it was moved, replaced or linked elsewhere
    while a request was using it."""


def init(case_dir):
    """Create the case folders and the store (idempotent). Returns how many documents are registered.
    A new case gets its default bare-cite checks (consent.CASE_DEFAULTS); a check a person already
    granted or turned off is left as it is."""
    from . import consent
    os.makedirs(case_dir, exist_ok=True)
    for f in FOLDERS:
        os.makedirs(os.path.join(case_dir, f), exist_ok=True)
    conn = store.connect(case_dir)
    try:
        n = conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
    finally:
        conn.close()
    consent.ensure_defaults(case_dir, note=False)
    return n


def is_case(path):
    """A folder with a real `.matter/` folder holding a regular `matter.db`. A link at either
    would make the case's records live somewhere else, so it doesn't count."""
    try:
        meta, db = os.lstat(os.path.join(path, ".matter")), os.lstat(store.db_path(path))
    except OSError:
        return False
    return stat.S_ISDIR(meta.st_mode) and stat.S_ISREG(db.st_mode)


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
    made = os.lstat(target)
    try:
        init(target)
    except BaseException:
        try:  # remove only the folder this call made, not one moved in under its name since
            now = os.lstat(target)
        except OSError:
            now = None
        if now is not None and stat.S_ISDIR(now.st_mode) and _same(now, made):
            shutil.rmtree(target, ignore_errors=True)
        raise
    for c in discover(ws):
        if c["rel"] == name:
            return c
    raise CaseError(f"{name!r} was created but is not listed")


# -- pinning a case for one request --
# With dir_fd support every folder is opened by handle, starting at the workspace and never
# following a link, so later steps act on the folders that were checked. Without it (Windows)
# the same rules run on paths with lstat checks, which a race can slip between.
DIR_FD = (all(f in os.supports_dir_fd for f in (os.open, os.stat, os.mkdir, os.link, os.unlink))
          and hasattr(os, "O_DIRECTORY"))
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _same(a, b):
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


class Dir:
    """One folder, opened without following a link. `path` is for messages and path-only code."""

    def __init__(self, path, fd, st):
        self.path, self.fd, self.st = path, fd, st

    @classmethod
    def root(cls, path):
        if DIR_FD:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            return cls(path, fd, os.fstat(fd))
        st = os.stat(path)
        if not stat.S_ISDIR(st.st_mode):
            raise CaseChanged(f"{path} is not a folder")
        return cls(path, None, st)

    def _p(self, name):
        return os.path.join(self.path, name)

    def sub(self, name, create=False):
        """The folder `name` in this one; CaseChanged if it is missing, a link or not a folder."""
        if create:
            try:
                os.mkdir(name, dir_fd=self.fd) if DIR_FD else os.mkdir(self._p(name))
            except FileExistsError:
                pass
        try:
            if DIR_FD:
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW, dir_fd=self.fd)
                return Dir(self._p(name), fd, os.fstat(fd))
            st = os.lstat(self._p(name))
        except OSError:
            raise CaseChanged(f"{self._p(name)} is not a folder (or is a link)") from None
        if not stat.S_ISDIR(st.st_mode):
            raise CaseChanged(f"{self._p(name)} is not a folder (or is a link)")
        return Dir(self._p(name), None, st)

    def lstat(self, name):
        return os.stat(name, dir_fd=self.fd, follow_symlinks=False) if DIR_FD else os.lstat(self._p(name))

    def create(self, name, mode=0o644):
        """A new file opened for writing: never replaces a file, never follows a link."""
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | getattr(os, "O_BINARY", 0)
        return os.open(name, flags, mode, dir_fd=self.fd) if DIR_FD else os.open(self._p(name), flags, mode)

    def read(self, name):
        """The bytes of the regular file `name` here, not following a link."""
        flags = os.O_RDONLY | _NOFOLLOW | getattr(os, "O_BINARY", 0)
        try:
            if not DIR_FD and stat.S_ISLNK(os.lstat(self._p(name)).st_mode):
                raise CaseChanged(f"{self._p(name)} is a link")
            fd = os.open(name, flags, dir_fd=self.fd) if DIR_FD else os.open(self._p(name), flags)
        except OSError:
            raise CaseChanged(f"{self._p(name)} can't be opened (it was moved, or is a link)") from None
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise CaseChanged(f"{self._p(name)} is not a regular file")
            return f.read()

    def link_from(self, src, src_name, name):
        """Give `src`/`src_name` a second name here. Fails if `name` exists: never replaces."""
        if DIR_FD:
            os.link(src_name, name, src_dir_fd=src.fd, dst_dir_fd=self.fd)
        else:
            os.link(src._p(src_name), self._p(name))

    def unlink_if(self, name, st):
        """Remove `name` only while it is still the file `st` describes. True if removed."""
        try:
            if not _same(self.lstat(name), st):
                return False
            os.unlink(name, dir_fd=self.fd) if DIR_FD else os.unlink(self._p(name))
        except FileNotFoundError:
            return False
        return True

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class Pinned:
    """A case opened for one request. `dir` and `meta` are the case folder and its `.matter/`;
    `path` is where path-only code (SQLite, ingest, brief check) finds the same folder. `check()`
    confirms that path still leads to everything pinned, so that code can be bracketed by checks."""

    def __init__(self, path, case_dir, meta, db):
        self.path, self.dir, self.meta, self._db = path, case_dir, meta, db
        self._opened = []

    def sub(self, name, create=False):
        """A folder directly in the case (a taxonomy folder), pinned and covered by check()."""
        d = self.dir.sub(name, create=create)
        self._opened.append(d)
        return d

    def incoming(self):
        """`.matter/incoming/`: where uploads are written before they get their name. Walks skip
        `.matter`, so a scan never sees a file that is still arriving."""
        d = self.meta.sub("incoming", create=True)
        self._opened.append(d)
        return d

    def read(self, rel):
        """The bytes of the case file at rel (a/b/c), reached folder by folder without links."""
        *dirs, name = rel.split("/")
        opened, cur = [], self.dir
        try:
            for part in dirs:
                cur = cur.sub(part)
                opened.append(cur)
            return cur.read(name)
        finally:
            for d in opened:
                d.close()

    def check(self):
        """CaseChanged unless the case's path still leads to the pinned folders and store."""
        pairs = [(self.path, self.dir.st), (self.meta.path, self.meta.st),
                 (os.path.join(self.meta.path, "matter.db"), self._db)]
        pairs += [(d.path, d.st) for d in self._opened]
        for p, st in pairs:
            try:
                if not _same(os.lstat(p), st):
                    raise CaseChanged(f"{p} was replaced while Matter Kit was using it")
            except OSError:
                raise CaseChanged(f"{p} was moved while Matter Kit was using it") from None

    def close(self):
        for d in self._opened + [self.meta, self.dir]:
            d.close()


@contextmanager
def pinned(workspace, entry):
    """Open a discovered case (an entry from `discover`) for one request: its folder, `.matter/`
    and `matter.db`, each reached from the workspace without following a link. The workspace
    path itself is resolved once, so a workspace opened through a link still works."""
    root = os.path.realpath(workspace)
    parts = [] if entry["rel"] == "." else entry["rel"].split("/")
    opened = [Dir.root(root)]
    pin = None
    try:
        for part in parts:
            opened.append(opened[-1].sub(part))
        opened.append(opened[-1].sub(".matter"))
        try:
            db = opened[-1].lstat("matter.db")
        except OSError:
            db = None
        if db is None or not stat.S_ISREG(db.st_mode):
            raise CaseChanged(f"{opened[-1].path}/matter.db is missing or a link")
        pin = Pinned(os.path.join(root, *parts), opened[-2], opened[-1], db)
        for d in opened[:-2]:
            d.close()
        opened = []
        yield pin
    finally:
        for d in opened:
            d.close()
        if pin is not None:
            pin.close()
