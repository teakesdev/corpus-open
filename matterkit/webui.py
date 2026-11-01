"""matter ui: a browser UI over a workspace of matters, for people who don't use a terminal.

A WSGI app, served locally by stdlib wsgiref on 127.0.0.1 only. The same app can later run
under a production WSGI server behind a TLS proxy (design 2026-10-31 §2) without changes:
- identity comes from the server (the launch operator now, a signed-in user later), never
  from a request body;
- every API route goes through one authorize(identity, case_id, action) policy;
- allowed Host/Origin values are configuration, loopback by default.

Every API call needs the per-launch token in the X-Matter-Token header. Pages set text with
textContent under a strict CSP. Nothing here sends, files or contacts anyone.
"""
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import traceback
from dataclasses import dataclass
from socketserver import TCPServer, ThreadingMixIn
from urllib.parse import parse_qs
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from . import amendments, cases, deadlines, facts, ingest, store, subcase
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
    """The WSGI application. `hosts` are the Host header values accepted (host:port)."""

    def __init__(self, workspace, operator, token, hosts=(), policy=allow_local_operator, max_upload=MAX_UPLOAD):
        self.workspace = os.path.abspath(workspace)
        self.identity = Identity(check_operator(operator))
        self.token = token
        self.hosts = set(hosts)
        self.policy = policy
        self.max_upload = max_upload
        self.routes = [(method, re.compile(pattern), action, fn) for method, pattern, action, fn in ROUTES]

    def set_port(self, port):
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

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
        if origin is not None and origin not in {f"http://{h}" for h in self.hosts}:
            raise HttpError(403, "forbidden origin")
        if req.header("Sec-Fetch-Site") == "cross-site":
            raise HttpError(403, "forbidden origin")
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
            if not self.policy(self.identity, case_id, action):
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


_STATUS = {200: "200 OK", 400: "400 Bad Request", 403: "403 Forbidden", 404: "404 Not Found",
           405: "405 Method Not Allowed", 409: "409 Conflict", 413: "413 Payload Too Large",
           415: "415 Unsupported Media Type", 500: "500 Internal Server Error"}


def _public(entry):
    """A case as the page sees it: no absolute paths."""
    return {k: entry[k] for k in ("id", "rel", "title", "parent_id")}


def _read(case):
    try:
        return store.connect_readonly(case["path"])
    except (FileNotFoundError, sqlite3.Error):
        raise HttpError(404, "That case's store can't be opened.") from None


# -- route handlers: fn(app, req, case) -> JSON-able --

def h_session(app, req, case):
    return {"operator": app.identity.name, "workspace": os.path.basename(app.workspace) or app.workspace,
            "api": "v0", "folders": list(cases.FOLDERS), "labels": sorted(store.VALID_STATUS),
            "layers": list(LAYERS), "max_upload_bytes": app.max_upload}


def h_cases(app, req, case):
    return {"cases": [_public(c) for c in cases.discover(app.workspace)]}


def h_case_create(app, req, case):
    body = req.json()
    try:
        return {"case": _public(cases.create(app.workspace, body.get("name")))}
    except cases.CaseError as e:
        raise HttpError(400, str(e)) from None


def h_status(app, req, case):
    conn = _read(case)
    try:
        data = pk.status_data(conn, case["path"])
        data["amendments_open"] = amendments.counts(conn)["open"]  # 0 on a store that predates the tables
    finally:
        conn.close()
    data.pop("matter_dir", None)
    return {"case": _public(case), "status": data}


def h_documents(app, req, case):
    conn = _read(case)
    try:
        rows = conn.execute("SELECT id, rel_path, kind, bytes, sha256, source_url FROM documents"
                            " ORDER BY rel_path COLLATE NOCASE").fetchall()
    finally:
        conn.close()
    return {"documents": [dict(r) for r in rows]}


def h_verify(app, req, case):
    conn = _read(case)
    try:
        ok, changed, missing, failures = ingest.verify_integrity(conn, case["path"])
    finally:
        conn.close()
    return {"ok": ok, "changed": changed, "missing": missing,
            "failures": [{"kind": k, "rel_path": p} for k, p in failures]}


