"""A dotted "Section 552.101" cross-reference is one section, not "Section 552".

The extractor stopped the section word family at a dot, so "Under Section 552.101, records are
closed." read as "Section 552"; with "5 U.S.C. § 552" logged, `brief check` bound the draft to
statute:5usc552 and exited 0. "§ 552.101" was already read whole.

A dotted number is compared as written, like the § path: "5.1" and "5.10" are different
sections, never one decimal. A dot that is not followed by a digit ends the number, so a
sentence-final period is not taken, and a "605.0101 - 605.1108" range is left to the range
pattern.

How this could fail, and the test that catches it:
  1. the number is still cut at the dot              TestDottedSectionExtraction, TestDottedSectionFalsePass,
                                                     TestDottedSectionBinding
  2. a sentence-final period is taken as a part      test_sentence_final_period_is_not_taken
  3. "5.1" and "5.10" are read as one section        TestDecimalStyleNumbersStayDistinct
  4. a list member is dropped or a count invented    TestDottedCoordinatedMembers, TestMembersOfAnotherDepth
  5. a range passes on one endpoint                  test_dotted_range_is_not_split_into_its_endpoints
  6. a dotted short form binds a longer logged section ('5.1' -> '§ 51', '552.101' -> '§ 2552.101')
                                                     TestDottedShortFormBindsOnlyItsOwnSection
  7. the Florida "Sections 605.0101 - 605.1108" range in holdout_v1 is re-read
                                                     test_florida_range_is_left_to_the_range_pattern

Synthetic data only. Run: python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from matterkit import briefcheck, citations  # noqa: E402
from test_briefcheck import Base, codes  # noqa: E402


def extracted(text):
    return [r["raw_fragment"] for r in citations.extract_citations(text) if r["status"] == "extracted"]


def cites(report, raw):
    return [c for row in report.rows for c in row.cites if c.kind == "authority" and c.raw == raw]


def raws(report):
    return [c.raw for row in report.rows for c in row.cites if c.kind == "authority"]


def unlogged(report):
    return [f.cite for f in report.findings if f.code == "authority-not-logged"]


class DottedBase(Base):
    """5 U.S.C. § 552 and two Texas sections are logged; § 552.103 is not."""
    LOGGED = ("5 U.S.C. § 552", "Tex. Gov't Code § 552.101", "Tex. Gov't Code § 552.102")

    def setUp(self):
        super().setUp()
        for cite in self.LOGGED:
            self.m.authority(cite, "source-checked", via="https://example.org/official",
                             evidence="opened the official text")
        self.m.conn.commit()


class TestDottedSectionExtraction(unittest.TestCase):
    def exact(self, text, needle):
        self.assertIn(needle, extracted(text), text)

    def test_extraction_takes_the_whole_dotted_number(self):
        self.exact("Under Section 552.101, records are closed.", "Section 552.101")
        self.exact("See section 552.101(a) for the rule.", "section 552.101(a)")
        self.exact("Sec. 107.5 sets the fee.", "Sec. 107.5")
        self.assertNotIn("Section 552", extracted("Under Section 552.101, records are closed."))

    def test_extraction_offsets_cover_the_whole_number(self):
        text = "Under Section 552.101, records are closed."
        [row] = [r for r in citations.extract_citations(text) if r["raw_fragment"].startswith("Section")]
        self.assertEqual(text[row["char_start"]:row["char_end"]], "Section 552.101")
        self.assertEqual(row["status"], "extracted")

    def test_hyphenated_and_dotted_parts_together(self):
        self.exact("Section 13-1-4.1 applies.", "Section 13-1-4.1")

    def test_sentence_final_period_is_not_taken(self):
        self.assertEqual(extracted("See Section 552."), ["Section 552"])
        self.assertEqual(extracted("See Section 552.101."), ["Section 552.101"])

    def test_coordinated_dotted_members_are_nested_rows(self):
        text = "See section 552.101 or 552.102 for the procedure."
        got = extracted(text)
        for needle in ("section 552.101 or 552.102", "section 552.101", "552.102"):
            self.assertIn(needle, got)

    def test_plain_and_hyphenated_numbers_unchanged(self):
        self.exact("Compare Section 80 of the 1909 Act.", "Section 80")
        self.exact("Under Section 21-23-8, the board may act.", "Section 21-23-8")
        self.exact("Bring Section 1983-style claims here.", "Section 1983")

    def test_florida_range_is_left_to_the_range_pattern(self):
        # holdout_v1 fl_stat_605: gold labels the range, not a "Sections 605…" cross-reference
        got = extracted("Short title. Sections 605.0101 - 605.1108 may be cited as the Act.")
        self.assertIn("605.0101 - 605.1108", got)
        self.assertFalse([g for g in got if g.startswith("Sections")], got)

    def test_statute_base_keeps_the_dotted_section(self):
        self.assertEqual(briefcheck._statute_base("Section 552.101"), ("statute-short:section552101", "552.101"))
        self.assertEqual(briefcheck._statute_base("Section 552.101(a)")[1], "552.101")


class TestDottedSectionFalsePass(Base):
    """The reported case: only "5 U.S.C. § 552" is logged."""
    def setUp(self):
        super().setUp()
        self.m.authority("5 U.S.C. § 552", "source-checked", via="https://example.org/official",
                         evidence="opened the official text")
        self.m.conn.commit()

    def test_dotted_section_does_not_bind_the_shorter_logged_section(self):
        r = self.m.check("Under Section 552.101, records are closed.")
        self.assertEqual(unlogged(r), ["Section 552.101"])
        self.assertEqual(r.exit_code, 1)
        self.assertFalse([c for row in r.rows for c in row.cites if c.target == "statute:5usc552"])

    def test_sec_abbreviation_reads_the_same(self):
        r = self.m.check("Under Sec. 552.101, records are closed.")
        self.assertEqual(unlogged(r), ["Sec. 552.101"])
        self.assertEqual(r.exit_code, 1)

    def test_section_sign_reads_the_same(self):
        r = self.m.check("Under § 552.101, records are closed.")
        self.assertEqual(unlogged(r), ["§ 552.101"])

    def test_plain_section_still_binds_the_federal_statute(self):
        r = self.m.check("Under Section 552, records are open.")
        [c] = cites(r, "Section 552")
        self.assertEqual(c.target, "statute:5usc552")


class TestDottedSectionBinding(DottedBase):
    def test_dotted_section_binds_the_logged_dotted_statute(self):
        r = self.m.check("Under Section 552.101, records are closed.")
        self.assertEqual(unlogged(r), [])
        [c] = cites(r, "Section 552.101")
        self.assertEqual(c.target, "statute:texgov'tcode552101")

    def test_section_word_binds_to_earlier_full_cite(self):
        r = self.m.check("Tex. Gov't Code § 552.101 closes them. Section 552.101 also applies.")
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertNotIn("short-form-without-full-cite", codes(r, "WARN"))
        [c] = cites(r, "Section 552.101")
        self.assertEqual(c.target, "statute:texgov'tcode552101")

    def test_section_word_binds_like_section_sign(self):
        by_word = self.m.check("The records are closed by Section 552.102.")
        by_sign = self.m.check("The records are closed by § 552.102.")
        w, s = by_word.rows[0].cites[0], by_sign.rows[0].cites[0]
        self.assertEqual(w.target, s.target)
        self.assertEqual(w.target, "statute:texgov'tcode552102")
        self.assertEqual((codes(by_word, "BLOCK"), codes(by_word, "WARN")),
                         (codes(by_sign, "BLOCK"), codes(by_sign, "WARN")))

    def test_unlogged_dotted_section_blocks(self):
        r = self.m.check("Under Section 552.103, records are closed.")
        self.assertEqual(unlogged(r), ["Section 552.103"])


class TestDecimalStyleNumbersStayDistinct(Base):
    def log(self, cite):
        self.m.authority(cite, "source-checked", via="https://example.org/official", evidence="opened")
        self.m.conn.commit()

    def test_5_1_does_not_bind_5_10(self):
        self.log("Tex. Bus. Orgs. Code § 5.10")
        r = self.m.check("The name must be distinct under Section 5.1.")
        self.assertEqual(unlogged(r), ["Section 5.1"])

    def test_5_10_does_not_bind_5_1(self):
        self.log("Tex. Bus. Orgs. Code § 5.1")
        r = self.m.check("The name must be distinct under Section 5.10.")
        self.assertEqual(unlogged(r), ["Section 5.10"])

    def test_5_1_binds_5_1(self):
        self.log("Tex. Bus. Orgs. Code § 5.1")
        r = self.m.check("The name must be distinct under Section 5.1.")
        self.assertEqual(unlogged(r), [])
        [c] = cites(r, "Section 5.1")
        self.assertEqual(c.target, "statute:texbusorgscode51")


class TestDottedCoordinatedMembers(DottedBase):
    FULL = "Tex. Gov't Code § 552.101 applies. "

    def test_comma_member_is_checked(self):
        r = self.m.check(self.FULL + "Section 552.101, 552.103 closes them.")
        self.assertEqual(unlogged(r), ["Section 552.103"])
        self.assertEqual(r.exit_code, 1)

    def test_or_and_and_members_are_each_checked(self):
        for tail in ("Section 552.101 or 552.103 closes them.", "Sections 552.101 and 552.103 close them."):
            r = self.m.check(self.FULL + tail)
            self.assertEqual(unlogged(r), ["Section 552.103"], tail)
            self.assertFalse([x for x in raws(r) if " or " in x or " and " in x], tail)

    def test_three_members_with_serial_comma(self):
        r = self.m.check(self.FULL + "Sections 552.101, 552.102, and 552.103 close them.")
        self.assertEqual(unlogged(r), ["Section 552.103"])
        [c] = cites(r, "Section 552.102")
        self.assertEqual(c.target, "statute:texgov'tcode552102")

    def test_all_members_logged_pass(self):
        r = self.m.check(self.FULL + "Sections 552.101 and 552.102 close them.")
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertEqual(r.exit_code, 0)
        # each member binds its own Texas section, not 5 U.S.C. § 552
        self.assertEqual([c.target for c in cites(r, "Sections 552.101") + cites(r, "Section 552.102")],
                         ["statute:texgov'tcode552101", "statute:texgov'tcode552102"])

    def test_a_plain_count_after_a_dotted_section_is_not_a_member(self):
        r = self.m.check(self.FULL + "Under Section 552.101, 552 pages were withheld.")
        self.assertNotIn("Section 552", raws(r))

    def test_dotted_range_is_not_split_into_its_endpoints(self):
        # both endpoints are logged; a range must not pass on them
        r = self.m.check("Sections 552.101 through 552.102 close them.")
        self.assertIn("authority-not-logged", codes(r, "BLOCK"))
        self.assertNotIn("Section 552.102", raws(r))
        self.assertFalse([c for row in r.rows for c in row.cites if c.target == "statute:5usc552"])

    def test_a_plain_count_after_a_plain_section_is_not_a_dotted_member(self):
        r = self.m.check("Under Section 552, 5.5 percent were withheld.")
        self.assertNotIn("Section 5.5", raws(r))


class TestMembersOfAnotherDepth(DottedBase):
    """SWE-2 P3-2: a comma list stopped silently at a member with more or fewer dotted parts
    than the head ("…, and 552.103.5"), so the rest of the list was never checked and the
    draft could exit 0. SWE-2's round-2 P3: the same happened at a member with another
    hyphen count ("…, 552.102-1, and 552.103"). Neither depth ends a list now; only a plain
    number ("552") is held to a plain head."""
    FULL = "Tex. Gov't Code § 552.101 applies. "

    def setUp(self):
        super().setUp()
        for cite in ("Miss. Code Ann. § 21-23-7", "Miss. Code Ann. § 21-23-8"):
            self.m.authority(cite, "source-checked", via="https://example.org/official", evidence="opened")
        self.m.conn.commit()

    def test_deeper_last_member_is_checked(self):
        r = self.m.check(self.FULL + "Sections 552.101, 552.102, and 552.103.5 close them.")
        self.assertEqual(unlogged(r), ["Section 552.103.5"])
        self.assertEqual(r.exit_code, 1)

    def test_deeper_middle_member_does_not_end_the_list(self):
        r = self.m.check(self.FULL + "Sections 552.101, 552.103.5, and 552.102 close them.")
        self.assertEqual(unlogged(r), ["Section 552.103.5"])
        [c] = cites(r, "Section 552.102")
        self.assertEqual(c.target, "statute:texgov'tcode552102")

    def test_dotted_subsection_of_a_hyphenated_section(self):
        r = self.m.check("Miss. Code Ann. § 21-23-7 applies. Section 21-23-7, 21-23-7.1 governs the time.")
        self.assertEqual(unlogged(r), ["Section 21-23-7.1"])

    def test_shallower_member_after_a_dotted_head(self):
        r = self.m.check("Section 21-23-7.1, 21-23-8 govern the time.")
        self.assertEqual(raws(r), ["Section 21-23-7.1", "Section 21-23-8"])
        self.assertEqual(unlogged(r), ["Section 21-23-7.1"])

    def test_or_form_with_a_deeper_member_is_split(self):
        r = self.m.check("Miss. Code Ann. § 21-23-7 applies. Section 21-23-7 or 21-23-7.1 governs.")
        self.assertEqual(unlogged(r), ["Section 21-23-7.1"])
        self.assertFalse([x for x in raws(r) if " or " in x])

    def test_member_with_more_hyphens_does_not_end_the_list(self):
        r = self.m.check(self.FULL + "Sections 552.101, 552.102-1, and 552.103 govern.")
        self.assertEqual(unlogged(r), ["Section 552.102-1", "Section 552.103"])
        self.assertEqual(r.exit_code, 1)

    def test_member_with_fewer_hyphens_does_not_end_the_list(self):
        r = self.m.check("Miss. Code Ann. § 21-23-7 applies. Section 21-23-7, 21-23, and 21-23-8 govern the time.")
        self.assertEqual(unlogged(r), ["Section 21-23"])
        [c] = cites(r, "Section 21-23-8")
        self.assertEqual(c.target, "statute:misscode21-23-8")

    def test_or_form_with_another_hyphen_count_is_split(self):
        r = self.m.check(self.FULL + "Section 552.101 or 552.102-1 governs.")
        self.assertEqual(unlogged(r), ["Section 552.102-1"])
        self.assertFalse([x for x in raws(r) if " or " in x])

    def test_a_hyphenated_count_after_a_plain_section_is_not_a_member(self):
        r = self.m.check("Under Section 1983, 2-1 majorities are rare.")
        self.assertNotIn("Section 2-1", raws(r))

    def test_a_number_of_another_hyphen_count_and_title_is_not_a_member(self):
        # a member with another hyphen count must share the head's leading number (title or chapter)
        for draft, phantom in (("Section 21-23-7, 1972-73 amendments aside, governs.", "Section 1972-73"),
                               ("Under Section 21-23-7, 2-1 majorities are rare.", "Section 2-1")):
            r = self.m.check("Miss. Code Ann. § 21-23-7 applies. " + draft)
            self.assertNotIn(phantom, raws(r), draft)

    def test_member_of_another_title_with_the_head_hyphen_count_is_still_checked(self):
        for tail, member in (("Section 21-23-7, 11-46-9 governs.", "Section 11-46-9"),
                             ("Section 21-23-7, 11-46-11.1 governs.", "Section 11-46-11.1")):
            r = self.m.check("Miss. Code Ann. § 21-23-7 applies. " + tail)
            self.assertEqual(unlogged(r), [member], tail)


class TestDottedShortFormBindsOnlyItsOwnSection(Base):
    """SWE-2 P1/P2 (review 46a967f): a short "Section 5.1" with no full cite earlier must not bind
    a logged section whose dot-stripped key merely ends the same way. Needs the short-form
    binding from fix/brief-short-section (a short form binds only to a logged key's own section)."""
    def log(self, *cites_):
        for cite in cites_:
            self.m.authority(cite, "source-checked", via="https://example.org/official", evidence="opened")
        self.m.conn.commit()

    def bound_to(self, r, key):
        return [c for row in r.rows for c in row.cites if c.target == key]

    def test_5_1_does_not_bind_51(self):
        self.log("Tex. Bus. Orgs. Code § 51")
        for draft, raw in (("Under Section 5.1, the name must differ.", "Section 5.1"),
                           ("Under § 5.1, the name must differ.", "§ 5.1")):
            r = self.m.check(draft)
            self.assertEqual(unlogged(r), [raw], draft)
            self.assertEqual(r.exit_code, 1, draft)

    def test_5_1_does_not_bind_15_1(self):
        self.log("Tex. Bus. Orgs. Code § 15.1")
        r = self.m.check("Under Section 5.1, the name must differ.")
        self.assertEqual(unlogged(r), ["Section 5.1"])

    def test_552_101_does_not_bind_2552_101(self):
        self.log("Tex. Gov't Code § 2552.101")
        r = self.m.check("Under Section 552.101, records are closed.")
        self.assertEqual(unlogged(r), ["Section 552.101"])
        self.assertFalse(self.bound_to(r, "statute:texgov'tcode2552101"))

    def test_member_does_not_bind_a_longer_section(self):
        self.log("Tex. Gov't Code § 552.101", "Tex. Gov't Code § 1552.102")
        r = self.m.check("Tex. Gov't Code § 552.101 applies. Sections 552.101 and 552.102 close them.")
        self.assertEqual(unlogged(r), ["Section 552.102"])

    def test_id_after_a_dotted_short_form_does_not_bind(self):
        self.log("Tex. Bus. Orgs. Code § 51")
        r = self.m.check("Under Section 5.1, the name must differ. Id.")
        self.assertFalse(self.bound_to(r, "statute:texbusorgscode51"))
        self.assertEqual(r.exit_code, 1)


if __name__ == "__main__":
    unittest.main()
