"""Headings: a sentence that opens with an initial is not an outline heading, and a heading's
own cites and quotations are checked.

Any one-line block matching "A. …", "II. …" or "1. …" was a heading, and headings were skipped
whole, so "J. Smith testified that the clerk refused. [Doc. 1]" and "W. Va. Code § 21-23-7
controls." passed with no row and exit 0. A single-letter marker now makes a heading only when
the rest of the line reads as a title (short and unpunctuated, or Title Case) and carries no
citation. Headings still open and close fact sections, but their record cites, authorities,
quotations and fact tags are checked like any sentence; only uncited-fact is not asked of them.

Ways this could fail, each pinned below:
- an initial or a one-letter abbreviation ("J.", "W. Va.") reads as an outline letter;
- a single letter that is also a Roman numeral ("C.") reads as a level-1 heading;
- a misread "heading" closes a fact section, so later uncited facts pass;
- a real heading's cite or quotation is skipped, and an Id. after it is left unbound;
- real headings ("A. Background", "II. ARGUMENT", "1. Introduction", "## Facts") stop
  structuring fact sections, or are themselves flagged uncited-fact.

Synthetic data only. Run: python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from matterkit import briefcheck  # noqa: E402
from test_briefcheck import Base, codes  # noqa: E402


def found(report, code):
    return [f.cite for f in report.findings if f.code == code]


class TestHeadingClassifier(unittest.TestCase):
    def test_sentences_opening_with_an_initial_are_not_headings(self):
        for line in ["J. Smith testified that the clerk refused the request.",
                     "J. Smith testified that the clerk refused. [Doc. 1]",
                     "J. R. Smith testified at the hearing.",
                     "W. Va. Code § 21-23-7 controls.",
                     "W. Va. law governs the request",
                     "C. Smith v. Jones controls.",
                     "A. Plaintiff's claim is barred by the statute of limitations.",
                     "B. Rule 15(a)(2) permits amendment here.",
                     "I. The Court may set a limited deadline consistent with Rule 15(a)(2)."]:
            with self.subTest(line=line):
                self.assertIsNone(briefcheck._heading(line))

    def test_outline_headings_are_still_headings(self):
        for line, level in [("A. Background", 2),
                            ("B. The Clerk Refused the Request", 2),
                            ("D. Statement of the Case.", 2),
                            ("E. Facts relevant to the motion", 2),
                            ("F. Dr. Doe's Report", 2),
                            ("I. Introduction", 1),
                            ("II. ARGUMENT", 1),
                            ("II. The Court may set a limited deadline consistent with Rule 15(a)(2).", 1),
                            ("1. Introduction", 3),
                            ("1. Background", 3),
                            ("## Facts", 2),
                            ("STATEMENT OF FACTS", 1)]:
            with self.subTest(line=line):
                self.assertEqual(briefcheck._heading(line), level)

    def test_numbered_paragraphs_are_unchanged(self):
        self.assertIsNone(briefcheck._heading("1. On January 1, officers arrested Plaintiff at home."))
        self.assertIsNone(briefcheck._heading("1. The complaint was filed in July. Doc. 1."))


class TestSentencesOpeningWithAnInitialAreChecked(Base):
    def test_statute_sentence_is_checked(self):
        r = self.m.check("W. Va. Code § 21-23-7 controls.")
        self.assertEqual(found(r, "authority-not-logged"), ["W. Va. Code § 21-23-7"])
        self.assertEqual(r.exit_code, 1)

    def test_logged_statute_sentence_passes(self):
        self.m.authority("W. Va. Code § 21-23-7", "source-checked", via="https://example.org/wvc",
                         evidence="opened official text")
        self.m.conn.commit()
        r = self.m.check("W. Va. Code § 21-23-7 controls.")
        self.assertEqual([f.code for f in r.findings], [])
        self.assertEqual(r.exit_code, 0)

    def test_record_cite_after_an_initial_is_checked(self):
        r = self.m.check("J. Smith testified that the clerk refused. [Doc. 1]")
        self.assertEqual(found(r, "record-not-in-store"), ["Doc. 1"])
        self.assertEqual(r.exit_code, 1)

    def test_case_cite_after_a_roman_letter_is_checked(self):
        r = self.m.check("C. Smith v. Jones, 123 F.3d 456 (5th Cir. 1999), controls.")
        self.assertIn("authority-not-logged", codes(r, "BLOCK"))

    def test_uncited_fact_after_an_initial_is_flagged(self):
        r = self.m.check("STATEMENT OF FACTS\n\nJ. Smith testified that the clerk refused the request.\n")
        self.assertEqual(found(r, "uncited-fact"), ["J. Smith testified that the clerk refused the request."])

    def test_initial_sentence_does_not_close_the_fact_section(self):
        r = self.m.check("A. Background\n\nJ. Smith testified at the hearing. Doc. 16.\n\n"
                         "The clerk refused the request on a holiday.\n")
        self.assertEqual(found(r, "uncited-fact"), ["The clerk refused the request on a holiday."])


class TestHeadingContentsAreChecked(Base):
    def test_record_cite_in_markdown_heading_is_checked(self):
        r = self.m.check("## The Clerk's Refusal [Doc. 1]\n\nThe motion should be denied.\n")
        self.assertEqual(found(r, "record-not-in-store"), ["Doc. 1"])

    def test_authority_in_roman_heading_is_checked(self):
        r = self.m.check("II. Rule 15 Does Not Displace W. Va. Code § 21-23-7\n\nThe motion should be denied.\n")
        self.assertEqual(found(r, "authority-not-logged"), ["W. Va. Code § 21-23-7"])

    def test_quotation_in_heading_is_checked(self):
        r = self.m.check('## Defendants Said "bail was always granted" Doc. 16\n\nThe motion should be denied.\n')
        self.assertEqual(found(r, "quote-not-found"), ["bail was always granted"])

    def test_id_after_heading_cite_binds_to_it(self):
        r = self.m.check("## Dietz v. Bouldin, 579 U.S. 40 (2016)\n\nThe court has inherent power. Id. at 45.\n")
        self.assertEqual(found(r, "id-unbound"), [])
        self.assertEqual(r.exit_code, 0)

    def test_headings_are_not_flagged_as_uncited_facts(self):
        r = self.m.check("STATEMENT OF FACTS\n\nA. The Arrest at Home\n\n"
                         "Officers arrested Plaintiff at home on a holiday.\n")
        self.assertEqual(found(r, "uncited-fact"), ["Officers arrested Plaintiff at home on a holiday."])

    def test_headings_still_close_fact_sections(self):
        for close in ["II. ARGUMENT", "II. The Court should deny the motion.", "ARGUMENT", "# Argument"]:
            with self.subTest(close=close):
                r = self.m.check("I. FACTS\n\nThe arrest happened at home on a holiday. Doc. 16.\n\n"
                                 f"{close}\n\nThe motion should be denied for many reasons.\n")
                self.assertEqual(found(r, "uncited-fact"), [])

    def test_lettered_title_heading_still_opens_a_fact_section(self):
        r = self.m.check("A. Background\n\nThe arrest happened at home on a holiday.\n\n"
                         "B. Argument\n\nThe motion should be denied for many reasons.\n")
        self.assertEqual(found(r, "uncited-fact"), ["The arrest happened at home on a holiday."])


class TestSWE2RoundOne(Base):
    """SWE-2's round-1 review (docs/superpowers/reviews/2026-10-23-brief-heading-sentences-code/).

    A real fact heading misread as body text is the worst miss: the section never opens and
    every uncited fact in it passes. So a short lettered line that names a fact section is a
    heading even with a period or a cite; a misread there can only open a section, never close one.
    """

    def test_title_headings_that_e50de74_lost(self):
        for line, level in [("A. Background.", 2),                     # P1-1: one dotted title word
                            ("D. Conclusion.", 2),
                            ("A. Factual background.", 2),             # sentence-case fact headings
                            ("B. Procedural history.", 2),
                            ("D. Statement of facts.", 2),
                            ("A. Background Doc. 16", 2),              # a fact heading carrying a cite
                            ("B. Smith v. Jones Controls The Outcome.", 2),  # P2-1: "v." is a minor word
                            ("B. In re Smith Does Not Control the Outcome Here.", 2),
                            ("A. Hon. Lee's Evidentiary Rulings", 2),  # P3-4: honorifics
                            ("B. (Optional) The Standard For Review Of Denial Motions", 2)]:  # P3-3
            with self.subTest(line=line):
                self.assertEqual(briefcheck._heading(line), level)

    def test_known_limits_are_pinned(self):
        # accepted corners (handoff C4 and the fact-heading rule): short lines read as headings
        self.assertEqual(briefcheck._heading("J. Smith testified"), 2)
        self.assertEqual(briefcheck._heading("J. Smith described the facts."), 2)
        # a longer sentence that only mentions a fact word is still body text
        self.assertIsNone(briefcheck._heading("J. Smith testified about the background of the arrest at length."))
        self.assertIsNone(briefcheck._heading("J. R. Smith described the facts."))

    def test_dotted_fact_heading_opens_its_section(self):
        r = self.m.check("A. Background.\n\nThe arrest happened at home on a holiday.\nThe clerk refused the request.\n")
        self.assertEqual(found(r, "uncited-fact"),
                         ["The arrest happened at home on a holiday.", "The clerk refused the request."])

    def test_sentence_case_fact_heading_opens_its_section(self):
        r = self.m.check("A. Factual background.\n\nThe arrest happened at home on a holiday.\n")
        self.assertEqual(found(r, "uncited-fact"), ["The arrest happened at home on a holiday."])

    def test_cited_fact_heading_opens_its_section_and_its_cite_is_checked(self):
        r = self.m.check("A. Background Doc. 1\n\nThe arrest happened at home on a holiday.\n")
        self.assertEqual(found(r, "uncited-fact"), ["The arrest happened at home on a holiday."])
        self.assertEqual(found(r, "record-not-in-store"), ["Doc. 1"])

    def test_case_name_heading_closes_the_fact_section(self):
        r = self.m.check("A. Background\n\nThe arrest happened at home. Doc. 16.\n\n"
                         "B. Smith v. Jones Controls The Outcome.\n\nThe motion should be denied for many reasons.\n")
        self.assertEqual(found(r, "uncited-fact"), [])

    def test_honorific_heading_closes_the_fact_section(self):
        r = self.m.check("A. Background\n\nThe arrest happened at home. Doc. 16.\n\n"
                         "B. Hon. Lee's Evidentiary Rulings\n\nThe motion should be denied for many reasons.\n")
        self.assertEqual(found(r, "uncited-fact"), [])

    def test_id_after_a_heading_cite_binds_to_the_heading_cite(self):
        # decided (P3-1): Id. takes the nearest preceding cite, a heading's included; a wrong guess BLOCKs
        r = self.m.check("The court has inherent power. Dietz v. Bouldin, 579 U.S. 40, 45 (2016).\n\n"
                         "## Procedural History Doc. 16\n\nThe record was silent. Id. at 45.\n")
        self.assertEqual(found(r, "pin-out-of-range"), ["Id. at 45"])
        self.assertEqual(r.exit_code, 1)

    def test_uncited_quotation_in_a_heading_is_flagged(self):
        # decided (P3-2): a heading's quotation needs a source like any other
        r = self.m.check('## The "File Is Closed" Call\n\nThe clerk closed the file. Doc. 16.\n')
        self.assertEqual(found(r, "quote-uncited"), ["File Is Closed"])


class TestSWE2RoundTwo(Base):
    """SWE-2's round-2 review (docs/superpowers/reviews/2026-10-23-brief-heading-sentences-code-r2/).

    A fact heading never narrows a fact section that is already open: the section keeps its
    outer level, so only a heading at that level or above closes it. A lone dotted fragment
    ("W. Va.", "A. Doc.") is not a title. Known limit, pinned: a short lettered line that names a
    fact section is a heading even when it goes on as a sentence ("A. Facts: the clerk lied.").
    A colon rule would make "A. Facts: Doc. 16 at 2." lose its section, which is worse.
    """

    def test_misread_fact_line_does_not_narrow_an_open_fact_section(self):
        r = self.m.check("I. FACTS\n\nJ. Smith described the facts.\n\nA. The Arrest\n\n"
                         "The officer arrived late on a holiday.\n")
        self.assertEqual(found(r, "uncited-fact"), ["The officer arrived late on a holiday."])

    def test_nested_fact_subsections_stay_in_the_fact_section(self):
        r = self.m.check("I. FACTS\n\nA. Background\n\nThe officer arrived late on a holiday.\n\n"
                         "B. The Arrest\n\nThe clerk refused the request on a holiday.\n\n"
                         "II. ARGUMENT\n\nThe motion should be denied for many reasons.\n")
        self.assertEqual(found(r, "uncited-fact"), ["The officer arrived late on a holiday.",
                                                    "The clerk refused the request on a holiday."])

    def test_known_limit_argument_nested_under_facts_is_flagged(self):
        # the cost of never narrowing: an argument filed under a facts heading reads as facts (loud)
        r = self.m.check("I. FACTS\n\nA. Background\n\nThe officer arrived late on a holiday.\n\n"
                         "B. Argument\n\nThe motion should be denied for many reasons.\n")
        self.assertEqual(found(r, "uncited-fact"), ["The officer arrived late on a holiday.",
                                                    "The motion should be denied for many reasons."])

    def test_lone_dotted_fragments_are_not_titles(self):
        for line in ["W. Va.", "A. Doc.", "A. Exh."]:
            with self.subTest(line=line):
                self.assertIsNone(briefcheck._heading(line))
        for line in ["A. Background.", "B. Facts.", "D. Torts."]:
            with self.subTest(line=line):
                self.assertEqual(briefcheck._heading(line), 2)

    def test_lone_dotted_fragment_does_not_close_a_fact_section(self):
        r = self.m.check("A. Background\n\nThe arrest happened at home. Doc. 16.\n\nW. Va.\n\n"
                         "The clerk refused the request on a holiday.\n")
        self.assertEqual(found(r, "uncited-fact"), ["The clerk refused the request on a holiday."])

    def test_known_limit_fact_line_that_goes_on_as_a_sentence(self):
        self.assertEqual(briefcheck._heading("A. Facts: the clerk lied."), 2)

    def test_known_limit_one_letter_point_sentence_does_not_end_the_facts(self):
        # "I." may be an initial, so "I. <sentence with a cite>" is text and the facts stay open (loud);
        # "II. …" ends them (tests/test_briefcheck_complaint_mode.py::test_p3_roman_heading_with_rule_stays_a_heading)
        r = self.m.check("STATEMENT OF FACTS\n\nThe arrest happened at home on a holiday.\n\n"
                         "I. Leave is proper under Rule 15(a)(2).\n\n"
                         "The motion should be denied because the claims are plausible.\n")
        self.assertEqual(found(r, "uncited-fact"), ["The arrest happened at home on a holiday.",
                                                    "The motion should be denied because the claims are plausible."])


if __name__ == "__main__":
    unittest.main()
