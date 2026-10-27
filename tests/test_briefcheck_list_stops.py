"""A coordinated section list that stops at a number says so instead of dropping it silently.

`_xref_members` reads "Section X, Y, and Z" member by member and stops at a number it does not take
for a member: a count ("552 pages"), a number of another title and shape ("11-46" after
"21-23-7"), or a dotted number after a plain head ("552.101" after "Section 552"). Before, the
rest of the list was never checked and the draft could exit 0 (SWE-2, review of fix/brief-hyphen-
members, P3-2). Now the stop is a WARN `list-member-unchecked` naming the number, so a real section
the rule missed is visible, while correct prose ("552 pages") keeps exit 0: brief check has no way
to dismiss a finding, so a guess must not BLOCK.

A member of another hyphen count must also share the head's leading number *and the separator after
it*, so "552-553 pages" after "Section 552.101," or "5-4 splits" after "Section 5.1," is a stop,
not an invented BLOCK (P3-1), while "552.102-1" after 552.101 is still checked.

How this could fail, and the test that catches it:
  1. a stop is still silent                         TestStopsAreReported
  2. the warning changes the exit code              test_a_count_warns_but_keeps_exit_0
  3. a list that ends normally warns anyway         TestNoStopWhenTheListEnds
  4. a hyphenated count is invented as a member     TestLeadSeparator
  5. a real member of another depth stops warning   test_real_members_of_another_depth_are_still_checked
  6. stored citations get new keys                  TestStoredCitationsUnchanged

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

FULL = "Miss. Code Ann. § 21-23-7 applies. "


def stops(report):
    return [f.cite for f in report.findings if f.code == "list-member-unchecked"]


def raws(report):
    return [c.raw for row in report.rows for c in row.cites if c.kind == "authority"]


def unlogged(report):
    return [f.cite for f in report.findings if f.code == "authority-not-logged"]


class StopBase(Base):
    LOGGED = ("Miss. Code Ann. § 21-23-7", "Miss. Code Ann. § 21-23-8", "Tex. Gov't Code § 552.101",
              "Tex. Gov't Code § 552.102", "Tex. Bus. Orgs. Code § 5.1", "5 U.S.C. § 552")

    def setUp(self):
        super().setUp()
        for cite in self.LOGGED:
            self.m.authority(cite, "source-checked", via="https://example.org/official", evidence="opened")
        self.m.conn.commit()


class TestStopsAreReported(StopBase):
    def test_member_of_another_title_and_hyphen_count(self):
        r = self.m.check(FULL + "Section 21-23-7, 11-46 governs the time.")
        self.assertEqual(stops(r), ["11-46"])
        self.assertIn("list-member-unchecked", codes(r, "WARN"))

    def test_dotted_member_after_a_plain_head(self):
        r = self.m.check("Under Section 552, 552.101 also applies.")
        self.assertEqual(stops(r), ["552.101"])

    def test_stop_after_checked_members(self):
        r = self.m.check(FULL + "Sections 21-23-7, 21-23-8, and 11-46 govern the time.")
        self.assertEqual(stops(r), ["11-46"])
        self.assertEqual(unlogged(r), [])  # the members before the stop are still checked

    def test_a_count_warns_but_keeps_exit_0(self):
        r = self.m.check("Tex. Gov't Code § 552.101 applies. Under Section 552.101, 552 pages were withheld.")
        self.assertEqual(stops(r), ["552"])
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertEqual(r.exit_code, 0)

    def test_the_warning_names_the_line(self):
        r = self.m.check("Intro.\n\n" + FULL + "Section 21-23-7, 11-46 governs.")
        [f] = [f for f in r.findings if f.code == "list-member-unchecked"]
        self.assertEqual(f.line, 3)


class TestNoStopWhenTheListEnds(StopBase):
    def test_members_only(self):
        for draft in (FULL + "Sections 21-23-7 and 21-23-8 govern.",
                      FULL + "Section 21-23-7, 21-23-8 governs.",
                      "Tex. Gov't Code § 552.101 applies. Sections 552.101, 552.102, and 552.103 close them."):
            self.assertEqual(stops(self.m.check(draft)), [], draft)

    def test_another_cite_or_a_word_follows(self):
        for draft in (FULL + "Section 21-23-7, 1972 Miss. Laws ch. 5, governs.",
                      "Under Section 1983, 42 U.S.C. § 1983 also applies.",
                      FULL + "Section 21-23-7, which governs the time, applies.",
                      FULL + "Section 21-23-7. 11 days remain."):
            self.assertEqual(stops(self.m.check(draft)), [], draft)

    def test_an_or_composite_blocks_without_a_second_report(self):
        r = self.m.check(FULL + "Section 21-23-7 or 11-46 governs.")
        self.assertIn("authority-not-logged", codes(r, "BLOCK"))
        self.assertEqual(stops(r), [])

    def test_a_range_is_not_a_stop(self):
        self.assertEqual(stops(self.m.check("Sections 21-23-7 through 21-23-8 govern.")), [])


class TestLeadSeparator(StopBase):
    def test_hyphenated_count_after_a_dotted_head_is_a_stop_not_a_member(self):
        r = self.m.check("Tex. Gov't Code § 552.101 applies. Under Section 552.101, 552-553 pages were withheld.")
        self.assertNotIn("Section 552-553", raws(r))
        self.assertEqual(stops(r), ["552-553"])
        self.assertEqual(r.exit_code, 0)

    def test_vote_split_after_a_short_dotted_head(self):
        r = self.m.check("Tex. Bus. Orgs. Code § 5.1 applies. Under Section 5.1, 5-4 splits are rare.")
        self.assertNotIn("Section 5-4", raws(r))
        self.assertEqual(stops(r), ["5-4"])
        self.assertEqual(r.exit_code, 0)

    def test_real_members_of_another_depth_are_still_checked(self):
        r = self.m.check("Tex. Gov't Code § 552.101 applies. Sections 552.101, 552.102-1, and 552.103 govern.")
        self.assertEqual(unlogged(r), ["Section 552.102-1", "Section 552.103"])
        r = self.m.check(FULL + "Section 21-23-7, 21-23, and 21-23-8 govern the time.")
        self.assertEqual(unlogged(r), ["Section 21-23"])
        self.assertEqual(stops(r), [])


class TestStoredCitationsUnchanged(unittest.TestCase):
    """The stop collector is opt-in: stored citations key exactly as before."""
    CITES = ("Section 21-23-7, 11-46", "Section 552, 552.101", "Section 552.101, 552-553",
             "Miss. Code Ann. § 21-23-7", "Sections 552.101 and 552.102")

    def test_scan_without_a_collector_yields_only_cites(self):
        for cite in self.CITES:
            kinds = {kind for *_, kind, _ in briefcheck.scan_authorities(cite)}
            self.assertLessEqual(kinds, {"statute", "case", "case-short", "rule", "bare-rule"}, cite)

    def test_collector_reports_the_stop_and_nothing_else(self):
        got = []
        cites = list(briefcheck.scan_authorities("Section 21-23-7, 11-46 governs.", stops=got))
        self.assertEqual([c[1] for c in cites], ["Section 21-23-7"])
        self.assertEqual([g[0] for g in got], ["11-46"])

    def test_authority_keys_ignore_stops(self):
        self.assertEqual(briefcheck.authority_keys("Section 21-23-7, 11-46"),
                         {"statute-short:section21-23-7", "full:section21-23-711-46"})


if __name__ == "__main__":
    unittest.main()
