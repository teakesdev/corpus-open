"""R3: Corpus MCP adapter — protocol errors vs empty results; get_node
consent; no verify path. Live network calls only where marked."""
import json
import os
import tempfile
import unittest
import urllib.request
from unittest import mock

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matterkit import consent, search, store
from matterkit.adapters import corpus_search as cs


GRANT = [{
    "provider": "corpus-search",
    "endpoints": ["corpuslaw.us"],
    "payload_types": ["query_text"],
    "daily_budget": 20,
    "created_by": "ty",
    "kind": "human",
    "authorized_callers": [],
}]


def matter_with_grant():
    d = tempfile.mkdtemp(prefix="mk-r3-")
    store.connect(d)
    consent.save_grants(d, GRANT)
    return d


SEARCH_TEXT = (
    "1. Miss. Code Ann. § 75-29-951 — Regulation of cottage food operations\n"
    "   [Miss. Code Ann. § 75-29-951 — Regulation of cottage food operations]"
    "(https://corpuslaw.us/code/a99cab9a-b4bd-4400-96b8-4a75c3ccccde)\n"
    "2. Miss. Code Ann. § 27-65-3 — Definitions — sales tax permit\n"
    "   [Miss. Code Ann. § 27-65-3](https://corpuslaw.us/code/9682a1af-9cce-4674-9d12-1abb0ba16d0c)\n"
)


class TestR3Protocol(unittest.TestCase):
    def setUp(self):
        self.dir = matter_with_grant()

    def test_zero_result_is_not_an_error(self):
        adapter = search.build_adapter("corpus-search", self.dir, "ty")
        with mock.patch.object(cs.CorpusSearchAdapter, "_rpc",
                               side_effect=[{"jsonrpc": "2.0", "id": 1,
                                             "result": {"serverInfo": {}}},
                                            {"jsonrpc": "2.0", "id": 2,
                                             "result": {"text": "No results found."}}]):
            rows = adapter.search(search.SearchQuery(text="zzqqx nonsense"), "ty")
        self.assertEqual(rows, [])

    def test_rpc_error_raises_protocol_error(self):
        adapter = search.build_adapter("corpus-search", self.dir, "ty")
        with mock.patch.object(cs.CorpusSearchAdapter, "_rpc",
                               side_effect=[{"jsonrpc": "2.0", "id": 1,
                                             "result": {}},
                                            {"jsonrpc": "2.0", "id": 2,
                                             "error": {"code": -32002,
                                                       "message": "allotment used up"}}]):
            with self.assertRaises(cs.MCPRpcError) as ctx:
                adapter.search(search.SearchQuery(text="food truck"), "ty")
        self.assertIn("-32002", str(ctx.exception))

    def test_is_error_tool_result_raises(self):
        adapter = search.build_adapter("corpus-search", self.dir, "ty")
        with mock.patch.object(cs.CorpusSearchAdapter, "_rpc",
                               side_effect=[{"result": {}},
                                            {"result": {"isError": True,
                                                        "text": "bad jurisdiction"}}]):
            with self.assertRaises(cs.MCPRpcError):
                adapter.search(search.SearchQuery(text="food truck"), "ty")

    def test_parse_hits_only_uuid_links(self):
        hits = cs._extract_hits(SEARCH_TEXT)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0]["node_id"], "a99cab9a-b4bd-4400-96b8-4a75c3ccccde")
        # prose without a link never becomes an authority
        self.assertEqual(cs._extract_hits("The law says be careful."), [])

    def test_search_records_provenance_not_verified(self):
        adapter = search.build_adapter("corpus-search", self.dir, "ty")
        with mock.patch.object(cs.CorpusSearchAdapter, "_rpc",
                               side_effect=[{"result": {}},
                                            {"result": {"text": SEARCH_TEXT}}]):
            rows = adapter.search(search.SearchQuery(text="cottage food"), "ty")
        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[0].provenance["verified"])
        self.assertFalse(rows[0].provenance["authenticated"])
        self.assertTrue(rows[0].provenance["url"].startswith("https://corpuslaw.us/code/"))
        conn = store.connect(self.dir)
        n = conn.execute("SELECT COUNT(*) c FROM authorities WHERE status='source-checked'").fetchone()["c"]
        self.assertEqual(n, 0)

    def test_get_node_requires_consent_and_records_kind(self):
        adapter = search.build_adapter("corpus-search", self.dir, "ty")
        with mock.patch.object(cs.CorpusSearchAdapter, "_rpc",
                               side_effect=[{"result": {}},
                                            {"result": {"text": "§ 1. Definition"}}]):
            r = adapter.get_node("a99cab9a-b4bd-4400-96b8-4a75c3ccccde", "ty")
        self.assertTrue(r.provenance["url"].endswith("a99cab9a-b4bd-4400-96b8-4a75c3ccccde"))
        self.assertEqual(r.provenance["kind"], "law.get_node")
        self.assertFalse(r.provenance["verified"])
        # egress log records the identifier
        log = open(consent.egress_path(self.dir)).read()
        self.assertIn("corpuslaw.us", log)

    def test_authenticated_without_key_fails_before_egress(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORPUS_API_KEY", None)
            adapter = search.build_adapter("corpus-search", self.dir, "ty",
                                           auth_mode="authenticated")
            with self.assertRaises(consent.ConsentError):
                adapter.search(search.SearchQuery(text="x"), "ty")

    def test_no_grant_refused(self):
        empty = tempfile.mkdtemp(prefix="mk-r3-empty-")
        store.connect(empty)
        with self.assertRaises(consent.ConsentError):
            search.build_adapter("corpus-search", empty, "ty")

    def test_redirect_refused_class_shared(self):
        from matterkit.adapters.corpus_search import _RefuseRedirects
        from matterkit.adapters.courtlistener import _RefuseRedirects as CL
        self.assertIs(_RefuseRedirects.__name__, CL.__name__)


if __name__ == "__main__":
    unittest.main()
