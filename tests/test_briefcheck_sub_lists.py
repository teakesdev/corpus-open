"""Brief check: a rule list whose later members are subsections of the same rule, "7(b) and (c)".

The rule-list tail read only members that start with a rule number ("7(b) and 15"), so "(c)" in
"L.U.Civ.R. 7(b) and (c)" was never a cite: a pack without 7(c) gave no warning (SWE-2's F8 on the
rules pack). A member that is only subsection marks now stands for a sibling of the last cite's
subsection of the same kind: "7(b) and (c)" is 7(c), "7(b)(1) and (2)" is 7(b)(2).

How this could fail, and the test that would catch it:
- a sibling is read at the wrong level -> test_siblings_replace_the_mark_of_their_kind
- a roman sibling is read as a letter, or a letter as a roman -> test_romans_and_letters
- a member after a sibling is lost -> test_a_list_mixes_rules_and_siblings
- a mark with no kind to replace is invented as a cite -> test_no_subsection_to_replace_reads_nothing
- prose after a semicolon becomes a cite -> test_a_semicolon_ends_the_list
- the pack check still misses the sibling -> test_a_sibling_missing_from_the_pack_warns

Synthetic data only. Run: python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from matterkit import briefcheck  # noqa: E402
from test_briefcheck_rules_pack import PackBase  # noqa: E402


def cites(text):
    return [disp for key, disp, *_ in briefcheck.scan_authorities(text) if key.startswith("rule:")]


class TestSiblings(unittest.TestCase):
    def test_siblings_replace_the_mark_of_their_kind(self):
        self.assertEqual(cites("Fed. R. Civ. P. 7(b) and (c)."), ["Fed. R. Civ. P. 7(b)", "Fed. R. Civ. P. 7(c)"])
        self.assertEqual(cites("L.U.Civ.R. 7(b)(1) and (2)."), ["L.U.Civ.R. 7(b)(1)", "L.U.Civ.R. 7(b)(2)"])
        self.assertEqual(cites("L.U.Civ.R. 7(b)(1) and (c)."), ["L.U.Civ.R. 7(b)(1)", "L.U.Civ.R. 7(c)"])
        self.assertEqual(cites("Fed. R. Civ. P. 15(a)(1)(B) and (C)."),
                         ["Fed. R. Civ. P. 15(a)(1)(B)", "Fed. R. Civ. P. 15(a)(1)(C)"])
        self.assertEqual(cites("Fed. R. Civ. P. 12(b)(1) or (6)."),
                         ["Fed. R. Civ. P. 12(b)(1)", "Fed. R. Civ. P. 12(b)(6)"])

    def test_romans_and_letters(self):
        self.assertEqual(cites("Fed. R. Civ. P. 26(a)(1)(A)(i), (ii), and (iv)."),
                         ["Fed. R. Civ. P. 26(a)(1)(A)(i)", "Fed. R. Civ. P. 26(a)(1)(A)(ii)",
                          "Fed. R. Civ. P. 26(a)(1)(A)(iv)"])
        self.assertEqual(cites("Fed. R. Civ. P. 26(a)(1)(A)(i) and (c)."),
                         ["Fed. R. Civ. P. 26(a)(1)(A)(i)", "Fed. R. Civ. P. 26(c)"])
        self.assertEqual(cites("Fed. R. Civ. P. 4(h) and (i)."), ["Fed. R. Civ. P. 4(h)", "Fed. R. Civ. P. 4(i)"])
        self.assertEqual(cites("Fed. R. Civ. P. 4(i)(1) and (2)."),
                         ["Fed. R. Civ. P. 4(i)(1)", "Fed. R. Civ. P. 4(i)(2)"])

    def test_a_list_mixes_rules_and_siblings(self):
        self.assertEqual(cites("L.U.Civ.R. 7(b) and (c) and 15(a)."),
                         ["L.U.Civ.R. 7(b)", "L.U.Civ.R. 7(c)", "L.U.Civ.R. 15(a)"])
        self.assertEqual(cites("L.U.Civ.R. 7(b)(4) and 15(a) and (b)."),
                         ["L.U.Civ.R. 7(b)(4)", "L.U.Civ.R. 15(a)", "L.U.Civ.R. 15(b)"])

    def test_spelled_out_and_bare_rules(self):
        got = cites("Local Rule 7(b) and (c) apply.")
        self.assertEqual(len(got), 2)
        self.assertEqual(got[1], "L.U.Civ.R. 7(c)")
        self.assertEqual(cites("Rule 12(b)(1) and (6) apply."), ["Rule 12(b)(1)", "Rule 12(b)(6)"])

    def test_no_subsection_to_replace_reads_nothing(self):
        self.assertEqual(cites("L.U.Civ.R. 7 and (c) apply."), ["L.U.Civ.R. 7"])
        self.assertEqual(cites("L.U.Civ.R. 7(b) and (2) apply."), ["L.U.Civ.R. 7(b)"])

    def test_a_semicolon_ends_the_list(self):
        self.assertEqual(cites("Plaintiffs rely on Fed. R. Civ. P. 12(b)(6); and (c) the order."),
                         ["Fed. R. Civ. P. 12(b)(6)"])

    def test_keys_are_the_rule_not_the_subsection(self):
        keys = [k for k, *_ in briefcheck.scan_authorities("L.U.Civ.R. 7(b) and (c).") if k.startswith("rule:")]
        self.assertEqual(keys, ["rule:lucivr7", "rule:lucivr7"])


class TestSiblingInPack(PackBase):
    def test_a_sibling_missing_from_the_pack_warns(self):
        self.attach(self.local)
        r = self.m.check("Both apply. L.U.Civ.R. 7(b) and (z).\n")
        [w] = self.pack_warnings(r)
        self.assertEqual((w.severity, w.code), ("WARN", "rule-subsection-not-in-pack"))
        self.assertIn("L.U.Civ.R. 7(z)", w.message)

    def test_a_sibling_in_the_pack_is_listed(self):
        self.attach(self.local)
        r = self.m.check("Both apply. L.U.Civ.R. 7(b) and (c).\n")
        self.assertEqual(self.pack_warnings(r), [])
        self.assertIn("rules.md#lucivr-7-c", r.render())


if __name__ == "__main__":
    unittest.main()
