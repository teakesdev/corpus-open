"""R2: human activation + CourtListener manual search (no auto-verify)."""
import json
import os
import tempfile
import unittest
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

        class Resp:
            def read(self):
                return json.dumps(payload).encode()
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        with mock.patch("urllib.request.urlopen", return_value=Resp()):
            rows = search.manual_search(self.dir, "ty", "347 U.S. 483", limit=3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].citation, "347 U.S. 483")
        self.assertTrue(rows[0].provenance["url"].startswith("https://www.courtlistener.com/"))
        # never source-checked
        conn = store.connect(self.dir)
        n = conn.execute("SELECT COUNT(*) c FROM authorities WHERE status='source-checked'").fetchone()["c"]
        self.assertEqual(n, 0)
        log = open(consent.egress_path(self.dir)).read()
        self.assertIn("courtlistener-free", log)
        self.assertNotIn("347 U.S. 483", log)  # hash-only


if __name__ == "__main__":
    unittest.main()
