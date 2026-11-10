"""Ingest files into the matter store: hash, classify, register. Sources are never modified."""
import hashlib
import os
import re
import sqlite3

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".matter"}
SKIP_FILES = {".DS_Store"}
# A subcase's documents.rel_path "parent:<rel_path>" names a document whose bytes live in the
# parent matter (see subcase.py). The prefix is reserved: ingest never registers a local file under it.
PARENT_PREFIX = "parent:"

EXT_KINDS = {
    ".pdf": "filing", ".docx": "filing", ".doc": "filing",
    ".md": "research", ".txt": "research",
    ".jpg": "media", ".jpeg": "media", ".png": "media",
    ".mp3": "media", ".m4a": "media", ".wav": "media", ".mov": "media", ".mp4": "media",
    ".csv": "evidence", ".xlsx": "evidence", ".eml": "correspondence",
}

DIR_KINDS = [
    ("filings", "filing"), ("filing", "filing"),
    ("evidence", "evidence"),
    ("correspondence", "correspondence"), ("records_requests", "correspondence"),
    ("case_tracking", "tracker"),
    ("research", "research"),
    ("source_orders", "media"), ("media", "media"),
    ("admin", "admin"),
]


def classify(rel_path: str) -> str:
    parts = rel_path.lower().split(os.sep)
    for hint, kind in DIR_KINDS:
        if hint in parts:
            # filings dominate when nested (e.g. Filings/plaintext/*.md)
            return kind
    ext = os.path.splitext(rel_path)[1].lower()
    return EXT_KINDS.get(ext, "other")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def is_nested_matter(path: str) -> bool:
    """True if `path` is a directory holding its own `.matter/` store."""
    return os.path.isdir(os.path.join(path, ".matter"))


def nested_matter_root(path: str, matter_dir: str):
    """The nearest directory strictly below matter_dir, at or above `path`, that is its own
    matter (holds `.matter/`); None if `path` is not inside one. matter_dir itself never counts."""
    abs_matter = os.path.abspath(matter_dir)
    cur = os.path.abspath(path)
    if not os.path.isdir(cur):
        cur = os.path.dirname(cur)
    while cur != abs_matter and is_within_matter(cur, abs_matter):
        if is_nested_matter(cur):
            return cur
        cur = os.path.dirname(cur)
    return None


