"""A hard wrap with trailing whitespace inside a cite still reads as that cite.

Editors often leave a space before a line break, and Markdown's hard break is two spaces and a newline.
The rule and reporter patterns allowed one whitespace character between the parts of an abbreviation
("Fed.\\s?R.", "F.\\s?Supp."), so "Fed. \\nR. Civ. P. 12" and "100 F. \\nSupp. 2d 200" matched nothing:
an unlogged cite in a wrapped draft passed with no finding. "100 F. Supp. \\n2d 200" read as the wrong case
("100 F. Supp. 2"). Any run of whitespace now joins the parts.

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

WRAPS = [" \n", "  \n", "\t\n", " \r\n"]
RULES = {  # each "{}" is where a wrap falls
    "Fed.{}R. Civ. P. 12": "rule:fedrcivp12",
    "Fed. R.{}Civ. P. 12": "rule:fedrcivp12",
    "Fed. R. Civ.{}P. 12": "rule:fedrcivp12",
    "Fed. R.{}Evid. 401": "rule:fedrevid401",
    "Fed. R. App.{}P. 4": "rule:fedrappp4",
    "Tex.{}R. Civ. P. 91": "rule:texrcivp91",
    "L.{}U.{}Civ.{}R. 7": "rule:lucivr7",
}
CASES = {
    "100 F.{}Supp. 2d 200": "case:100 fsupp2d 200",
    "100 F. Supp.{}2d 200": "case:100 fsupp2d 200",
    "100 F.{}Supp. 200": "case:100 fsupp 200",
    "550 U.{}S. 544": "case:550 us 544",
    "1 S.{}Ct. 2": "case:1 sct 2",
    "1 L.{}Ed. 2d 2": "case:1 led2d 2",
    "5 F.{}3d 9": "case:5 f3d 9",
    "5 So.{}2d 9": "case:5 so2d 9",
    "5 S.W.{}3d 9": "case:5 sw3d 9",
    "5 Fed.{}App'x 9": "case:5 fedappx 9",
}


def keys(text):
    return [k for k, *_ in briefcheck.scan_authorities(text)]


class TestWhitespaceRunInsideACite(unittest.TestCase):
    def test_rule_parts_join_across_any_whitespace_run(self):
        for form, key in RULES.items():
            for w in WRAPS:
                with self.subTest(cite=form.replace("{}", w)):
                    self.assertEqual(keys(form.replace("{}", w)), [key])

    def test_reporter_parts_join_across_any_whitespace_run(self):
        for form, key in CASES.items():
            for w in WRAPS:
                with self.subTest(cite=form.replace("{}", w)):
                    self.assertEqual(keys(form.replace("{}", w)), [key])

    def test_short_form_reporter_joins_too(self):
        self.assertEqual(keys("550 U. \nS. at 556"), ["case-short:550 us"])

    def test_unwrapped_cites_are_unchanged(self):
        for form, key in {**RULES, **CASES}.items():
            for w in (" ", ""):
                if not w and form.startswith("100 F.{}Supp. 2"):
                    continue  # "F.Supp." with no space is its own spelling
                with self.subTest(cite=form.replace("{}", w)):
                    self.assertEqual(keys(form.replace("{}", w)), [key])


class TestWrappedDraft(Base):
    def test_unlogged_rule_wrapped_with_a_trailing_space_blocks(self):
        r = self.m.check("The motion is timely under Fed. \nR. Civ. P. 12 as the order notes. Doc. 16.")
        self.assertIn("authority-not-logged", codes(r, "BLOCK"))

    def test_markdown_hard_break_inside_a_rule_blocks(self):
        r = self.m.check("The motion is timely under Fed. R.  \nCiv. P. 12 as the order notes. Doc. 16.")
        self.assertIn("authority-not-logged", codes(r, "BLOCK"))

    def test_unlogged_case_wrapped_inside_its_reporter_blocks(self):
        r = self.m.check("The court so held. Doe v. Roe, 100 F. \nSupp. 2d 200 (S.D. Miss. 2000). Doc. 16.")
        self.assertIn("authority-not-logged", codes(r, "BLOCK"))
        self.assertEqual([a.key for a in r.authorities], ["case:100 fsupp2d 200"])

    def test_logged_rule_wrapped_with_a_trailing_space_binds(self):
        r = self.m.check("The rule allows it. Fed. R. \nCiv. P. 15. Doc. 16.")
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertEqual([a.store_citation for a in r.authorities], ["Fed. R. Civ. P. 15"])


if __name__ == "__main__":
    unittest.main()
