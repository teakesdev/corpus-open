"""Brief check near-false pass: a statute short form with no earlier full cite ("§ 983")
fell back to a suffix match on the stored statute keys, so it bound to "42 U.S.C. § 1983"
and drew only a WARN. The fallback must match a whole section number.

How it could fail:
  1. A tail of a section number binds: "§ 983" -> 1983, "§ 3-8" / "§ 23-8" -> 21-23-8,
     or a section that differs only by a dot: "§ 1.01" -> 101 (keys drop dots).
  2. The fix over-corrects and a whole section stops binding: "§ 1983" or "§ 21-23-8"
     (with or without a subsection), or a logged cite written without "§"
     ("42 U.S.C. 1983"), now BLOCKs as authority-not-logged.
  3. A section that two logged statutes share binds to one of them at random.
  4. (SWE-2 round 1, P1) A number elsewhere in the logged citation counts as its section:
     "§ 2" -> "5 U.S.C. § 552(b)(2)", "§ 8" -> "8 U.S.C. § 1108" (the title),
     "§ 1983" -> "Miss. Code Ann. § 41-1983 (1983)" (a year).
  5. A key `authority_keys` makes for a logged citation has no recorded section, so that
     statute's short form BLOCKs although it is logged (a "§§" list member, say).

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


def cites(report, raw):
    """Every authority Cite in the report whose raw text is `raw`."""
    return [c for row in report.rows for c in row.cites if c.kind == "authority" and c.raw == raw]


def unlogged(report):
    return [f.cite for f in report.findings if f.code == "authority-not-logged"]


class ShortSectionBase(Base):
    def setUp(self):
        super().setUp()
        # the fixture already logs "42 U.S.C. § 1983"
        for cite in ("Miss. Code Ann. § 21-23-7", "Miss. Code Ann. § 21-23-8"):
            self.m.authority(cite, "source-checked", via="https://example.org/official",
                             evidence="opened the official text")
        self.m.conn.commit()

    def log(self, *cites, drop_1983=False):
        if drop_1983:  # the fixture's own "42 U.S.C. § 1983"
            self.m.conn.execute("DELETE FROM authorities WHERE citation = '42 U.S.C. § 1983'")
        for cite in cites:
            self.m.authority(cite, "source-checked", via="https://example.org/official",
                             evidence="opened the official text")
        self.m.conn.commit()

    def assert_unbound(self, draft, raw, not_target):
        r = self.m.check(draft)
        [c] = cites(r, raw)
        self.assertNotEqual(c.target, not_target)
        self.assertEqual(unlogged(r), [raw])
        self.assertIn("authority-not-logged", codes(r, "BLOCK"))
        self.assertIn("short-form-without-full-cite", codes(r, "WARN"))
        self.assertEqual(r.exit_code, 1)

    def assert_bound(self, draft, raw, target):
        r = self.m.check(draft)
        [c] = cites(r, raw)
        self.assertEqual(c.target, target)
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertIn("short-form-without-full-cite", codes(r, "WARN"))
        self.assertEqual(r.exit_code, 0)


class TestPartialSectionDoesNotBind(ShortSectionBase):
    def test_tail_of_1983_does_not_bind(self):
        self.assert_unbound("A § 983 claim fails here.", "§ 983", "statute:42usc1983")

    def test_last_two_parts_of_hyphenated_section_do_not_bind(self):
        self.assert_unbound("Under § 3-8, the board meets monthly.", "§ 3-8", "statute:misscode21-23-8")

    def test_tail_starting_at_a_hyphen_does_not_bind(self):
        self.assert_unbound("Under § 23-8, the board meets monthly.", "§ 23-8", "statute:misscode21-23-8")

    def test_section_that_differs_only_by_a_dot_does_not_bind(self):
        # keys drop dots, so "§ 1.01" and "§ 101" share a key tail; the sections differ
        self.m.authority("42 U.S.C. § 101", "source-checked", via="https://example.org/official",
                         evidence="opened the official text")
        self.m.conn.commit()
        self.assert_unbound("Under § 1.01, the term is defined.", "§ 1.01", "statute:42usc101")

    def test_head_before_a_dot_does_not_bind(self):
        # "§ 2" is the start of "§ 2.2", and the key ends with "2"
        self.m.authority("42 U.S.C. § 2.2", "source-checked", via="https://example.org/official",
                         evidence="opened the official text")
        self.m.conn.commit()
        self.assert_unbound("Under § 2, the term is defined.", "§ 2", "statute:42usc22")

    def test_tail_after_full_cite_of_another_section_does_not_bind(self):
        # the earlier full cite is to § 1983, not § 983: no in-draft binding, and no fallback one
        self.assert_unbound("Claims arise under 42 U.S.C. § 1983. A § 983 claim fails here.",
                            "§ 983", "statute:42usc1983")

    def test_number_in_a_subsection_does_not_bind(self):
        for logged, target in (("5 U.S.C. § 552(b)(2)", "statute:5usc552"),
                               ("42 U.S.C. § 2000e-2(a)(2)", "statute:42usc2000e-2")):
            with self.subTest(logged=logged):
                self.m.conn.execute("DELETE FROM authorities WHERE citation LIKE '%(2)%'")
                self.log(logged)
                self.assert_unbound("Under § 2, the board may act.", "§ 2", target)

    def test_title_number_does_not_bind(self):
        self.log("8 U.S.C. § 1108")
        self.assert_unbound("Under § 8, the board may act.", "§ 8", "statute:8usc1108")

    def test_year_in_a_parenthetical_does_not_bind(self):
        self.log("Miss. Code Ann. § 41-1983 (1983)", drop_1983=True)
        self.assert_unbound("A § 1983 claim fails here.", "§ 1983", "statute:misscode41-1983")

    def test_tail_of_a_lettered_section_does_not_bind(self):
        self.log("5 U.S.C. § 552a")
        self.assert_unbound("Under § 2a, records are closed.", "§ 2a", "statute:5usc552a")

    def test_section_a_dot_splits_does_not_bind(self):
        self.log("42 U.S.C. § 1.01")
        self.assert_unbound("Under § 101, the term is defined.", "§ 101", "statute:42usc101")


class TestWholeSectionStillBinds(ShortSectionBase):
    def test_1983_binds_to_logged_usc_cite(self):
        self.assert_bound("A § 1983 claim needs a person.", "§ 1983", "statute:42usc1983")

    def test_1983_with_subsection_binds(self):
        self.assert_bound("A § 1983(a) claim needs a person.", "§ 1983(a)", "statute:42usc1983")

    def test_hyphenated_section_binds_to_logged_state_code_cite(self):
        self.assert_bound("Under § 21-23-8, the board meets monthly.", "§ 21-23-8",
                          "statute:misscode21-23-8")

    def test_hyphenated_section_with_subsection_binds(self):
        self.assert_bound("Under § 21-23-8(1), the board meets monthly.", "§ 21-23-8(1)",
                          "statute:misscode21-23-8")

    def test_sibling_section_binds_to_its_own_statute(self):
        self.assert_bound("Under § 21-23-7, the clerk keeps minutes.", "§ 21-23-7",
                          "statute:misscode21-23-7")

    def test_binds_to_logged_cite_written_without_section_sign(self):
        self.m.conn.execute("UPDATE authorities SET citation='42 U.S.C. 1983' WHERE citation='42 U.S.C. § 1983'")
        self.m.conn.commit()
        self.assert_bound("A § 1983 claim needs a person.", "§ 1983", "statute:42usc1983")

    def test_lettered_section_binds_case_insensitively(self):
        self.log("5 U.S.C. § 552a")
        self.assert_bound("Under § 552A, records are closed.", "§ 552A", "statute:5usc552a")

    def test_binds_through_trailing_matter_in_the_logged_cite(self):
        for logged in ("42 U.S.C. § 1983 et seq.", "42 U.S.C. § 1983 (2018)"):
            with self.subTest(logged=logged):
                self.m.conn.execute("DELETE FROM authorities WHERE citation LIKE '42 U.S.C. %1983%'")
                self.log(logged)
                self.assert_bound("A § 1983 claim needs a person.", "§ 1983", "statute:42usc1983")

    def test_row_logging_two_statutes_binds_the_one_named(self):
        # SWE-2 P3: the 42 U.S.C. § 11983 key also matched "§ 1983", leaving two candidates
        self.log("42 U.S.C. § 11983; 5 U.S.C. § 1983", drop_1983=True)
        self.assert_bound("A § 1983 claim needs a person.", "§ 1983", "statute:5usc1983")

    def test_full_cite_earlier_in_draft_still_binds_without_warn(self):
        r = self.m.check("Claims arise under 42 U.S.C. § 1983. A § 1983 claim needs a person.")
        [c] = cites(r, "§ 1983")
        self.assertEqual(c.target, "statute:42usc1983")
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertNotIn("short-form-without-full-cite", codes(r, "WARN"))


class TestSharedSectionStaysUnbound(ShortSectionBase):
    def test_section_logged_under_two_titles_does_not_pick_one(self):
        self.m.authority("18 U.S.C. § 1983", "source-checked", via="https://example.org/official",
                         evidence="opened the official text")
        self.m.conn.commit()
        r = self.m.check("A § 1983 claim needs a person.")
        [c] = cites(r, "§ 1983")
        self.assertFalse(c.target.startswith("statute:"), c.target)
        self.assertIn("authority-not-logged", codes(r, "BLOCK"))


class TestEveryLoggedStatuteKeyHasItsSection(unittest.TestCase):
    """`statute_sections` must cover every `statute:` key `authority_keys` makes. A key producer
    that skips it (a "§§" list member, say) leaves that statute's short form unbound, so it
    BLOCKs although logged, and a merge shows no conflict. This test makes that drift fail."""
    CITES = ("42 U.S.C. § 1983", "42 U.S.C. 1983", "42 U.S.C. § 1983 et seq.", "42 U.S.C. § 1983.",
             "5 U.S.C. § 552(b)(2)", "8 U.S.C. § 1108", "5 U.S.C. § 552a", "12 CFR 1026.1",
             "Miss. Code Ann. § 21-23-8", "Miss. Code Annotated § 21-23-7", "Miss. Code § 11-46-11(1)",
             "Miss. Code Ann. § 41-1983 (1983)", "42 U.S.C. §§ 1983, 1985",
             "Miss. Code Ann. §§ 21-21-3, 21-3-15", "42 U.S.C. § 11983; 5 U.S.C. § 1983",
             "Pub. L. 111-148", "Fed. R. Civ. P. 15", "Dietz v. Bouldin, 579 U.S. 40 (2016)")

    def test_sections_cover_every_statute_key(self):
        for cite in self.CITES:
            keys = {k for k in briefcheck.authority_keys(cite) if k.startswith("statute:")}
            self.assertEqual(set(briefcheck.statute_sections(cite)), keys, cite)

    def test_sections_are_the_ones_the_keys_were_made_from(self):
        expect = {
            "42 U.S.C. § 1983": {"statute:42usc1983": {"1983"}},
            "42 U.S.C. 1983": {"statute:42usc1983": {"1983"}},
            "42 U.S.C. § 1983.": {"statute:42usc1983": {"1983"}},
            "5 U.S.C. § 552(b)(2)": {"statute:5usc552": {"552"}},
            "8 U.S.C. § 1108": {"statute:8usc1108": {"1108"}},
            "5 U.S.C. § 552a": {"statute:5usc552a": {"552a"}},
            "12 CFR 1026.1": {"statute:12cfr10261": {"1026.1"}},
            "Miss. Code Ann. § 41-1983 (1983)": {"statute:misscode41-1983": {"41-1983"}},
            "42 U.S.C. § 11983; 5 U.S.C. § 1983": {"statute:42usc11983": {"11983"},
                                                   "statute:5usc1983": {"1983"}},
            "Pub. L. 111-148": {"statute:publ111-148": set()},  # keyed, but no section to bind
        }
        for cite, want in expect.items():
            self.assertEqual(briefcheck.statute_sections(cite), want, cite)


if __name__ == "__main__":
    unittest.main()
