"""Source guard: Hermes's own tools may not change a case's source documents or its store.

Runs as a pre_tool_call hook. A registered source document is one whose rel_path is in a case
store's documents table; the store is everything under a case's .matter/ folder.

- write_file and patch (including a V4A patch's Update/Add/Delete/Move lines) aimed at either one
  are blocked, with a message naming the file and its hash.
- terminal and execute_code text that names either one and looks like it changes files goes to
  the person's approval. Reading a command's text is a guess, so it never blocks outright.
- When a path is inside a case but its store can't be read, the call goes to approval.

A path is judged by the file it opens, not by its spelling. macOS volumes are usually case- and
normalization-insensitive, so 'evidence/lease.txt', '.Matter/consent.json' or an NFD name can open
a registered file. The guard compares (st_dev, st_ino): a path any of whose existing folders is a
case's .matter folder is in the store, and an existing file that is a registered source or a file
under .matter/ is that file, under any other name a hard link gives it, inside the case or out.
Names that don't exist yet, and names in a command's text, are also compared case-folded and
NFC-normalized.

Every other call passes untouched. This guards against accidents by an agent that is trying to
help; it is not a sandbox, and a determined program can still change any file the user can.
"""
from __future__ import annotations

import os
import re
import sqlite3
import stat
import unicodedata

from ..matterkit import store

FILE_TOOLS = ("write_file", "patch")
CODE_TOOLS = {"terminal": "command", "execute_code": "code"}
RULE_KEY = "corpus-open:source-guard"
MAX_TOKENS = 400
MAX_STORE_FILES = 20000  # entries listed under one .matter/; past this the store counts as unreadable

_V4A = re.compile(r"^\*\*\* (?:(?:Update|Add|Delete) File|Move to):[ \t]*(.+?)[ \t]*$", re.M)
_TOKEN = re.compile(r"[^\s'\"`;|&<>(),={}\[\]]+")
_REDIRECT_NOISE = re.compile(r"\d*>&\d+|&>\s*/dev/null|\d*>>?\s*/dev/null")
_REDIRECT = re.compile(r">")
_SHELL_WRITE = re.compile(
    r"(?:^|[\s;|&(`])(?:sudo\s+)?(?:rm|rmdir|mv|cp|ln|dd|tee|touch|truncate|shred|unlink|chmod|chown|"
    r"rsync|install|sqlite3|patch)(?=\s|$)"
    r"|\b(?:sed|perl|ruby)\s+(?:-\w+\s+)*-\w*i"
    r"|\bgit\s+(?:checkout|restore|reset|clean|rm|mv|stash|apply)\b")
_PY_WRITE = re.compile(
    r"\bopen\([^)]*,\s*(?:mode\s*=\s*)?[rbt]?['\"][^'\"]*[wax+]"
    r"|\.write_(?:text|bytes)\(|\.(?:unlink|touch|rename|replace|chmod|rmdir)\("
    r"|\bos\.(?:remove|unlink|rename|replace|rmdir|truncate|chmod|chown|link|symlink)\("
    r"|\bshutil\.\w+\(|\bsqlite3\.connect\(|\bsubprocess\.|\bos\.system\(")

_docs_cache: dict = {}


class Unreadable(Exception):
    def __init__(self, root: str, err: Exception):
        super().__init__(f"{root}: {err}")
        self.root = root


def check(tool_name, args, matter_dir: str | None = None) -> dict | None:
    """The pre_tool_call directive for one call, or None to let it run."""
    if not isinstance(args, dict):
        return None
    if tool_name in FILE_TOOLS:
        return _check_paths(_file_targets(args), matter_dir)
    if tool_name in CODE_TOOLS:
        text = args.get(CODE_TOOLS[tool_name])
        if not isinstance(text, str) or not text.strip():
            return None
        return _check_text(text, matter_dir, shell=(tool_name == "terminal"))
    return None


def fallback(tool_name, args, matter_dir: str | None = None) -> dict | None:
    """After an internal error: approval when the call names anything inside a case, else None."""
    try:
        raws = []
        if isinstance(args, dict):
            if tool_name in FILE_TOOLS:
                raws = _file_targets(args)
            elif tool_name in CODE_TOOLS and isinstance(args.get(CODE_TOOLS[tool_name]), str):
                raws = _TOKEN.findall(args[CODE_TOOLS[tool_name]])[:MAX_TOKENS]
        for raw in raws:
            for cand in _candidates(raw, matter_dir):
                for root in _cases_above(cand):
                    return _approve(f"corpus-open's source guard hit an error while checking {cand}, "
                                    f"which is inside the case at {root}. Allow only if you meant to "
                                    "change it.")
    except Exception:  # noqa: BLE001 - a guard that raises would block every tool call
        return None
    return None


