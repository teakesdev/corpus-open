"""Draft statute lists: a draft's "Miss. Code Ann. §§ 21-21-3, 21-3-15" is one statute cite per section.

The extractor marks "Miss. Code Ann. §§ 21-21-3" extraction-failed, so the draft scanner used to see
only the nested "§ 21-21-3" (a short form) and never the second section: `brief check` warned
short-form-without-full-cite on the first and let an unlogged second section pass. Each listed
section now yields its own cite keyed "statute:<code><section>", the same key the stored side gives
it, with a span over that section's text.

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

MISS_LIST = "Miss. Code Ann. §§ 21-21-3, 21-3-15"


def scan(text):
    return list(briefcheck.scan_authorities(text))


def statute_cites(text):
    return [(k, text[a:b]) for k, _, _, a, b, kind, _ in scan(text) if kind == "statute"]


class TestDraftStatuteListScan(unittest.TestCase):
    def test_each_listed_section_is_its_own_full_cite(self):
        text = f"The board sets the rate. {MISS_LIST}."
        self.assertEqual(statute_cites(text), [("statute:misscode21-21-3", "Miss. Code Ann. §§ 21-21-3"),
                                               ("statute:misscode21-3-15", "21-3-15")])

    def test_each_section_displays_as_the_single_cite(self):
        self.assertEqual([d for _, d, *_ in scan(MISS_LIST)],
                         ["Miss. Code Ann. § 21-21-3", "Miss. Code Ann. § 21-3-15"])

    def test_each_section_carries_its_section_number(self):
        self.assertEqual([x for *_, x in scan(MISS_LIST)], ["21-21-3", "21-3-15"])

    def test_draft_keys_match_the_stored_list_keys(self):
        for cite in (MISS_LIST, "Miss. Code Ann. §§ 21-21-3 and 21-3-15", "42 U.S.C. §§ 1983, 1985(3), 1988(b)",
                     "28 U.S.C. § 1331; 42 U.S.C. §§ 1983, 1988", "§§ 21-21-3, 21-3-15"):
            stored = {k for k in briefcheck.authority_keys(cite) if k.startswith("statute")}
            self.assertEqual({k for k, *_ in scan(cite)}, stored, cite)

    def test_separators_and_subsections(self):
        text = "See 42 U.S.C. §§ 1983, 1985(3), and 1988(b)."
        self.assertEqual(statute_cites(text), [("statute:42usc1983", "42 U.S.C. §§ 1983"),
                                               ("statute:42usc1985", "1985(3)"),
                                               ("statute:42usc1988", "1988(b)")])

    def test_first_section_span_covers_its_subsections(self):
        self.assertEqual(statute_cites("Miss. Code Ann. §§ 21-21-3(1)(a), 21-3-15")[0],
                         ("statute:misscode21-21-3", "Miss. Code Ann. §§ 21-21-3(1)(a)"))

    def test_list_with_no_code_name_yields_short_forms(self):
        self.assertEqual(statute_cites("These apply. §§ 21-21-3, 21-3-15."), [("statute-short:21-21-3", "§§ 21-21-3"),
                                                                     ("statute-short:21-3-15", "21-3-15")])

    def test_code_name_does_not_carry_across_cites(self):
        self.assertEqual([k for k, _ in statute_cites("28 U.S.C. § 1331; 42 U.S.C. §§ 1983, 1988.")],
                         ["statute:28usc1331", "statute:42usc1983", "statute:42usc1988"])

    def test_list_stops_before_a_following_cite(self):
        text = "See Miss. Code Ann. §§ 21-21-3, 21-3-15, 42 U.S.C. § 1983."
        self.assertEqual(statute_cites(text), [("statute:misscode21-21-3", "Miss. Code Ann. §§ 21-21-3"),
                                               ("statute:misscode21-3-15", "21-3-15"),
                                               ("statute:42usc1983", "42 U.S.C. § 1983")])
        self.assertEqual([k for k, _ in statute_cites("42 U.S.C. §§ 1983 and 1988, 28 U.S.C. § 1331.")],
                         ["statute:42usc1983", "statute:42usc1988", "statute:28usc1331"])

    def test_stored_list_followed_by_a_cite_keys_that_cite(self):
        self.assertEqual({k for k in briefcheck.authority_keys("42 U.S.C. §§ 1983 and 1988, 28 U.S.C. § 1331")
                          if k.startswith("statute")},
                         {"statute:42usc1983", "statute:42usc1988", "statute:28usc1331"})

    def test_single_section_cites_are_unchanged(self):
        self.assertEqual(statute_cites("Miss. Code Ann. § 21-3-15."),
                         [("statute:misscode21-3-15", "Miss. Code Ann. § 21-3-15")])
        self.assertEqual(statute_cites("Miss. Code Ann. § 21-21-3 and § 21-3-15"),
                         [("statute:misscode21-21-3", "Miss. Code Ann. § 21-21-3"),
                          ("statute-short:21-3-15", "§ 21-3-15")])


class TestDraftStatuteListsInBriefCheck(Base):
    def only(self, cite):
        self.m.conn.execute("DELETE FROM authorities")
        self.m.authority(cite, "source-checked", via="https://example.org/code", evidence="opened official text")
        self.m.conn.commit()

    def test_stored_list_covers_a_draft_list(self):
        self.only(MISS_LIST)
        r = self.m.check(f"The board sets the rate and the officer takes the bond. {MISS_LIST}.")
        self.assertNotIn("authority-not-logged", codes(r, "BLOCK"))
        self.assertNotIn("short-form-without-full-cite", codes(r, "WARN"))
        self.assertEqual(sorted((a.key, a.store_citation, a.store_status) for a in r.authorities),
                         [("statute:misscode21-21-3", MISS_LIST, "source-checked"),
                          ("statute:misscode21-3-15", MISS_LIST, "source-checked")])
        self.assertEqual(r.exit_code, 0)

    def test_unlogged_listed_section_blocks(self):
        self.only("Miss. Code Ann. § 21-21-3")
        r = self.m.check("# Argument\n\nThe board sets the rate.\nThe officer takes the bond. "
                         f"{MISS_LIST}.\n")
        blocks = [(f.code, f.line, f.cite) for f in r.findings if f.severity == "BLOCK"]
        self.assertEqual(blocks, [("authority-not-logged", 4, "Miss. Code Ann. § 21-3-15")])
        self.assertNotIn("short-form-without-full-cite", codes(r, "WARN"))
        used = {a.key: a.store_citation for a in r.authorities}
        self.assertEqual(used, {"statute:misscode21-21-3": "Miss. Code Ann. § 21-21-3",
                                "statute:misscode21-3-15": None})

    def test_cite_after_a_list_is_checked_on_its_own(self):
        self.only(MISS_LIST)
        self.m.authority("42 U.S.C. § 1983", "source-checked", via="https://example.org/1983", evidence="opened")
        self.m.conn.commit()
        r = self.m.check(f"The officer takes the bond. {MISS_LIST}, 42 U.S.C. § 1983.")
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertNotIn("short-form-without-full-cite", codes(r, "WARN"))
        self.assertEqual(sorted(a.key for a in r.authorities),
                         ["statute:42usc1983", "statute:misscode21-21-3", "statute:misscode21-3-15"])

    def test_later_short_form_binds_to_the_listed_section(self):
        self.only(MISS_LIST)
        r = self.m.check(f"The officer takes the bond. {MISS_LIST}. The bond is filed. § 21-3-15.")
        self.assertNotIn("short-form-without-full-cite", codes(r, "WARN"))
        self.assertEqual(codes(r, "BLOCK"), [])
        use = {a.key: a for a in r.authorities}["statute:misscode21-3-15"]
        self.assertEqual(len(use.lines), 2)

    def test_id_after_a_list_binds_to_the_last_listed_section(self):
        self.only(MISS_LIST)
        r = self.m.check(f"The officer takes the bond. {MISS_LIST}. The bond is filed. Id.")
        self.assertEqual(codes(r, "BLOCK"), [])
        use = {a.key: a for a in r.authorities}["statute:misscode21-3-15"]
        self.assertEqual(len(use.lines), 2)

    def test_unlogged_cite_in_a_parenthetical_blocks(self):
        self.only(MISS_LIST)
        r = self.m.check(f"The bond is set. {MISS_LIST} (42 U.S.C. § 1983).")
        self.assertEqual([(f.code, f.cite) for f in r.findings if f.severity == "BLOCK"],
                         [("authority-not-logged", "42 U.S.C. § 1983")])

    def test_section_before_a_parenthetical_case_is_checked(self):
        self.only("Miss. Code Ann. § 21-21-3")
        self.m.authority("Bell Atl. Corp. v. Twombly, 550 U.S. 544 (2007)", "source-checked",
                         via="https://example.org/twombly", evidence="opened")
        self.m.conn.commit()
        r = self.m.check(f"The bond is set. {MISS_LIST} (550 U.S. 544).")
        self.assertEqual([(f.code, f.cite) for f in r.findings if f.severity == "BLOCK"],
                         [("authority-not-logged", "Miss. Code Ann. § 21-3-15")])

    def test_stored_list_with_a_range_covers_its_second_section(self):
        self.only("Fla. Stat. §§ 605.0101, 605.0102 - 605.1108")
        r = self.m.check("The company is formed. Fla. Stat. § 605.0102.")
        self.assertEqual(codes(r, "BLOCK"), [])

    def test_trailing_bare_number_blocks_rather_than_passes(self):
        self.only(MISS_LIST)
        r = self.m.check(f"The bond is set. {MISS_LIST} and 5 others.")
        self.assertEqual([(f.code, f.cite) for f in r.findings if f.severity == "BLOCK"],
                         [("authority-not-logged", "Miss. Code Ann. § 5")])

    def test_list_row_and_single_row_for_one_section_warn_duplicate(self):
        self.only(MISS_LIST)
        self.m.authority("Miss. Code Ann. § 21-3-15", "source-checked", via="https://example.org/code", evidence="opened")
        self.m.conn.commit()
        r = self.m.check(f"The bond is set. {MISS_LIST}.")
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertEqual([(f.code, f.cite) for f in r.findings if f.severity == "WARN"],
                         [("authority-duplicate", "Miss. Code Ann. § 21-3-15")])


class TestListMemberBoundaries(unittest.TestCase):
    """A listed section is its number plus short subsection marks; a parenthetical cite after it is its own cite."""

    def test_cite_in_a_parenthetical_after_a_section_is_its_own_cite(self):
        for paren in ("(42 U.S.C. § 1983)", "(see 42 U.S.C. § 1983)"):
            self.assertEqual(statute_cites(f"The bond is set. {MISS_LIST} {paren}."),
                             [("statute:misscode21-21-3", "Miss. Code Ann. §§ 21-21-3"),
                              ("statute:misscode21-3-15", "21-3-15"),
                              ("statute:42usc1983", "42 U.S.C. § 1983")], paren)

    def test_section_display_leaves_out_a_parenthetical(self):
        self.assertEqual([d for _, d, *_ in scan(f"{MISS_LIST} (42 U.S.C. § 1983)")],
                         ["Miss. Code Ann. § 21-21-3", "Miss. Code Ann. § 21-3-15", "42 U.S.C. § 1983"])

    def test_case_or_wl_in_a_parenthetical_keeps_the_section(self):
        for paren, key in (("(550 U.S. 544)", "case:550 us 544"), ("(2019 WL 1234567)", "wl:2019:1234567")):
            self.assertEqual(sorted(k for k, *_ in scan(f"The bond is set. {MISS_LIST} {paren}.")),
                             sorted([key, "statute:misscode21-21-3", "statute:misscode21-3-15"]), paren)

    def test_list_runs_on_past_a_parenthetical_that_is_not_a_cite(self):
        self.assertEqual(statute_cites("Miss. Code Ann. §§ 21-21-3 (Rev. 2019), 21-3-15."),
                         [("statute:misscode21-21-3", "Miss. Code Ann. §§ 21-21-3"),
                          ("statute:misscode21-3-15", "21-3-15")])

    def test_subsection_marks_stay_with_the_section(self):
        self.assertEqual(statute_cites("Miss. Code Ann. §§ 21-21-3, 21-3-15(2)(ii).")[1],
                         ("statute:misscode21-3-15", "21-3-15(2)(ii)"))

    def test_stored_list_with_a_parenthetical_cite_keys_that_cite(self):
        self.assertEqual({k for k in briefcheck.authority_keys(f"{MISS_LIST} (42 U.S.C. § 1983)")
                          if k.startswith("statute")},
                         {"statute:misscode21-21-3", "statute:misscode21-3-15", "statute:42usc1983"})

    def test_numeric_range_does_not_end_the_list(self):
        fla = "Fla. Stat. §§ 605.0101, 605.0102 - 605.1108"
        self.assertEqual(statute_cites(f"The company is formed. {fla}."),
                         [("statute:flastat605.0101", "Fla. Stat. §§ 605.0101"), ("statute:flastat605.0102", "605.0102")])
        self.assertEqual({k for k in briefcheck.authority_keys(fla) if k.startswith("statute")},
                         {"statute:flastat605.0101", "statute:flastat605.0102"})


class TestListsWithAnnotatedHeads(unittest.TestCase):
    """With the false-BLOCK fixes: "Ann." is not part of a key, and a "Code Annotated" head is the code name."""
    SPELLINGS = ("Miss. Code Ann. §§ 21-21-3, 21-3-15", "Miss. Code Annotated §§ 21-21-3, 21-3-15",
                 "Miss. Code §§ 21-21-3, 21-3-15")

    def test_annotated_head_keys_every_listed_section(self):
        self.assertEqual(statute_cites("The rule applies. Miss. Code Annotated §§ 21-21-3, 21-3-15."),
                         [("statute:misscode21-21-3", "Miss. Code Annotated §§ 21-21-3"),
                          ("statute:misscode21-3-15", "21-3-15")])

    def test_ann_spelling_does_not_change_list_keys(self):
        want = ["statute:misscode21-21-3", "statute:misscode21-3-15"]
        for cite in self.SPELLINGS:
            self.assertEqual([k for k, _ in statute_cites(f"The rule applies. {cite}.")], want, cite)
            self.assertEqual(sorted(k for k in briefcheck.authority_keys(cite) if k.startswith("statute")), want, cite)

    def test_annotated_head_skips_a_preceding_abbreviation(self):
        self.assertEqual(statute_cites("Acme, Inc. Miss. Code Annotated §§ 21-21-3, 21-3-15.")[0],
                         ("statute:misscode21-21-3", "Miss. Code Annotated §§ 21-21-3"))

    def test_federal_code_annotated_list_keeps_short_forms(self):
        self.assertEqual([k for k, _ in statute_cites("U.S. Code Annotated §§ 1983, 1988.")],
                         ["statute-short:1983", "statute-short:1988"])

    def test_list_and_section_cross_reference_in_one_sentence(self):
        self.assertEqual(statute_cites("See Miss. Code Ann. §§ 21-21-3, 21-3-15; Section 21-23-7, 21-23-9."),
                         [("statute:misscode21-21-3", "Miss. Code Ann. §§ 21-21-3"),
                          ("statute:misscode21-3-15", "21-3-15"),
                          ("statute-short:section21-23-7", "Section 21-23-7"),
                          ("statute-short:section21-23-9", "21-23-9")])


class TestListsWithAnnotatedHeadsInBriefCheck(Base):
    def test_stored_ann_list_covers_a_draft_annotated_list(self):
        self.m.conn.execute("DELETE FROM authorities")
        self.m.authority(MISS_LIST, "source-checked", via="https://example.org/code", evidence="opened")
        self.m.conn.commit()
        r = self.m.check("The rule applies. Miss. Code Annotated §§ 21-21-3, 21-3-15.")
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertNotIn("short-form-without-full-cite", codes(r, "WARN"))
        self.assertEqual(sorted((a.key, a.store_citation) for a in r.authorities),
                         [("statute:misscode21-21-3", MISS_LIST), ("statute:misscode21-3-15", MISS_LIST)])


class TestListShapes(unittest.TestCase):
    def test_list_followed_by_a_case_or_wl_cite(self):
        for tail, key in ((", 550 U.S. 544", "case:550 us 544"), (", 2019 WL 1234567", "wl:2019:1234567")):
            self.assertEqual(sorted(k for k, *_ in scan(f"The bond is set. {MISS_LIST}{tail}.")),
                             sorted([key, "statute:misscode21-21-3", "statute:misscode21-3-15"]), tail)

    def test_two_lists_in_one_sentence(self):
        self.assertEqual([k for k, _ in statute_cites(f"See {MISS_LIST}; 42 U.S.C. §§ 1983, 1988.")],
                         ["statute:misscode21-21-3", "statute:misscode21-3-15",
                          "statute:42usc1983", "statute:42usc1988"])

    def test_list_across_a_line_break(self):
        self.assertEqual(statute_cites("Miss. Code Ann. §§ 21-21-3,\n21-3-15."),
                         [("statute:misscode21-21-3", "Miss. Code Ann. §§ 21-21-3"),
                          ("statute:misscode21-3-15", "21-3-15")])

    def test_et_seq_keys_the_first_section_only(self):
        self.assertEqual(statute_cites("Miss. Code Ann. §§ 21-21-3 et seq."),
                         [("statute:misscode21-21-3", "Miss. Code Ann. §§ 21-21-3")])


if __name__ == "__main__":
    unittest.main()
