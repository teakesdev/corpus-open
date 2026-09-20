"""Capture a URL into the matter: write bytes, register, record provenance.

Network-free by construction: the caller injects `fetch(url) -> bytes`. The
only urllib fetcher lives in matterkit.adapters.capture_fetch.
"""
import hashlib
import os
import re
from html.parser import HTMLParser

from . import ingest as ing
from .store import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS captures (
  url TEXT NOT NULL,
  retrieved_at TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  rel_path TEXT NOT NULL
);
"""


KINDS = {"media", "research"}


class CaptureError(Exception):
    """Refused. Fail closed: nothing was written and nothing was registered."""


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def _resolve_out(matter_dir: str, out: str):
    """(abs_out, rel_path). A relative out is joined to the matter root, never cwd."""
    abs_matter = os.path.abspath(matter_dir)
    if not out:
        raise CaptureError("--out required")
    abs_out = os.path.abspath(os.path.join(abs_matter, out))
    try:
        rel_path = ing.to_rel_path(abs_out, abs_matter)
    except ValueError:
        raise CaptureError(f"--out is outside matter: {out}") from None
    if rel_path.split(os.sep)[0].lower() == ".matter":  # same dir on case-insensitive filesystems
        raise CaptureError(f"--out is inside .matter/ (derived store, not a source): {out}")
    return abs_out, rel_path


def _record(conn, url, retrieved_at, digest, rel_path, unchanged) -> dict:
    conn.execute("INSERT INTO captures (url, retrieved_at, sha256, rel_path) VALUES (?,?,?,?)",
                 (url, retrieved_at, digest, rel_path))
    conn.commit()
    return {"url": url, "retrieved_at": retrieved_at, "sha256": digest,
            "rel_path": rel_path, "unchanged": unchanged, "extract": None}


def capture_url(conn, matter_dir: str, url: str, out: str, fetch, kind=None, now=None) -> dict:
    """Fetch url, write the bytes to out (matter-relative), register the document.

    Every refusal happens before fetch() — no egress for a capture that cannot land.
    Bytes already in the store (documents.sha256 is UNIQUE) are not written twice:
    the retrieval is recorded against the existing rel_path and that row is untouched.
    """
    ensure_schema(conn)
    if not (url or "").lower().startswith(("http://", "https://")):
        raise CaptureError(f"only http(s) URLs can be captured: {url!r}")
    authority = re.split(r"[/?#]", url.split("://", 1)[1], maxsplit=1)[0]
    if "@" in authority:
        # the url lands in source_url and in a table meant to be pasted into memos
        raise CaptureError("URL carries credentials (user@host) — not captured, not recorded")
    abs_out, rel_path = _resolve_out(matter_dir, out)
    if kind is None:
        kind = "research" if ing.classify(rel_path) == "research" else "media"
    if kind not in KINDS:
        raise CaptureError(f"kind must be one of {sorted(KINDS)}, not {kind!r}")
    if os.path.lexists(abs_out):
        raise CaptureError(f"refusing to overwrite {rel_path}")
    if conn.execute("SELECT 1 FROM documents WHERE rel_path=?", (rel_path,)).fetchone():
        raise CaptureError(
            f"{rel_path} is already registered but not on disk (MISSING) — "
            "resolve that first; capture will not replace it")
    body = fetch(url)
    if not body:
        raise CaptureError(f"empty response body from {url} — nothing to capture")
    digest = hashlib.sha256(body).hexdigest()
    retrieved_at = now or now_iso()
    existing = conn.execute("SELECT rel_path FROM documents WHERE sha256=?", (digest,)).fetchone()
    if existing:
        return _record(conn, url, retrieved_at, digest, existing["rel_path"], unchanged=True)
    os.makedirs(os.path.dirname(abs_out), exist_ok=True)
    f = open(abs_out, "xb")  # never an existing file; from here on abs_out is this call's
    try:
        with f:
            f.write(body)
        # ingest is the write path: register through it, then annotate the row.
        ing.ingest_paths(conn, matter_dir, [abs_out])
        row = conn.execute("SELECT sha256 FROM documents WHERE rel_path=?", (rel_path,)).fetchone()
        if row is None or row["sha256"] != digest:
            raise CaptureError(
                f"{rel_path} did not register with the fetched sha256 — blocker, capture undone")
        conn.execute("UPDATE documents SET source_url=?, kind=? WHERE rel_path=?",
                     (url, kind, rel_path))
        res = _record(conn, url, retrieved_at, digest, rel_path, unchanged=False)
    except BaseException:
        os.remove(abs_out)
        # rel_path was unregistered before the fetch, so any such row is this call's.
        conn.rollback()
        conn.execute("DELETE FROM documents WHERE rel_path=?", (rel_path,))
        conn.commit()
        raise
    try:
        res["extract"] = _write_extract(matter_dir, res, body)
    except OSError as e:
        # the capture is landed and registered; a derived file is regenerable
        res["extract_error"] = str(e)
    return res


class _VisibleText(HTMLParser):
    SKIP = {"script", "style", "noscript", "template"}

    def __init__(self):
        super().__init__()
        self.chunks, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.chunks.append(" ".join(data.split()))


def _write_extract(matter_dir: str, res: dict, body: bytes):
    """Derived text goes to .matter/out/, keyed by the source sha256 — never beside
    the source, never ingested. Returns its matter-relative path, or None (not HTML)."""
    if os.path.splitext(res["rel_path"])[1].lower() not in (".html", ".htm"):
        return None
    parser = _VisibleText()
    parser.feed(body.decode("utf-8", errors="replace"))
    rel = os.path.join(".matter", "out", "captures", res["sha256"] + ".txt")
    path = os.path.join(os.path.abspath(matter_dir), rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"[derived text extract — not the source; cite {res['rel_path']} "
                f"sha256={res['sha256']} url={res['url']} retrieved_at={res['retrieved_at']}]\n")
        f.write("\n".join(parser.chunks) + "\n")
    return rel


def render_list(conn) -> str:
    """The provenance table, as Markdown."""
    ensure_schema(conn)
    rows = conn.execute("SELECT url, retrieved_at, sha256, rel_path FROM captures"
                        " ORDER BY retrieved_at, rowid").fetchall()
    if not rows:
        return "no captures recorded"
    lines = ["| url | retrieved_at | sha256 | rel_path |", "|---|---|---|---|"]
    for r in rows:
        lines.append("| " + " | ".join(str(v).replace("|", "\\|") for v in r) + " |")
    return "\n".join(lines)
