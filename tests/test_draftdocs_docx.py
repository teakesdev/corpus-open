"""draftdocs.read_docx: what brief check sees of a Word draft.

Synthetic drafts only, built in memory by draft_builders. Run: python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import draftdocs  # noqa: E402
from draft_builders import (comments_xml, docx, enref, fnref, lvl, numbering_xml, para, run, styles_xml,  # noqa: E402
                            table)

HEADINGS = styles_xml([("Heading1", "heading 1", None, ""), ("Heading2", "heading 2", None, ""),
                       ("Heading3", "heading 3", None, "")])


def read(*a, **kw):
    return draftdocs.read_docx(docx(*a, **kw))


def blocks(d):
    """(label, text) for each block: its first line and that line's label."""
    lines = d.text.split("\n")
    return [(d.labels[i], lines[i]) for i in range(len(lines))
            if lines[i].strip() and (i == 0 or not lines[i - 1].strip())]


class TestParagraphs(unittest.TestCase):
    def test_each_paragraph_is_a_block_numbered_in_reading_order(self):
        d = read(para("Alpha.") + para("Beta."))
        self.assertEqual(d.kind, "docx")
        self.assertEqual(d.text, "Alpha.\n\nBeta.")
        self.assertEqual(d.labels, ["para. 1", "", "para. 2"])

    def test_empty_paragraphs_are_neither_blocks_nor_counted(self):
        d = read(para("Alpha.") + para() + para("  ") + para("Beta."))
        self.assertEqual(blocks(d), [("para. 1", "Alpha."), ("para. 2", "Beta.")])

    def test_table_cell_paragraphs_say_where_in_the_table(self):
        d = read(para("Before.") + table([[[para("A1.")], [para("B1.")]], [[para("A2.")], [para("B2.")]]]))
        self.assertEqual(blocks(d), [
            ("para. 1", "Before."),
            ("para. 2 (table 1, row 1, cell 1)", "A1."), ("para. 3 (table 1, row 1, cell 2)", "B1."),
            ("para. 4 (table 1, row 2, cell 1)", "A2."), ("para. 5 (table 1, row 2, cell 2)", "B2.")])

    def test_where_gives_the_label_of_a_line_and_nothing_for_an_unknown_one(self):
        d = read(para("Alpha.") + para("Beta."))
        self.assertEqual(d.where(3), "para. 2")
        self.assertEqual(d.where(0), "document")
        self.assertEqual(d.where(99), "")

    def test_tabs_and_breaks_read_as_spaces_and_hyphens_survive(self):
        d = read(para("II.", "<w:r><w:tab/></w:r>", "ARGUMENT", "<w:r><w:br/></w:r>", "NOTES",
                      "<w:r><w:noBreakHyphen/></w:r>", "ONE", "<w:r><w:softHyphen/></w:r>", "TWO"))
        self.assertEqual(d.text, "II. ARGUMENT NOTES-ONETWO")

    def test_smart_quotes_nbsp_and_section_signs_inside_cites_are_normalized(self):
        d = read(para("See 42 U.S.C. § 1983; Smith v. Doe, 10 Fed. App’x 5 (2d Cir. 2001); "
                      "Fed. R. Civ. P. 15(a)(2); the court said “the file is closed.”"))
        self.assertEqual(d.text, "See 42 U.S.C. § 1983; Smith v. Doe, 10 Fed. App'x 5 (2d Cir. 2001); "
                                 "Fed. R. Civ. P. 15(a)(2); the court said “the file is closed.”")

    def test_a_field_result_is_read_and_its_instruction_is_not(self):
        fld = ('<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> HYPERLINK "http://x.example" </w:instrText></w:r>'
               '<w:r><w:fldChar w:fldCharType="separate"/></w:r>' + run("Doc. 16 at 2") +
               '<w:r><w:fldChar w:fldCharType="end"/></w:r>')
        d = read(para("See ", fld, "."))
        self.assertEqual(d.text, "See Doc. 16 at 2.")
        d = read(para("See ", '<w:hyperlink w:anchor="x">' + run("Doc. 16") + "</w:hyperlink>", "."))
        self.assertEqual(d.text, "See Doc. 16.")

    def test_a_document_with_no_text_is_refused_not_passed(self):
        with self.assertRaisesRegex(draftdocs.DraftError, "no text"):
            read(para() + para("   "))


