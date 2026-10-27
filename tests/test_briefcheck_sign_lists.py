"""A "§"-led comma list is read member by member, like a "Section X, Y" list.

"42 U.S.C. § 1983, 1985 also apply." and "Miss. Code Ann. § 21-23-7, 21-23-9 govern." were one
cite: the extractor stops at the comma, and only a "Section"-worded head was walked, so the rest of
the list was never checked and an unlogged § 1985 exited 0 (SWE-2, review of fix/brief-list-stops,
P2). Now a fragment ending in "§ N" is walked with the same member rule and the same
`list-member-unchecked` WARN as a "Section" list. Each member carries the head's code, so "1985"
after "42 U.S.C. § 1983," is the full cite "42 U.S.C. § 1985", as a "§§" list member is.

After the extracted fragment, where a number may be prose rather than a list, a plain number must
also be within one digit of the head's length. "§ 1983, 1985" and "Sections 9, 10" are lists;
"Under § 1983, 12 plaintiffs sued" and "Section 552, 5 records" are a stop (WARN), not an invented
cite that BLOCKs. Brief check cannot dismiss a finding, so a guess must not BLOCK.

How this could fail, and the test that catches it:
  1. the tail of a "§" list is still dropped            TestSignListMembers
  2. a member is keyed without the head's code          test_member_carries_the_code
  3. a count after a "§" or "Section" head BLOCKs       TestCountsAfterAHead
  4. another cite after the comma is taken as a member  TestAnotherCiteFollows
  5. stored citations miss a listed section             TestStoredSignLists
  6. "Section" lists and "§§" lists change otherwise    the existing suites

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


def cites(report):
    return [(c.raw, c.target) for row in report.rows for c in row.cites if c.kind == "authority"]


def unlogged(report):
    return [f.cite for f in report.findings if f.code == "authority-not-logged"]


def stops(report):
    return [f.cite for f in report.findings if f.code == "list-member-unchecked"]


class SignBase(Base):
    """The fixture logs 42 U.S.C. § 1983; these add the Mississippi sections and § 1988."""
    def setUp(self):
        super().setUp()
        for cite in ("Miss. Code Ann. § 21-23-7", "Miss. Code Ann. § 21-23-8", "42 U.S.C. § 1988"):
            self.m.authority(cite, "source-checked", via="https://example.org/official", evidence="opened")
        self.m.conn.commit()


class TestSignListMembers(SignBase):
    def test_comma_member_is_checked(self):
        r = self.m.check("42 U.S.C. § 1983, 1985 also apply.")
        self.assertEqual(unlogged(r), ["42 U.S.C. § 1985"])
        self.assertEqual(r.exit_code, 1)

    def test_and_or_and_serial_members_are_checked(self):
        for draft, missing in (("42 U.S.C. § 1983 and 1985 apply.", ["42 U.S.C. § 1985"]),
                               ("42 U.S.C. § 1983 or 1985 applies.", ["42 U.S.C. § 1985"]),
                               ("42 U.S.C. § 1983, 1985, and 1986 apply.", ["42 U.S.C. § 1985", "42 U.S.C. § 1986"])):
            self.assertEqual(unlogged(self.m.check(draft)), missing, draft)

    def test_all_members_logged_pass(self):
        r = self.m.check("42 U.S.C. § 1983 and 1988 apply.")
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertIn(("42 U.S.C. § 1988", "statute:42usc1988"), cites(r))

    def test_member_carries_the_code(self):
        r = self.m.check("Miss. Code Ann. § 21-23-7, 21-23-8 govern.")
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertIn(("Miss. Code Ann. § 21-23-8", "statute:misscode21-23-8"), cites(r))
        r = self.m.check("Miss. Code Ann. § 21-23-7, 21-23-9 govern.")
        self.assertEqual(unlogged(r), ["Miss. Code Ann. § 21-23-9"])

    def test_short_form_head(self):
        r = self.m.check("Under § 1983, 1988 also applies.")
        self.assertIn(("§ 1988", "statute:42usc1988"), cites(r))
        r = self.m.check("Under § 1983, 1985 also applies.")
        self.assertEqual(unlogged(r), ["§ 1985"])

    def test_subsection_marks(self):
        r = self.m.check("42 U.S.C. § 1983(a), 1985(3) apply.")
        self.assertEqual(unlogged(r), ["42 U.S.C. § 1985(3)"])

    def test_member_of_another_title_is_a_stop(self):
        r = self.m.check("Miss. Code Ann. § 21-23-7, 11-46 governs the time.")
        self.assertEqual(stops(r), ["11-46"])
        self.assertEqual(r.exit_code, 0)


class TestCountsAfterAHead(SignBase):
    def test_count_after_a_sign_head(self):
        r = self.m.check("Under 42 U.S.C. § 1983, 12 plaintiffs sued.")
        self.assertEqual(cites(r), [("42 U.S.C. § 1983", "statute:42usc1983")])
        self.assertEqual(stops(r), ["12"])
        self.assertEqual(codes(r, "BLOCK"), [])

    def test_count_after_a_section_head(self):
        r = self.m.check("Under Section 552, 5 records were released.")
        self.assertNotIn("Section 5", [raw for raw, _ in cites(r)])
        self.assertEqual(stops(r), ["5"])

    def test_numbers_one_digit_apart_are_still_a_list(self):
        r = self.m.check("Sections 9, 10, and 11 of the Act apply.")
        self.assertEqual([raw for raw, _ in cites(r)], ["Sections 9", "Section 10", "Section 11"])
        self.assertEqual(stops(r), [])

    def test_and_inside_the_fragment_is_unchanged(self):
        # the extractor took "Sections 2 and 105" as one phrase; its members split as before
        r = self.m.check("Sections 2 and 105 apply.")
        self.assertEqual([raw for raw, _ in cites(r)], ["Sections 2", "Section 105"])


class TestAnotherCiteFollows(SignBase):
    def test_no_member_and_no_stop(self):
        for draft in ("42 U.S.C. § 1983, 42 U.S.C. § 1988 apply.",
                      "See 42 U.S.C. § 1983, 550 U.S. 544 (2007).",
                      "Apr. 20, 1871, ch. 22, § 1, 17 Stat. 13.",
                      "42 U.S.C. § 1983, which governs, applies."):
            r = self.m.check(draft)
            self.assertEqual(stops(r), [], draft)
            self.assertFalse([raw for raw, _ in cites(r) if raw.endswith(("§ 42", "§ 550", "§ 17"))], draft)

    def test_double_sign_list_unchanged(self):
        r = self.m.check("42 U.S.C. §§ 1983, 1985 apply.")
        self.assertEqual(unlogged(r), ["42 U.S.C. § 1985"])


class TestStoredSignLists(unittest.TestCase):
    def test_each_listed_section_keys(self):
        keys = briefcheck.authority_keys("42 U.S.C. § 1983, 1985")
        self.assertLessEqual({"statute:42usc1983", "statute:42usc1985"}, keys)

    def test_sections_recorded_for_each_key(self):
        self.assertEqual(briefcheck.statute_sections("Miss. Code Ann. § 21-23-7, 21-23-8"),
                         {"statute:misscode21-23-7": {"21-23-7"}, "statute:misscode21-23-8": {"21-23-8"}})

    def test_a_stored_cite_with_a_count_keys_only_the_cite(self):
        keys = {k for k in briefcheck.authority_keys("42 U.S.C. § 1983, 12") if k.startswith("statute")}
        self.assertEqual(keys, {"statute:42usc1983"})


if __name__ == "__main__":
    unittest.main()
