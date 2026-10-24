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
        self.assertEqual(statute_cites(text), [("statute:misscodeann21-21-3", "Miss. Code Ann. §§ 21-21-3"),
                                               ("statute:misscodeann21-3-15", "21-3-15")])

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
                         ("statute:misscodeann21-21-3", "Miss. Code Ann. §§ 21-21-3(1)(a)"))

    def test_list_with_no_code_name_yields_short_forms(self):
        self.assertEqual(statute_cites("These apply. §§ 21-21-3, 21-3-15."), [("statute-short:21-21-3", "§§ 21-21-3"),
                                                                     ("statute-short:21-3-15", "21-3-15")])

    def test_code_name_does_not_carry_across_cites(self):
        self.assertEqual([k for k, _ in statute_cites("28 U.S.C. § 1331; 42 U.S.C. §§ 1983, 1988.")],
                         ["statute:28usc1331", "statute:42usc1983", "statute:42usc1988"])

    def test_single_section_cites_are_unchanged(self):
        self.assertEqual(statute_cites("Miss. Code Ann. § 21-3-15."),
                         [("statute:misscodeann21-3-15", "Miss. Code Ann. § 21-3-15")])
        self.assertEqual(statute_cites("Miss. Code Ann. § 21-21-3 and § 21-3-15"),
                         [("statute:misscodeann21-21-3", "Miss. Code Ann. § 21-21-3"),
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
                         [("statute:misscodeann21-21-3", MISS_LIST, "source-checked"),
                          ("statute:misscodeann21-3-15", MISS_LIST, "source-checked")])
        self.assertEqual(r.exit_code, 0)

    def test_unlogged_listed_section_blocks(self):
        self.only("Miss. Code Ann. § 21-21-3")
        r = self.m.check("# Argument\n\nThe board sets the rate.\nThe officer takes the bond. "
                         f"{MISS_LIST}.\n")
        blocks = [(f.code, f.line, f.cite) for f in r.findings if f.severity == "BLOCK"]
        self.assertEqual(blocks, [("authority-not-logged", 4, "Miss. Code Ann. § 21-3-15")])
        self.assertNotIn("short-form-without-full-cite", codes(r, "WARN"))
        used = {a.key: a.store_citation for a in r.authorities}
        self.assertEqual(used, {"statute:misscodeann21-21-3": "Miss. Code Ann. § 21-21-3",
                                "statute:misscodeann21-3-15": None})

    def test_later_short_form_binds_to_the_listed_section(self):
        self.only(MISS_LIST)
        r = self.m.check(f"The officer takes the bond. {MISS_LIST}. The bond is filed. § 21-3-15.")
        self.assertNotIn("short-form-without-full-cite", codes(r, "WARN"))
        self.assertEqual(codes(r, "BLOCK"), [])
        use = {a.key: a for a in r.authorities}["statute:misscodeann21-3-15"]
        self.assertEqual(len(use.lines), 2)

    def test_id_after_a_list_binds_to_the_last_listed_section(self):
        self.only(MISS_LIST)
        r = self.m.check(f"The officer takes the bond. {MISS_LIST}. The bond is filed. Id.")
        self.assertEqual(codes(r, "BLOCK"), [])
        use = {a.key: a for a in r.authorities}["statute:misscodeann21-3-15"]
        self.assertEqual(len(use.lines), 2)


if __name__ == "__main__":
    unittest.main()