# -- what a call touches -------------------------------------------------------------------------
def _file_targets(args: dict) -> list:
    out = []
    if isinstance(args.get("path"), str) and args["path"].strip():
        out.append(args["path"].strip())
    if isinstance(args.get("patch"), str):
        out.extend(_V4A.findall(args["patch"]))
    return out


def _candidates(raw: str, matter_dir: str | None) -> list:
    """Absolute forms of raw: against the cwd and the case folder setting, with links resolved."""
    path = os.path.expanduser(raw)
    bases = [None] if os.path.isabs(path) else [os.getcwd()]
    if not os.path.isabs(path) and matter_dir:
        bases.append(os.path.expanduser(str(matter_dir)))
    out = []
    for base in bases:
        p = os.path.abspath(path if base is None else os.path.join(base, path))
        for form in (p, os.path.realpath(p)):
            if form not in out:
                out.append(form)
    return out


def _cases_above(path: str):
    """Case roots at or above path, innermost first."""
    d = path
    while True:
        if os.path.isdir(os.path.join(d, ".matter")):
            yield d
        parent = os.path.dirname(d)
        if parent == d:
            return
        d = parent


def _documents(root: str) -> dict:
    """rel_path -> sha256 for the case at root, read-only, cached on the store file's stat."""
    db = store.db_path(root)
    try:
        st = os.stat(db)
        key = (st.st_mtime_ns, st.st_size)
        hit = _docs_cache.get(db)
        if hit and hit[0] == key:
            return hit[1]
        conn = store.connect_readonly(root)
        try:
            docs = {r[0]: r[1] for r in conn.execute("SELECT rel_path, sha256 FROM documents")}
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as e:
        raise Unreadable(root, e) from e
    _docs_cache[db] = (key, docs)
    return docs


def _fold(name: str) -> str:
    """name compared the way a case- and normalization-insensitive volume would (or more loosely)."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFD", name).casefold())


def _stat(path: str):
    """os.stat of the file path opens, links followed; None when there is none."""
    try:
        return os.stat(path)
    except (OSError, ValueError):
        return None


def _ident(path: str):
    """(st_dev, st_ino) of the file path opens, links followed; None when there is none."""
    st = _stat(path)
    return None if st is None else (st.st_dev, st.st_ino)


def _in_store(path: str, root: str) -> bool:
    """True when path, or an existing folder above it inside root, is root's .matter folder."""
    store_id = _ident(os.path.join(root, ".matter"))
    if store_id is None:
        return False
    d = path
    while d != root:
        if _ident(d) == store_id:
            return True
        parent = os.path.dirname(d)
        if parent == d:
            return False
        d = parent
    return False


def _source_ids(root: str, docs: dict, memo: dict) -> dict:
    """(st_dev, st_ino) -> rel_path for the case's sources on disk, read once per call."""
    if ("ids", root) not in memo:
        ids = {}
        for rel in sorted(docs):
            ident = _ident(os.path.join(root, rel))
            if ident is not None:
                ids.setdefault(ident, rel)
        memo[("ids", root)] = ids
    return memo[("ids", root)]


def _store_ids(root: str, memo: dict) -> dict:
    """(st_dev, st_ino) -> '.matter/<rel>' for every regular file under root's .matter/, once per call.

    Listed, not named, so a file the kit adds later is covered too. A store that can't be listed,
    or holds more than MAX_STORE_FILES entries, raises Unreadable, and the call goes to approval."""
    if ("store", root) not in memo:
        try:
            ids, todo, count = {}, [os.path.join(root, ".matter")], 0
            while todo:
                with os.scandir(todo.pop()) as entries:
                    for e in entries:
                        count += 1
                        if count > MAX_STORE_FILES:
                            raise OSError(f"more than {MAX_STORE_FILES} entries under .matter/")
                        if e.is_dir(follow_symlinks=False):
                            todo.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            st = e.stat(follow_symlinks=False)
                            ids.setdefault((st.st_dev, st.st_ino),
                                           os.path.relpath(e.path, root).replace(os.sep, "/"))
            memo[("store", root)] = ids
        except OSError as e:
            memo[("store", root)] = Unreadable(root, e)
    hit = memo[("store", root)]
    if isinstance(hit, Unreadable):
        raise hit
    return hit


def _folded(root: str, docs: dict, memo: dict) -> dict:
    """_fold(rel_path) -> rel_path for the case's sources, once per call."""
    if ("names", root) not in memo:
        names = {}
        for rel in sorted(docs):
            names.setdefault(_fold(rel), rel)
        memo[("names", root)] = names
    return memo[("names", root)]


def _home_roots(matter_dir: str | None) -> list:
    """The configured case and any case above it; their sources are known wherever a path is."""
    if not matter_dir:
        return []
    try:
        return list(_cases_above(os.path.abspath(os.path.expanduser(str(matter_dir)))))
    except (OSError, TypeError, ValueError):
        return []


