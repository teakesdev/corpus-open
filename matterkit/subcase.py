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

from . import store
from .store import now_iso

MANIFEST = "matter.json"
SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")


class SubcaseError(ValueError):
    """A refused subcase operation; nothing was written."""


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