def h_scan(app, req, case):
    conn = store.connect(case["path"])
    try:
        added, skipped, excluded = ingest.ingest_paths(conn, case["path"], [case["path"]])
    finally:
        conn.close()
    return {"added": added, "already_registered": skipped, "excluded": excluded}


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
    root = case["path"]
    real_root = os.path.realpath(root)
    folder_path = os.path.join(root, folder)
    if not os.path.lexists(folder_path):
        os.mkdir(folder_path)
    if os.path.islink(folder_path) or os.path.realpath(folder_path) != os.path.join(real_root, folder):
        raise HttpError(400, f"The {folder} folder is a link to somewhere else; Matter Kit won't write through it.")
    target = os.path.join(folder_path, name)
    rel = f"{folder}/{name}"
    try:  # O_EXCL: never replace an existing file, and never follow a link planted at the name
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    except FileExistsError:
        raise HttpError(409, f"{rel} already exists. Rename your file and try again; "
                             "Matter Kit never replaces a file in a case.") from None
    stream, left = req.environ["wsgi.input"], n
    try:
        with os.fdopen(fd, "wb") as f:
            while left:
                chunk = stream.read(min(left, 1024 * 1024))
                if not chunk:
                    raise HttpError(400, "The upload stopped before the whole file arrived. Try again.")
                f.write(chunk)
                left -= len(chunk)
    except BaseException:
        os.unlink(target)  # only ever the partial file this request created
        raise
    digest = ingest.sha256_file(target)
    conn = store.connect(root)
    try:
        dup = conn.execute("SELECT rel_path FROM documents WHERE sha256=?", (digest,)).fetchone()
        if dup:
            os.unlink(target)  # the bytes are already in the case; keep the tree as it was
            raise HttpError(409, f"This file is already in the case as {dup['rel_path']}.")
        added, _, excluded = ingest.ingest_paths(conn, root, [target])
    finally:
        conn.close()
    if not added:
        os.unlink(target)
        raise HttpError(400, "Matter Kit couldn't register that file, so it wasn't kept.")
    return {"rel_path": rel, "sha256": digest, "bytes": n, "kind": ingest.classify(rel)}


def h_deadlines(app, req, case):
    show_all = req.query.get("all") in ("1", "true")
    conn = _read(case)
    try:
        rows = conn.execute("SELECT * FROM deadlines" + ("" if show_all else " WHERE state='open'")
                            + " ORDER BY due_date").fetchall()
    finally:
        conn.close()
    return {"deadlines": [{"id": r["id"], "label": r["label"], "due_date": r["due_date"],
                           "rule_source": r["rule_source"], "confidence": r["confidence"],
                           "verification": r["verification_status"], "state": r["state"],
                           "overdue": deadlines.is_overdue(r["due_date"], r["state"])} for r in rows]}


def h_facts(app, req, case):
    conn = _read(case)
    try:
        return {"facts": facts.list_(conn)}
    finally:
        conn.close()


def h_fact_add(app, req, case):
    body = req.json()
    if not isinstance(body.get("text"), str) or not isinstance(body.get("status", ""), str):
        raise HttpError(400, "A fact needs its text and a label.")
    if not isinstance(body.get("sources") or [], list):
        raise HttpError(400, "sources must be a list")
    refs = []
    for s in body.get("sources") or []:
        if not isinstance(s, dict) or not isinstance(s.get("document_id"), str):
            raise HttpError(400, "each source needs a document_id")
        loc = s.get("locator") or ""
        if not isinstance(loc, str) or len(loc) > 200 or "\n" in loc or "\r" in loc:
            raise HttpError(400, "a source's page or location must be one short line")
        refs.append(s["document_id"] + (f"@{loc.strip()}" if loc.strip() else ""))
    conn = store.connect(case["path"])
    try:
        known = {r["id"] for r in conn.execute("SELECT id FROM documents")}
        unknown = [r.partition("@")[0] for r in refs if r.partition("@")[0] not in known]
        if unknown:  # the picker only offers this case's documents; anything else is refused, not dropped
            raise HttpError(400, "That source isn't a document registered in this case.")
        try:
            return {"fact": facts.add(conn, case["path"], body.get("text"), body.get("status") or "",
                                      refs, by=store.attribution(app.identity.author))}
        except (facts.FactError, subcase.SubcaseError) as e:
            raise HttpError(400, str(e)) from None
    finally:
        conn.close()


