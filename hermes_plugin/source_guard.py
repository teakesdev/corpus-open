"""Source guard: Hermes's own tools may not change a case's source documents or its store.

Runs as a pre_tool_call hook. A registered source document is one whose rel_path is in a case
store's documents table; the store is everything under a case's .matter/ folder.

- write_file and patch (including a V4A patch's Update/Add/Delete/Move lines) aimed at either one
  are blocked, with a message naming the file and its hash.
- terminal and execute_code text that names either one and looks like it changes files goes to
  the person's approval. Reading a command's text is a guess, so it never blocks outright.
- When a path is inside a case but its store can't be read, the call goes to approval.

Every other call passes untouched. This guards against accidents by an agent that is trying to
help; it is not a sandbox, and a determined program can still change any file the user can.
"""
from __future__ import annotations

import os
import re
import sqlite3

from ..matterkit import store

FILE_TOOLS = ("write_file", "patch")
CODE_TOOLS = {"terminal": "command", "execute_code": "code"}
RULE_KEY = "corpus-open:source-guard"
MAX_TOKENS = 400

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


def _classify(path: str):
    """('store'|'source'|'holds', root, rel, sha) for a protected path, else None."""
    for root in _cases_above(path):
        rel = os.path.relpath(path, root)
        parts = rel.split(os.sep)
        if ".matter" in parts:
            return ("store", root, rel, None)
        docs = _documents(root)
        key = rel.replace(os.sep, "/")
        if key in docs:
            return ("source", root, key, docs[key])
        if rel == os.curdir or any(r.startswith(key + "/") for r in docs):
            return ("holds", root, key, None)
    return None


# -- decisions -----------------------------------------------------------------------------------
def _approve(message: str) -> dict:
    return {"action": "approve", "message": message, "rule_key": RULE_KEY}


def _check_paths(raws: list, matter_dir: str | None) -> dict | None:
    pending = None
    for raw in raws:
        for cand in _candidates(raw, matter_dir):
            try:
                hit = _classify(cand)
            except Unreadable as e:
                pending = pending or _approve(
                    f"corpus-open's source guard could not read the case store at {e.root} to check "
                    f"whether {cand} is a source document. Allow only if you meant to change it.")
                continue
            if not hit or hit[0] == "holds":
                continue
            kind, root, rel, sha = hit
            if kind == "store":
                return {"action": "block", "message": (
                    f"corpus-open source guard: {cand} is inside the case store (.matter/) of the case at "
                    f"{root}. Only the matter tools (matter_fact_add, matter_ingest_path and the rest) "
                    "write the store, so its hashes and history stay true. Use them instead.")}
            return {"action": "block", "message": (
                f"corpus-open source guard: {rel} is a registered source document in the case at {root} "
                f"(sha256 {sha[:12]}…). Sources are never changed, so every fact that cites them stays "
                "checkable. Write your changes to a new file (for example Drafts/<name>). If the source "
                "itself really changed, a person adds the new version with `matter.py ingest`.")}
    return pending


def _check_text(text: str, matter_dir: str | None, shell: bool) -> dict | None:
    writes = bool(_PY_WRITE.search(text) or _SHELL_WRITE.search(text))
    if shell and not writes:
        writes = bool(_REDIRECT.search(_REDIRECT_NOISE.sub(" ", text)))
    if not writes:
        return None
    seen, unreadable = set(), None
    for tok in _TOKEN.findall(text)[:MAX_TOKENS]:
        for cand in _candidates(tok, matter_dir):
            if cand in seen:
                continue
            seen.add(cand)
            try:
                hit = _classify(cand)
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