class TestFootnotes(unittest.TestCase):
    def test_a_footnote_follows_the_paragraph_that_cites_it_and_is_labelled_fn(self):
        d = read(para("Alpha.", fnref(1)) + para("Beta."), footnotes={1: "See Doc. 16 at 2."})
        self.assertEqual(blocks(d), [("para. 1", "Alpha."), ("fn. 1", "See Doc. 16 at 2."), ("para. 2", "Beta.")])
        self.assertEqual(d.blocks[3].flow, "fn")
        self.assertEqual(d.blocks[1].flow, "body")

    def test_footnote_numbers_follow_the_order_of_the_references_not_the_ids(self):
        d = read(para("A.", fnref(7)) + para("B.", fnref(3)), footnotes={3: "Three.", 7: "Seven."})
        self.assertEqual(blocks(d), [("para. 1", "A."), ("fn. 1", "Seven."), ("para. 2", "B."), ("fn. 2", "Three.")])

    def test_two_references_in_one_paragraph_give_two_notes_in_order(self):
        d = read(para("A", fnref(1), " and B", fnref(2), "."), footnotes={1: "One.", 2: "Two."})
        self.assertEqual([b[0] for b in blocks(d)], ["para. 1", "fn. 1", "fn. 2"])

    def test_a_multi_paragraph_footnote_keeps_its_label_on_every_paragraph(self):
        d = read(para("A.", fnref(1)), footnotes={1: ["First.", "<w:r><w:t>Second.</w:t></w:r>"]})
        self.assertEqual(blocks(d)[1:], [("fn. 1", "First."), ("fn. 1", "Second.")])

    def test_a_footnote_cited_from_a_table_cell_follows_that_cell(self):
        d = read(table([[[para("Cell.", fnref(1))]]]), footnotes={1: "Note."})
        self.assertEqual([b[1] for b in blocks(d)], ["Cell.", "Note."])

    def test_separator_notes_are_not_read_and_an_unreferenced_note_is_noted(self):
        d = read(para("A.", fnref(1)), footnotes={1: "One.", 2: "Orphan, not in the text."})
        self.assertNotIn("Orphan", d.text)
        self.assertNotIn("separator", d.text)
        self.assertTrue(any("1 footnote" in n and "not referenced" in n for n in d.notes), d.notes)

    def test_a_reference_to_a_missing_note_is_a_warning_not_silence(self):
        d = read(para("A.", fnref(9)), footnotes={1: "One."})
        self.assertIn("footnote-missing", [w[0] for w in d.warnings])

    def test_a_footnote_part_that_is_missing_but_referenced_is_a_warning(self):
        d = read(para("A.", fnref(1)))
        self.assertIn("footnote-missing", [w[0] for w in d.warnings])

    def test_endnotes_are_read_and_labelled_with_the_default_roman_numeral(self):
        d = read(para("A.", enref(1)) + para("B.", enref(2)), endnotes={1: "First.", 2: "Second."})
        self.assertEqual([b[0] for b in blocks(d)], ["para. 1", "endnote i", "para. 2", "endnote ii"])
        self.assertEqual(d.blocks[3].flow, "fn")

    def test_footnotes_that_restart_or_use_symbols_are_warned_about(self):
        settings = ('<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    '<w:footnotePr><w:numRestart w:val="eachPage"/></w:footnotePr></w:settings>')
        d = read(para("A.", fnref(1)), footnotes={1: "One."}, settings=settings)
        self.assertIn("footnote-numbering", [w[0] for w in d.warnings])


NUM_ROMAN = numbering_xml({0: [lvl(0, "upperRoman", "%1."), lvl(1, "upperLetter", "%2."), lvl(2, "decimal", "%3.")]},
                          {1: 0})


class TestAutoNumbering(unittest.TestCase):
    def test_roman_letter_and_decimal_levels_render_their_markers(self):
        d = read(para("STATEMENT OF FACTS", num=(1, 0)) + para("Procedural History", num=(1, 1)) +
                 para("Filing", num=(1, 2)) + para("ARGUMENT", num=(1, 0)), numbering=NUM_ROMAN)
        self.assertEqual([b[1] for b in blocks(d)], ["I. STATEMENT OF FACTS", "A. Procedural History", "1. Filing",
                                                    "II. ARGUMENT"])

    def test_a_deeper_level_restarts_under_each_new_parent(self):
        d = read(para("One", num=(1, 0)) + para("a", num=(1, 1)) + para("b", num=(1, 1)) + para("Two", num=(1, 0)) +
                 para("c", num=(1, 1)), numbering=NUM_ROMAN)
        self.assertEqual([b[1] for b in blocks(d)], ["I. One", "A. a", "B. b", "II. Two", "A. c"])

    def test_the_label_names_the_marker_so_a_person_can_find_it(self):
        d = read(para("ARGUMENT", num=(1, 0)) + para("Plain."), numbering=NUM_ROMAN)
        self.assertEqual(blocks(d)[0][0], "para. 1 (numbered I.)")
        self.assertEqual(blocks(d)[1][0], "para. 2")

    def test_a_numbered_empty_paragraph_still_takes_its_number(self):
        d = read(para(num=(1, 0)) + para("ARGUMENT", num=(1, 0)), numbering=NUM_ROMAN)
        self.assertEqual(blocks(d)[0][1], "II. ARGUMENT")

    def test_complaint_style_decimal_paragraphs(self):
        num = numbering_xml({0: [lvl(0, "decimal", "%1.")]}, {1: 0})
        d = read(para("Plaintiff lives in Exampleton.", num=(1, 0)) + para("Defendant arrested Plaintiff.", num=(1, 0)),
                 numbering=num)
        self.assertEqual([b[1] for b in blocks(d)], ["1. Plaintiff lives in Exampleton.", "2. Defendant arrested Plaintiff."])

    def test_list_numbering_is_shared_by_nums_of_one_abstract_list_unless_restarted(self):
        num = numbering_xml({0: [lvl(0, "decimal", "%1.")]}, {1: 0, 2: 0, 3: (0, {0: 10})})
        d = read(para("a", num=(1, 0)) + para("b", num=(2, 0)) + para("c", num=(3, 0)) + para("d", num=(3, 0)), numbering=num)
        self.assertEqual([b[1] for b in blocks(d)], ["1. a", "2. b", "10. c", "11. d"])

    def test_a_heading_style_that_carries_the_numbering(self):
        styles = styles_xml([("Heading1", "heading 1", None, '<w:numPr><w:numId w:val="1"/></w:numPr>'),
                             ("Heading2", "heading 2", None, '<w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr>')])
        num = numbering_xml({0: [lvl(0, "upperRoman", "%1.", pstyle="Heading1"), lvl(1, "upperLetter", "%2.", pstyle="Heading2")]},
                            {1: 0})
        d = read(para("ARGUMENT", style="Heading1") + para("Standing", style="Heading2") + para("Merits", style="Heading2"),
                 numbering=num, styles=styles)
        self.assertEqual([b[1] for b in blocks(d)], ["I. ARGUMENT", "A. Standing", "B. Merits"])

    def test_a_style_inherits_numbering_through_based_on(self):
        styles = styles_xml([("Base", "Brief Heading", None, '<w:numPr><w:numId w:val="1"/></w:numPr>'),
                             ("Kid", "Brief Heading 2", "Base", "")])
        num = numbering_xml({0: [lvl(0, "decimal", "%1.")]}, {1: 0})
        d = read(para("Facts", style="Kid"), numbering=num, styles=styles)
        self.assertEqual(d.text, "1. Facts")

    def test_bullets_and_numId_zero_get_no_marker(self):
        num = numbering_xml({0: [lvl(0, "bullet", "•"), lvl(1, "decimal", "%2.")]}, {1: 0})
        d = read(para("Point", num=(1, 0)) + para("Off", num=(0, 0)), numbering=num)
        self.assertEqual(d.text, "Point\n\nOff")

    def test_a_list_with_no_numbering_part_is_a_warning(self):
        d = read(para("ARGUMENT", num=(1, 0)))
        self.assertEqual(d.text, "ARGUMENT")
        self.assertIn("numbering-unresolved", [w[0] for w in d.warnings])

    def test_legal_and_multi_level_text(self):
        num = numbering_xml({0: [lvl(0, "decimal", "%1."), lvl(1, "decimal", "%1.%2")]}, {1: 0})
        d = read(para("a", num=(1, 0)) + para("b", num=(1, 1)) + para("c", num=(1, 1)), numbering=num)
        self.assertEqual([b[1] for b in blocks(d)], ["1. a", "1.1 b", "1.2 c"])

    def test_letters_run_past_z_the_way_word_does(self):
        num = numbering_xml({0: [lvl(0, "lowerLetter", "(%1)", start=26)]}, {1: 0})
        d = read(para("z", num=(1, 0)) + para("aa", num=(1, 0)), numbering=num)
        self.assertEqual([b[1] for b in blocks(d)], ["(z) z", "(aa) aa"])


class TestStylesAndStructure(unittest.TestCase):
    def test_heading_styles_give_a_level_hint_to_the_block(self):
        d = read(para("Statement of Facts", style="Heading1") + para("Arrest", style="Heading2") + para("Body."),
                 styles=HEADINGS)
        self.assertEqual([d.blocks[l].heading for l in (1, 3, 5)], [1, 2, 0])

    def test_an_outline_level_also_marks_a_heading(self):
        styles = styles_xml([("BriefHead", "Brief Head", None, '<w:outlineLvl w:val="1"/>')])
        d = read(para("Argument", style="BriefHead") + para("Body."), styles=styles)
        self.assertEqual([d.blocks[l].heading for l in (1, 3)], [2, 0])

    def test_toc_and_toa_paragraphs_are_not_read_and_are_counted(self):
        styles = styles_xml([("TOC1", "toc 1", None, ""), ("TOAHeading", "toa heading", None, ""),
                             ("TableofAuthorities", "table of authorities", None, "")])
        d = read(para("I. ARGUMENT .... 3", style="TOC1") + para("Dietz v. Bouldin, 579 U.S. 40 (2016) .... 3", style="TableofAuthorities")
                 + para("Real text."), styles=styles)
        self.assertEqual(d.text, "Real text.")
        self.assertEqual(d.labels[0], "para. 3")
        self.assertTrue(any("2 table-of-contents or authorities" in n for n in d.notes), d.notes)

    def test_a_content_control_wrapping_paragraphs_is_read(self):
        d = read("<w:sdt><w:sdtPr/><w:sdtContent>" + para("Inside.") + "</w:sdtContent></w:sdt>" + para("After."))
        self.assertEqual(d.text, "Inside.\n\nAfter.")

    def test_hidden_text_is_left_out_and_warned(self):
        d = read(para("Shown ", run("hidden Doc. 99 ", vanish=True), "end."))
        self.assertEqual(d.text, "Shown end.")
        self.assertIn("hidden-text", [w[0] for w in d.warnings])

    def test_text_boxes_are_not_read_and_are_warned(self):
        box = ("<w:r><w:pict><v:shape xmlns:v='urn:v'><v:textbox><w:txbxContent>" + para("Boxed Doc. 5.") +
               "</w:txbxContent></v:textbox></v:shape></w:pict></w:r>")
        d = read(para("Body ", box, "text."))
        self.assertEqual(d.text, "Body text.")
        self.assertIn("text-box", [w[0] for w in d.warnings])

    def test_math_and_embedded_content_are_warned_about_not_dropped(self):
        math = "<m:oMath xmlns:m='http://schemas.openxmlformats.org/officeDocument/2006/math'><m:r><m:t>x=1</m:t></m:r></m:oMath>"
        d = read(para("Value ", math, " here.") + "<w:altChunk/>")
        self.assertEqual(d.warnings[0][0], "unread-content")
        self.assertIn("oMath", d.warnings[0][2])
        self.assertIn("altChunk", d.warnings[0][2])

    def test_headers_footers_and_comments_are_listed_as_not_read(self):
        d = read(para("A."), parts={"word/header1.xml": "<x/>", "word/footer1.xml": "<x/>"}, comments=comments_xml(2))
        self.assertTrue(any("headers and footers" in n and "not read" in n for n in d.notes), d.notes)
        self.assertIn("comments", [w[0] for w in d.warnings])
        self.assertNotIn("check this", d.text)


class TestTrackedChanges(unittest.TestCase):
    INS = '<w:ins w:id="1" w:author="Pat Reviewer" w:date="2026-01-01T00:00:00Z">' + run("added words") + "</w:ins>"
    DEL = ('<w:del w:id="2" w:author="Sam Editor" w:date="2026-01-01T00:00:00Z"><w:r><w:delText>struck</w:delText></w:r></w:del>')

    def test_tracked_insertions_and_deletions_are_refused_with_who_and_how_many(self):
        with self.assertRaises(draftdocs.DraftError) as cm:
            read(para("A ", self.INS, " ", self.DEL, " B."))
        msg = str(cm.exception)
        self.assertIn("tracked changes", msg)
        self.assertIn("1 insertion", msg)
        self.assertIn("1 deletion", msg)
        self.assertIn("Pat Reviewer", msg)
        self.assertIn("Sam Editor", msg)
        self.assertIn("Accept", msg)

    def test_a_move_is_a_tracked_change(self):
        mv = '<w:moveFrom w:id="3" w:author="Pat"><w:r><w:delText>x</w:delText></w:r></w:moveFrom>'
        with self.assertRaisesRegex(draftdocs.DraftError, "tracked changes"):
            read(para("A ", mv))

    def test_tracked_changes_in_a_footnote_are_refused_too(self):
        with self.assertRaisesRegex(draftdocs.DraftError, "tracked changes"):
            read(para("A.", fnref(1)), footnotes={1: [self.INS]})

    def test_a_tracked_paragraph_mark_is_a_tracked_change(self):
        with self.assertRaisesRegex(draftdocs.DraftError, "tracked changes"):
            read(para("A.", ppr='<w:rPr><w:ins w:id="4" w:author="Pat"/></w:rPr>'))

    def test_formatting_only_changes_are_a_warning_not_a_refusal(self):
        fmt = '<w:rPrChange w:id="5" w:author="Pat"><w:rPr/></w:rPrChange>'
        d = read(para("A.", "<w:r><w:rPr>" + fmt + "</w:rPr><w:t>bold</w:t></w:r>"))
        self.assertIn("formatting-changes", [w[0] for w in d.warnings])
        self.assertIn("A.bold", d.text)


if __name__ == "__main__":
    unittest.main()
