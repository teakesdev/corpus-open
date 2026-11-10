"""No connection, now that statute and code cites are looked up by default.

Before the default, only a person who had granted the Corpus lookup could meet this. Now anyone
who opens Hermes on a plane does. What could go wrong, and so what these tests pin:

- The citation block shows Python's own error text (`<urlopen error [Errno 8] ...>`).
- A failure to connect reads as an answer: "Corpus returned no section with this exact cite".
- The cites after the first are tried anyway, each waiting on a connection that is not there.
"""
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hermes_plugin import REPLY, PluginCase, corpus  # noqa: E402

REAL_RPC = corpus.CorpusSearchAdapter._rpc  # PluginCase puts a stand-in here; these tests need the real one


class Down:
    """An opener on a computer with no connection."""
    tried = 0

    def open(self, req, timeout=None):
        Down.tried += 1
        raise urllib.error.URLError(OSError(8, "nodename nor servname provided, or not known"))


class TestNoConnection(PluginCase):
    def setUp(self):
        super().setUp()
        Down.tried = 0
        for p in (mock.patch.object(corpus.CorpusSearchAdapter, "_rpc", REAL_RPC),
                  mock.patch.object(corpus, "opener", Down)):
            p.start()
            self.addCleanup(p.stop)

    def test_the_block_says_it_in_words(self):
        out = self.plugin().on_reply(response_text=REPLY)
        self.assertIn("Statute and code cites were not looked up: could not reach Corpus (corpuslaw.us)", out)
        self.assertNotIn("urlopen", out)
        self.assertNotIn("<", out[len(REPLY):])

    def test_a_failure_is_not_an_answer(self):
        out = self.plugin().on_reply(response_text=REPLY)
        self.assertNotIn("NOT MATCHED", out)
        self.assertNotIn("IN CORPUS", out)
        for cite in ("Miss. Code Ann. § 89-7-27", "Miss. Code Ann. § 89-8-99"):
            self.assertIn(f"**NOT LOOKED UP** · {cite}", out)

    def test_the_second_cite_does_not_wait_on_the_same_dead_connection(self):
        self.plugin().on_reply(response_text=REPLY)
        self.assertEqual(Down.tried, 1)

    def test_the_adapter_raises_one_kind_of_error(self):
        adapter = corpus.CorpusStatuteLookupAdapter(self.dir, "anonymous")
        with self.assertRaises(RuntimeError) as caught:
            adapter.search(corpus.SearchQuery(text="42 U.S.C. § 1983", jurisdiction="US", limit=5), "hermes")
        self.assertEqual(str(caught.exception), "could not reach Corpus (corpuslaw.us): [Errno 8] nodename nor "
                                                "servname provided, or not known")


if __name__ == "__main__":
    unittest.main()
