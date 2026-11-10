"""matter ui: a browser UI over a workspace of matters, for people who don't use a terminal.

A WSGI app, served locally by stdlib wsgiref on 127.0.0.1 only. It keeps the seams a later
private-server phase needs (design 2026-10-31 §2, §8) to host it behind a production WSGI server
and a TLS proxy:
- the person behind each request comes from `identify` (the launch operator now; whoever a
  trusted sign-in front end names later), never from a request body;
- every API route goes through one policy(identity, case_id, action), and the case list and
  status show only cases that policy lets the person read;
- allowed hosts and origins are configuration, loopback by default.
Sign-in, TLS, real per-case grants and hosting are that phase's work; none of it is here.

Every API call needs the per-launch token in the X-Matter-Token header. Pages set text with
textContent under a strict CSP. Nothing here sends, files or contacts anyone. The Case law lookups
panel is where a person turns CourtListener lookups on for a case and saves their key; the
lookups themselves run from the CLI and the MCP server, never from this server.
"""
import hashlib
import hmac
import json
import os
import posixpath
import re
import secrets
import sqlite3
import stat
import sys
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from socketserver import TCPServer, ThreadingMixIn
from urllib.parse import parse_qs
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from . import amendments, caselaw, cases, credentials, deadlines, facts, ingest, store, subcase
from . import packet as pk

API = "/api/v0"
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui_static")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8")}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = [("Content-Security-Policy", CSP), ("X-Content-Type-Options", "nosniff"),
                    ("X-Frame-Options", "DENY"), ("Referrer-Policy", "no-referrer"),
                    ("Cross-Origin-Resource-Policy", "same-origin")]
MAX_JSON = 1024 * 1024
MAX_UPLOAD = 200 * 1024 * 1024
BUSY_TIMEOUT = 5.0  # seconds to wait for a case store another program is writing to
BUSY = "Another program is writing to this case right now. Wait a moment and try again."
CHANGED = ("This case's folder was moved, renamed or replaced while Matter Kit was using it, so nothing "
           "was saved. Reload the page and try again.")
LAYERS = ("30s", "3min", "15min")
DRAFT_EXTS = (".md", ".markdown", ".txt")
UNSUPPORTED_DRAFT_EXTS = (".docx", ".doc", ".pdf", ".rtf", ".odt", ".pages")
# The server sets the author. A body naming one is refused, so a page can't write as someone else.
AUTHOR_KEYS = {"author", "by", "created_by", "directed_by", "identity", "operator", "recorded_by"}
_NAME_BAD = re.compile(r"[\x00-\x1f\x7f/\\]")


@dataclass(frozen=True)
class Identity:
    """Who is acting. Locally: the operator named at launch. On a server: the signed-in user."""
    name: str

    @property
    def author(self) -> str:
        return f"human:{self.name}"


def allow_local_operator(identity, case_id, action):
    """v1 policy: the person who launched the UI on their own computer may do everything."""
    return True


def deny_all(identity, case_id, action):
    return False


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def check_operator(name):
    """The operator is recorded as the author of every write; store.attribution's rules apply."""
    name = (name or "").strip()
    if not name or len(name) > 80:
        raise ValueError("operator name must be 1-80 characters")
    store.attribution(f"human:{name}")  # raises on ';' or line breaks
    return name


def safe_filename(name):
    """A plain file name: no folders, no hidden files, no control characters."""
    name = (name or "").strip()
    if not name or len(name) > 200 or name in (".", "..") or name.startswith(".") or _NAME_BAD.search(name):
        raise HttpError(400, "That file name can't be used. Use a plain name such as 'letter-2026-10-01.pdf'.")
    return name


