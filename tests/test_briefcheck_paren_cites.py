"""A cite in a parenthetical after a statute: "Miss. Code Ann. § 21-23-9 (550 U.S. 544)".

The extractor keeps a trailing parenthetical of up to 24 characters in a statute fragment, and the
statute claimed that whole span. A case or WL cite inside it had already been claimed, so the
statute was dropped and never checked; a statute cite inside it was hidden by the outer statute.
The same held for each member of "Section 21-23-7, 21-23-9 (…)". Either way brief check exited 0
on an unlogged cite. A statute, a "Section" head and each member now claim their number plus short
subsection marks only ("(a)", "(2)(ii)", "(2018)": up to 4 letters or digits), as a "§§" list
section does; the parenthetical is scanned on its own. Keys are unchanged.

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

SEC7, SEC9 = "Miss. Code Ann. § 21-23-7", "Miss. Code Ann. § 21-23-9"
CASE = "Bell Atl. Corp. v. Twombly, 550 U.S. 544 (2007)"


def cites(text):
    """(key, the text the cite claims) for every authority cite, in scan order."""
    return [(k, text[a:b]) for k, _, _, a, b, *_ in briefcheck.scan_authorities(text)]


def statutes(text):
    return [(k, t) for k, t in cites(text) if k.startswith("statute")]


class TestParentheticalCiteAfterAStatute(unittest.TestCase):
    def test_statute_before_a_parenthetical_case_is_scanned(self):
        text = "The board must act. Miss. Code Ann. § 21-23-9 (550 U.S. 544)."
        self.assertEqual(statutes(text), [("statute:misscode21-23-9", "Miss. Code Ann. § 21-23-9")])
        self.assertIn(("case:550 us 544", "550 U.S. 544"), cites(text))

    def test_section_before_a_parenthetical_case_is_scanned(self):
        self.assertEqual(statutes("The board must act. Section 21-23-9 (550 U.S. 544)."),
                         [("statute-short:section21-23-9", "Section 21-23-9")])

    def test_statute_in_a_parenthetical_is_its_own_cite(self):
        for paren in ("(42 U.S.C. § 1983)", "(citing 42 U.S.C. § 1983)"):
            self.assertEqual(statutes(f"The board must act. Miss. Code Ann. § 21-23-9 {paren}."),
                             [("statute:misscode21-23-9", "Miss. Code Ann. § 21-23-9"),
                              ("statute:42usc1983", "42 U.S.C. § 1983")], paren)

    def test_member_before_a_parenthetical_cite_is_scanned(self):
        for paren in ("(550 U.S. 544)", "(2019 WL 1234567)", "(42 U.S.C. § 1983)"):
            got = statutes(f"The board must act. Section 21-23-7, 21-23-9 {paren}.")
            self.assertEqual(got[:2], [("statute-short:section21-23-7", "Section 21-23-7"),
                                       ("statute-short:section21-23-9", "21-23-9")], paren)

    def test_or_form_before_a_parenthetical_case_keeps_both_members(self):
        self.assertEqual(statutes("The board must act. Section 21-23-7 or 21-23-9 (550 U.S. 544)."),
                         [("statute-short:section21-23-7", "Section 21-23-7"),
                          ("statute-short:section21-23-9", "21-23-9")])

    def test_lowercase_or_form_before_a_parenthetical_case_keeps_both_members(self):
        # for lowercase "sections", the extractor also gives nested member rows, "503 (550 U.S. 544)" among them
        self.assertEqual(statutes("The agency must act under sections 502 or 503 (550 U.S. 544)."),
                         [("statute-short:sections502", "sections 502"), ("statute-short:section503", "503")])

    def test_member_does_not_claim_a_parenthetical_statute(self):
        self.assertEqual(statutes("The board must act. Sections 21-23-7 and 21-23-9 (see 42 U.S.C. § 1983)."),
                         [("statute-short:sections21-23-7", "Sections 21-23-7"),
                          ("statute-short:section21-23-9", "21-23-9"),
                          ("statute:42usc1983", "42 U.S.C. § 1983")])

    def test_parenthetical_that_is_not_a_cite_is_not_claimed(self):
        self.assertEqual(statutes("The board must act. Section 21-23-7, 21-23-9 (Rev. 2019)."),
                         [("statute-short:section21-23-7", "Section 21-23-7"),
                          ("statute-short:section21-23-9", "21-23-9")])
        self.assertEqual(statutes("The board must act. Miss. Code Ann. § 21-23-9 (Supp. 2019)."),
                         [("statute:misscode21-23-9", "Miss. Code Ann. § 21-23-9")])


class TestCrossReferenceWalksPastParentheticals(unittest.TestCase):
    """SWE-2 round 1: a "Section X, Y or Z" list walks past any parenthetical, as a "§§" list does; each
    member claims its number and marks only, and the parenthetical is scanned on its own."""

    def test_or_form_with_a_parenthetical_after_the_head(self):
        self.assertEqual(statutes("The board must act under Section 21-23-7 (Rev. 2020) or 21-23-9."),
                         [("statute-short:section21-23-7", "Section 21-23-7"), ("statute-short:section21-23-9", "21-23-9")])

    def test_lowercase_and_form_with_a_parenthetical_after_the_head(self):
        self.assertEqual(statutes("The agency must act under sections 502 (West 2019) and 503."),
                         [("statute-short:sections502", "sections 502"), ("statute-short:section503", "503")])

    def test_parenthetical_mid_chain(self):
        self.assertEqual(statutes("The board must act under Section 7 or 8 (Supp. 2019) or 9."),
                         [("statute-short:section7", "Section 7"), ("statute-short:section8", "8"),
                          ("statute-short:section9", "9")])

    def test_comma_list_runs_past_a_parenthetical(self):
        self.assertEqual(statutes("The board must act under Section 21-23-7, 21-23-9 (Rev. 2020), 21-23-10."),
                         [("statute-short:section21-23-7", "Section 21-23-7"), ("statute-short:section21-23-9", "21-23-9"),
                          ("statute-short:section21-23-10", "21-23-10")])

    def test_list_runs_past_a_parenthetical_cite(self):
        for text, other in (
                ("Section 21-23-7 (550 U.S. 544), 21-23-9.", ("case:550 us 544", "550 U.S. 544")),
                ("Section 21-23-7 (550 U.S. 544) or 21-23-9.", ("case:550 us 544", "550 U.S. 544")),
                ("Section 21-23-7 (42 U.S.C. § 1983) or 21-23-9.", ("statute:42usc1983", "42 U.S.C. § 1983")),
                ("sections 21-23-7 (42 U.S.C. § 1983) or 21-23-9.", ("statute:42usc1983", "42 U.S.C. § 1983"))):
            got = cites(f"The board must act under {text}")
            head = "sections" if text.startswith("sections") else "section"
            for want in ((f"statute-short:{head}21-23-7", text.split(" (")[0]),
                         ("statute-short:section21-23-9", "21-23-9"), other):
                self.assertIn(want, got, text)

    def test_pin_page_after_a_parenthetical_case_reads_as_a_member(self):
        # the cost of walking past a cite: in a chain of plain numbers a pin page becomes a member, which BLOCKs
        self.assertEqual(statutes("The claim lies under Section 1983 (550 U.S. 544), 555."),
                         [("statute-short:section1983", "Section 1983"), ("statute-short:section555", "555")])


class TestParentheticalShapes(unittest.TestCase):
    def test_open_paren_inside_a_parenthetical_is_cut(self):
        self.assertEqual(statutes("The board must act. Miss. Code Ann. § 21-23-9 (550 U.S. 544 (2007)."),
                         [("statute:misscode21-23-9", "Miss. Code Ann. § 21-23-9")])

    def test_footnote_parenthetical_is_cut(self):
        self.assertEqual(statutes("The board must act. Miss. Code Ann. § 21-23-9 (n.3)."),
                         [("statute:misscode21-23-9", "Miss. Code Ann. § 21-23-9")])

    def test_rule_in_a_parenthetical_is_its_own_cite(self):
        got = cites("The board must act. Section 21-23-9 (Fed. R. Civ. P. 15).")
        self.assertIn(("statute-short:section21-23-9", "Section 21-23-9"), got)
        self.assertIn(("rule:fedrcivp15", "Fed. R. Civ. P. 15"), got)

    def test_long_or_late_marks_are_cut_but_keys_do_not_change(self):
        for text in ("The claim lies. 42 U.S.C. § 1983(xviii).", "The claim lies. 42 U.S.C. § 1983 (Rev. 2019) (a)."):
            self.assertEqual(statutes(text), [("statute:42usc1983", "42 U.S.C. § 1983")], text)


class TestListStopsAfterParentheticals(Base):
    """With fix/brief-list-stops: a list that ends at a number it does not read as a member WARNs once."""

    def store(self, *cites):
        self.m.conn.execute("DELETE FROM authorities")
        for c in cites:
            self.m.authority(c, "source-checked", via="https://example.org/x", evidence="opened")
        self.m.conn.commit()

    def stops(self, draft):
        return [f.cite for f in self.m.check(draft).findings if f.code == "list-member-unchecked"]

    def test_stop_after_a_parenthetical_warns(self):
        self.store(SEC7)
        self.assertEqual(self.stops("The board must act under Section 21-23-7 (Rev. 2020), 11-46."), ["11-46"])

    def test_stop_after_lowercase_members_warns_once(self):
        # the extractor's nested rows of "sections 502 or 503" reach the same stop again
        self.store("5 U.S.C. § 502", "5 U.S.C. § 503")
        self.assertEqual(self.stops("The agency must act under sections 502 or 503, 552-553 pages."), ["552-553"])

    def test_pin_page_after_a_parenthetical_case_on_a_hyphenated_head_warns(self):
        self.store(SEC7, CASE)
        r = self.m.check("The board must act under Section 21-23-7 (550 U.S. 544), 555.")
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertEqual([f.cite for f in r.findings if f.code == "list-member-unchecked"], ["555"])


class TestSubsectionMarksStay(unittest.TestCase):
    """Controls: short marks stay with the cite, and keys do not change."""

    def test_subsection_marks_stay_with_a_statute(self):
        self.assertEqual(statutes("The claim lies. 42 U.S.C. § 1983(a)(1)."),
                         [("statute:42usc1983", "42 U.S.C. § 1983(a)(1)")])
        self.assertEqual(statutes("The claim lies. 42 U.S.C. § 1983 (2018)."),
                         [("statute:42usc1983", "42 U.S.C. § 1983 (2018)")])

    def test_subsection_marks_stay_with_a_member(self):
        self.assertEqual(statutes("The board must act. Section 21-23-7(b), 21-23-9(a)."),
                         [("statute-short:section21-23-7", "Section 21-23-7(b)"),
                          ("statute-short:section21-23-9", "21-23-9(a)")])

    def test_stored_keys_ignore_a_parenthetical(self):
        for cite in ("Miss. Code Ann. § 21-23-9 (Supp. 2019)", "Miss. Code Ann. § 21-23-9"):
            self.assertEqual({k for k in briefcheck.authority_keys(cite) if k.startswith("statute")},
                             {"statute:misscode21-23-9"}, cite)


class TestParentheticalCitesInBriefCheck(Base):
    def store(self, *cites):
        self.m.conn.execute("DELETE FROM authorities")
        for c in cites:
            self.m.authority(c, "source-checked", via="https://example.org/x", evidence="opened")
        self.m.conn.commit()

    def blocks(self, draft):
        r = self.m.check(draft)
        return [(f.code, f.cite) for f in r.findings if f.severity == "BLOCK"]

    def test_unlogged_statute_before_a_parenthetical_case_blocks(self):
        self.store(CASE)
        self.assertEqual(self.blocks(f"The board must act. {SEC9} (550 U.S. 544)."),
                         [("authority-not-logged", SEC9)])

    def test_unlogged_member_before_a_parenthetical_case_blocks(self):
        self.store(SEC7, CASE)
        self.assertEqual(self.blocks("The board must act. Section 21-23-7, 21-23-9 (550 U.S. 544)."),
                         [("authority-not-logged", "Section 21-23-9")])
        self.assertEqual(self.blocks("The board must act. Section 21-23-7 or 21-23-9 (550 U.S. 544)."),
                         [("authority-not-logged", "Section 21-23-9")])

    def test_unlogged_statute_in_a_parenthetical_blocks(self):
        self.store(SEC9)
        self.assertEqual(self.blocks(f"The board must act. {SEC9} (citing 42 U.S.C. § 1983)."),
                         [("authority-not-logged", "42 U.S.C. § 1983")])
        self.store(SEC7, SEC9)
        self.assertEqual(self.blocks("The board must act. Sections 21-23-7 and 21-23-9 (see 42 U.S.C. § 1983)."),
                         [("authority-not-logged", "42 U.S.C. § 1983")])

    def test_logged_or_form_with_a_parenthetical_passes(self):
        self.store(SEC7, SEC9)
        self.assertEqual(self.blocks("The board must act under Section 21-23-7 (Rev. 2020) or 21-23-9. "
                                     "The rule is § 21-23-7."), [])

    def test_unlogged_member_after_a_parenthetical_blocks(self):
        self.store(SEC7, SEC9)
        self.assertEqual(self.blocks("The board must act under Section 21-23-7, 21-23-9 (Rev. 2020), 21-23-10."),
                         [("authority-not-logged", "Section 21-23-10")])
        self.store(SEC7, CASE)
        self.assertEqual(self.blocks("The board must act under Section 21-23-7 (550 U.S. 544), 21-23-9."),
                         [("authority-not-logged", "Section 21-23-9")])

    def test_unlogged_statute_in_a_mid_chain_parenthetical_blocks(self):
        self.store(SEC7, SEC9)
        self.assertEqual(self.blocks("The board must act under Section 21-23-7 (42 U.S.C. § 1983) or 21-23-9."),
                         [("authority-not-logged", "42 U.S.C. § 1983")])

    def test_id_after_a_parenthetical_cite_warns(self):
        # "Id." binds by position, so after "§ 21-23-9 (citing 42 U.S.C. § 1983)" it is ambiguous and says so
        self.store(SEC9, "42 U.S.C. § 1983")
        r = self.m.check(f"The board must act. {SEC9} (citing 42 U.S.C. § 1983). The clerk files it. Id.")
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertIn("id-ambiguous", codes(r, "WARN"))

    def test_logged_cites_with_a_parenthetical_pass(self):
        self.store(SEC7, SEC9, CASE, "42 U.S.C. § 1983")
        r = self.m.check(f"The board must act. {SEC9} (550 U.S. 544). Section 21-23-7, 21-23-9 (42 U.S.C. § 1983).")
        self.assertEqual(codes(r, "BLOCK"), [])


if __name__ == "__main__":
    unittest.main()