def _classify(path: str, memo: dict, home_roots=()):
    """('store'|'source'|'holds', root, rel, sha) for a protected path, else None.

    Identity first, so a different spelling of the same file is caught; then case-folded NFC names,
    for paths that don't exist yet. A file with one name can't be another name of a store file, so
    the store is listed only for a file that has more than one."""
    st = _stat(path)
    ident = None if st is None else (st.st_dev, st.st_ino)
    linked = st is not None and stat.S_ISREG(st.st_mode) and st.st_nlink > 1
    roots = list(_cases_above(path))
    for root in roots:
        rel = os.path.relpath(path, root)
        key = rel.replace(os.sep, "/")
        if rel != os.curdir and (any(_fold(p) == ".matter" for p in rel.split(os.sep))
                                 or _in_store(path, root)):
            return ("store", root, key, None)
        hit = _store_ids(root, memo).get(ident) if linked else None
        if hit:
            return ("store", root, hit, None)
        docs = _documents(root)
        hit = _source_ids(root, docs, memo).get(ident) if ident is not None else None
        names = _folded(root, docs, memo)
        hit = hit or names.get(_fold(key))
        if hit:
            return ("source", root, hit, docs[hit])
        if rel == os.curdir or any(n.startswith(_fold(key) + "/") for n in names):
            return ("holds", root, key, None)
    if ident is not None:
        # A hard link outside every case still opens a store file or source of the configured case.
        for root in home_roots:
            if root in roots:
                continue
            hit = _store_ids(root, memo).get(ident) if linked else None  # unlisted: approval
            if hit:
                return ("store", root, hit, None)
            try:
                docs = _documents(root)
            except Unreadable:
                continue  # an extra layer only; a store we can't read says nothing about this path
            hit = _source_ids(root, docs, memo).get(ident)
            if hit:
                return ("source", root, hit, docs[hit])
    return None


# -- decisions -----------------------------------------------------------------------------------
def _approve(message: str) -> dict:
    return {"action": "approve", "message": message, "rule_key": RULE_KEY}


def _check_paths(raws: list, matter_dir: str | None) -> dict | None:
    pending, memo, home = None, {}, _home_roots(matter_dir)
    for raw in raws:
        for cand in _candidates(raw, matter_dir):
            try:
                hit = _classify(cand, memo, home)
            except Unreadable as e:
                pending = pending or _approve(
                    f"corpus-open's source guard could not read the case store at {e.root} to check "
                    f"whether {cand} is a source document or a store file. Allow only if you meant to "
                    "change it.")
                continue
            if not hit or hit[0] == "holds":
                continue
            kind, root, rel, sha = hit
            if kind == "store":
                where = ("inside the case store (.matter/)"
                         if os.path.relpath(cand, root).replace(os.sep, "/") == rel
                         else f"another name (a hard link) for {rel} in the case store")
                return {"action": "block", "message": (
                    f"corpus-open source guard: {cand} is {where} of the case at "
                    f"{root}. Only the matter tools (matter_fact_add, matter_ingest_path and the rest) "
                    "write the store, so its hashes and history stay true. Use them instead.")}
            return {"action": "block", "message": (
                f"corpus-open source guard: {rel} is a registered source document in the case at {root} "
                f"(sha256 {sha[:12]}…). Sources are never changed, so every fact that cites them stays "
                "checkable. Write your changes to a new file (for example Drafts/<name>). If the source "
                "itself really changed, a person adds the new version with `matter.py ingest`.")}
    return pending


def _looks_like_write(text: str, shell: bool) -> bool:
    """Whether a command's text (shell) or a program's looks like it changes files. A guess from
    its words; the quote guard uses the same one."""
    if _PY_WRITE.search(text) or _SHELL_WRITE.search(text):
        return True
    return shell and bool(_REDIRECT.search(_REDIRECT_NOISE.sub(" ", text)))


def _check_text(text: str, matter_dir: str | None, shell: bool) -> dict | None:
    if not _looks_like_write(text, shell):
        return None
    seen, unreadable, memo, home = set(), None, {}, _home_roots(matter_dir)
    for tok in _TOKEN.findall(text)[:MAX_TOKENS]:
        for cand in _candidates(tok, matter_dir):
            if cand in seen:
                continue
            seen.add(cand)
            try:
                hit = _classify(cand, memo, home)
            except Unreadable as e:
                unreadable = unreadable or e.root
                continue
            if hit:
                kind, root, rel, _sha = hit
                what = {"store": "the case store (.matter/)",
                        "source": f"the registered source document {rel}",
                        "holds": f"{rel or 'the folder'}, which holds registered source documents"}[kind]
                return _approve(
                    f"This command names {what} in the case at {root} and looks like it changes files. "
                    "corpus-open keeps sources and the store unchanged. Allow only if you meant to "
                    "change them.")
    if unreadable:
        return _approve(f"This command looks like it changes files, and corpus-open could not read the "
                        f"case store at {unreadable} to check what it touches. Allow only if you meant it.")
    return None