class Request:
    def __init__(self, environ):
        self.environ = environ
        self.method = environ.get("REQUEST_METHOD", "GET").upper()
        self.path = environ.get("PATH_INFO", "") or "/"
        self.query = {k: v[0] for k, v in parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True).items()}
        self.params = {}  # named groups of the matched route pattern, set by App.handle

    def header(self, name):
        return self.environ.get("HTTP_" + name.upper().replace("-", "_"))

    def content_length(self):
        try:
            n = int(self.environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            raise HttpError(400, "bad Content-Length") from None
        if n < 0:
            raise HttpError(400, "bad Content-Length")
        return n

    def json(self):
        if (self.environ.get("CONTENT_TYPE") or "").split(";")[0].strip() != "application/json":
            raise HttpError(415, "send JSON (Content-Type: application/json)")
        n = self.content_length()
        if n > MAX_JSON:
            raise HttpError(413, "request too large")
        try:
            body = json.loads(self.environ["wsgi.input"].read(n) or b"{}")
        except (ValueError, UnicodeDecodeError):
            raise HttpError(400, "body is not valid JSON") from None
        if not isinstance(body, dict):
            raise HttpError(400, "body must be a JSON object")
        named = sorted(AUTHOR_KEYS & body.keys())
        if named:
            raise HttpError(400, f"the server records who did this; remove {', '.join(named)!s} from the request")
        return body


class App:
    """The WSGI application. `hosts` are the Host header values accepted (host:port); `origins`
    the Origin values (scheme://host[:port]), by default http:// plus each host. `identify(environ)`
    names the person behind a request (an Identity, or None to refuse it): by default the launch
    operator. A deployment passes one that reads only what its trusted sign-in front end sets,
    never a header a browser can send. The app keeps no per-request state of its own."""

    def __init__(self, workspace, operator, token, hosts=(), origins=None, policy=allow_local_operator,
                 identify=None, max_upload=MAX_UPLOAD):
        self.workspace = os.path.abspath(workspace)
        self.operator = Identity(check_operator(operator))
        self.token = token
        self.hosts = set(hosts)
        self.origins = None if origins is None else set(origins)
        self.policy = policy
        self.identify = identify or (lambda environ: self.operator)
        self.max_upload = max_upload
        self.routes = [(method, re.compile(pattern), action, fn) for method, pattern, action, fn in ROUTES]

    def set_port(self, port):
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    def allowed_origins(self):
        return self.origins if self.origins is not None else {f"http://{h}" for h in self.hosts}

    # -- WSGI entry --
    def __call__(self, environ, start_response):
        req = Request(environ)
        try:
            status, headers, body = self.handle(req)
        except HttpError as e:
            status, headers, body = _json(e.status, {"error": e.message})
        except Exception:  # never leak a traceback to the page
            traceback.print_exc(file=sys.stderr)
            status, headers, body = _json(500, {"error": "Something went wrong inside Matter Kit. "
                                                         "Details are in the terminal window."})
        start_response(_STATUS.get(status, f"{status} Error"),
                       headers + SECURITY_HEADERS + [("Content-Length", str(len(body)))])
        return [body]

    def handle(self, req):
        if req.header("Host") not in self.hosts:  # DNS rebinding: a foreign name pointed at 127.0.0.1
            raise HttpError(403, "forbidden host")
        if not req.path.startswith(API + "/"):
            return self.static(req)
        token = req.header("X-Matter-Token") or ""
        if not hmac.compare_digest(token.encode(), self.token.encode()):
            raise HttpError(403, "missing or wrong token; open the link printed in the terminal")
        origin = req.header("Origin")
        if origin is not None and origin not in self.allowed_origins():
            raise HttpError(403, "forbidden origin")
        if req.header("Sec-Fetch-Site") == "cross-site":
            raise HttpError(403, "forbidden origin")
        req.identity = self.identify(req.environ)
        try:
            if not isinstance(req.identity, Identity) or check_operator(req.identity.name) != req.identity.name:
                raise ValueError("no usable identity")
        except ValueError:
            raise HttpError(401, "Sign in first.") from None
        path = req.path[len(API):]
        allowed = []
        for method, pattern, action, fn in self.routes:
            m = pattern.fullmatch(path)
            if not m:
                continue
            if method != req.method:
                allowed.append(method)
                continue
            req.params = m.groupdict()
            case_id = req.params.get("case")
            if not self.policy(req.identity, case_id, action):
                raise HttpError(403, "not allowed")
            case = self.case(case_id) if case_id else None
            return _json(200, fn(self, req, case))
        if allowed:
            raise HttpError(405, "method not allowed")
        raise HttpError(404, "not found")

    def static(self, req):
        if req.method != "GET" or req.path not in STATIC:
            raise HttpError(404, "not found")
        name, ctype = STATIC[req.path]
        with open(os.path.join(STATIC_DIR, name), "rb") as f:
            body = f.read()
        return 200, [("Content-Type", ctype), ("Cache-Control", "no-cache")], body

    def case(self, case_id):
        try:
            return cases.resolve(self.workspace, case_id)
        except cases.CaseError:
            raise HttpError(404, "That case isn't in this workspace.") from None


def _json(status, obj):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return status, [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store")], body


_STATUS = {200: "200 OK", 400: "400 Bad Request", 401: "401 Unauthorized", 403: "403 Forbidden",
           404: "404 Not Found", 405: "405 Method Not Allowed", 409: "409 Conflict", 413: "413 Payload Too Large",
           415: "415 Unsupported Media Type", 500: "500 Internal Server Error", 503: "503 Service Unavailable"}


def _public(entry):
    """A case as the page sees it: no absolute paths."""
    return {k: entry[k] for k in ("id", "rel", "title", "parent_id")}


def _as_seen(app, req, entry):
    """_public, as this person may see it: a case under a parent they may not read is shown on
    its own, without the parent's id or folder name."""
    out = _public(entry)
    if entry["parent_id"] and not app.policy(req.identity, entry["parent_id"], "read"):
        out.update(parent_id=None, rel=posixpath.basename(entry["rel"]))
    return out


def _busy(e):
    return isinstance(e, sqlite3.OperationalError) and ("locked" in str(e) or "busy" in str(e))


@contextmanager
def _pin(app, case):
    """The case pinned for this request (cases.pinned). A folder swapped part way is a 409."""
    try:
        with cases.pinned(app.workspace, case) as pin:
            yield pin
    except cases.CaseChanged:
        raise HttpError(409, CHANGED) from None


@contextmanager
def _open_store(pin, write=False):
    """The pinned case's own store, opened between two checks that its path still leads to the
    pinned folders. Never creates a store. A store another writer holds too long is a 503."""
    pin.check()
    try:
        conn = store.connect_existing(pin.path, timeout=BUSY_TIMEOUT) if write else store.connect_readonly(pin.path)
    except (FileNotFoundError, sqlite3.Error) as e:
        raise HttpError(503, BUSY) if _busy(e) else HttpError(404, "That case's store can't be opened.") from None
    try:
        pin.check()
        yield conn
    except sqlite3.OperationalError as e:
        if _busy(e):
            raise HttpError(503, BUSY) from None
        raise
    finally:
        conn.close()


@contextmanager
def _store(app, case, write=False):
    with _pin(app, case) as pin, _open_store(pin, write) as conn:
        yield conn, pin


# -- route handlers: fn(app, req, case) -> JSON-able --

def h_session(app, req, case):
    return {"operator": req.identity.name, "workspace": os.path.basename(app.workspace) or app.workspace,
            "api": "v0", "folders": list(cases.FOLDERS), "labels": sorted(store.VALID_STATUS),
            "layers": list(LAYERS), "max_upload_bytes": app.max_upload,
            # Whether this computer has a CourtListener key (where from, never the key): the new-case
            # form asks for one only when there is none.
            "courtlistener_key": credentials.courtlistener_token_source(),
            "courtlistener_key_url": caselaw.KEY_URL}


def h_cases(app, req, case):
    """The cases this person may read, and nothing about the others."""
    return {"cases": [_as_seen(app, req, c) for c in cases.discover(app.workspace)
                      if app.policy(req.identity, c["id"], "read")]}


def h_case_create(app, req, case):
    body = req.json()
    try:
        return {"case": _public(cases.create(app.workspace, body.get("name")))}
    except cases.CaseError as e:
        raise HttpError(400, str(e)) from None


def h_status(app, req, case):
    with _store(app, case) as (conn, pin):
        data = pk.status_data(conn, pin.path)
        data["amendments_open"] = amendments.counts(conn)["open"]  # 0 on a store that predates the tables
    for k in ("matter_dir", "parent_line", "children_lines"):  # paths, and other cases' summaries
        data.pop(k, None)
    return {"case": _as_seen(app, req, case), "status": data}


def h_documents(app, req, case):
    with _store(app, case) as (conn, pin):
        rows = conn.execute("SELECT id, rel_path, kind, bytes, sha256, source_url FROM documents"
                            " ORDER BY rel_path COLLATE NOCASE").fetchall()
    return {"documents": [dict(r) for r in rows]}


def h_verify(app, req, case):
    with _store(app, case) as (conn, pin):
        ok, changed, missing, failures = ingest.verify_integrity(conn, pin.path)
        pin.check()
    return {"ok": ok, "changed": changed, "missing": missing,
            "failures": [{"kind": k, "rel_path": p} for k, p in failures]}


def h_scan(app, req, case):
    with _store(app, case, write=True) as (conn, pin):
        added, skipped, excluded = ingest.ingest_paths(conn, pin.path, [pin.path])
    return {"added": added, "already_registered": skipped, "excluded": excluded}


def _exists(rel):
    return f"{rel} already exists. Rename your file and try again; Matter Kit never replaces a file in a case."


def _receive(req, fd, n):
    """Write the n-byte body to fd (closing it) and return its sha256."""
    h, stream, left = hashlib.sha256(), req.environ["wsgi.input"], n
    with os.fdopen(fd, "wb") as f:
        while left:
            chunk = stream.read(min(left, 1024 * 1024))
            if not chunk:
                raise HttpError(400, "The upload stopped before the whole file arrived. Try again.")
            f.write(chunk)
            h.update(chunk)
            left -= len(chunk)
    return h.hexdigest()


def _insert_document(conn, rel, digest, st):
    conn.execute("INSERT INTO documents (id, sha256, rel_path, bytes, mtime, kind) VALUES (?,?,?,?,?,?)",
                 (store.new_id(), digest, rel, st.st_size, st.st_mtime, ingest.classify(rel)))


def _publish(pin, dest, incoming, part, staged, rel, digest):
    """Give the received file its name and its documents row together, under the store's write
    lock, so a scan, the CLI or MCP never sees one without the other. On any failure the row is
    rolled back and the name removed, but only while it is still this request's file."""
    name = rel.rpartition("/")[2]
    info = {"rel_path": rel, "sha256": digest, "bytes": staged.st_size, "kind": ingest.classify(rel)}
    with _open_store(pin, write=True) as conn:
        published = False
        try:
            conn.execute("BEGIN IMMEDIATE")  # other writers wait here until this request is done
            dup = conn.execute("SELECT rel_path FROM documents WHERE sha256=?", (digest,)).fetchone()
            if dup and dup["rel_path"] == rel:  # the same bytes under the same name: already done
                conn.rollback()
                return {**info, "already_registered": True}
            if dup:
                raise HttpError(409, f"This file is already in the case as {dup['rel_path']}.")
            if conn.execute("SELECT 1 FROM documents WHERE rel_path=?", (rel,)).fetchone():
                raise HttpError(409, _exists(rel))
            pin.check()
            try:
                dest.link_from(incoming, part, name)  # fails if the name exists: never replaces
            except FileExistsError:
                raise HttpError(409, _exists(rel)) from None
            published = True
            st = dest.lstat(name)
            if (st.st_dev, st.st_ino) != (staged.st_dev, staged.st_ino):
                raise cases.CaseChanged(f"{rel} was replaced while it was being saved")
            _insert_document(conn, rel, digest, st)
            pin.check()
            conn.commit()
            published = False
        except BaseException:
            conn.rollback()
            if published:
                dest.unlink_if(name, staged)
            raise
    return {**info, "already_registered": False}


def h_upload(app, req, case):
    folder = req.query.get("folder", "")
    if folder not in cases.FOLDERS:
        raise HttpError(400, f"Pick one of the case folders: {', '.join(cases.FOLDERS)}.")
    name = safe_filename(req.query.get("name"))
    n = req.content_length()
    if n == 0:
        raise HttpError(400, "That file is empty.")
    if n > app.max_upload:
        raise HttpError(413, f"That file is larger than {app.max_upload // (1024 * 1024)} MB. "
                             "Copy it into the case folder yourself, then use 'Look for new files'.")
    with _pin(app, case) as pin:
        try:
            linked = stat.S_ISLNK(pin.dir.lstat(folder).st_mode)
        except FileNotFoundError:
            linked = False
        if linked:
            raise HttpError(400, f"The {folder} folder is a link to somewhere else; Matter Kit won't write through it.")
        dest, incoming = pin.sub(folder, create=True), pin.incoming()
        part = f"upload-{secrets.token_hex(8)}.part"
        fd = incoming.create(part, 0o600)
        made = os.fstat(fd)
        try:
            digest = _receive(req, fd, n)
            staged = incoming.lstat(part)  # its size and time as written
            if (staged.st_dev, staged.st_ino) != (made.st_dev, made.st_ino):
                raise cases.CaseChanged("the upload's own file was replaced while it arrived")
            return _publish(pin, dest, incoming, part, staged, f"{folder}/{name}", digest)
        finally:
            incoming.unlink_if(part, made)


def h_deadlines(app, req, case):
    show_all = req.query.get("all") in ("1", "true")
    with _store(app, case) as (conn, pin):
        rows = conn.execute("SELECT * FROM deadlines" + ("" if show_all else " WHERE state='open'")
                            + " ORDER BY due_date").fetchall()
    return {"deadlines": [{"id": r["id"], "label": r["label"], "due_date": r["due_date"],
                           "rule_source": r["rule_source"], "confidence": r["confidence"],
                           "verification": r["verification_status"], "state": r["state"], "owner": r["owner"],
                           "overdue": deadlines.is_overdue(r["due_date"], r["state"])} for r in rows]}


def h_facts(app, req, case):
    with _store(app, case) as (conn, pin):
        return {"facts": facts.list_(conn)}


def h_fact_add(app, req, case):
    body = req.json()
    if not isinstance(body.get("text"), str) or not isinstance(body.get("status", ""), str):
        raise HttpError(400, "A fact needs its text and a label.")
    if not isinstance(body.get("sources") or [], list):
        raise HttpError(400, "sources must be a list")
    sources = []
    for s in body.get("sources") or []:
        if not isinstance(s, dict) or not isinstance(s.get("document_id"), str):
            raise HttpError(400, "each source needs a document_id")
        loc = s.get("locator") or ""
        if not isinstance(loc, str) or len(loc) > 200 or "\n" in loc or "\r" in loc:
            raise HttpError(400, "a source's page or location must be one short line")
        sources.append({"document_id": s["document_id"], "locator": loc.strip() or None})
    with _store(app, case, write=True) as (conn, pin):
        known = {r["id"] for r in conn.execute("SELECT id FROM documents")}
        if any(s["document_id"] not in known for s in sources):
            # the picker only offers this case's documents; anything else is refused, not dropped
            raise HttpError(400, "That source isn't a document registered in this case.")
        try:
            return {"fact": facts.add(conn, pin.path, body.get("text"), body.get("status") or "",
                                      sources, by=store.attribution(req.identity.author))}
        except (facts.FactError, subcase.SubcaseError) as e:
            raise HttpError(400, str(e)) from None


def h_amendments(app, req, case):
    with _store(app, case) as (conn, pin):
        try:
            items = amendments.list_(conn, req.query.get("status", "open"))
        except amendments.AmendmentError as e:
            raise HttpError(400, str(e)) from None
        return {"amendments": items, "counts": amendments.counts(conn)}


def h_amendment_add(app, req, case):
    body = req.json()
    facts_in, docs_in = body.get("facts", []), body.get("documents", [])
    if not isinstance(facts_in, list):
        raise HttpError(400, "facts must be a list of fact ids")
    if not isinstance(docs_in, list):
        raise HttpError(400, "documents must be a list")
    pairs = []
    for d in docs_in:
        # named as the fact form names a source: {"document_id", "locator"}; the first spec's "id" still works
        doc_id = d.get("document_id", d.get("id")) if isinstance(d, dict) else None
        if not isinstance(doc_id, str) or d.get("document_id", doc_id) != d.get("id", doc_id):
            raise HttpError(400, "each document needs a document_id")
        pairs.append((doc_id, d.get("locator")))  # the page or place is checked by amendments.add
    with _store(app, case, write=True) as (conn, pin):
        try:  # a subcase's reference to a parent document is re-hashed against the parent, as a fact's is
            made = amendments.add(conn, body.get("text"), body.get("paragraphs"), body.get("reason"),
                                  body.get("target", amendments.DEFAULT_TARGET), facts_in, pairs,
                                  by=store.attribution(req.identity.author), matter_dir=pin.path)
        except (amendments.AmendmentError, subcase.SubcaseError) as e:
            raise HttpError(400, str(e)) from None
        return {"amendment": made}


def h_amendment_status(app, req, case):
    body = req.json()
    item_id = req.params["item"]
    with _store(app, case, write=True) as (conn, pin):
        try:
            amendments.set_status(conn, item_id, body.get("status"), body.get("note"),
                                  by=store.attribution(req.identity.author))
        except amendments.AmendmentError as e:
            raise HttpError(404 if e.kind == "not-found" else 400, str(e)) from None
        return {"amendment": amendments.get(conn, item_id)}


def _drafts(root):
    out = []
    for fp in ingest.walk_files(root):
        if fp.lower().endswith(DRAFT_EXTS + UNSUPPORTED_DRAFT_EXTS) and not os.path.islink(fp):
            out.append(os.path.relpath(fp, root).replace(os.sep, "/"))
    return sorted(out, key=str.lower)


def h_drafts(app, req, case):
    with _pin(app, case) as pin:
        return {"drafts": [{"path": p, "supported": p.lower().endswith(DRAFT_EXTS)} for p in _drafts(pin.path)]}


def h_brief_check(app, req, case):
    from . import briefcheck
    body = req.json()
    path, mode = body.get("path"), body.get("mode") or "brief"
    if mode not in ("brief", "complaint"):
        raise HttpError(400, "mode must be brief or complaint")
    with _pin(app, case) as pin:
        if not isinstance(path, str) or path not in _drafts(pin.path):  # only names this case's own walk produced
            raise HttpError(400, "Pick a draft from this case.")
        if path.lower().endswith(UNSUPPORTED_DRAFT_EXTS):
            raise HttpError(400, "Brief check reads .md and .txt drafts only for now. In Word, use "
                                 "File > Save As > Plain Text (.txt), put the copy in the case, and check that.")
        shown = hashlib.sha256(pin.read(path)).hexdigest()  # read without following a link
        cites = briefcheck.CiteCheck(caller_id=req.identity.name)  # bare case cites to CourtListener, by default
        try:
            rep = briefcheck.check(pin.path, os.path.join(pin.path, path), mode=mode, cites=cites)
        except (ValueError, FileNotFoundError) as e:
            raise HttpError(400, f"Brief check refused: {e}") from None
        if rep.draft_sha256 != shown:  # the report must describe the file that was picked
            raise HttpError(409, "The draft changed while it was being checked. Save it, then run the check again.")
        pin.check()
    return {"exit_code": rep.exit_code, "summary": rep.summary(), "report": rep.render()}


def h_packet(app, req, case):
    layer = req.query.get("layer") or "3min"
    if layer not in LAYERS:
        raise HttpError(400, f"layer must be one of {', '.join(LAYERS)}")
    with _store(app, case) as (conn, pin):
        return {"layer": layer, "text": pk.packet(conn, pin.path, layer=layer)}


def h_caselaw(app, req, case):
    with _pin(app, case) as pin:
        pin.check()
        return {"caselaw": caselaw.state(pin.path)}


def h_caselaw_set(app, req, case):
    """The person's button press: on or off. The server names the issuer; the body can't."""
    on = req.json().get("on")
    if not isinstance(on, bool):
        raise HttpError(400, "Send on: true or on: false.")
    with _pin(app, case) as pin:
        pin.check()
        if on:
            caselaw.turn_on(pin.path, req.identity.name, "web-ui")
        else:
            caselaw.turn_off(pin.path, req.identity.name, "web-ui")
        pin.check()
        return {"caselaw": caselaw.state(pin.path)}


def h_courtlistener_key(app, req, case):
    """Save or forget the operator's CourtListener key. The key is never sent back."""
    body = req.json()
    if body.get("forget") is True:
        credentials.forget_courtlistener_token()
    else:
        try:
            credentials.save_courtlistener_token(body.get("key"))
        except ValueError as e:
            raise HttpError(400, str(e)) from None
    return {"key": credentials.courtlistener_token_source()}


CASE = r"/cases/(?P<case>[0-9a-f]{16})"
ITEM = r"(?P<item>am_[0-9a-f]{12})"  # an amendment-list item id
# (method, path under /api/v0, action, handler). Every API route is here, and App.handle runs
# the policy before any handler, so no route can skip authorization.
ROUTES = [
    ("GET", r"/session", "read", h_session),
    ("GET", r"/cases", "read", h_cases),
    ("POST", r"/cases", "create-case", h_case_create),
    ("GET", CASE + r"/status", "read", h_status),
    ("GET", CASE + r"/documents", "read", h_documents),
    ("POST", CASE + r"/verify", "read", h_verify),
    ("POST", CASE + r"/scan", "write", h_scan),
    ("PUT", CASE + r"/files", "write", h_upload),
    ("GET", CASE + r"/deadlines", "read", h_deadlines),
    ("GET", CASE + r"/facts", "read", h_facts),
    ("POST", CASE + r"/facts", "write", h_fact_add),
    ("GET", CASE + r"/amendments", "read", h_amendments),
    ("POST", CASE + r"/amendments", "write", h_amendment_add),
    ("POST", CASE + r"/amendments/" + ITEM + r"/status", "write", h_amendment_status),
    ("GET", CASE + r"/drafts", "read", h_drafts),
    ("POST", CASE + r"/brief-check", "read", h_brief_check),
    ("GET", CASE + r"/packet", "read", h_packet),
    ("GET", CASE + r"/caselaw", "read", h_caselaw),
    ("POST", CASE + r"/caselaw", "write", h_caselaw_set),
    ("POST", r"/courtlistener-key", "account", h_courtlistener_key),
]


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True

    def server_bind(self):
        # HTTPServer.server_bind runs socket.getfqdn(host): a reverse DNS lookup that can reach a
        # resolver. The name is always 127.0.0.1 here, so skip the lookup.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]
        self.setup_environ()


class QuietHandler(WSGIRequestHandler):
    server_version = "MatterKit"

    def log_message(self, fmt, *args):  # one short line per request; never headers (the token lives there)
        if getattr(self.server, "quiet", False):
            return
        sys.stderr.write(f"  {self.command} {self.path.split('?')[0]} -> {args[1] if len(args) > 1 else ''}\n")


def make(workspace, operator, port=0, policy=allow_local_operator):
    """Bind 127.0.0.1 (never another address) and return (server, app, url)."""
    if not os.path.isdir(workspace):
        raise ValueError(f"not a folder: {workspace}")
    token = secrets.token_urlsafe(32)
    app = App(workspace, operator, token, policy=policy)
    server = make_server("127.0.0.1", port, app, server_class=ThreadingWSGIServer, handler_class=QuietHandler)
    app.set_port(server.server_port)
    return server, app, f"http://127.0.0.1:{server.server_port}/#t={token}"


def serve(workspace, operator, port=0, open_browser=True):
    server, app, url = make(workspace, operator, port)
    print(f"Matter Kit is open at {url}", flush=True)
    print("Keep this window open while you work. Press Ctrl+C to stop.", flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
