"""A dot between two digits is part of an authority key: "§ 5.1" is not "§ 51".

`_norm_key` dropped every dot, so "Tex. Gov't Code § 5.1" and "Tex. Gov't Code § 51" shared
statute:texgov'tcode51, and "Fed. R. Civ. P. 5.1" and "Fed. R. Civ. P. 51" shared
rule:fedrcivp51. With "§ 51" logged and source-checked, a draft citing "§ 5.1" passed with exit 0.
The same function builds the `full:` key, so `authority find` and the ledger import merged the
two as well.

A dot is now kept when a digit stands on both sides of it. Every other dot is dropped as before
("U.S.C.", "Fed. R.", a sentence-final period), so a key with no dotted number ("21-23-8",
"1983", "15") is unchanged. Keys are made from the stored citation text on every run, so no
stored data changes.

How this could fail, and the test that catches it:
  1. a dotted statute section still keys as the undotted one   TestStatuteSectionsStayDistinct
  2. a dotted rule number still keys as the undotted one       TestRuleNumbersStayDistinct
  3. the dot moves within the number ("1630.2" / "163.02")      test_dot_position_matters
  4. "5.1" and "5.10" collide on the full-cite path            test_5_1_and_5_10_full_cites_stay_distinct
  5. a dot that is not between digits is now kept, so a cite
     stops matching its own logged row                          TestKeysWithoutADottedNumberAreUnchanged
  6. "Ann." / "Annotated" stops being dropped, or keeps the
     old dot-stripped key                                       TestAnnotatedAndPlainSpellings
  7. `authority find` or the ledger import still merge them    TestStoreLookups
  8. two different sections still warn authority-duplicate,
     or a real duplicate stops warning                          TestDuplicateWarning
  9. a short form logged as "§ 51" answers "§ 5.1" by key, or
     splitting keys turns a tie into one wrong candidate        TestShortFormKeysStayDistinct
 10. a stored "5.1; 6" rule list keys its later rules under
     the dotted rule's number ("5.6")                           TestRuleListAfterADottedRule

Synthetic data only. Run: python3 -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from matterkit import authority, briefcheck  # noqa: E402
from test_briefcheck import Base, codes  # noqa: E402


def cites(report, raw):
    return [c for row in report.rows for c in row.cites if c.kind == "authority" and c.raw == raw]


def unlogged(report):
    return [f.cite for f in report.findings if f.code == "authority-not-logged"]


class DottedKeyBase(Base):
    def log(self, *cites_):
        for cite in cites_:
            self.m.authority(cite, "source-checked", via="https://example.org/official",
                             evidence="opened the official text")
        self.m.conn.commit()

    def assertBlocks(self, draft, cite):
        r = self.m.check(draft)
        self.assertEqual(unlogged(r), [cite], draft)
        self.assertEqual(r.exit_code, 1, draft)
        return r

    def assertPasses(self, draft):
        r = self.m.check(draft)
        self.assertEqual(codes(r, "BLOCK"), [], draft)
        self.assertEqual(r.exit_code, 0, draft)
        return r

    def _ledger(self, rows):
        tmp = tempfile.mkdtemp(dir=os.path.dirname(self.m.dir))
        path = os.path.join(tmp, "ledger.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("| Authority | Status | Source |\n|---|---|---|\n")
            for cite in rows:
                f.write(f"| {cite} | V-LEAD | https://example.org/official |\n")
        return path


class TestStatuteSectionsStayDistinct(DottedKeyBase):
    def test_reported_repro_5_1_does_not_pass_on_a_logged_51(self):
        self.log("Tex. Gov't Code § 51")
        r = self.assertBlocks("The name must differ. Tex. Gov't Code § 5.1.", "Tex. Gov't Code § 5.1")
        self.assertEqual(codes(r, "WARN"), [])

    def test_51_does_not_pass_on_a_logged_5_1(self):
        self.log("Tex. Gov't Code § 5.1")
        self.assertBlocks("The name must differ. Tex. Gov't Code § 51.", "Tex. Gov't Code § 51")

    def test_5_1_and_5_10_full_cites_stay_distinct(self):
        self.log("Tex. Gov't Code § 5.10")
        self.assertBlocks("The name must differ. Tex. Gov't Code § 5.1.", "Tex. Gov't Code § 5.1")

    def test_5_10_does_not_pass_on_a_logged_5_1(self):
        self.log("Tex. Gov't Code § 5.1")
        self.assertBlocks("The name must differ. Tex. Gov't Code § 5.10.", "Tex. Gov't Code § 5.10")

    def test_dotted_section_binds_to_itself_with_the_dot_in_its_key(self):
        self.log("Tex. Gov't Code § 5.1")
        r = self.assertPasses("The name must differ. Tex. Gov't Code § 5.1.")
        [c] = cites(r, "Tex. Gov't Code § 5.1")
        self.assertEqual(c.target, "statute:texgov'tcode5.1")

    def test_dot_position_matters(self):
        self.log("29 C.F.R. § 163.02")
        self.assertBlocks("The term is defined. 29 C.F.R. § 1630.2.", "29 C.F.R. § 1630.2")

    def test_dotted_part_after_hyphens_stays_distinct(self):
        self.log("Miss. Code Ann. § 13-1-41")
        self.assertBlocks("The rule applies. Miss. Code Ann. § 13-1-4.1.", "Miss. Code Ann. § 13-1-4.1")

    def test_keys_differ(self):
        self.assertFalse(briefcheck.authority_keys("Tex. Gov't Code § 5.1")
                         & briefcheck.authority_keys("Tex. Gov't Code § 51"))
        self.assertFalse(briefcheck.authority_keys("Tex. Gov't Code § 5.1")
                         & briefcheck.authority_keys("Tex. Gov't Code § 5.10"))


class TestRuleNumbersStayDistinct(DottedKeyBase):
    def test_frcp_5_1_does_not_pass_on_a_logged_51(self):
        self.log("Fed. R. Civ. P. 51")
        self.assertBlocks("The notice was filed. Fed. R. Civ. P. 5.1.", "Fed. R. Civ. P. 5.1")

    def test_frcp_51_does_not_pass_on_a_logged_5_1(self):
        self.log("Fed. R. Civ. P. 5.1")
        self.assertBlocks("The jury was charged. Fed. R. Civ. P. 51.", "Fed. R. Civ. P. 51")

    def test_frcp_5_1_and_5_10_stay_distinct(self):
        self.log("Fed. R. Civ. P. 5.10")
        self.assertBlocks("The notice was filed. Fed. R. Civ. P. 5.1.", "Fed. R. Civ. P. 5.1")

    def test_bare_rule_5_1_does_not_pass_on_a_logged_frcp_51(self):
        self.log("Fed. R. Civ. P. 51")
        r = self.assertBlocks("The notice was filed under Rule 5.1.", "Rule 5.1")
        [c] = cites(r, "Rule 5.1")
        self.assertEqual(c.target, "rule:fedrcivp5.1")

    def test_local_rule_7_2_does_not_pass_on_a_logged_72(self):
        self.log("L.U.Civ.R. 72")
        self.assertBlocks("The motion is timely. L.U.Civ.R. 7.2.", "L.U.Civ.R. 7.2")
        self.assertBlocks("The motion is timely under Local Rule 7.2.", "Local Rule 7.2")

    def test_state_rule_binds_to_itself_with_the_dot_in_its_key(self):
        self.log("Miss. R. Crim. P. 5.1")
        r = self.assertPasses("Miss. R. Crim. P. 5.1 governs the first appearance.\n\n"
                              "Rule 5.1(b) requires the court to set conditions.\n")
        [c] = cites(r, "Rule 5.1(b)")
        self.assertEqual(c.target, "rule:missrcrimp5.1")

    def test_rule_list_of_5_1_and_51_is_two_authorities(self):
        self.log("Fed. R. Civ. P. 5.1")
        r = self.m.check("The notice and the charge were proper. Fed. R. Civ. P. 5.1 and 51.")
        self.assertEqual(len(unlogged(r)), 1)  # 51 is not logged
        self.assertEqual(r.exit_code, 1)
        self.assertLessEqual({"rule:fedrcivp5.1", "rule:fedrcivp51"}, {u.key for u in r.authorities})

    def test_keys_differ(self):
        self.assertFalse(briefcheck.authority_keys("Fed. R. Civ. P. 5.1")
                         & briefcheck.authority_keys("Fed. R. Civ. P. 51"))
        self.assertFalse(briefcheck.authority_keys("Rule 5.1") & briefcheck.authority_keys("Rule 51"))


class TestKeysWithoutADottedNumberAreUnchanged(DottedKeyBase):
    def test_hyphenated_section_key_is_unchanged(self):
        for cite in ("Miss. Code Ann. § 21-23-8", "Miss. Code § 21-23-8", "Miss. Code Annotated § 21-23-8"):
            self.assertIn("statute:misscode21-23-8", briefcheck.authority_keys(cite), cite)

    def test_hyphenated_section_still_binds_across_spellings(self):
        self.log("Miss. Code Ann. § 21-23-8")
        r = self.assertPasses("The court has jurisdiction. Miss. Code § 21-23-8.")
        [c] = cites(r, "Miss. Code § 21-23-8")
        self.assertEqual(c.target, "statute:misscode21-23-8")

    def test_letter_dots_and_sentence_final_periods_are_still_dropped(self):
        self.assertIn("statute:42usc1983", briefcheck.authority_keys("42 U.S.C. § 1983."))
        self.assertIn("rule:fedrcivp15", briefcheck.authority_keys("Fed. R. Civ. P. 15."))
        self.assertIn("rule:lucivr7", briefcheck.authority_keys("L.U.Civ.R. 7"))
        self.assertIn("statute:texgov'tcode5.1", briefcheck.authority_keys("Tex. Gov't Code § 5.1."))
        self.assertEqual(briefcheck._norm_key("Tex. Gov't Code § 5.1."), briefcheck._norm_key("Tex. Gov't Code § 5.1"))

    def test_norm_key_keeps_only_a_dot_between_digits(self):
        self.assertEqual(briefcheck._norm_key("Fed. R. Civ. P. 5.1"), "fedrcivp5.1")
        self.assertEqual(briefcheck._norm_key("§ 552.101(a)."), "552.101(a)")
        self.assertEqual(briefcheck._norm_key("§ 13-1-4.1"), "13-1-4.1")
        self.assertEqual(briefcheck._norm_key("U.S.C. § 1983"), "usc1983")


class TestAnnotatedAndPlainSpellings(DottedKeyBase):
    def test_ann_and_plain_spellings_of_a_dotted_section_are_one_key(self):
        keys = [briefcheck.authority_keys(c) for c in
                ("Fla. Stat. Ann. § 5.1", "Fla. Stat. § 5.1")]
        self.assertIn("statute:flastat5.1", keys[0] & keys[1])
        keys = [briefcheck.authority_keys(c) for c in
                ("Miss. Code Ann. § 5.1", "Miss. Code Annotated § 5.1", "Miss. Code § 5.1")]
        self.assertIn("statute:misscode5.1", keys[0] & keys[1] & keys[2])

    def test_ann_draft_does_not_pass_on_a_logged_plain_51(self):
        self.log("Fla. Stat. § 51")
        self.assertBlocks("The duty is statutory. Fla. Stat. Ann. § 5.1.", "Fla. Stat. Ann. § 5.1")

    def test_plain_draft_does_not_pass_on_a_logged_ann_51(self):
        self.log("Fla. Stat. Ann. § 51")
        self.assertBlocks("The duty is statutory. Fla. Stat. § 5.1.", "Fla. Stat. § 5.1")

    def test_annotated_draft_does_not_pass_on_a_logged_plain_51(self):
        self.log("Miss. Code § 51")
        r = self.m.check("The duty is statutory. Miss. Code Annotated § 5.1.")
        self.assertEqual(len(unlogged(r)), 1)
        self.assertEqual(r.exit_code, 1)

    def test_ann_and_plain_spellings_of_one_dotted_section_still_bind(self):
        self.log("Fla. Stat. Ann. § 5.1")
        r = self.assertPasses("The duty is statutory. Fla. Stat. § 5.1.")
        [c] = cites(r, "Fla. Stat. § 5.1")
        self.assertEqual(c.target, "statute:flastat5.1")


class TestStoreLookups(DottedKeyBase):
    def test_find_does_not_return_a_different_section(self):
        self.log("Tex. Gov't Code § 5.1", "Fed. R. Civ. P. 5.1")
        self.assertIsNone(authority.find(self.m.conn, "Tex. Gov't Code § 51"))
        self.assertIsNone(authority.find(self.m.conn, "Fed. R. Civ. P. 51"))
        self.assertIsNone(authority.find(self.m.conn, "Tex. Gov't Code § 5.10"))

    def test_find_still_returns_the_same_section_in_another_spelling(self):
        self.log("Fla. Stat. Ann. § 5.1", "Fed. R. Civ. P. 5.1")
        self.assertEqual(authority.find(self.m.conn, "Fla. Stat. § 5.1")["citation"], "Fla. Stat. Ann. § 5.1")
        self.assertEqual(authority.find(self.m.conn, "Fed. R. Civ. P. 5.1(b)")["citation"], "Fed. R. Civ. P. 5.1")

    def test_find_is_not_ambiguous_between_5_1_and_51(self):
        self.log("Tex. Gov't Code § 5.1", "Tex. Gov't Code § 51")
        self.assertEqual(authority.find(self.m.conn, "Tex. Gov't Code § 5.1")["citation"], "Tex. Gov't Code § 5.1")
        self.assertEqual(authority.find(self.m.conn, "Tex. Gov't Code § 51")["citation"], "Tex. Gov't Code § 51")

    def test_ledger_import_keeps_5_1_and_51_as_two_rows(self):
        path = self._ledger(["Tex. Gov't Code § 5.1", "Tex. Gov't Code § 51",
                             "Fed. R. Civ. P. 5.1", "Fed. R. Civ. P. 51"])
        planned, skipped = authority.import_ledger(self.m.conn, self.m.dir, path)
        self.assertEqual([r["citation"] for r in planned],
                         ["Tex. Gov't Code § 5.1", "Tex. Gov't Code § 51", "Fed. R. Civ. P. 5.1", "Fed. R. Civ. P. 51"])
        self.assertEqual(skipped, [])

    def test_ledger_import_does_not_call_a_different_section_already_in_store(self):
        self.log("Tex. Gov't Code § 51")
        path = self._ledger(["Tex. Gov't Code § 5.1"])
        planned, skipped = authority.import_ledger(self.m.conn, self.m.dir, path)
        self.assertEqual([r["citation"] for r in planned], ["Tex. Gov't Code § 5.1"])
        self.assertEqual(skipped, [])

    def test_ledger_import_still_skips_a_true_duplicate(self):
        path = self._ledger(["Fla. Stat. Ann. § 5.1", "Fla. Stat. § 5.1"])
        planned, skipped = authority.import_ledger(self.m.conn, self.m.dir, path)
        self.assertEqual([r["citation"] for r in planned], ["Fla. Stat. Ann. § 5.1"])
        self.assertEqual([r["reason"] for r in skipped], ["duplicate within the ledger"])


class TestShortFormKeysStayDistinct(DottedKeyBase):
    """A short form keys as `statute-short:` plus the section, so a store entry logged as a bare
    "§ 51" answered a draft's "§ 5.1" by key, with no section check (SWE-2 round-2 P3 #4 on the
    dotted-section stack). A short form with no full cite earlier binds through
    `statute_sections` (design B); the tie cases are SWE-2's P2 on this branch."""

    def test_bare_5_1_does_not_pass_on_a_logged_bare_51(self):
        self.log("§ 51")
        self.assertBlocks("Under § 5.1, the name must differ.", "§ 5.1")

    def test_bare_51_does_not_pass_on_a_logged_bare_5_1(self):
        self.log("§ 5.1")
        self.assertBlocks("Under § 51, the name must differ.", "§ 51")

    def test_section_word_5_1_does_not_pass_on_a_logged_section_51(self):
        self.log("Section 51")
        self.assertBlocks("Under Section 5.1, the name must differ.", "Section 5.1")

    def test_bare_5_1_still_answers_to_a_logged_bare_5_1(self):
        self.log("§ 5.1")
        r = self.m.check("Under § 5.1, the name must differ.")
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertEqual(r.exit_code, 0)
        [c] = cites(r, "§ 5.1")
        self.assertEqual(c.target, "statute-short:5.1")

    def test_5_1_binds_neither_15_1_nor_51_when_both_are_logged(self):
        self.log("Tex. Bus. Orgs. Code § 15.1", "Tex. Gov't Code § 51")
        r = self.assertBlocks("The name must be distinct under Section 5.1.", "Section 5.1")
        [c] = cites(r, "Section 5.1")
        self.assertNotIn(c.target, ("statute:texbusorgscode15.1", "statute:texgov'tcode51"))

    def test_51_binds_neither_151_nor_5_1_when_both_are_logged(self):
        self.log("Tex. Gov't Code § 5.1", "Tex. Bus. Orgs. Code § 151")
        r = self.assertBlocks("The name must be distinct under Section 51.", "Section 51")
        [c] = cites(r, "Section 51")
        self.assertNotIn(c.target, ("statute:texbusorgscode151", "statute:texgov'tcode5.1"))

    def test_5_1_binds_the_one_logged_5_1(self):
        self.log("Tex. Bus. Orgs. Code § 5.1", "Tex. Gov't Code § 51")
        r = self.m.check("The name must be distinct under Section 5.1.")
        self.assertEqual(codes(r, "BLOCK"), [])
        [c] = cites(r, "Section 5.1")
        self.assertEqual(c.target, "statute:texbusorgscode5.1")


