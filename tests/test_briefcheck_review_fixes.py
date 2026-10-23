"""SWE-2 review (2026-10-23) findings for brief check, one test per finding.

Synthetic data only. Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zlib

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from matterkit import authority, briefcheck, ingest, store, subcase  # noqa: E402
from test_briefcheck import Base, codes  # noqa: E402


class TestP1IdBinding(Base):
    def test_id_binds_to_nearest_preceding_cite_in_text_order(self):
        r = self.m.check("Defendants moved. Dietz v. Bouldin, 579 U.S. 40, 45 (2016). Doc. 16 at 2. Id. at 99.")
        idc = [c for c in r.rows[-1].cites if c.via_id][0]
        self.assertEqual((idc.kind, idc.target), ("record", "Filings/PACER/case-16-MotionDismiss.txt"))
        self.assertIn("pin-out-of-range", codes(r, "BLOCK"))
        self.assertNotIn("pin-not-in-check", codes(r, "WARN"))


class TestP2Quotes(Base):
    def test_bracketed_alteration_must_match_rest_of_word(self):
        r = self.m.check('The Complaint "[M]otion does not allege that the Chief." Doc. 16 at 2.')
        self.assertIn("quote-not-found", codes(r, "BLOCK"))

    def test_case_alteration_of_a_real_word_is_found(self):
        r = self.m.check('Defendants argue "[t]he Complaint does not allege that the Chief participated." Doc. 16 at 2.')
        self.assertEqual(r.rows[0].quotes[0].status, "found")

    def test_suffix_alteration_is_found(self):
        r = self.m.check('Defendants say the Complaint "does not alleg[e] that the Chief participat[ed]." Doc. 16 at 2.')
        self.assertEqual(r.rows[0].quotes[0].status, "found")

    def test_inserted_words_split_the_quote(self):
        r = self.m.check('The Complaint "does not allege that [the] Chief [of Police] participated in any way." Doc. 16 at 2.')
        self.assertEqual(r.rows[0].quotes[0].status, "found")


class TestP2ExitContract(Base):
    def test_uncited_fact_needs_a_human_not_all_tied(self):
        r = self.m.check("STATEMENT OF FACTS\n\nPlaintiff was home alone that night.\n")
        self.assertIn("uncited-fact", codes(r, "UNCHECKED"))
        self.assertEqual(r.exit_code, 2)
        self.assertNotIn("ALL TIED", r.summary())

    def test_uncited_quote_needs_a_human(self):
        r = self.m.check('He said "something nobody can check" to them.')
        self.assertIn("quote-uncited", codes(r, "UNCHECKED"))
        self.assertEqual(r.exit_code, 2)

    def test_exit_zero_with_warnings_does_not_claim_all_tied_cleanly(self):
        r = self.m.check("Courts manage dockets. Dietz v. Bouldin, 579 U.S. 40, 52 (2016).")
        self.assertEqual(r.exit_code, 0)
        self.assertIn("WARNINGS", r.summary())


class TestP3Segmentation(Base):
    def test_single_digit_pin_does_not_swallow_next_sentence(self):
        r = self.m.check("STATEMENT OF FACTS\n\nDefendants say so. Doc. 16 at 3. Plaintiff then walked home alone.\n")
        self.assertIn("uncited-fact", codes(r, "UNCHECKED"))

    def test_initials_still_do_not_split(self):
        r = self.m.check("STATEMENT OF FACTS\n\nOfficer J. Doe arrested him. Doc. 16 at 1.\n")
        self.assertEqual([f.code for f in r.findings if f.code == "uncited-fact"], [])


class TestP3RecordPins(Base):
    def test_pin_after_multi_entry_cite_is_applied_to_last_and_flagged(self):
        r = self.m.check("Both say so. Docs. 27 and 16 at 9.")
        self.assertIn("pin-ambiguous", codes(r, "WARN"))
        self.assertIn("pin-out-of-range", codes(r, "BLOCK"))  # Doc. 16 has 3 pages

    def test_quote_across_page_break_outside_pins_warns(self):
        r = self.m.check('Defendants "move to dismiss. The Complaint does not allege" this. Doc. 16 at 3.')
        self.assertIn("quote-wrong-page", codes(r, "WARN"))

    def test_quote_across_page_break_inside_pins_found(self):
        r = self.m.check('Defendants "move to dismiss. The Complaint does not allege" this. Doc. 16 at 1-2.')
        self.assertEqual(r.rows[0].quotes[0].status, "found")
        self.assertIn("pp. 1–2", r.rows[0].quotes[0].where)


class TestP3Pleadings(Base):
    def test_id_page_pin_after_pleading_is_checked(self):
        r = self.m.check("The clerk refused. Compl. ¶ 3. The clerk refused again. Id. at 40.")
        self.assertIn("pin-unchecked", codes(r, "UNCHECKED"))

    def test_pleading_resolved_by_filename_only_is_unconfirmed(self):
        self.m.docket({"entries": {"16": "Filings/PACER/case-16-MotionDismiss.txt"}, "pleadings": {"Compl.": "15"}})
        r = self.m.check("The County answered. Compl. ¶ 1.")
        self.assertIn("record-unconfirmed", codes(r, "UNCHECKED"))


class TestP3Authorities(Base):
    def test_local_civil_rule_is_not_read_as_federal_rule(self):
        r = self.m.check("Exhibits are required. Local Uniform Civil Rule 15.")
        blocked = [f for f in r.findings if f.code == "authority-not-logged"]
        self.assertEqual(len(blocked), 1)
        self.assertIn("L.U.Civ.R. 15", r.authorities[0].display)

    def test_local_civil_rule_matches_logged_local_rule(self):
        r = self.m.check("Responses are due in 14 days. Local Civil Rule 7(b)(4).")
        self.assertEqual(codes(r, "BLOCK"), [])

    def test_section_short_form_without_full_cite_warns(self):
        r = self.m.check("A § 1983 claim needs a person.")
        self.assertIn("short-form-without-full-cite", codes(r, "WARN"))

    def test_duplicate_store_rows_for_one_authority_warn(self):
        self.m.authority("Dietz v. Bouldin, 579 U.S. 40", "unverified")
        self.m.conn.commit()
        r = self.m.check("Courts manage dockets. Dietz v. Bouldin, 579 U.S. 40, 45 (2016).")
        self.assertIn("authority-duplicate", codes(r, "WARN"))


class TestP3Coverage(Base):
    def test_attachment_reference_does_not_create_phantom_entries(self):
        self.m.write("Filings/PACER/case-27-Order.txt", "ORDER. See Doc. 16-1 at 2.")
        # re-register the changed order under a new path to keep integrity clean
        self.m.write("Filings/PACER/case-26-Order2.txt", "ORDER. See Doc. 16-1 at 2.")
        ingest.ingest_paths(self.m.conn, self.m.dir, [os.path.join(self.m.dir, "Filings/PACER/case-26-Order2.txt")])
        self.m.docket({"entries": {"16": "Filings/PACER/case-16-MotionDismiss.txt",
                                   "26": "Filings/PACER/case-26-Order2.txt"}})
        r = self.m.check("Nothing cited.")
        refs = " ".join(r.docket_refs)
        self.assertIn("Doc. 16-1", refs)
        self.assertNotIn("Doc. 1 ", refs + " ")


class TestP3Robustness(Base):
    def test_malformed_docket_map_is_refused_not_crashed(self):
        for bad in ([1, 2], {"entries": {"abc": "x"}}, {"entries": {"16": 5}}, {"pleadings": "x"}):
            self.m.docket(bad)
            with self.assertRaises(ValueError):
                self.m.check("Doc. 16.")

    def test_pdf_page_count_ignores_content_stream_text(self):
        fake = (b"%PDF-1.7\n1 0 obj << /Type /Pages /Count 2 /Kids [] >> endobj\n"
                b"2 0 obj << /Length 40 /Filter /FlateDecode >> stream\n"
                + zlib.compress(b"BT (/Type /Pages /Count 9999) Tj ET") + b"\nendstream endobj\n")
        self.assertEqual(briefcheck._pdf_page_count(fake), 2)

    def test_pdf_page_count_reads_object_streams(self):
        body = zlib.compress(b"1 0 << /Type /Pages /Count 7 /Kids [] >>")
        fake = (b"%PDF-1.7\n5 0 obj << /Type /ObjStm /N 1 /First 4 /Filter /FlateDecode >> stream\n"
                + body + b"\nendstream endobj\n")
        self.assertEqual(briefcheck._pdf_page_count(fake), 7)


class TestP3Authority(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.join(self._tmp.name, "case")
        self.env = {k: v for k, v in os.environ.items() if k not in ("MATTER_AUTHOR", "MATTER_DIRECTED_BY")}
        self.env["PATH"] = ""
        self.cli("init", self.dir)

    def cli(self, *a):
        return subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), *a], capture_output=True,
                              text=True, env=self.env)

    def test_authority_add_records_attribution(self):
        p = self.cli("authority", "add", self.dir, "Example v. Case, 1 U.S. 1", "--author", "agent:model-x")
        self.assertEqual(p.returncode, 0, p.stderr)
        conn = store.connect_readonly(self.dir)
        self.assertIn("agent:model-x", conn.execute("SELECT note FROM authorities").fetchone()[0])
        conn.close()

    def test_authority_commands_honor_docket_bare_rule(self):
        with open(os.path.join(self.dir, ".matter", "docket.json"), "w") as f:
            json.dump({"bare_rule": "Miss. R. Civ. P."}, f)
        p = self.cli("authority", "check", self.dir, "Miss. R. Civ. P. 12", "--source-url", "https://e.org/r")
        self.assertEqual(p.returncode, 0, p.stderr)
        p = self.cli("authority", "check", self.dir, "Rule 12", "--source-url", "https://e.org/r2")
        self.assertEqual(p.returncode, 0, p.stderr)
        conn = store.connect_readonly(self.dir)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM authorities").fetchone()[0], 1)
        conn.close()

    def test_subcases_regular_file_is_refused_cleanly(self):
        with open(os.path.join(self.dir, "Subcases"), "w") as f:
            f.write("not a folder")
        p = self.cli("subcase", "init", self.dir, "X", "--title", "x")
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn("Traceback", p.stderr)

    def test_unregistered_parent_docket_entry_explains_how_to_register(self):
        p = self.cli("subcase", "init", self.dir, "Child", "--title", "child")
        self.assertEqual(p.returncode, 0, p.stderr)
        child = os.path.join(self.dir, "Subcases", "Child")
        with open(os.path.join(child, ".matter", "docket.json"), "w") as f:
            json.dump({"entries": {"5": "parent:Media/x.txt"}}, f)
        draft = os.path.join(child, "d.md")
        with open(draft, "w") as f:
            f.write("It said so. Doc. 5.")
        r = briefcheck.check(child, draft, use_pdftotext=False)
        msg = [f.message for f in r.findings if f.code == "record-not-in-store"][0]
        self.assertIn("fact add", msg)


if __name__ == "__main__":
    unittest.main()
