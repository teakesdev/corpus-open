"""Rules packs from outline-numbered sources ("A.", "1.", "i."), with `--markers dotted`.

How this could fail, and the test that would catch it:
- a dotted marker is anchored at the wrong level, or a list restart under a new parent collides ->
  test_levels_follow_the_outline
- a wrapped line that starts with a capital and a period ("R. CIV. P. 5.") becomes a subsection ->
  test_a_wrapped_line_is_not_a_subsection
- a number out of sequence becomes a subsection -> test_out_of_sequence_is_text_with_a_note
- the default (parenthesized) build changes -> test_the_default_build_is_unchanged
- the word check no longer holds -> every build here passes it or the build raises

Synthetic text only. Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from matterkit import rules  # noqa: E402
from test_rules_attach import LOCAL, Base  # noqa: E402

HEADING = r"^Section\s+(?P<num>\d+)\.\s+(?P<title>[A-Z].*)$"

OUTLINE = "\n".join([
    "Section 1. INTRODUCTION",
    "    These procedures govern filing.",
    "    A.    Authority",
    "          The court authorizes electronic filing.",
    "    B.    Participants",
    "          1.    Attorneys register with the system.",
    "          2.    Parties without counsel file on paper, as FED.",
    "R. CIV. P. 5 provides.",
    "          3.    Service follows the rules:",
    "                (a)   by mail;",
    "                (b)   by hand.",
    "C.   Training",
    "    Training is offered monthly.",
    "Section 2. REGISTRATION",
    "    A.    Eligibility",
    "          1.    Members of the bar may register.",
    "    B.    Passwords",
    "          i.    A password is personal.",
    "          ii.   It may be changed at any time.",
    "Section 3. ELECTRONIC SERVICE",
    "    A.    Service",
    "          1.    The notice of electronic filing is service.",
    "          5.    A stray number in the text.",
    "    B.    Certificate",
    "          A certificate of service is still required.",
]) + "\n"


def build(text=OUTLINE, **kw):
    kw.setdefault("heading_re", HEADING)
    kw.setdefault("marker_style", "dotted")
    kw.setdefault("unit", "Section")
    return rules.build_text(text, "Admin. P.", "admp", **kw)


class TestOutline(unittest.TestCase):
    def test_levels_follow_the_outline(self):
        b = build()
        self.assertEqual(b.anchors, [
            "admp-1", "admp-1-A", "admp-1-B", "admp-1-B-1", "admp-1-B-2", "admp-1-B-3", "admp-1-B-3-a",
            "admp-1-B-3-b", "admp-1-C", "admp-2", "admp-2-A", "admp-2-A-1", "admp-2-B", "admp-2-B-i",
            "admp-2-B-ii", "admp-3", "admp-3-A", "admp-3-A-1", "admp-3-B"])
        self.assertIn('<a id="admp-1-B-3-a"></a>**[1(B)(3)(a)]** (a) by mail;', b.body)
        self.assertIn('<a id="admp-1-C"></a>**[1(C)]** C. Training', b.body)

    def test_headings_use_the_unit_word(self):
        b = build()
        self.assertIn("## Section 2. REGISTRATION", b.body)
        self.assertFalse([l for l in b.body if l.startswith("## Rule")])

    def test_a_wrapped_line_is_not_a_subsection(self):
        b = build()
        self.assertIn("R. CIV. P. 5 provides.", b.body)
        self.assertTrue(any("R. on page 1 is out of sequence" in n for n in b.notes), b.notes)

    def test_out_of_sequence_is_text_with_a_note(self):
        b = build()
        self.assertNotIn("admp-3-A-5", b.anchors)
        self.assertTrue(any("Section 3: 5. on page 1 is out of sequence" in n for n in b.notes), b.notes)

    def test_words_are_unchanged(self):
        b = build()
        self.assertEqual(rules.body_words(b.body), OUTLINE.split())

    def test_the_default_build_is_unchanged(self):
        default = rules.build_text(LOCAL, "L.U.Civ.R.")
        explicit = rules.build_text(LOCAL, "L.U.Civ.R.", marker_style="paren", unit="Rule")
        self.assertEqual(default.body, explicit.body)
        self.assertEqual(default.notes, explicit.notes)
        self.assertIn("## Rule 7. MOTIONS", default.body)

    def test_unknown_marker_style_is_refused(self):
        with self.assertRaises(rules.RulesError):
            build(marker_style="roman")


class TestOutlinePack(Base):
    def test_build_attach_and_show_a_dotted_pack(self):
        src = os.path.join(self._tmp.name, "procedures.txt")
        with open(src, "w", encoding="utf-8") as f:
            f.write(OUTLINE)
        out = os.path.join(self._tmp.name, "procedures")
        built = self.cli("rules", "build", src, out, "--id", "example-procedures", "--version", "2030-03-01",
                         "--title", "Example Procedures", "--cite-prefix", "Admin. P.", "--anchor-prefix", "admp",
                         "--current-as-of", "2030-03-01", "--heading-regex", HEADING, "--markers", "dotted",
                         "--unit", "Section", "--no-pdftotext")
        self.assertIn("19 anchors", built.stdout)
        with open(os.path.join(out, "manifest.json"), encoding="utf-8") as f:
            m = json.load(f)
        self.assertEqual((m["region"]["markers"], m["region"]["unit"]), ("dotted", "Section"))
        self.attach(out)
        shown = self.cli("rules", "show", self.dir, "Admin. P. 1(B)(3)(a)", "--today", "2030-06-01").stdout
        self.assertIn("Admin. P. 1(B)(3)(a) · pack example-procedures@2030-03-01", shown)
        self.assertIn("(a) by mail;", shown)

    def test_a_default_manifest_has_no_marker_keys(self):
        src = os.path.join(self._tmp.name, "local.txt")
        with open(src, "w", encoding="utf-8") as f:
            f.write(LOCAL)
        out = os.path.join(tempfile.mkdtemp(dir=self._tmp.name), "pack")
        m = rules.build_pack(src, out, pack_id="example-local-2", version="1", title="Local", cite_prefix="L.U.Civ.R.")
        self.assertNotIn("markers", m["region"])
        self.assertNotIn("unit", m["region"])


if __name__ == "__main__":
    unittest.main()
