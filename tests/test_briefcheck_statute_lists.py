"""Stored statute lists: "Miss. Code Ann. §§ 21-21-3, 21-3-15" keys every section under its code.

A ledger cell imported by `authority import` can list several sections after one code name. Each
listed section keys as the single cite "Miss. Code Ann. § 21-3-15" would, so a draft citing either
section matches the row. A neighbouring section does not, and single-section keys are unchanged.

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

MISS_LIST = "Miss. Code Ann. §§ 21-21-3, 21-3-15"


def statute_keys(citation):
    return {k for k in briefcheck.authority_keys(citation) if k.startswith("statute")}


def draft_keys(text):
    return {k for k, *_ in briefcheck.scan_authorities(text)}


class TestStatuteListKeys(unittest.TestCase):
    def test_each_listed_section_keys_under_the_code_name(self):
        self.assertEqual(statute_keys(MISS_LIST), {"statute:misscodeann21-21-3", "statute:misscodeann21-3-15"})

    def test_listed_sections_key_as_the_draft_single_cites_do(self):
        keys = briefcheck.authority_keys(MISS_LIST)
        self.assertLessEqual(draft_keys("Miss. Code Ann. § 21-21-3"), keys)
        self.assertLessEqual(draft_keys("Miss. Code Ann. § 21-3-15"), keys)

    def test_neighbouring_section_is_not_keyed(self):
        self.assertFalse(draft_keys("Miss. Code Ann. § 21-3-16") & briefcheck.authority_keys(MISS_LIST))

    def test_full_string_key_is_kept(self):
        self.assertIn("full:" + briefcheck._norm_key(MISS_LIST), briefcheck.authority_keys(MISS_LIST))

    def test_and_separator_and_subsections(self):
        self.assertEqual(statute_keys("Miss. Code Ann. §§ 21-21-3 and 21-3-15"),
                         {"statute:misscodeann21-21-3", "statute:misscodeann21-3-15"})
        self.assertEqual(statute_keys("42 U.S.C. §§ 1983, 1985(3), 1988(b)"),
                         {"statute:42usc1983", "statute:42usc1985", "statute:42usc1988"})

    def test_code_name_does_not_carry_across_segments(self):
        self.assertEqual(statute_keys("28 U.S.C. § 1331; 42 U.S.C. §§ 1983, 1988"),
                         {"statute:28usc1331", "statute:42usc1983", "statute:42usc1988"})

    def test_list_with_no_code_name_keys_short_forms(self):
        self.assertEqual(statute_keys("§§ 21-21-3, 21-3-15"), {"statute-short:21-21-3", "statute-short:21-3-15"})

    def test_single_section_keys_are_unchanged(self):
        self.assertEqual(briefcheck.authority_keys("42 U.S.C. § 1983"), {"statute:42usc1983", "full:42usc1983"})
        self.assertEqual(briefcheck.authority_keys("Miss. Code Ann. § 21-3-15"),
                         {"statute:misscodeann21-3-15", "full:misscodeann21-3-15"})

    def test_draft_scanning_is_unchanged(self):
        self.assertEqual(draft_keys("Miss. Code Ann. § 21-21-3"), {"statute:misscodeann21-21-3"})
        self.assertEqual(draft_keys("Miss. Code Ann. § 21-3-15"), {"statute:misscodeann21-3-15"})
        self.assertEqual(draft_keys("42 U.S.C. § 1983"), {"statute:42usc1983"})


class TestStatuteListsInBriefCheck(Base):
    def only(self, cite):
        self.m.conn.execute("DELETE FROM authorities")
        self.m.authority(cite, "source-checked", via="https://example.org/code", evidence="opened official text")
        self.m.conn.commit()

    def assert_covered_by(self, draft, cite):
        r = self.m.check(draft)
        self.assertNotIn("authority-not-logged", codes(r, "BLOCK"))
        self.assertEqual([(a.store_citation, a.store_status) for a in r.authorities], [(cite, "source-checked")])

    def test_list_covers_the_first_listed_section(self):
        self.only(MISS_LIST)
        self.assert_covered_by("The board sets the rate. Miss. Code Ann. § 21-21-3.", MISS_LIST)

    def test_list_covers_the_second_listed_section(self):
        self.only(MISS_LIST)
        self.assert_covered_by("The officer takes the bond. Miss. Code Ann. § 21-3-15.", MISS_LIST)

    def test_section_short_forms_resolve_to_the_list_row(self):
        self.only(MISS_LIST)
        self.assert_covered_by("The board sets the rate. § 21-21-3.", MISS_LIST)
        self.assert_covered_by("The officer takes the bond. § 21-3-15.", MISS_LIST)

    def test_list_does_not_cover_a_neighbouring_section(self):
        self.only(MISS_LIST)
        r = self.m.check("The clerk keeps the minutes. Miss. Code Ann. § 21-3-16.")
        self.assertIn("authority-not-logged", codes(r, "BLOCK"))

    def test_single_federal_section_still_matches_its_row(self):
        r = self.m.check("The statute creates the remedy. 42 U.S.C. § 1983.")
        self.assertNotIn("authority-not-logged", codes(r, "BLOCK"))
        self.assertEqual([a.store_citation for a in r.authorities], ["42 U.S.C. § 1983"])


class TestStatuteListsInAuthorityLookup(Base):
    def test_find_returns_the_list_row_for_a_listed_section_only(self):
        self.m.authority(MISS_LIST, "source-checked", via="https://example.org/code", evidence="opened")
        self.m.conn.commit()
        self.assertEqual(authority.find(self.m.conn, "Miss. Code Ann. § 21-3-15")["citation"], MISS_LIST)
        self.assertIsNone(authority.find(self.m.conn, "Miss. Code Ann. § 21-3-16"))

    def test_import_skips_a_later_row_for_a_section_the_list_covers(self):
        ledger = os.path.join(self._tmp.name, "ledger.md")
        with open(ledger, "w", encoding="utf-8") as f:
            f.write("| Authority | Source | Status |\n|---|---|---|\n"
                    f"| {MISS_LIST} | official text | V-LEAD |\n"
                    "| Miss. Code Ann. § 21-3-15 | official text | V-LEAD |\n"
                    "| Miss. Code Ann. § 21-3-16 | official text | V-LEAD |\n")
        planned, skipped = authority.import_ledger(self.m.conn, self.m.dir, ledger)
        self.assertEqual([r["citation"] for r in planned], [MISS_LIST, "Miss. Code Ann. § 21-3-16"])
        self.assertEqual([(r["citation"], r["reason"]) for r in skipped],
                         [("Miss. Code Ann. § 21-3-15", "duplicate within the ledger")])


if __name__ == "__main__":
    unittest.main()
