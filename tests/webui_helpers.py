"""Shared fixtures for the local UI tests: a synthetic workspace and a live server on loopback.

Synthetic data only. Not a test module (no test_ prefix); test_webui_*.py import it.
"""
import contextlib
import hashlib
import http.client
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import cases, deadlines, ingest, store, subcase, webui  # noqa: E402

OPERATOR = "Pat Example"


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def build_workspace(root):
    """Two client cases (Alpha with a subcase and data, Beta empty) plus a non-case folder."""
    alpha, beta = os.path.join(root, "Alpha v. City"), os.path.join(root, "Beta Matter")
    cases.init(alpha)
    cases.init(beta)
    os.makedirs(os.path.join(root, "not-a-case", "Evidence"))
    write(os.path.join(alpha, "Evidence", "memo.md"), "# Memo\n\nThe officer arrived at 9:14 PM.\n")
    write(os.path.join(alpha, "Filings", "drafts", "response_v1.md"),
          "# Response\n\n## Facts\n\nThe officer arrived at 9:14 PM.\n")
    write(os.path.join(alpha, "Filings", "drafts", "response_v1.docx"), "not really word")
    conn = store.connect(alpha)
    quiet(ingest.ingest_paths, conn, alpha, [os.path.join(alpha, "Evidence")])
    deadlines.add(conn, "Reply brief", "2000-01-03", "human")  # long past: overdue
    deadlines.add(conn, "Status report", "2999-12-31", "human")
    conn.close()
    subcase.init(alpha, "Related-Claim", "Related state claim", "human", cases.FOLDERS)
    return alpha, beta


def facts_add_direct(case_dir, text, status, sources):
    from matterkit import facts
    conn = store.connect(case_dir)
    try:
        return facts.add(conn, case_dir, text, status, sources, by="human")
    finally:
        conn.close()


def tree_snapshot(root):
    """{rel path: sha256} of every file under root, .matter included: proves 'nothing written'."""
    out = {}
    for dirpath, _, files in os.walk(root):
        for fn in files:
            p = os.path.join(dirpath, fn)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root)] = hashlib.sha256(f.read()).hexdigest()
    return out


class ServerCase(unittest.TestCase):
    """A workspace in a tempdir and the UI serving it on 127.0.0.1:<random>."""
    policy = staticmethod(webui.allow_local_operator)
    max_upload = webui.MAX_UPLOAD

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mk-webui-")
        self.ws = os.path.join(self.tmp, "workspace")
        os.makedirs(self.ws)
        self.alpha, self.beta = build_workspace(self.ws)
        self.server, self.app, self.url = webui.make(self.ws, OPERATOR, policy=self.policy)
        self.app.max_upload = self.max_upload
        self.port = self.server.server_port
        self.host = f"127.0.0.1:{self.port}"
        self.server.quiet = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.ids = {c["rel"]: c["id"] for c in cases.discover(self.ws)}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def request(self, method, path, body=None, *, token=True, host=None, headers=None, raw=None):
        """(status, headers dict lower-cased, parsed JSON or raw bytes)."""
        h = {"Host": self.host if host is None else host}
        if token:
            h["X-Matter-Token"] = self.app.token if token is True else token
        if body is not None:
            raw = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request(method, path, body=raw, headers=h)
            resp = conn.getresponse()
            data = resp.read()
            hdrs = {k.lower(): v for k, v in resp.getheaders()}
        finally:
            conn.close()
        if hdrs.get("content-type", "").startswith("application/json"):
            data = json.loads(data)
        return resp.status, hdrs, data

    def api(self, method, path, body=None, **kw):
        return self.request(method, webui.API + path, body, **kw)

    def case_path(self, rel, sub):
        return f"/cases/{self.ids[rel]}{sub}"

    def fact_count(self, case_dir):
        conn = store.connect_readonly(case_dir)
        try:
            return conn.execute("SELECT COUNT(*) c FROM assertions").fetchone()["c"]
        finally:
            conn.close()


HOST = "127.0.0.1:8"


def direct_app(ws, **kw):
    """The app with no server around it, for calls that need an exact interleaving."""
    return webui.App(ws, OPERATOR, "test-token", hosts={HOST}, **kw)


class Body(io.BytesIO):
    """A request body that runs `on_first` before its first bytes go out, `on_end` once the last
    byte has been read, and raises `fail` on the first read (after on_first), if given."""

    def __init__(self, data, on_first=None, on_end=None, fail=None):
        super().__init__(data)
        self.size, self.on_first, self.on_end, self.fail = len(data), on_first, on_end, fail

    def read(self, n=-1):
        if self.on_first:
            fn, self.on_first = self.on_first, None
            fn()
        if self.fail:
            raise self.fail
        chunk = super().read(n)
        if self.on_end and self.tell() >= self.size:
            fn, self.on_end = self.on_end, None
            fn()
        return chunk


def call(app, method, path, body=None, *, raw=None, stream=None, headers=None, host=HOST):
    """Call the WSGI app directly, no socket: (status, parsed JSON). `stream` stands in for the
    body reader of `raw` (see Body), to pause or fail an upload part way."""
    from wsgiref.util import setup_testing_defaults
    data = json.dumps(body).encode() if body is not None else (raw or b"")
    env = {}
    setup_testing_defaults(env)
    env.update({"REQUEST_METHOD": method, "PATH_INFO": webui.API + path.partition("?")[0],
                "QUERY_STRING": path.partition("?")[2], "HTTP_HOST": host, "HTTP_X_MATTER_TOKEN": app.token,
                "CONTENT_LENGTH": str(len(data)), "wsgi.input": stream or io.BytesIO(data)})
    if body is not None:
        env["CONTENT_TYPE"] = "application/json"
    for k, v in (headers or {}).items():
        env["HTTP_" + k.upper().replace("-", "_")] = v
    seen = {}
    with contextlib.redirect_stderr(io.StringIO()):
        payload = b"".join(app(env, lambda status, hdrs: seen.setdefault("status", int(status.split()[0]))))
    return seen["status"], json.loads(payload)


def documents(case_dir):
    """{rel_path: sha256} of a case's registered documents."""
    conn = store.connect_readonly(case_dir)
    try:
        return {r["rel_path"]: r["sha256"] for r in conn.execute("SELECT rel_path, sha256 FROM documents")}
    finally:
        conn.close()


def shipped_text(name):
    """The text of one shipped page file (app.js, app.css, index.html)."""
    with open(os.path.join(webui.STATIC_DIR, name), encoding="utf-8") as f:
        return f.read()


def route_samples(case_id):
    """One concrete request per API route, for 'every route' tests."""
    out = []
    for method, pattern, _, _ in webui.ROUTES:
        path = webui.API + pattern.replace(webui.CASE, f"/cases/{case_id}").replace(webui.ITEM, "am_000000000000")
        if method == "PUT":
            path += "?folder=Evidence&name=x.txt"
        body = {"text": "x", "status": "UNKNOWN"} if method == "POST" else None
        out.append((method, path, body))
    return out
