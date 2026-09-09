"""R2: human activation + CourtListener manual search (no auto-verify)."""
import json
import os
import tempfile
import unittest
import urllib.request
from unittest import mock

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matterkit import consent, search, store
from matterkit.adapters.courtlistener import CourtListenerFreeAdapter


class TestR2ConsentAuthoring(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        store.connect(self.dir)

    def test_proposal_is_inert(self):
        consent.stage_proposal(
            self.dir, "courtlistener-free", ["www.courtlistener.com"],
            ["query_text"], "agent")
        with self.assertRaises(consent.ConsentError):
            consent.authorize(self.dir, "courtlistener-free",
                              "www.courtlistener.com", "query_text", "agent")

    def test_human_grant_authorizes_issuer(self):
        consent.save_grants(self.dir, [{
            "provider": "courtlistener-free",
            "endpoints": ["www.courtlistener.com"],
            "payload_types": ["query_text"],
            "daily_budget": 20,
            "created_by": "ty",
            "kind": "human",
            "authorized_callers": [],
        }])
        g = consent.authorize(self.dir, "courtlistener-free",
                              "www.courtlistener.com", "query_text", "ty")
        self.assertEqual(g["kind"], "human")

    def test_activate_without_tty_refuses(self):
        real_open = open

        def fake_open(path, *a, **k):
            if path == "/dev/tty":
                raise OSError("no tty")
            return real_open(path, *a, **k)

        with mock.patch("builtins.open", fake_open):
            with self.assertRaises(consent.ConsentError) as ctx:
                consent.activate_human_grant(
                    self.dir, "courtlistener-free",
                    ["www.courtlistener.com"], ["query_text"], "ty")
        self.assertIn("TTY", str(ctx.exception))


class TestR2Search(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        store.connect(self.dir)
        consent.save_grants(self.dir, [{
            "provider": "courtlistener-free",
            "endpoints": ["www.courtlistener.com"],
            "payload_types": ["query_text"],
            "daily_budget": 20,
            "created_by": "ty",
            "kind": "human",
            "authorized_callers": [],
        }])

    def test_search_without_grant_refused(self):
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        store.connect(empty.name)
        with self.assertRaises(consent.ConsentError):
            search.manual_search(empty.name, "ty", "347 U.S. 483")

    def test_mocked_search_returns_provenance_not_verified(self):
        payload = {
            "count": 1,
            "results": [{
                "cluster_id": 1,
                "caseName": "Brown v. Board of Education",
                "citation": ["347 U.S. 483"],
                "absolute_url": "/opinion/1/brown/",
                "court_citation_string": "U.S.",
                "dateFiled": "1954-05-17",
                "status": "Published",
                "opinions": [{"snippet": "We conclude that in the field of public education..."}],
            }],
        }

        with mock.patch("matterkit.adapters.courtlistener.get_json", return_value=payload):
            rows = search.manual_search(self.dir, "ty", "347 U.S. 483", limit=3,
                                        auth_mode="anonymous")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].citation, "347 U.S. 483")
        self.assertTrue(rows[0].provenance["url"].startswith("https://www.courtlistener.com/"))
        self.assertFalse(rows[0].provenance["authenticated"])
        # never source-checked
        conn = store.connect(self.dir)
        n = conn.execute("SELECT COUNT(*) c FROM authorities WHERE status='source-checked'").fetchone()["c"]
        self.assertEqual(n, 0)
        log = open(consent.egress_path(self.dir)).read()
        self.assertIn("courtlistener-free", log)
        self.assertNotIn("347 U.S. 483", log)  # hash-only

    def test_authenticated_without_token_fails_before_egress(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COURTLISTENER_TOKEN", None)
            called = []
            with mock.patch("matterkit.adapters.courtlistener.get_json",
                            side_effect=lambda *a, **k: called.append(1)):
                with self.assertRaises(consent.ConsentError) as ctx:
                    search.manual_search(self.dir, "ty", "347 U.S. 483",
                                        auth_mode="authenticated")
        self.assertIn("COURTLISTENER_TOKEN", str(ctx.exception))
        self.assertEqual(called, [])
        self.assertFalse(os.path.exists(consent.egress_path(self.dir)))


class TestR2Transport(unittest.TestCase):
    """Cross-host redirect must error with no second request and no token leak."""

    def test_cross_host_redirect_no_second_request(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from matterkit.adapters import courtlistener as cl

        stolen = []

        class Steal(BaseHTTPRequestHandler):
            def do_GET(self):
                stolen.append({
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "host": self.headers.get("Host"),
                })
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        box = {"steal": ""}

        class Bounce(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", box["steal"])
                self.end_headers()

            def log_message(self, *a):
                pass

        steal = HTTPServer(("127.0.0.1", 0), Steal)
        bounce = HTTPServer(("127.0.0.1", 0), Bounce)
        box["steal"] = f"http://127.0.0.1:{steal.server_port}/exfil"
        bounce_url = f"http://127.0.0.1:{bounce.server_port}/search"
        t1 = threading.Thread(target=steal.handle_request, daemon=True)
        t2 = threading.Thread(target=bounce.handle_request, daemon=True)
        t1.start(); t2.start()
        with self.assertRaises(consent.ConsentError) as ctx:
            cl.get_json(bounce_url, auth_mode="authenticated", token="secret-token")
        t1.join(timeout=2)
        t2.join(timeout=2)
        steal.server_close(); bounce.server_close()
        self.assertIn("redirect", str(ctx.exception).lower())
        self.assertEqual(stolen, [], "destination received a request — redirect was followed")

    def test_authenticated_mode_sends_token_only_when_set(self):
        from matterkit.adapters import courtlistener as cl
        seen = []

        class Capture(urllib.request.HTTPHandler):
            def http_open(self, req):
                seen.append(req)
                raise consent.ConsentError("stop")

        # anonymous: no Authorization
        op = urllib.request.build_opener(cl._RefuseRedirects, Capture())
        with mock.patch.object(cl, "opener", lambda: op):
            with self.assertRaises(consent.ConsentError):
                cl.get_json("http://127.0.0.1/x", auth_mode="anonymous", token="")
        self.assertTrue(seen)
        self.assertIsNone(seen[0].get_header("Authorization"))

    def test_get_json_authenticated_without_token_no_request(self):
        from matterkit.adapters import courtlistener as cl
        with mock.patch.object(cl, "opener", side_effect=AssertionError("egress")):
            with self.assertRaises(consent.ConsentError):
                cl.get_json("http://127.0.0.1/x", auth_mode="authenticated", token="")


if __name__ == "__main__":
    unittest.main()
