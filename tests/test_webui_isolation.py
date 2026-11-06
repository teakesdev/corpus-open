"""The local UI makes no connection off the machine: `matter.py ui` runs in a child process under
a loopback-only socket harness while the parent drives every API route.

Design: docs/superpowers/specs/2026-10-31-local-frontend-design.md §4, §6 task 6. Synthetic data only.
Run: python3 -m unittest discover -s tests -v
"""
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from webui_helpers import KIT, build_workspace, cases, webui  # noqa: E402

HARNESS = os.path.join(KIT, "tests", "loopback_sitecustomize")


def harness_env(config=None):
    env = {k: v for k, v in os.environ.items() if k not in ("MATTER_AUTHOR", "MATTER_DIRECTED_BY", "COURTLISTENER_TOKEN")}
    env["PYTHONPATH"] = HARNESS
    if config:
        env["MATTER_KIT_CONFIG"] = config  # the key route writes here, never to the person's own settings
    return env


class TestHarnessIsActive(unittest.TestCase):
    """Negative controls: the harness must really deny, or the isolation test proves nothing."""

    def run_child(self, code):
        return subprocess.run([sys.executable, "-c", code], env=harness_env(), capture_output=True, text=True, timeout=30)

    def test_outbound_connect_denied(self):
        r = self.run_child("import socket; socket.create_connection(('192.0.2.1', 80), timeout=1)")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("network denied by loopback harness", r.stderr)

    def test_name_lookup_denied(self):
        r = self.run_child("import socket; socket.getaddrinfo('example.org', 443)")
        self.assertIn("network denied by loopback harness", r.stderr)

    def test_bind_all_interfaces_denied(self):
        r = self.run_child("import socket; socket.socket().bind(('0.0.0.0', 0))")
        self.assertIn("network denied by loopback harness", r.stderr)

    def test_reverse_lookup_denied(self):
        # getfqdn swallows errors and returns its input, so call the primitive it uses
        r = self.run_child("import socket; socket.gethostbyaddr('127.0.0.1')")
        self.assertIn("network denied by loopback harness", r.stderr)

    def test_loopback_allowed(self):
        r = self.run_child("import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); print('ok')")
        self.assertEqual(r.stdout.strip(), "ok", r.stderr)

    def test_the_harness_is_what_changes_socket(self):
        # the denials above come from the harness, not from this machine's network setup
        code = "import socket; print(socket.socket.__name__)"
        self.assertEqual(self.run_child(code).stdout.strip(), "LoopbackSocket")
        plain = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(plain.stdout.strip(), "socket")


class TestUiUnderHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mk-webui-iso-")
        self.ws = os.path.join(self.tmp, "workspace")
        os.makedirs(self.ws)
        self.alpha, _ = build_workspace(self.ws)
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(KIT, "matter.py"), "ui", self.ws, "--operator", "Pat Example", "--no-open"],
            env=harness_env(os.path.join(self.tmp, "config")), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        line = self.proc.stdout.readline()
        m = re.search(r"http://127\.0\.0\.1:(\d+)/#t=([A-Za-z0-9_-]+)", line)
        if not m:
            self.proc.kill()
            self.fail(f"no URL from matter.py ui: {line!r} {self.proc.stderr.read()}")
        self.port, self.token = int(m.group(1)), m.group(2)

    def stop(self):
        """Stop the child once; returns its stderr."""
        if not hasattr(self, "stderr"):
            self.proc.terminate()
            try:
                self.stderr = self.proc.communicate(timeout=10)[1]
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.stderr = self.proc.communicate()[1]
        return self.stderr

    def tearDown(self):
        self.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, method, path, body=None, raw=None):
        headers = {"Host": f"127.0.0.1:{self.port}", "X-Matter-Token": self.token}
        if body is not None:
            raw = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            conn.request(method, webui.API + path, body=raw, headers=headers)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read() or b"null")
        finally:
            conn.close()

    def test_every_route_works_with_only_loopback(self):
        cid = {c["rel"]: c["id"] for c in cases.discover(self.ws)}["Alpha v. City"]
        c = f"/cases/{cid}"
        with open(os.path.join(self.alpha, "Correspondence", "letter.txt"), "w") as f:
            f.write("synthetic letter\n")
        status, made = self.call("POST", c + "/amendments", {"text": "Synthetic change"})
        self.assertEqual(status, 200, made)
        item = made["amendment"]["id"]
        calls = [("GET", "/session", None, None), ("GET", "/cases", None, None),
                 ("POST", "/cases", {"name": "Gamma v. County"}, None),
                 ("GET", c + "/status", None, None), ("GET", c + "/documents", None, None),
                 ("POST", c + "/verify", {}, None), ("POST", c + "/scan", {}, None),
                 ("PUT", c + "/files?folder=Evidence&name=upload.txt", None, b"synthetic upload\n"),
                 ("GET", c + "/deadlines?all=1", None, None), ("GET", c + "/facts", None, None),
                 ("POST", c + "/facts", {"text": "Synthetic fact", "status": "UNKNOWN"}, None),
                 ("GET", c + "/drafts", None, None),
                 ("POST", c + "/brief-check", {"path": "Filings/drafts/response_v1.md"}, None),
                 ("GET", c + "/packet?layer=15min", None, None),
                 ("GET", c + "/amendments?status=all", None, None),
                 ("POST", c + "/amendments", {"text": "Another synthetic change", "paragraphs": "para 1"}, None),
                 ("POST", c + f"/amendments/{item}/status", {"status": "approved", "note": "ok"}, None),
                 ("GET", c + "/caselaw", None, None), ("POST", c + "/caselaw", {"on": True}, None),
                 ("POST", "/courtlistener-key", {"key": "0123456789abcdef0123456789abcdef01234567"}, None)]
        # every route in the table is exercised here
        exercised = {(m, re.sub(r"\?.*$", "", p).replace(c, webui.CASE).replace(item, webui.ITEM))
                     for m, p, _, _ in calls}
        self.assertEqual(exercised, {(m, p) for m, p, _, _ in webui.ROUTES})
        for method, path, body, raw in calls:
            with self.subTest(route=f"{method} {path}"):
                status, data = self.call(method, path, body, raw)
                self.assertEqual(status, 200, data)
        self.assertNotIn("network denied", self.stop())


if __name__ == "__main__":
    unittest.main()