def rule_keys(citation):
    return {k for k in briefcheck.authority_keys(citation) if k.startswith("rule:")}


class TestRuleListAfterADottedRule(DottedKeyBase):
    """SWE-2 round 2, P2: a stored "Fed. R. Civ. P. 5.1; 6" keys each ';' segment by the prefix of
    the rule before it, and that prefix was the key minus its last digits. With the dot kept,
    "rule:fedrcivp5.1" left "rule:fedrcivp5.", so the list keyed rule:fedrcivp5.6 and never
    rule:fedrcivp6: FRCP 6 BLOCKed and an unlogged FRCP 5.6 passed."""

    def test_each_segment_after_a_dotted_rule_keys_as_its_own_rule(self):
        for cite, want in (
                ("Fed. R. Civ. P. 5.1; 6", {"rule:fedrcivp5.1", "rule:fedrcivp6"}),
                ("Fed. R. Civ. P. 5.1; 6; 7", {"rule:fedrcivp5.1", "rule:fedrcivp6", "rule:fedrcivp7"}),
                ("Fed. R. Civ. P. 5.1; 6.1", {"rule:fedrcivp5.1", "rule:fedrcivp6.1"}),
                ("L.U.Civ.R. 7.2; 15", {"rule:lucivr7.2", "rule:lucivr15"}),
                ("Fed. R. Civ. P. 6; 5.1", {"rule:fedrcivp6", "rule:fedrcivp5.1"}),
                ("Fed. R. Civ. P. 5(b); 6(a), (d); 15", {"rule:fedrcivp5", "rule:fedrcivp6", "rule:fedrcivp15"})):
            self.assertEqual(rule_keys(cite), want, cite)

    def test_a_listed_rule_passes_and_an_invented_one_blocks(self):
        self.log("Fed. R. Civ. P. 5.1; 6")
        self.assertPasses("The deadline passed. Fed. R. Civ. P. 6.")
        self.assertPasses("The notice was filed. Fed. R. Civ. P. 5.1.")
        self.assertBlocks("The standard differs. Fed. R. Civ. P. 5.6.", "Fed. R. Civ. P. 5.6")

    def test_find_answers_the_listed_rule_only(self):
        self.log("Fed. R. Civ. P. 5.1; 6")
        self.assertEqual(authority.find(self.m.conn, "Fed. R. Civ. P. 6")["citation"], "Fed. R. Civ. P. 5.1; 6")
        self.assertIsNone(authority.find(self.m.conn, "Fed. R. Civ. P. 5.6"))

    def test_ledger_import_does_not_call_an_invented_rule_a_duplicate(self):
        self.log("Fed. R. Civ. P. 5.1; 6")
        path = self._ledger(["Fed. R. Civ. P. 5.6"])
        planned, skipped = authority.import_ledger(self.m.conn, self.m.dir, path)
        self.assertEqual([r["citation"] for r in planned], ["Fed. R. Civ. P. 5.6"])
        self.assertEqual(skipped, [])

    def test_ledger_import_keeps_a_list_and_an_invented_rule_as_two_rows(self):
        path = self._ledger(["Fed. R. Civ. P. 5.1; 6", "Fed. R. Civ. P. 5.6"])
        planned, skipped = authority.import_ledger(self.m.conn, self.m.dir, path)
        self.assertEqual([r["citation"] for r in planned], ["Fed. R. Civ. P. 5.1; 6", "Fed. R. Civ. P. 5.6"])
        self.assertEqual(skipped, [])


class TestDuplicateWarning(DottedKeyBase):
    def test_5_1_and_51_rows_are_not_duplicates(self):
        self.log("Tex. Gov't Code § 5.1", "Tex. Gov't Code § 51")
        r = self.assertPasses("The name must differ. Tex. Gov't Code § 51.")
        self.assertNotIn("authority-duplicate", codes(r, "WARN"))
        [c] = cites(r, "Tex. Gov't Code § 51")
        self.assertEqual(c.target, "statute:texgov'tcode51")

    def test_ann_and_plain_rows_of_one_dotted_section_still_warn(self):
        self.log("Fla. Stat. Ann. § 5.1", "Fla. Stat. § 5.1")
        r = self.m.check("The duty is statutory. Fla. Stat. § 5.1.")
        self.assertIn("authority-duplicate", codes(r, "WARN"))


if __name__ == "__main__":
    unittest.main()