def h_amendments(app, req, case):
    conn = _read(case)
    try:
        try:
            items = amendments.list_(conn, req.query.get("status", "open"))
        except amendments.AmendmentError as e:
            raise HttpError(400, str(e)) from None
        return {"amendments": items, "counts": amendments.counts(conn)}
    finally:
        conn.close()


def h_amendment_add(app, req, case):
    body = req.json()
    facts_in, docs_in = body.get("facts", []), body.get("documents", [])
    if not isinstance(facts_in, list):
        raise HttpError(400, "facts must be a list of fact ids")
    if not isinstance(docs_in, list):
        raise HttpError(400, "documents must be a list")
    pairs = []
    for d in docs_in:
        if not isinstance(d, dict) or not isinstance(d.get("id"), str):
            raise HttpError(400, "each document needs an id")
        pairs.append((d["id"], d.get("locator")))  # the page or place is checked by amendments.add
    conn = store.connect(case["path"])
    try:
        try:
            made = amendments.add(conn, body.get("text"), body.get("paragraphs"), body.get("reason"),
                                  body.get("target", amendments.DEFAULT_TARGET), facts_in, pairs,
                                  by=store.attribution(app.identity.author))
        except amendments.AmendmentError as e:
            raise HttpError(400, str(e)) from None
        return {"amendment": made}
    finally:
        conn.close()


def h_amendment_status(app, req, case):
    body = req.json()
    item_id = req.params["item"]
    conn = store.connect(case["path"])
    try:
        try:
            amendments.set_status(conn, item_id, body.get("status"), body.get("note"),
                                  by=store.attribution(app.identity.author))
        except amendments.AmendmentError as e:
            raise HttpError(404 if e.kind == "not-found" else 400, str(e)) from None
        return {"amendment": amendments.get(conn, item_id)}
    finally:
        conn.close()


def _drafts(root):
    out = []
    for fp in ingest.walk_files(root):
        if fp.lower().endswith(DRAFT_EXTS + UNSUPPORTED_DRAFT_EXTS) and not os.path.islink(fp):
            out.append(os.path.relpath(fp, root).replace(os.sep, "/"))
    return sorted(out, key=str.lower)


def h_drafts(app, req, case):
    return {"drafts": [{"path": p, "supported": p.lower().endswith(DRAFT_EXTS)} for p in _drafts(case["path"])]}


def h_brief_check(app, req, case):
    from . import briefcheck
    body = req.json()
    path, mode = body.get("path"), body.get("mode") or "brief"
    if mode not in ("brief", "complaint"):
        raise HttpError(400, "mode must be brief or complaint")
    if path not in _drafts(case["path"]):  # only names this case's own walk produced; no path joins from input
        raise HttpError(400, "Pick a draft from this case.")
    if path.lower().endswith(UNSUPPORTED_DRAFT_EXTS):
        raise HttpError(400, "Brief check reads .md and .txt drafts only for now. In Word, use "
                             "File > Save As > Plain Text (.txt), put the copy in the case, and check that.")
    try:
        rep = briefcheck.check(case["path"], os.path.join(case["path"], path), mode=mode)
    except (ValueError, FileNotFoundError) as e:
        raise HttpError(400, f"Brief check refused: {e}") from None
    return {"exit_code": rep.exit_code, "summary": rep.summary(), "report": rep.render()}


def h_packet(app, req, case):
    layer = req.query.get("layer") or "3min"
    if layer not in LAYERS:
        raise HttpError(400, f"layer must be one of {', '.join(LAYERS)}")
    conn = _read(case)
    try:
        return {"layer": layer, "text": pk.packet(conn, case["path"], layer=layer)}
    finally:
        conn.close()


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