def walk_files(root: str):
    """Files under root, skipping SKIP_DIRS and any nested matter (a subdirectory with its own
    `.matter/` is a boundary: its files belong to its own store). `root` itself is not tested."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not is_nested_matter(os.path.join(dirpath, d))]
        for fn in sorted(filenames):
            if fn in SKIP_FILES:
                continue
            yield os.path.join(dirpath, fn)


def is_within_matter(path: str, matter_dir: str) -> bool:
    """True iff abspath(path) is the matter root or a descendant (no .. escape)."""
    abs_path = os.path.abspath(path)
    abs_matter = os.path.abspath(matter_dir)
    try:
        rel = os.path.relpath(abs_path, abs_matter)
    except ValueError:
        return False
    if rel == os.curdir:
        return True
    return not _invalid_rel(rel)


def resolve_in_matter(path: str, matter_dir: str):
    """`path` as a path under matter_dir, or None when it is outside. A relative path joins the
    matter root. An absolute path that is outside as written but whose folder is, once links are
    resolved, inside the matter's real location (a case set up through a link, or /var vs
    /private/var on a Mac) is mapped back under matter_dir. Only the folder part is resolved: a
    link at the last step keeps its own name, so later link checks still see it."""
    root = os.path.abspath(matter_dir)
    cand = os.path.abspath(path if os.path.isabs(path) else os.path.join(root, path))
    if is_within_matter(cand, root):
        return cand
    if not os.path.isabs(path):
        return None
    real_root = os.path.realpath(root)
    if _same_dir(os.path.realpath(cand), real_root):
        return root
    head, leaf = os.path.split(cand)
    rel = _rel_under(os.path.realpath(head), real_root)
    if rel is None:
        return None
    return os.path.normpath(os.path.join(root, rel, leaf))


def _same_dir(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _rel_under(real_head: str, real_root: str):
    """real_head (no links left in it) relative to real_root, or None when it is not under it. The
    folders are compared as folders on the disk, not as spellings, so a disk that ignores capital
    letters (the Mac default) finds "/cases/CASE/evidence" under "/cases/case"."""
    if is_within_matter(real_head, real_root):
        return os.path.relpath(real_head, real_root)
    cur = real_head
    while True:
        if _same_dir(cur, real_root):
            return os.path.relpath(real_head, cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def case_relative_notes(printed: str, matter_dir: str) -> list:
    """The "  ! reason: /abs/path" lines ingest_paths printed, as "reason: rel/path" for a reply:
    only the leading marker is dropped, and the matter root is cut only where a path starts (after
    a space or at the start), never from the middle of a name."""
    lead = re.compile(r"(?:(?<=\s)|^)" + re.escape(os.path.abspath(matter_dir) + os.sep))
    return [lead.sub("", ln.strip()[2:]) for ln in printed.splitlines() if ln.strip().startswith("! ")]


def reads_inside(path: str, matter_dir: str) -> bool:
    """True if reading `path` reads bytes inside the matter's real location: a link is followed
    only when its target is inside too. A path that does not exist is not refused here."""
    return (not os.path.lexists(path)
            or is_within_matter(os.path.realpath(path), os.path.realpath(matter_dir)))


def _links_out(fp: str, matter_dir: str) -> bool:
    """True if a link makes fp's bytes live outside matter_dir, or inside a nested matter (whose
    files belong to its own store). A path with no link resolves inside and is never refused."""
    real_root = os.path.realpath(matter_dir)
    real = os.path.realpath(fp)
    return not is_within_matter(real, real_root) or nested_matter_root(real, real_root) is not None


def _invalid_rel(rel_path: str) -> bool:
    """True if rel_path is not a safe matter-relative locator."""
    if not rel_path or os.path.isabs(rel_path):
        return True
    parts = rel_path.replace("\\", "/").split("/")
    return any(p == ".." for p in parts)


def to_rel_path(fp: str, matter_dir: str) -> str:
    """Matter-root-relative locator: no leading slash, no .. . Raises ValueError if outside."""
    abs_fp = os.path.abspath(fp)
    abs_matter = os.path.abspath(matter_dir)
    if not is_within_matter(abs_fp, abs_matter):
        raise ValueError(f"path outside matter: {fp}")
    rel = os.path.relpath(abs_fp, abs_matter)
    if rel == os.curdir or _invalid_rel(rel):
        raise ValueError(f"invalid matter-relative path: {rel!r}")
    return rel


def resolve_document_path(matter_dir: str, rel_path: str):
    """Join rel_path to abs matter. None if absolute, .., or would escape — do not read."""
    if rel_path is None or _invalid_rel(rel_path):
        return None
    abs_matter = os.path.abspath(matter_dir)
    resolved = os.path.normpath(os.path.join(abs_matter, rel_path))
    if not is_within_matter(resolved, abs_matter):
        return None
    if os.path.abspath(resolved) == abs_matter:
        return None
    return resolved


def ingest_paths(conn, matter_dir: str, paths, exclude=()):
    """Register files under the given paths. Returns (added, skipped_dup, excluded) counts.

    documents.rel_path is always relative to abspath(matter_dir), never cwd.
    Paths that resolve outside the matter are excluded (fail-closed).
    """
    abs_matter = os.path.abspath(matter_dir)
    added = skipped = excluded = 0
    for p in paths:
        ap = os.path.abspath(p)
        if not os.path.exists(ap):
            print(f"  ! not found: {p}")
            continue
        nested = nested_matter_root(ap, abs_matter)
        if nested:  # an explicit path into a nested matter is refused once, not file by file
            print(f"  ! inside nested matter {nested}: {p}")
            excluded += 1
            continue
        targets = ([ap] if os.path.isfile(ap) else walk_files(ap))
        for fp in targets:
            if not is_within_matter(fp, abs_matter):
                print(f"  ! outside matter: {fp}")
                excluded += 1
                continue
            if _links_out(fp, abs_matter):
                print(f"  ! links outside this matter (or into a nested matter): {fp}")
                excluded += 1
                continue
            if not os.path.exists(fp):  # a link whose target is gone: nothing to hash
                print(f"  ! not found: {fp}")
                excluded += 1
                continue
            try:
                rel_path = to_rel_path(fp, abs_matter)
            except ValueError:
                print(f"  ! outside matter: {fp}")
                excluded += 1
                continue
            if rel_path.startswith(PARENT_PREFIX):
                print(f"  ! reserved name (parent: marks references to the parent matter): {rel_path}")
                excluded += 1
                continue
            if any(x in rel_path for x in exclude):
                excluded += 1
                continue
            digest = sha256_file(fp)
            row = conn.execute(
                "SELECT id FROM documents WHERE sha256=? OR rel_path=?", (digest, rel_path)
            ).fetchone()
            if row:
                skipped += 1
                continue
            st = os.stat(fp)
            kind = classify(rel_path)
            try:
                conn.execute(
                    "INSERT INTO documents (id, sha256, rel_path, bytes, mtime, kind)"
                    " VALUES (?,?,?,?,?,?)",
                    (_new_id(),
                     digest, rel_path, st.st_size, st.st_mtime, kind),
                )
            except sqlite3.IntegrityError:  # another writer registered it after the lookup above
                skipped += 1
                continue
            added += 1
    conn.commit()
    return added, skipped, excluded


def verify_integrity(conn, matter_dir: str):
    """Re-hash stored documents against files under matter_dir.

    Old cwd-relative locators are not rewritten; they report MISSING or ESCAPED
    until the caller re-ingests. Verify walks stored rows, not directories, so the
    nested-matter boundary stops new registration (walk_files) but never hides a row
    already registered: a legacy row under a folder that later became a matter is
    still checked here.
    """
    abs_matter = os.path.abspath(matter_dir)
    rows = conn.execute("SELECT id, sha256, rel_path FROM documents").fetchall()
    ok = bad = missing = 0
    failures = []
    view = None  # parent: rows resolve through the parent matter, read-only, opened on first use
    for r in rows:
        if r["rel_path"].startswith(PARENT_PREFIX):
            if view is None:
                from .subcase import ParentView
                view = ParentView(abs_matter)
            verdict = view.check(r["rel_path"][len(PARENT_PREFIX):], r["sha256"])
            if verdict is None:
                ok += 1
            else:
                if verdict == "CHANGED":
                    bad += 1
                else:  # MISSING (parent, registration or file gone) and ESCAPED count as missing, like local rows
                    missing += 1
                failures.append((verdict, r["rel_path"]))
            continue
        resolved = resolve_document_path(abs_matter, r["rel_path"])
        if resolved is None:
            missing += 1
            failures.append(("ESCAPED", r["rel_path"]))
            continue
        if not os.path.exists(resolved):
            missing += 1
            failures.append(("MISSING", r["rel_path"]))
            continue
        if sha256_file(resolved) != r["sha256"]:
            bad += 1
            failures.append(("CHANGED", r["rel_path"]))
        else:
            ok += 1
    if view is not None:
        view.close()
    return ok, bad, missing, failures


def _new_id():
    from .store import new_id  # relative: Hermes loads the kit under its own package name
    return new_id()
