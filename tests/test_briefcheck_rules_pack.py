"""Brief check with rules packs attached: a rule cite's subsection is looked up in the pack.

A subsection the pack does not have is a warning; what blocks is unchanged (a rule cite still
needs a logged, source-checked authority). With no pack attached the report is as before.

Synthetic data only. Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from matterkit import rules  # noqa: E402
from test_briefcheck import Base, codes  # noqa: E402
from test_rules_attach import FEDERAL, LOCAL, make_pack  # noqa: E402


class PackBase(Base):
    def setUp(self):
        super().setUp()
        root = self._tmp.name
        self.local = make_pack(root, "local-pack", LOCAL, pack_id="example-local", version="2030-01-01",
                               title="Example Local Rules", cite_prefix="L.U.Civ.R.", current_as_of="2030-01-01")
        self.federal = make_pack(root, "federal-pack", FEDERAL, pack_id="example-frcp", version="2030-12-01",
                                 title="Example Federal Rules", cite_prefix="Fed. R. Civ. P.",
                                 current_as_of="2030-12-01")

    def attach(self, *packs):
        for p in packs:
            rules.attach(self.m.dir, p, "human")

    def pack_warnings(self, report):
        return [f for f in report.findings if f.code.startswith(("rule-", "rules-"))]


class TestRuleText(PackBase):
    def test_subsection_in_the_pack_adds_nothing_and_is_listed(self):
        draft = "Responses are due fourteen days after service. L.U.Civ.R. 7(b)(4).\n"
        before = self.m.check(draft)
        self.attach(self.local)
        after = self.m.check(draft)
        self.assertEqual([(f.severity, f.code) for f in before.findings], [(f.severity, f.code) for f in after.findings])
        out = after.render()
        self.assertIn("- Rules packs: `example-local@2030-01-01` (L.U.Civ.R., current as of 2030-01-01", out)
        self.assertIn("## Rule text in attached packs", out)
        self.assertIn("rules.md#lucivr-7-b-4", out)

    def test_subsection_not_in_the_pack_warns_and_blocks_nothing_new(self):
        draft = "Responses are due fourteen days after service. L.U.Civ.R. 7(b)(9).\n"
        before = self.m.check(draft)
        self.attach(self.local)
        after = self.m.check(draft)
        self.assertEqual(after.exit_code, before.exit_code)
        self.assertEqual(codes(after, "BLOCK"), codes(before, "BLOCK"))
        self.assertEqual(codes(after, "UNCHECKED"), codes(before, "UNCHECKED"))
        [w] = self.pack_warnings(after)
        self.assertEqual((w.severity, w.code), ("WARN", "rule-subsection-not-in-pack"))
        self.assertIn("example-local@2030-01-01 has no L.U.Civ.R. 7(b)(9)", w.message)
        self.assertIn("nearest text it has is L.U.Civ.R. 7(b)", w.message)

    def test_rule_not_in_the_pack(self):
        self.attach(self.local)
        r = self.m.check("The rule says so. L.U.Civ.R. 99(a).\n")
        self.assertIn("rule-not-in-pack", codes(r, "WARN"))
        self.assertIn("authority-not-logged", codes(r, "BLOCK"))  # unchanged: the authority is not logged

    def test_a_rule_list_checks_each_member(self):
        self.attach(self.local)
        r = self.m.check("Both apply. L.U.Civ.R. 7(b)(4) and 15(z).\n")
        [w] = self.pack_warnings(r)
        self.assertIn("15(z)", w.message)

    def test_local_rule_in_words(self):
        self.attach(self.local)
        r = self.m.check("Local Rule 7(b)(9) governs the response.\n")
        self.assertIn("rule-subsection-not-in-pack", codes(r, "WARN"))

    def test_bare_rule_is_checked_in_the_rule_set_it_reads_as(self):
        self.attach(self.local, self.federal)
        ok = self.m.check("Leave is freely given. Rule 15(a)(2).\n")
        self.assertEqual(self.pack_warnings(ok), [])
        bad = self.m.check("Leave is freely given. Rule 15(a)(9).\n")
        [w] = self.pack_warnings(bad)
        self.assertIn("example-frcp@2030-12-01 has no Fed. R. Civ. P. 15(a)(9)", w.message)

    def test_cite_to_a_rule_set_with_no_pack_is_not_looked_up(self):
        self.attach(self.local)
        r = self.m.check("Leave is freely given. Fed. R. Civ. P. 15(a)(9).\n")
        self.assertEqual(self.pack_warnings(r), [])


class TestPackState(PackBase):
    def test_no_pack_leaves_the_report_as_it_was(self):
        out = self.m.check("Responses are due. L.U.Civ.R. 7(b)(9).\n").render()
        self.assertNotIn("Rules packs", out)
        self.assertNotIn("Rule text in attached packs", out)

    def test_a_pack_changed_after_attach_is_not_used(self):
        self.attach(self.local)
        path = os.path.join(self.m.dir, "Research", "Rules", "example-local_2030-01-01", "rules.md")
        with open(path, "a", encoding="utf-8") as f:
            f.write("edited\n")
        r = self.m.check("Responses are due. L.U.Civ.R. 7(b)(9).\n")
        self.assertEqual([f.code for f in self.pack_warnings(r)], ["rules-pack-unreadable"])
        self.assertIn("not used", r.render())

    def test_a_malformed_attachment_file_refuses_the_check(self):
        with open(os.path.join(self.m.dir, ".matter", "rules.json"), "w") as f:
            json.dump({"packs": "nope"}, f)
        with self.assertRaises(ValueError):
            self.m.check("Responses are due. L.U.Civ.R. 7(b)(4).\n")

    def test_a_stale_pack_says_so_in_the_header(self):
        self.attach(self.local)
        out = self.m.check("Responses are due. L.U.Civ.R. 7(b)(4).\n").render()
        self.assertRegex(out, r"Rules packs: .*\[(STALE|ok)")


if __name__ == "__main__":
    unittest.main()
