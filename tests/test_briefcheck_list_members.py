"""A "§§" list is read one member at a time, so a member's parenthetical never splits the list.

The list code split the matched "§§" list on its separators (", ", " and ", " or ") anywhere,
including inside a member's parenthetical. "Miss. Code Ann. §§ 21-3-15 (2 and ), 21-4" split inside
"(2 and )", left the piece ")" and crashed `brief check` (AttributeError, no report). A stored citation
of that shape crashed every check of the matter. "(2 and 3)" made a phantom member "§ 3" that BLOCKed
although nothing cites it.

A list that names subsections of one section before the next section ("§§ 21-3-15 (a) and (b), 21-4")
stopped at "(a)", because "(b)" does not start with a digit, so "21-4" was never checked: a false pass
when it is not logged. A bare subsection now continues the section before it, and the list goes on.

Synthetic data only. Run: python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from matterkit import authority, briefcheck  # noqa: E402
from test_briefcheck import Base, codes  # noqa: E402

CODE = "Miss. Code Ann. §§ "
A, B = "statute:misscode21-3-15", "statute:misscode21-4"  # keys drop "Ann." (fix/brief-false-blocks)


def statute_keys(text):
    return [k for k, *_, kind, _ in briefcheck.scan_authorities(text) if kind == "statute"]


class TestParentheticalInsideAListMember(unittest.TestCase):
    def test_a_connector_inside_a_parenthetical_does_not_split_the_list(self):
        for secs in ["21-3-15 (2 and ), 21-4", "21-3-15 (2 and\n), 21-4", "21-3-15 (2 or ), 21-4",
                     "21-3-15 (see 2, 3), 21-4"]:
            with self.subTest(secs=secs):
                self.assertEqual(statute_keys(CODE + secs), [A, B])

    def test_an_empty_piece_inside_a_parenthetical_does_not_crash(self):
        self.assertEqual(statute_keys(CODE + "21-3-15 (2 and , 3)"), [A])

    def test_a_parenthetical_with_a_connector_makes_no_phantom_member(self):
        self.assertEqual(statute_keys(CODE + "21-3-15 (2 and 3)"), [A])

    def test_each_member_keeps_its_span_and_display(self):
        text = CODE + "21-3-15 (2 and ), 21-4"
        found = [(d, text[a:b]) for _, d, _, a, b, kind, _ in briefcheck.scan_authorities(text) if kind == "statute"]
        self.assertEqual(found, [("Miss. Code Ann. § 21-3-15", "Miss. Code Ann. §§ 21-3-15"),
                                 ("Miss. Code Ann. § 21-4", "21-4")])


class TestSubsectionMembers(unittest.TestCase):
    def test_a_bare_subsection_continues_the_section_before_it(self):
        for secs in ["21-3-15 (a) and (b), 21-4", "21-3-15(a) and (b), 21-4", "21-3-15(a), (b), and (c), 21-4",
                     "21-3-15(a)(1) & (b)(2), 21-4"]:
            with self.subTest(secs=secs):
                self.assertEqual(statute_keys(CODE + secs), [A, B])

    def test_subsections_alone_add_no_member(self):
        self.assertEqual(statute_keys(CODE + "21-3-15(a) and (b)"), [A])

    def test_a_long_parenthetical_after_a_connector_is_not_a_subsection(self):
        self.assertEqual(statute_keys(CODE + "21-3-15 and (as amended) 21-4"), [A])

    def test_plain_lists_are_unchanged(self):
        self.assertEqual(statute_keys(CODE + "21-3-15, 21-4"), [A, B])
        self.assertEqual(statute_keys(CODE + "21-3-15 and 21-4"), [A, B])
        self.assertEqual(statute_keys(CODE + "21-3-15(2)(ii), 21-4"), [A, B])


DRAFT = "The clerk must act. {} Doc. 16."


class TestListMembersInCheck(Base):
    def log(self, cite):
        self.m.authority(cite, "source-checked", via="https://example.org/code", evidence="opened")
        self.m.conn.commit()

    def test_draft_list_with_a_connector_in_a_parenthetical_is_checked(self):
        self.log("Miss. Code Ann. § 21-3-15")
        r = self.m.check(DRAFT.format(CODE + "21-3-15 (2 and ), 21-4."))
        self.assertIn("Miss. Code Ann. § 21-4", [f.cite for f in r.findings if f.code == "authority-not-logged"])

    def test_no_phantom_block_for_a_connector_in_a_parenthetical(self):
        self.log("Miss. Code Ann. § 21-3-15")
        r = self.m.check(DRAFT.format(CODE + "21-3-15 (2 and 3)."))
        self.assertEqual(codes(r, "BLOCK"), [])

    def test_section_after_subsections_blocks_when_unlogged(self):
        self.log("Miss. Code Ann. § 21-3-15")
        r = self.m.check(DRAFT.format(CODE + "21-3-15 (a) and (b), 21-4."))
        self.assertIn("Miss. Code Ann. § 21-4", [f.cite for f in r.findings if f.code == "authority-not-logged"])
        self.assertEqual(r.exit_code, 1)

    def test_stored_list_with_a_connector_in_a_parenthetical_does_not_stop_checks(self):
        stored = CODE + "21-3-15 (2 and ), 21-4"
        self.log(stored)
        r = self.m.check(DRAFT.format("Miss. Code Ann. § 21-4."))
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertEqual(authority.find(self.m.conn, "Miss. Code Ann. § 21-4")["citation"], stored)


if __name__ == "__main__":
    unittest.main()
