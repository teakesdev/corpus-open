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


class TestSwe2ComplaintModeReview(Base):
    """SWE-2 review of complaint mode (2026-10-24): every finding pinned."""

    def test_p2_short_numbered_lines_with_checkable_content_are_text(self):
        for mode in ("brief", "complaint"):
            for draft in ("12. See Doc. 99 at 3", "105. See Doc. 99 at 3", "5. Statement (Doc. 99)", "1. Doc. 99."):
                r = self.m.check(draft, mode=mode)
                self.assertIn("record-not-in-store", codes(r, "BLOCK"), (mode, draft))

    def test_p2_quote_ended_numbered_line_reaches_the_quote_checks(self):
        r = self.m.check('3. The captain said "stand down"', mode="complaint")
        self.assertEqual([q for _, q in r.notes], ["stand down"])
        r = self.m.check('3. The captain said "stand down"')
        self.assertIn("quote-uncited", codes(r, "UNCHECKED"))

    def test_p2_punctuated_title_case_numbered_heading_starts_fact_section_in_brief_mode(self):
        for heading in ("1. Statement of Facts.", "1. Factual Allegations:", "2. Background"):
            r = self.m.check(f"{heading}\n\nThe arrest happened at home on a holiday.\n")
            self.assertEqual([f.cite for f in r.findings if f.code == "uncited-fact"],
                             ["The arrest happened at home on a holiday."], heading)

    def test_sentence_numbered_paragraph_is_still_text(self):
        r = self.m.check("FACTS\n\n1. On January 1, officers arrested Plaintiff at home.\n")
        self.assertIn("uncited-fact", codes(r, "UNCHECKED"))

    def test_p3_letter_numbered_allegation_with_cite_is_checked(self):
        for mode in ("brief", "complaint"):
            r = self.m.check("A. Plaintiff lives in Exampleton. Doc. 99.", mode=mode)
            self.assertIn("record-not-in-store", codes(r, "BLOCK"), mode)

    def test_p3_roman_heading_with_rule_stays_a_heading(self):
        # superseded in round 2: a heading keeps its section role (it ends the facts) and its
        # rule cite is now checked too (Rule 99 is not logged, so it blocks)
        r = self.m.check("STATEMENT OF FACTS\n\nThe arrest happened at home on a holiday.\n\n"
                         "I. Leave is proper under Rule 99(z).\n\nThe motion should be denied because the claims are plausible.\n")
        self.assertEqual([f.cite for f in r.findings if f.code == "uncited-fact"],
                         ["The arrest happened at home on a holiday."])
        self.assertIn("authority-not-logged", codes(r, "BLOCK"))

    def test_p3_summary_mentions_listed_quotations(self):
        r = self.m.check('3. The captain said "stand down" to him.', mode="complaint")
        self.assertIn("1 alleged quotation listed", r.summary())

    def test_p3_mcp_mode_schema_is_typed_and_invalid_mode_is_an_error(self):
        mcp = open(os.path.join(KIT, "matter-mcp.py"), encoding="utf-8").read()
        self.assertIn('"mode": {"type": "string", "enum": ["brief", "complaint"]', mcp)
        draft = os.path.join(self.m.dir, "Filings", "fac.md")
        with open(draft, "w") as f:
            f.write("1. Text.")
        self.m.conn.close()
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "matter.brief_check", "arguments": {"path": "Filings/fac.md", "mode": "motion"}}}
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter-mcp.py")], input=json.dumps(req) + "\n",
                           capture_output=True, text=True, env=dict(os.environ, MATTER_DIR=self.m.dir), check=True)
        res = json.loads(p.stdout.strip().splitlines()[-1])["result"]
        self.assertTrue(res.get("isError"))
        self.m.conn = store.connect(self.m.dir)

    def test_p3_readme_states_the_actual_rule(self):
        readme = open(os.path.join(KIT, "README.md"), encoding="utf-8").read()
        self.assertIn("Headings are still read for cites and quotations", readme)


class TestSwe2ComplaintModeReviewRound2(Base):
    """SWE-2 round-2 re-review of complaint mode (2026-10-25)."""

    def test_p2_cites_inside_headings_are_checked(self):
        for mode in ("brief", "complaint"):
            for draft in ("1. Rule 99(z) Standard.", "3. Claims Under 99 U.S.C. 1983.",
                          "2. Facts Under Local Rule 99.", "II. Argument Under Rule 99.", "## The Rule 99 Standard"):
                r = self.m.check(draft, mode=mode)
                self.assertIn("authority-not-logged", codes(r, "BLOCK"), (mode, draft))

    def test_p2_headings_keep_their_section_role(self):
        r = self.m.check("STATEMENT OF FACTS\n\nThe arrest happened at home on a holiday.\n\n"
                         "II. Argument Under Rule 15\n\nThe motion should be denied because the claims are plausible.\n")
        self.assertEqual([f.cite for f in r.findings if f.code == "uncited-fact"],
                         ["The arrest happened at home on a holiday."])

    def test_p3_quote_only_lettered_heading_reaches_quote_checks(self):
        r = self.m.check('A. He said "the file is closed"')
        self.assertIn("quote-uncited", codes(r, "UNCHECKED"))
        r = self.m.check('A. He said "the file is closed"', mode="complaint")
        self.assertEqual([q for _, q in r.notes], ["the file is closed"])

    def test_p3_numbered_fact_subheading_does_not_close_the_fact_section(self):
        r = self.m.check("1. Statement of Facts.\n\n2. Plaintiff Was Arrested.\n\n"
                         "He was held for days without a hearing.\n")
        self.assertEqual([f.cite for f in r.findings if f.code == "uncited-fact"],
                         ["He was held for days without a hearing."])

    def test_p3_numbered_argument_heading_closes_the_fact_section(self):
        r = self.m.check("1. Statement of Facts.\n\nThe arrest happened at home on a holiday.\n\n2. Argument.\n\n"
                         "The motion should be denied because the claims are plausible.\n")
        self.assertEqual([f.cite for f in r.findings if f.code == "uncited-fact"],
                         ["The arrest happened at home on a holiday."])

    def test_heading_is_never_flagged_as_an_uncited_fact(self):
        r = self.m.check("STATEMENT OF FACTS\n\n## What Happened On The Holiday Weekend\n\nText. Doc. 16.\n")
        self.assertEqual([f.code for f in r.findings if f.code == "uncited-fact"], [])


class TestSwe2ComplaintModeReviewRound3(Base):
    """SWE-2 round-3 re-review of complaint mode (2026-10-26)."""

    def test_more_non_fact_section_names_close_the_fact_section(self):
        for name in ("Issues Presented", "Questions Presented", "Procedural Posture", "Nature of the Case",
                     "Introduction", "Summary", "Merits"):
            r = self.m.check(f"1. Statement of Facts.\n\nThe arrest happened at home on a holiday.\n\n2. {name}.\n\n"
                             "Whether the court erred is plain on this record.\n")
            self.assertEqual([f.cite for f in r.findings if f.code == "uncited-fact"],
                             ["The arrest happened at home on a holiday."], name)

    def test_readme_lists_every_veto_pattern(self):
        readme = open(os.path.join(KIT, "README.md"), encoding="utf-8").read()
        self.assertIn("short-form or Westlaw cite, Id.", readme)
