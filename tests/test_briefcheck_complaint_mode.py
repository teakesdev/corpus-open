"""brief check --mode complaint: allegations need no record cite, but every cite,
authority and sourced quotation is still checked.

Synthetic data only. Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from matterkit import briefcheck, store  # noqa: E402
from test_briefcheck import Base, codes  # noqa: E402

FACTS = "FACTS\n\n1. On January 1, officers arrested Plaintiff at home.\n\n2. The captain said \"you will sit until Monday\" to Plaintiff.\n"


class TestComplaintMode(Base):
    def test_uncited_allegations_are_not_flagged(self):
        brief = self.m.check(FACTS)
        self.assertIn("uncited-fact", codes(brief, "UNCHECKED"))
        r = self.m.check(FACTS, mode="complaint")
        self.assertEqual([f.code for f in r.findings], [])
        self.assertEqual(r.exit_code, 0)

    def test_alleged_quotations_are_listed_not_flagged(self):
        r = self.m.check(FACTS, mode="complaint")
        self.assertEqual([q for _, q in r.notes], ["you will sit until Monday"])
        self.assertIn("Alleged quotations with no cited source (1)", r.render())

    def test_sourced_quotation_is_still_checked(self):
        r = self.m.check('3. Defendants told the Court the Complaint "[M]otion does not allege that the Chief." Doc. 16 at 2.',
                         mode="complaint")
        self.assertIn("quote-not-found", codes(r, "BLOCK"))

    def test_record_and_authority_cites_still_checked(self):
        r = self.m.check("4. This Court has jurisdiction. Erickson v. Pardus, 551 U.S. 89 (2007). See Doc. 1.",
                         mode="complaint")
        self.assertIn("authority-not-logged", codes(r, "BLOCK"))
        self.assertIn("record-not-in-store", codes(r, "BLOCK"))

    def test_allegation_tag_is_fine_but_hypothesis_is_flagged(self):
        draft = (f"5. Officer Doe acted out of spite. {{fact:{self.m.fact_alleg}}}\n\n")
        r = self.m.check(draft, mode="complaint")
        self.assertEqual([f.code for f in r.findings], [])
        hyp = self.m.assertion("The chief ordered it.", "HYPOTHESIS", None)
        self.m.conn.commit()
        r = self.m.check(f"6. The chief ordered it. {{fact:{hyp}}}", mode="complaint")
        msg = [f.message for f in r.findings if f.code == "fact-not-documented"]
        self.assertEqual(len(msg), 1)
        self.assertIn("information and belief", msg[0])

    def test_report_names_the_mode(self):
        r = self.m.check(FACTS, mode="complaint")
        self.assertIn("Mode: complaint", r.render())

    def test_unknown_mode_refused(self):
        with self.assertRaises(ValueError):
            self.m.check(FACTS, mode="motion")


class TestComplaintModeCliMcp(Base):
    def test_cli_mode_flag(self):
        draft = os.path.join(self._tmp.name, "fac.md")
        with open(draft, "w") as f:
            f.write(FACTS)
        env = dict(os.environ, PATH="")
        run = lambda *a: subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), "brief", "check",  # noqa: E731
                                         self.m.dir, draft, *a], capture_output=True, text=True, env=env)
        self.assertEqual(run().returncode, 2)
        p = run("--mode", "complaint")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Mode: complaint", p.stdout)

    def test_mcp_mode_argument(self):
        draft = os.path.join(self.m.dir, "Filings", "fac.md")
        with open(draft, "w") as f:
            f.write(FACTS)
        self.m.conn.close()
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "matter.brief_check",
                          "arguments": {"path": "Filings/fac.md", "pdftotext": False, "mode": "complaint"}}}
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")], input=json.dumps(req) + "\n",
                           capture_output=True, text=True, env=dict(os.environ, MATTER_DIR=self.m.dir), check=True)
        text = json.loads(p.stdout.strip().splitlines()[-1])["result"]["content"][0]["text"]
        self.assertTrue(text.startswith("exit=0"), text[:200])
        self.m.conn = store.connect(self.m.dir)


if __name__ == "__main__":
    unittest.main()


class TestNumberedParagraphsAreNotHeadings(Base):
    def test_numbered_sentence_is_checked_not_skipped(self):
        r = self.m.check("1. The complaint was filed in July. Doc. 1.")
        self.assertIn("record-not-in-store", codes(r, "BLOCK"))

    def test_short_numbered_title_without_period_is_still_a_heading(self):
        r = self.m.check("STATEMENT OF FACTS\n\n1. Background\n\nThe arrest happened at home on a holiday.\n")
        self.assertIn("uncited-fact", codes(r, "UNCHECKED"))
        self.assertEqual([f.cite for f in r.findings if f.code == "uncited-fact"],
                         ["The arrest happened at home on a holiday."])

    def test_roman_numeral_heading_with_period_is_still_a_heading(self):
        r = self.m.check("I. The Court may set a limited deadline consistent with Rule 15(a)(2).\n\nText. Doc. 16.")
        self.assertEqual([f.code for f in r.findings if f.code == "authority-not-logged"], [])
