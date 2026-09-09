"""Counsel-outreach MVP: sourced shortlist → drafts → sha-gated nosend → ledger."""
import os
import tempfile
import unittest

from matterkit import outreach, store


SYNTH_POSTURE = {
    "court": "Example District Court",
    "role": "a self-represented plaintiff",
    "case_type": "a civil matter",
    "issue_general": "a civil dispute",
    "deadline": "2026-10-15",
    "deadline_label": "next known date (synthetic)",
    "fee": "limited-scope consult",
    "representation": "a consultation or limited-scope representation",
    "jurisdiction": "N.D. Example",
}


class OutreachTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="mk-ou-")
        self.conn = store.connect(self.d)

    def test_end_to_end_nosend_synthetic(self):
        ids = outreach.seed_synthetic(self.conn)
        self.assertEqual(len(ids), 3)
        drafts = outreach.draft_all(self.conn, SYNTH_POSTURE)
        self.assertEqual(len(drafts), 3)
        man = outreach.build_manifest(self.conn)
        self.assertEqual(man["n"], 3)
        path = outreach.write_manifest(self.d, man)
        self.assertTrue(os.path.isfile(path))
        bid = outreach.approve_and_simulate(
            self.conn, self.d, issuer="test", expected_sha=man["sha256"])
        self.assertTrue(bid.startswith("ob_"))
        outbox = os.path.join(self.d, ".matter", "outreach", "outbox")
        self.assertEqual(len(os.listdir(outbox)), 3)
        led = outreach.ledger(self.conn)
        self.assertIn("SYNTHETIC", led)
        self.assertIn("send=simulated", led)
        rid = ids[0]
        outreach.set_response(self.conn, rid, "declined", "not taking this kind of matter")
        led2 = outreach.ledger(self.conn)
        self.assertIn("declined", led2)

    def test_wrong_sha_refuses(self):
        outreach.seed_synthetic(self.conn)
        outreach.draft_all(self.conn, SYNTH_POSTURE)
        with self.assertRaises(ValueError):
            outreach.approve_and_simulate(
                self.conn, self.d, issuer="test", expected_sha="0" * 64)

    def test_smtp_transport_not_implemented(self):
        outreach.seed_synthetic(self.conn)
        outreach.draft_all(self.conn, SYNTH_POSTURE)
        man = outreach.build_manifest(self.conn)
        with self.assertRaises(ValueError):
            outreach.approve_and_simulate(
                self.conn, self.d, issuer="test", expected_sha=man["sha256"],
                transport="smtp")

    def test_refuse_invented_intake(self):
        with self.assertRaises(ValueError):
            outreach.add_recipient(
                self.conn, name="X", org="Y", jurisdiction="Z",
                practice_area="civil", intake_channel="guess",
                intake_url="not-a-url", match_reason="nope",
                source_url="https://example.org/x")

    def test_refuse_evidence_dump(self):
        outreach.refuse_if_unsafe("hello", "Can we talk about dates?")
        with self.assertRaises(ValueError):
            outreach.refuse_if_unsafe("hello", "I have strong evidence that the defendant lied")
        with self.assertRaises(ValueError):
            outreach.refuse_if_unsafe("hello", "See attached Exhibit A")
        with self.assertRaises(ValueError):
            outreach.refuse_if_unsafe("hello", "Please visit corpuslaw.us to form your LLC")

    def test_no_smtplib_in_module(self):
        path = os.path.join(os.path.dirname(__file__), "..", "matterkit", "outreach.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("import smtplib", src)
        self.assertNotIn("from smtplib", src)

    def test_empty_manifest_refuses(self):
        empty = outreach.build_manifest(self.conn)
        self.assertEqual(empty["n"], 0)
        with self.assertRaises(ValueError) as cm:
            outreach.approve_and_simulate(
                self.conn, self.d, issuer="test", expected_sha=empty["sha256"])
        self.assertIn("empty manifest", str(cm.exception))
        with self.assertRaises(ValueError):
            outreach.draft_all(self.conn, SYNTH_POSTURE)

    def test_redraft_replaces_does_not_duplicate(self):
        outreach.seed_synthetic(self.conn)
        outreach.draft_all(self.conn, SYNTH_POSTURE)
        first = outreach.build_manifest(self.conn)
        self.assertEqual(first["n"], 3)
        fixed = dict(SYNTH_POSTURE, deadline="2026-11-01")
        outreach.draft_all(self.conn, fixed)
        second = outreach.build_manifest(self.conn)
        self.assertEqual(second["n"], 3)
        n_drafts = self.conn.execute("SELECT COUNT(*) c FROM outreach_drafts").fetchone()["c"]
        self.assertEqual(n_drafts, 3)
        bodies = " ".join(it["body"] for it in second["items"])
        self.assertIn("2026-11-01", bodies)
        self.assertNotIn("2026-10-15", bodies)
        with self.assertRaises(ValueError) as cm:
            outreach.approve_and_simulate(
                self.conn, self.d, issuer="test", expected_sha=first["sha256"])
        self.assertIn("mismatch", str(cm.exception).lower())
        bid = outreach.approve_and_simulate(
            self.conn, self.d, issuer="test", expected_sha=second["sha256"])
        n_items = self.conn.execute(
            "SELECT COUNT(*) c FROM outreach_batch_items WHERE batch_id=?",
            (bid,)).fetchone()["c"]
        self.assertEqual(n_items, 3)

    def test_completed_batch_records_survive_later_redraft(self):
        outreach.seed_synthetic(self.conn)
        outreach.draft_all(self.conn, SYNTH_POSTURE)
        man = outreach.build_manifest(self.conn)
        bid = outreach.approve_and_simulate(
            self.conn, self.d, issuer="test", expected_sha=man["sha256"])
        old_drafts = {r["draft_id"] for r in self.conn.execute(
            "SELECT draft_id FROM outreach_batch_items WHERE batch_id=?", (bid,))}
        self.assertEqual(len(old_drafts), 3)
        outreach.add_recipient(
            self.conn, name="Alex Ng, Esq.", org="Ng Clinic (SYNTHETIC)",
            jurisdiction="N.D. Example", practice_area="civil",
            intake_channel="published web form",
            intake_url="https://example.org/ng/intake",
            match_reason="clinic listing civil intake",
            source_url="https://example.org/ng/about")
        outreach.draft_all(self.conn, SYNTH_POSTURE)
        still = {r["draft_id"] for r in self.conn.execute(
            "SELECT draft_id FROM outreach_batch_items WHERE batch_id=?", (bid,))}
        self.assertEqual(still, old_drafts)
        n_old = self.conn.execute(
            "SELECT COUNT(*) c FROM outreach_drafts WHERE id IN ({})".format(
                ",".join("?" * len(old_drafts))), tuple(old_drafts)).fetchone()["c"]
        self.assertEqual(n_old, 3)


if __name__ == "__main__":
    unittest.main()
