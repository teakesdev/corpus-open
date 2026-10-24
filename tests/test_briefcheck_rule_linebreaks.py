"""A line break inside a rule cite does not crash brief check.

A hard-wrapped draft can break a rule cite anywhere: "Fed.\\nR. Civ. P. 12", "Fed. R.\\nCiv. P. 12",
"Fed. R. Civ. P.\\n12". The rule patterns allow whitespace there, but the prefix was then cut out of
the match with a "." pattern, which stops at a newline. The match failed and scan_authorities raised
AttributeError, so `brief check` died with a traceback, wrote no report and exited 1 (the code for
blocking findings). A stored citation holding the same break crashed authority_keys, and with it
`authority find`, `authority import` and every check of that matter. Spelled-out local rules
("Local\\nBankruptcy Rule 7") failed the same way.

Synthetic data only. Run: python3 -m unittest discover -s tests -v
"""
import os
import subprocess
import sys
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from matterkit import authority, briefcheck  # noqa: E402
from test_briefcheck import Base, codes  # noqa: E402

BREAKS = {  # a rule cite broken at each place a hard wrap can fall
    "Fed.\nR. Civ. P. 12": "rule:fedrcivp12",
    "Fed. R.\nCiv. P. 12": "rule:fedrcivp12",
    "Fed. R. Civ.\nP. 12": "rule:fedrcivp12",
    "Fed. R. Civ. P.\n12": "rule:fedrcivp12",
    "Fed. R. Civ. P.\r\n12": "rule:fedrcivp12",
    "Tex.\nR. Civ. P. 91": "rule:texrcivp91",
    "Fed. R.\nEvid. 401": "rule:fedrevid401",
    "L.U.Civ.R.\n7": "rule:lucivr7",
    "Local\nBankruptcy Rule 7": "rule:localbankruptcyrule7",
    "Local Admiralty\nRule 3": "rule:localadmiraltyrule3",
}


def rule_keys(found):
    return [k for k, *_ in found if k.startswith("rule:")]


class TestRuleCiteAcrossALineBreak(unittest.TestCase):
    def test_each_break_keys_as_the_unbroken_cite(self):
        for cite, key in BREAKS.items():
            with self.subTest(cite=cite):
                self.assertEqual(rule_keys(briefcheck.scan_authorities(cite)), [key])
                self.assertEqual(rule_keys(briefcheck.scan_authorities(" ".join(cite.split()))), [key])

    def test_a_rule_list_across_a_break_keys_each_rule(self):
        self.assertEqual(rule_keys(briefcheck.scan_authorities("Fed. R. Civ. P.\n12 and 15")),
                         ["rule:fedrcivp12", "rule:fedrcivp15"])
        self.assertEqual(rule_keys(briefcheck.scan_authorities("Fed. R. Civ. P. 12 and\n15")),
                         ["rule:fedrcivp12", "rule:fedrcivp15"])

    def test_a_stored_citation_with_a_break_keys(self):
        keys = briefcheck.authority_keys("Fed. R. Civ. P.\n5(b); 6(a)")
        self.assertEqual({k for k in keys if k.startswith("rule:")}, {"rule:fedrcivp5", "rule:fedrcivp6"})


class TestHardWrappedDraft(Base):
    def test_unlogged_rule_broken_across_lines_blocks(self):
        r = self.m.check("The motion is timely under Fed. R. Civ. P.\n12 as the order notes. Doc. 16.")
        self.assertIn("authority-not-logged", codes(r, "BLOCK"))
        self.assertEqual(r.exit_code, 1)

    def test_logged_rule_broken_across_lines_binds(self):
        for draft in ("The rule allows it. Fed.\nR. Civ. P. 15. Doc. 16.",
                      "The rule allows it. L.U.Civ.R.\n7. Doc. 16."):
            with self.subTest(draft=draft):
                r = self.m.check(draft)
                self.assertEqual(codes(r, "BLOCK"), [])
                self.assertEqual(len(r.authorities), 1)
                self.assertIsNotNone(r.authorities[0].store_citation)

    def test_stored_citation_with_a_break_does_not_stop_the_check(self):
        self.m.authority("Fed. R. Civ. P.\n12", "source-checked", via="https://example.org/frcp",
                         evidence="opened official text of Rule 12")
        self.m.conn.commit()
        r = self.m.check("The motion is timely under Fed. R. Civ. P. 12. Doc. 16.")
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertEqual(authority.find(self.m.conn, "Fed. R. Civ. P. 12")["citation"], "Fed. R. Civ. P.\n12")

    def test_cli_reports_instead_of_crashing(self):
        draft = os.path.join(self._tmp.name, "wrapped.md")
        with open(draft, "w", encoding="utf-8") as f:
            f.write("The motion is timely under Fed. R. Civ. P.\n12 as the order notes. Doc. 16.\n")
        env = dict(os.environ, PATH="")  # no pdftotext: deterministic
        p = subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), "brief", "check", self.m.dir, draft],
                           capture_output=True, text=True, env=env)
        self.assertNotIn("Traceback", p.stderr)
        self.assertEqual(p.returncode, 1)
        self.assertIn("authority-not-logged", p.stdout)


if __name__ == "__main__":
    unittest.main()
