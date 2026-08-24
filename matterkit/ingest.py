"""Ingest files into the matter store: hash, classify, register. Sources are never modified."""
import hashlib
import os

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".matter"}
SKIP_FILES = {".DS_Store"}

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


def walk_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            if fn in SKIP_FILES:
                continue
            yield os.path.join(dirpath, fn)


def ingest_paths(conn, matter_dir: str, paths, exclude=()):
    """Register files under the given paths. Returns (added, skipped_dup, excluded) counts."""
    import itertools
    abs_matter = os.path.abspath(matter_dir)
    added = skipped = excluded = 0
    for p in paths:
        ap = os.path.abspath(p)
        if not os.path.exists(ap):
            print(f"  ! not found: {p}")
            continue
        targets = ([ap] if os.path.isfile(ap) else walk_files(ap))
        for fp in targets:
            rel_to_cwd = os.path.relpath(fp, os.getcwd())
            if any(x in rel_to_cwd for x in exclude):
                excluded += 1
                continue
            digest = sha256_file(fp)
            row = conn.execute(
                "SELECT id FROM documents WHERE sha256=? OR rel_path=?", (digest, rel_to_cwd)
            ).fetchone()
            if row:
                skipped += 1
                continue
            st = os.stat(fp)
            kind = classify(os.path.relpath(fp, abs_matter)) if fp.startswith(abs_matter) else \
                classify(rel_to_cwd) or "other"
            # prefer dir-hint classification against the SOURCE location, not the store
            for hint, k in DIR_KINDS:
                if hint in rel_to_cwd.lower().split(os.sep):
                    kind = k
                    break
            else:
                kind = EXT_KINDS.get(os.path.splitext(fp)[1].lower(), "other")
            conn.execute(
                "INSERT INTO documents (id, sha256, rel_path, bytes, mtime, kind)"
                " VALUES (?,?,?,?,?,?)",
                (__import__("matterkit.store", fromlist=["new_id"]).new_id(),
                 digest, rel_to_cwd, st.st_size, st.st_mtime, kind),
            )
            added += 1
    conn.commit()
    return added, skipped, excluded


def verify_integrity(conn):
    rows = conn.execute("SELECT id, sha256, rel_path FROM documents").fetchall()
    ok = bad = missing = 0
    failures = []
    for r in rows:
        if not os.path.exists(r["rel_path"]):
            missing += 1
            failures.append(("MISSING", r["rel_path"]))
            continue
        if sha256_file(r["rel_path"]) != r["sha256"]:
            bad += 1
            failures.append(("CHANGED", r["rel_path"]))
        else:
            ok += 1
    return ok, bad, missing, failures
