"""Rules pack builder: rule text becomes Markdown with anchors, and its words never change.

Each test names the failure it guards against (design doc
docs/superpowers/specs/2026-10-30-court-rules-pack-design.md, § 4).
Synthetic rules text only. Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import rules  # noqa: E402


def page(*lines, number=None):
    """One page of layout text; the page number, if any, is its last line."""
    out = list(lines)
    if number is not None:
        out += ["", " " * 40 + str(number)]
    return "\n".join(out) + "\n"


# A synthetic local-rules layout: title page and contents (skipped by start_at), a preamble,
# rules with nested subsections, a two-column table page and a forms section (cut by stop_at).
LOCAL = "\f".join([
    page("            EXAMPLE DISTRICT LOCAL RULES",
         "            CURRENT AS OF JANUARY 1, 2030",
         "",
         "TABLE OF CONTENTS",
         "Rule 1.     SCOPE ................ 1",
         "Rule 7.     MOTIONS .............. 1",
         number="i"),
    page("PREAMBLE",
         "These rules are synthetic test text.",
         "",
         "Rule 1.     SCOPE OF RULES",
         "(a)   Title. These are the example rules.",
         "(b)   Scope. They govern nothing real.",
         "Rule 7.     MOTIONS AND OTHER PAPERS",
         "(a)   Form. Every motion is in writing.",
         "(b)   Briefs.",
         "      (1)    Length. A brief may not exceed",
         "             ten pages.",
         "      (2)    Service. A brief is served on all parties as",
         "             required by subsection",
         "(a) of this rule.",
         number=1),
    page("      (3)    Rebuttal. A rebuttal is due seven days",
         "             after service of the response.",
         "      5",
         "      (4)    Response time. A response is due fourteen days",
         "             after service of the motion.",
         "             (A)    Extensions. The court may extend the time.",
         "             (B)    Waiver. A party may waive a response",
         "                    (i)    in writing; or",
         "                    (ii)   on the record.",
         "(c)   Proposed orders. Submit them by e-mail.",
         number=2),
    page("Rule 79.    RECORDS",
         "(a)   Sealed records.",
         "      (1)    A motion to seal is filed first.",
         "      (2)    The clerk dockets the motion.",
         "      (3)    The order states",
         "             (A)    the reasons; and",
         "             (B)    for each document,",
         "                         (1)    its title;",
         "                         (2)    its docket number; and",
         "                         (3)    its page count.",
         "      (4)    Sealed records are returned after the case ends.",
         number=3),
    page("DIVISION SITES AND COUNTIES",
         "Northern       Abbeville          Southern       Bayview",
         "Eastern        Corinth            Western        Delta",
         "Central        Easton             Coastal        Fairport",
         "Upland         Glenwood           Lowland        Harbor",
         number=4),
    page("OFFICIAL FORMS",
         "Form 1. Notice of appearance.",
         number=5),
])

OPTS = dict(cite_prefix="L.U.Civ.R.", start_at="PREAMBLE", stop_at="OFFICIAL FORMS")


def anchor_lines(built):
    return [l for l in built.body if l.startswith("<a id=")]


class TestWordsUnchanged(unittest.TestCase):
    """Extraction alters or drops words: the build fails unless the words match the source."""

    def test_body_words_equal_source_words(self):
        built = rules.build_text(LOCAL, **OPTS)
        self.assertEqual(rules.body_words(built.body), built.source_words)
        self.assertTrue(built.source_words[:3] == ["PREAMBLE", "These", "rules"])
        self.assertEqual(built.source_words[-1], "Harbor")  # the table page is kept, "OFFICIAL FORMS" is not

    def test_every_word_is_accounted_for(self):
        built = rules.build_text(LOCAL, **OPTS)
        w = built.words
        self.assertEqual(w["source"], w["before_start"] + w["after_stop"] + w["page_numbers"] + w["body"])
        self.assertEqual(w["body"], len(built.source_words))
        self.assertEqual(w["page_numbers"], 4)  # 1-4 inside the region; "i" and "5" are outside it

    def test_changed_body_fails_the_check(self):
        built = rules.build_text(LOCAL, **OPTS)
        body = list(built.body)
        i = next(n for n, l in enumerate(body) if "fourteen" in l)
        body[i] = body[i].replace("fourteen", "fifteen")
        self.assertNotEqual(rules.body_words(body), built.source_words)

    def test_source_line_that_looks_like_markup_fails_closed(self):
        text = page("Rule 1.     SCOPE", "## not a heading in the source", "(a)   Text.")
        with self.assertRaises(rules.RulesError) as cm:
            rules.build_text(text, cite_prefix="L.U.Civ.R.")
        self.assertIn("word check", str(cm.exception))

    def test_form_feeds_and_layout_spacing_do_not_survive(self):
        built = rules.build_text(LOCAL, **OPTS)
        self.assertFalse(any("\f" in l for l in built.body))
        self.assertFalse(any("  " in l for l in built.body if not l.startswith("#")))

    def test_region_ends_at_the_first_line_that_begins_with_stop_at(self):
        # a section title often runs on ("FORMS FOR THE DISTRICT"), and the contents page names it first
        text = page("CONTENTS", "PREAMBLE.......... I", "OTHER RULES .... 9", "PREAMBLE", "Rule 1.     A", "(a)   Kept.",
                    "OTHER RULES OF THE DISTRICT", "Rule 2.     B", "(a)   Not kept.", "OTHER RULES")
        built = rules.build_text(text, cite_prefix="L.U.Civ.R.", start_at="PREAMBLE", stop_at="OTHER RULES")
        self.assertEqual(built.rules, ["1"])
        self.assertEqual(built.source_words[-1], "Kept.")

    def test_missing_region_marker_is_refused(self):
        with self.assertRaises(rules.RulesError):
            rules.build_text(LOCAL, cite_prefix="L.U.Civ.R.", start_at="NO SUCH LINE")
        with self.assertRaises(rules.RulesError):
            rules.build_text(LOCAL, cite_prefix="L.U.Civ.R.", start_at="PREAMBLE", stop_at="NO SUCH LINE")


class TestAnchors(unittest.TestCase):
    """An anchor is mislabeled, e.g. a numbered list under a capital-letter item."""

    def setUp(self):
        self.built = rules.build_text(LOCAL, **OPTS)
        self.ids = [l.split('"')[1] for l in anchor_lines(self.built)]

    def test_rule_and_subsection_ids(self):
        for aid in ("lucivr-1", "lucivr-1-a", "lucivr-7", "lucivr-7-b", "lucivr-7-b-4", "lucivr-7-b-4-A",
                    "lucivr-7-b-4-B-i", "lucivr-7-b-4-B-ii", "lucivr-7-c", "lucivr-79-a-3-B"):
            self.assertIn(aid, self.ids)

    def test_ids_are_unique_and_labels_match(self):
        self.assertEqual(len(self.ids), len(set(self.ids)))
        line = next(l for l in anchor_lines(self.built) if '"lucivr-7-b-4"' in l)
        self.assertTrue(line.startswith('<a id="lucivr-7-b-4"></a>**[7(b)(4)]** (4) Response time.'), line)
        head = next(l for l in anchor_lines(self.built) if '"lucivr-7"' in l)
        self.assertIn("**[L.U.Civ.R. 7]** Rule 7. MOTIONS AND OTHER PAPERS", head)
        self.assertIn("## Rule 7. MOTIONS AND OTHER PAPERS", self.built.body)

    def test_list_nested_under_a_capital_item(self):
        for n in (1, 2, 3):
            self.assertIn(f"lucivr-79-a-3-B-{n}", self.ids)
        self.assertEqual([i for i in self.ids if i.startswith("lucivr-79-a-3-B-")],
                         ["lucivr-79-a-3-B-1", "lucivr-79-a-3-B-2", "lucivr-79-a-3-B-3"])

    def test_indentation_ends_the_nested_list(self):
        # "(4)" continues either list; its indentation matches the outer (1)-(3)
        self.assertIn("lucivr-79-a-4", self.ids)
        self.assertNotIn("lucivr-79-a-3-B-4", self.ids)

    def test_without_indentation_the_guess_is_recorded(self):
        flat = "\n".join(l.strip() for l in LOCAL.split("\n"))
        built = rules.build_text(flat, **OPTS)
        self.assertTrue(any("79" in n and "guess" in n for n in built.notes), built.notes)

    def test_out_of_sequence_marker_stays_text(self):
        # "(a) of this rule." wraps a cross-reference onto a new line under (b)(2)
        self.assertNotIn("lucivr-7-a-2", self.ids)
        self.assertEqual(self.ids.count("lucivr-7-a"), 1)
        self.assertIn("(a) of this rule.", self.built.body)
        self.assertTrue(any("(a)" in n and "out of sequence" in n for n in self.built.notes), self.built.notes)

    def test_contents_page_is_not_read_as_rule_headings(self):
        self.assertEqual(self.built.rules, ["1", "7", "79"])

    def test_cross_reference_to_an_earlier_rule_is_not_a_heading(self):
        text = page("Rule 3.     FIRST", "(a)   Text.", "Rule 5.     SECOND", "(a)   More text.",
                    "Rule 3.     SEE ABOVE for the earlier rule.")
        built = rules.build_text(text, cite_prefix="L.U.Civ.R.")
        self.assertEqual(built.rules, ["3", "5"])

    def test_letter_or_roman_decided_by_the_next_marker(self):
        def rule_with(after):
            letters = [f"({c})   Item {c}." for c in "abcdefg"]
            return page("Rule 9.     LISTS", *letters, "(h)   Item h.", "      (1)    One.",
                        "             (A)    Cap.", "(i)   Ambiguous.", after)
        roman = [l.split('"')[1] for l in rules.build_text(rule_with("(ii)  Second."),
                                                           cite_prefix="L.U.Civ.R.").body if l.startswith("<a id=")]
        letter = [l.split('"')[1] for l in rules.build_text(rule_with("(j)   Item j."),
                                                            cite_prefix="L.U.Civ.R.").body if l.startswith("<a id=")]
        self.assertIn("lucivr-9-h-1-A-i", roman)
        self.assertIn("lucivr-9-h-1-A-ii", roman)
        self.assertIn("lucivr-9-i", letter)
        self.assertIn("lucivr-9-j", letter)

    def test_dotted_rule_numbers(self):
        text = page("Rule 83.1.   ATTORNEYS", "(a)   Admission.", "Rule 83.2.   APPEARANCES", "(a)   Notice.")
        built = rules.build_text(text, cite_prefix="L.U.Civ.R.")
        self.assertEqual(built.rules, ["83.1", "83.2"])
        self.assertIn('<a id="lucivr-83.1-a"></a>**[83.1(a)]** (a) Admission.', built.body)


class TestPageNumbers(unittest.TestCase):
    """A real one-word line is dropped as a page number."""

    def test_only_page_edge_numbers_are_dropped(self):
        built = rules.build_text(LOCAL, **OPTS)
        self.assertEqual([t for _, t in built.dropped], ["1", "2", "3", "4"])
        self.assertIn("5", built.source_words)  # the lone "5" mid-page stays

    def test_a_page_without_a_number_is_allowed(self):
        text = "\f".join([page("Rule 1.     A", "(a)   One.", number=1), page("(b)   Two."),
                          page("(c)   Three.", number=3)])
        built = rules.build_text(text, cite_prefix="L.U.Civ.R.")
        self.assertEqual([t for _, t in built.dropped], ["1", "3"])

    def test_a_number_that_breaks_the_sequence_fails_the_build(self):
        text = "\f".join([page("Rule 1.     A", "(a)   One.", number=1), page("(b)   Two.", number=2),
                          page("(c)   Three.", "(d)   Four.", "", "  12")])
        with self.assertRaises(rules.RulesError) as cm:
            rules.build_text(text, cite_prefix="L.U.Civ.R.")
        self.assertIn("12", str(cm.exception))


class TestWarnings(unittest.TestCase):
    """Columns and lost glyphs survive a word check, so they are reported for a human."""

    def test_two_column_page_is_named(self):
        built = rules.build_text(LOCAL, **OPTS)
        self.assertTrue(any("page 5" in w and "column" in w for w in built.warnings), built.warnings)
        self.assertFalse(any("page 3" in w for w in built.warnings), built.warnings)

    def test_lost_glyphs_are_reported(self):
        text = page("Rule 1.     A", "(a)   Courts of Mi i ippi hear it.", "(b)   Text � here.")
        warnings = " ".join(rules.build_text(text, cite_prefix="L.U.Civ.R.").warnings)
        self.assertIn("Mi i ippi", warnings)
        self.assertIn("U+FFFD", warnings)


class TestPack(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.src = os.path.join(self._tmp.name, "local_rules.txt")
        with open(self.src, "w", encoding="utf-8") as f:
            f.write(LOCAL)
        self.out = os.path.join(self._tmp.name, "pack")

    def build(self, **kw):
        args = dict(pack_id="example-local", version="2030-01-01", title="Example District Local Rules",
                    court="Example District", current_as_of="2030-01-01", **OPTS)
        args.update(kw)
        return rules.build_pack(self.src, self.out, **args)

    def test_manifest_records_source_and_counts(self):
        m = self.build()
        with open(os.path.join(self.out, "manifest.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), m)
        self.assertEqual(m["format"], rules.FORMAT)
        self.assertEqual(m["anchor_prefix"], "lucivr")
        self.assertEqual(m["source"]["sha256"], rules.sha256_file(self.src))
        self.assertEqual(m["rules_md_sha256"], rules.sha256_file(os.path.join(self.out, "rules.md")))
        self.assertEqual(m["rules"], 3)
        self.assertTrue(m["warnings"])

    def test_load_round_trip_and_tamper(self):
        self.build()
        pack = rules.load_pack(self.out)
        self.assertEqual((pack.id, pack.version), ("example-local", "2030-01-01"))
        self.assertIn("lucivr-7-b-4", pack.anchors)
        with open(os.path.join(self.out, "rules.md"), "a", encoding="utf-8") as f:
            f.write("tampered\n")
        with self.assertRaises(rules.RulesError):
            rules.load_pack(self.out)

    def test_rules_md_says_it_is_not_the_official_copy(self):
        self.build()
        with open(os.path.join(self.out, "rules.md"), encoding="utf-8") as f:
            head = f.read().split(rules.BODY_MARK)[0]
        self.assertIn("not the official copy", head)
        self.assertIn("2030-01-01", head)

    def test_will_not_write_into_a_folder_with_files(self):
        os.makedirs(self.out)
        with open(os.path.join(self.out, "x.txt"), "w") as f:
            f.write("x")
        with self.assertRaises(rules.RulesError):
            self.build()

    def test_bad_id_or_dates_are_refused(self):
        for kw in ({"pack_id": "Bad Id"}, {"version": "../x"}, {"current_as_of": "Jan 1"}):
            with self.assertRaises(rules.RulesError):
                self.build(**kw)


if __name__ == "__main__":
    unittest.main()
