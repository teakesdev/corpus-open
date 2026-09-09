
import sys, os, json, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from matterkit import discovery as disc, store

class TestImportAtomicity(unittest.TestCase):
    def test_mixed_brief_is_all_or_nothing(self):
        d = tempfile.mkdtemp()
        conn = store.connect(d)
        brief = {"candidates": [
            {"name": "Good Org (SYNTHETIC)", "org": "G", "jurisdiction": "US",
             "practice_area": "civil", "intake_channel": "published web form",
             "intake_url": "https://example.org/good",
             "source_url": "https://example.org/ga",
             "retrieved_at": "2026-09-09",
             "intake_verbatim_on_source": "Contact us.", "match_reason": "t"},
            {"name": "Bad Org", "org": "B",
             "intake_url": "https://example.org/bad",
             "source_url": "https://example.org/ba",
             "retrieved_at": "2026-09-09"},  # missing intake_channel
        ]}
        p = os.path.join(d, "brief.json")
        json.dump(brief, open(p, "w"))
        with self.assertRaises(Exception):
            disc.import_brief(conn, p)
        n = conn.execute("SELECT COUNT(*) c FROM outreach_recipients").fetchone()["c"]
        self.assertEqual(n, 0, "partial import on failed brief — must be atomic")

if __name__ == "__main__":
    unittest.main()
