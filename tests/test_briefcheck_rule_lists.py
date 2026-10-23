"""Stored rule lists: "Fed. R. Civ. P. 5(b); 6(a); 15(a)" keys every rule, not just the first.

A ledger cell imported by `authority import` can list several rules after one prefix. Each
semicolon segment that starts with a bare rule number continues the most recent rule prefix.
Statute section lists and case cites are not rule lists.

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

FRCP_LIST = "Fed. R. Civ. P. 5(b)(2)(C), (E); 6(a)(1), (d); 15(a)(1)–(3)"
LOCAL_LIST = "L.U.Civ.R. 7(b)(2), (3)(A)–(E), (4), (5); 15; 16(b)(3)"


def rule_keys(citation):
    return {k for k in briefcheck.authority_keys(citation) if k.startswith("rule:")}


class TestRuleListKeys(unittest.TestCase):
    def test_federal_rule_list_keys_each_rule(self):
        self.assertEqual(rule_keys(FRCP_LIST), {"rule:fedrcivp5", "rule:fedrcivp6", "rule:fedrcivp15"})

    def test_local_rule_list_keys_each_rule_under_the_local_prefix(self):
        self.assertEqual(rule_keys(LOCAL_LIST), {"rule:lucivr7", "rule:lucivr15", "rule:lucivr16"})

    def test_existing_keys_are_kept(self):
        keys = briefcheck.authority_keys(FRCP_LIST)
        self.assertIn("full:" + briefcheck._norm_key(FRCP_LIST), keys)
        self.assertIn("rule:fedrcivp5", keys)

    def test_segment_continues_the_most_recent_rule_prefix(self):
        self.assertEqual(rule_keys("Fed. R. Civ. P. 4(m); Fed. R. Evid. 201; 15"),
                         {"rule:fedrcivp4", "rule:fedrevid201", "rule:fedrevid15"})

    def test_statute_section_list_is_unaffected(self):
        cite = "Miss. Code Ann. §§ 21-21-3, 21-3-15"
        keys = briefcheck.authority_keys(cite)
        self.assertEqual(rule_keys(cite), set())
        self.assertEqual(keys, {k for k, *_ in briefcheck.scan_authorities(cite)} | {"full:" + briefcheck._norm_key(cite)})

    def test_segment_after_a_statute_does_not_inherit_an_earlier_rule_prefix(self):
        self.assertEqual(rule_keys("Fed. R. Civ. P. 4(m); Miss. Code Ann. § 15-1-49; 11"), {"rule:fedrcivp4"})

    def test_segment_after_a_case_does_not_inherit_an_earlier_rule_prefix(self):
        self.assertEqual(rule_keys("Fed. R. Civ. P. 56; Celotex Corp. v. Catrett, 477 U.S. 317, 322 (1986); 15"),
                         {"rule:fedrcivp56"})

    def test_case_and_section_numbers_after_a_rule_are_not_rule_numbers(self):
        self.assertEqual(rule_keys("Fed. R. Civ. P. 6; 2019 WL 1234567"), {"rule:fedrcivp6"})
        self.assertEqual(rule_keys("Fed. R. Civ. P. 6; 21-3-15"), {"rule:fedrcivp6"})


class TestRuleListsInBriefCheck(Base):
    def only(self, cite):
        self.m.conn.execute("DELETE FROM authorities")
        self.m.authority(cite, "source-checked", via="https://example.org/rules", evidence="opened official text")
        self.m.conn.commit()

    def assert_covered_by(self, draft, cite):
        r = self.m.check(draft)
        self.assertNotIn("authority-not-logged", codes(r, "BLOCK"))
        self.assertEqual([(a.store_citation, a.store_status) for a in r.authorities], [(cite, "source-checked")])

    def test_bare_rule_in_draft_matches_a_later_rule_in_a_federal_list(self):
        self.only(FRCP_LIST)
        self.assert_covered_by("Leave is freely given. Rule 15(a)(2).", FRCP_LIST)

    def test_local_rule_in_draft_matches_a_later_rule_in_a_local_list(self):
        self.only(LOCAL_LIST)
        self.assert_covered_by("Exhibits are required. L.U.Civ.R. 15.", LOCAL_LIST)
        self.assert_covered_by("The order governs. L.U.Civ.R. 16(b)(3).", LOCAL_LIST)

    def test_local_list_does_not_cover_the_federal_rule_of_the_same_number(self):
        self.only(LOCAL_LIST)
        r = self.m.check("Leave is freely given. Fed. R. Civ. P. 15(a)(2).")
        self.assertIn("authority-not-logged", codes(r, "BLOCK"))


if __name__ == "__main__":
    unittest.main()
