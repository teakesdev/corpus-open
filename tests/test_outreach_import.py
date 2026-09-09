"""Source-backed import: stages, no guessed emails, sticky suppression."""
import json
import os
import tempfile
import unittest

from matterkit import discovery, outreach, store

BRIEF = os.path.join(os.path.dirname(__file__), "..", "evals", "outreach_import", "brief.json")


def _write(cands):
    d = tempfile.mkdtemp(prefix="mk-brief-")
    path = os.path.join(d, "brief.json")
    for c in cands:
        c.setdefault("destination_type", "form")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"candidates": cands}, f)
    return path


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="mk-imp-")
        self.conn = store.connect(self.d)

    def test_bundled_brief_unsendable_until_shortlisted(self):
        result = discovery.import_brief(self.conn, BRIEF)
        self.assertEqual(len(result["rejected"]), 0)
        self.assertEqual(len(result["imported"]), 4)
        rows = self.conn.execute(
            "SELECT status, destination_type, flags_json FROM outreach_recipients").fetchall()
        self.assertTrue(all(r["status"] == "candidate" for r in rows))
        self.assertTrue(all("verbatim-unverified" in r["flags_json"] for r in rows))
        dests = {r["destination_type"] for r in rows}
        self.assertEqual(dests, {"directory", "portal"})
        self.assertNotIn("source-checked", [r["status"] for r in rows])
        with self.assertRaises(ValueError):
            outreach.draft_all(self.conn, {"role": "a party"})
        directory_id = self.conn.execute(
            "SELECT id FROM outreach_recipients WHERE destination_type='directory' LIMIT 1"
        ).fetchone()["id"]
        discovery.shortlist(self.conn, directory_id)
        with self.assertRaises(ValueError) as cm:
            outreach.draft_all(self.conn, {"role": "a party"})
        self.assertIn("directory", str(cm.exception))
        portal_id = self.conn.execute(
            "SELECT id FROM outreach_recipients WHERE destination_type='portal' LIMIT 1"
        ).fetchone()["id"]
        discovery.shortlist(self.conn, portal_id)
        ids = outreach.draft_all(self.conn, {"role": "a party"})
        self.assertEqual(len(ids), 1)
        body = self.conn.execute("SELECT body FROM outreach_drafts").fetchone()["body"]
        self.assertIn("PREPARATION ONLY", body)
        self.assertIn("Destination-Type: portal", body)

    def test_guessed_email_refused(self):
        path = _write([{
            "name": "Pat", "org": "X", "jurisdiction": "US", "practice_area": "civil",
            "intake_channel": "email", "intake_url": "mailto:pat@example.org",
            "source_url": "https://example.org/x", "retrieved_at": "2026-09-09T00:00:00Z",
            "intake_verbatim_on_source": True, "match_reason": "nope",
        }])
        result = discovery.import_brief(self.conn, path)
        self.assertEqual(result["imported"], [])
        self.assertTrue(any("guessed email" in r["error"] for r in result["rejected"]))
        path2 = _write([{
            "name": "Pat", "org": "X", "jurisdiction": "US", "practice_area": "civil",
            "intake_channel": "published web form",
            "intake_url": "https://example.org/contact",
            "source_url": "https://example.org/x",
            "retrieved_at": "2026-09-09T00:00:00Z",
            "intake_verbatim_on_source": True,
            "email": "secret.partner@example.net",
            "quoted_from_source": "contact us on the web form",
            "match_reason": "nope",
        }])
        result2 = discovery.import_brief(self.conn, path2)
        self.assertTrue(any("guessed email" in r["error"] for r in result2["rejected"]))

    def test_reimport_does_not_reset_suppression(self):
        discovery.import_brief(self.conn, BRIEF)
        rid = self.conn.execute(
            "SELECT id FROM outreach_recipients WHERE destination_type='portal' LIMIT 1"
        ).fetchone()["id"]
        discovery.shortlist(self.conn, rid)
        outreach.draft_all(self.conn, {"role": "a party"})
        man = outreach.build_manifest(self.conn)
        outreach.approve_and_simulate(
            self.conn, self.d, issuer="test", expected_sha=man["sha256"])
        outreach.set_response(self.conn, rid, "declined", "not taking cases")
        n_items = self.conn.execute("SELECT COUNT(*) c FROM outreach_batch_items").fetchone()["c"]
        result = discovery.import_brief(self.conn, BRIEF)
        self.assertEqual(result["imported"], [])
        row = self.conn.execute(
            "SELECT status FROM outreach_recipients WHERE id=?", (rid,)).fetchone()
        self.assertEqual(row["status"], "declined")
        with self.assertRaises(ValueError):
            discovery.shortlist(self.conn, rid)
        n_items2 = self.conn.execute("SELECT COUNT(*) c FROM outreach_batch_items").fetchone()["c"]
        self.assertEqual(n_items2, n_items)

    def test_duplicate_channel_one_record(self):
        path = _write([
            {"name": "A", "org": "OrgA", "jurisdiction": "US", "practice_area": "civil",
             "intake_channel": "web", "intake_url": "https://example.org/aid",
             "source_url": "https://example.org/src1", "retrieved_at": "2026-09-09T00:00:00Z",
             "intake_verbatim_on_source": True, "match_reason": "x"},
            {"name": "B", "org": "OrgB", "jurisdiction": "US", "practice_area": "civil",
             "intake_channel": "web", "intake_url": "https://example.org/aid/",
             "source_url": "https://example.org/src2", "retrieved_at": "2026-09-09T00:00:00Z",
             "intake_verbatim_on_source": True, "match_reason": "y"},
        ])
        result = discovery.import_brief(self.conn, path)
        n = self.conn.execute("SELECT COUNT(*) c FROM outreach_recipients").fetchone()["c"]
        self.assertEqual(n, 1)
        self.assertEqual(len(result["reused"]), 1)
        flags = json.loads(self.conn.execute("SELECT flags_json FROM outreach_recipients").fetchone()["flags_json"])
        self.assertIn("duplicate-channel", flags)
        ev = self.conn.execute("SELECT COUNT(*) c FROM outreach_evidence").fetchone()["c"]
        self.assertEqual(ev, 2)

    def test_org_intake_conflict_flagged(self):
        path = _write([
            {"name": "A", "org": "Same Org", "jurisdiction": "US", "practice_area": "civil",
             "intake_channel": "web", "intake_url": "https://example.org/one",
             "source_url": "https://example.org/s", "retrieved_at": "2026-09-09T00:00:00Z",
             "intake_verbatim_on_source": True, "match_reason": "x"},
            {"name": "B", "org": "Same Org", "jurisdiction": "US", "practice_area": "civil",
             "intake_channel": "web", "intake_url": "https://example.org/two",
             "source_url": "https://example.org/s", "retrieved_at": "2026-09-09T00:00:00Z",
             "intake_verbatim_on_source": True, "match_reason": "y"},
        ])
        discovery.import_brief(self.conn, path)
        flags = [json.loads(r["flags_json"]) for r in
                 self.conn.execute("SELECT flags_json FROM outreach_recipients")]
        self.assertTrue(all("conflict:intake-mismatch" in f for f in flags))

    def test_stale_evidence_flagged_not_rejected(self):
        path = _write([{
            "name": "Old", "org": "Org", "jurisdiction": "US", "practice_area": "civil",
            "intake_channel": "web", "intake_url": "https://example.org/old",
            "source_url": "https://example.org/s", "retrieved_at": "2020-01-01T00:00:00Z",
            "intake_verbatim_on_source": True, "match_reason": "x",
        }])
        result = discovery.import_brief(self.conn, path)
        self.assertEqual(len(result["imported"]), 1)
        flags = json.loads(self.conn.execute("SELECT flags_json FROM outreach_recipients").fetchone()["flags_json"])
        self.assertIn("stale-evidence", flags)
        row = self.conn.execute("SELECT status FROM outreach_recipients").fetchone()
        self.assertEqual(row["status"], "candidate")

    def test_cannot_shortlist_candidate(self):
        # v3: no-verbatim still candidate; shortlist as a research lead is
        # allowed. Directory/unknown remain non-draftable.
        path = _write([{
            "name": "Incomplete", "org": "Org", "jurisdiction": "US", "practice_area": "civil",
            "intake_channel": "web", "intake_url": "https://example.org/inc",
            "source_url": "https://example.org/s", "retrieved_at": "2026-09-09",
            "destination_type": "directory",
            "match_reason": "x",
        }])
        result = discovery.import_brief(self.conn, path)
        rid = result["imported"][0]
        row = self.conn.execute("SELECT status FROM outreach_recipients").fetchone()
        self.assertEqual(row["status"], "candidate")
        discovery.shortlist(self.conn, rid)
        with self.assertRaises(ValueError) as cm:
            outreach.draft_all(self.conn, {"role": "a party"})
        self.assertIn("directory", str(cm.exception))

    def test_fabricated_verbatim_stays_candidate(self):
        path = _write([{
            "name": "Fake Firm", "org": "Fake", "jurisdiction": "US", "practice_area": "civil",
            "intake_channel": "web",
            "intake_url": "https://fake-firm.example/contact",
            "source_url": "https://nonexistent-source.example/about",
            "retrieved_at": "2026-09-09T00:00:00Z",
            "intake_verbatim_on_source": "Contact our firm for consultation.",
            "destination_type": "form",
            "match_reason": "fabricated",
        }])
        result = discovery.import_brief(self.conn, path)
        self.assertEqual(len(result["imported"]), 1)
        row = self.conn.execute(
            "SELECT status, flags_json FROM outreach_recipients").fetchone()
        self.assertEqual(row["status"], "candidate")
        flags = json.loads(row["flags_json"])
        self.assertIn("verbatim-unverified", flags)
        self.assertNotEqual(row["status"], "source-checked")

    def test_legacy_source_checked_downgraded(self):
        rid = outreach.add_recipient(
            self.conn, name="Legacy", org="Old", jurisdiction="US",
            practice_area="civil", intake_channel="web",
            intake_url="https://example.org/legacy",
            match_reason="old import", source_url="https://example.org/s",
            destination_type="unknown")
        self.conn.execute(
            "UPDATE outreach_recipients SET status='source-checked', flags_json='[]' WHERE id=?",
            (rid,))
        self.conn.commit()
        outreach._ensure(self.conn)
        row = self.conn.execute(
            "SELECT status, destination_type, flags_json FROM outreach_recipients WHERE id=?",
            (rid,)).fetchone()
        self.assertEqual(row["status"], "candidate")
        self.assertEqual(row["destination_type"], "unknown")
        flags = json.loads(row["flags_json"])
        self.assertIn("source-checked-revoked:unverified", flags)
        with self.assertRaises(ValueError):
            outreach.draft_all(self.conn, {"role": "a party"})


if __name__ == "__main__":
    unittest.main()
