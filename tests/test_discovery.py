
import sys, os, json, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from matterkit import discovery as disc, store

class TestDiscoveryImport(unittest.TestCase):
    def test_date_only_retrieved_at_does_not_crash(self):
        d = tempfile.mkdtemp()
        conn = store.connect(d)
        brief = {"candidates": [{
            "name": "Org (SYNTHETIC)", "org": "T", "jurisdiction": "US",
            "practice_area": "civil", "intake_channel": "published web form",
            "intake_url": "https://example.org/intake",
            "source_url": "https://example.org/about",
            "retrieved_at": "2026-09-09",   # date-only: naive datetime crash class
            "intake_verbatim_on_source": "Contact us for a consultation.",
            "match_reason": "regression",
        }]}
        p = os.path.join(d, "brief.json")
        json.dump(brief, open(p, "w"))
        r = disc.import_brief(conn, p)   # must not raise TypeError
        self.assertTrue(r)

    def test_incomplete_brief_raises_clear_error(self):
        d = tempfile.mkdtemp()
        conn = store.connect(d)
        brief = {"candidates": [{
            "name": "No-channel Org", "org": "T",
            "intake_url": "https://example.org/i",
            "source_url": "https://example.org/a",
            "retrieved_at": "2026-09-09",
        }]}  # no intake_channel, no verbatim
        p = os.path.join(d, "brief.json")
        json.dump(brief, open(p, "w"))
        with self.assertRaises(Exception):
            disc.import_brief(conn, p)
