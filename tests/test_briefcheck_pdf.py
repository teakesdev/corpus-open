"""brief check reads a PDF draft through pdftotext -layout: headers, footers and page numbers dropped, page:line
locations. Skipped when pdftotext is not installed. Synthetic PDFs, built in memory.

Run: python3 -m unittest discover -s tests -v
"""
import hashlib
import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import briefcheck, draftdocs  # noqa: E402
from draft_builders import pdf, text_pdf  # noqa: E402
from test_briefcheck import Base, codes  # noqa: E402

HAVE = shutil.which("pdftotext") is not None
HEADER = lambda n: f"Case 1:25-cv-00001 Document 16 Filed 01/01/26 Page {n} of 3"  # noqa: E731
FOOT = lambda n: str(n)  # noqa: E731


def read(data):
    return draftdocs.read_pdf(data)


@unittest.skipUnless(HAVE, "pdftotext not installed")
class TestReadPdf(unittest.TestCase):
    def test_running_headers_and_page_numbers_are_dropped(self):
        d = read(text_pdf([["First page text."], ["Second page text."], ["Third page text."]], header=HEADER, footer=FOOT))
        self.assertEqual(d.text.split(), "First page text. Second page text. Third page text.".split())
        self.assertTrue(any("running header" in n for n in d.notes) and any("page number" in n for n in d.notes), d.notes)

    def test_labels_count_text_lines_from_the_top_of_the_page_including_the_header(self):
        d = read(text_pdf([["One.", "Two."], ["Three.", "Four."]], header=HEADER, footer=FOOT))
        lines = d.text.split("\n")
        self.assertEqual({lines[i]: d.labels[i] for i in range(len(lines)) if lines[i]},
                         {"One.": "p. 1:2", "Two.": "p. 1:3", "Three.": "p. 2:2", "Four.": "p. 2:3"})

    def test_a_lone_number_that_breaks_the_page_sequence_is_kept(self):
        d = read(text_pdf([["Alpha."], ["Beta."]], footer=lambda n: ["1", "7"][n - 1]))
        self.assertIn("7", d.text.split())
        self.assertTrue(any("page number" in n and "kept" in n for n in d.notes), d.notes)

    def test_a_first_page_without_a_number_is_fine(self):
        d = read(text_pdf([["Alpha."], ["Beta."], ["Gamma."]], footer=lambda n: "" if n == 1 else str(n)))
        self.assertEqual(d.text.split(), ["Alpha.", "Beta.", "Gamma."])

    def test_single_spaced_text_with_a_blank_line_between_paragraphs(self):
        d = read(text_pdf([["First paragraph line one.", "line two.", "", "Second paragraph."]]))
        self.assertEqual(d.text, "First paragraph line one.\nline two.\n\nSecond paragraph.")

    def test_double_spaced_text_is_one_block_per_paragraph_by_indent(self):
        page = [(36, "First paragraph opens here and runs"), (0, "to a second line."), (36, "Second paragraph opens here."),
                (0, "and wraps again.")]
        d = read(text_pdf([page], pitch=28))
        self.assertEqual(d.text, "First paragraph opens here and runs\nto a second line.\n\n"
                                 "Second paragraph opens here.\nand wraps again.")

    def test_double_spaced_text_with_no_indent_runs_together_until_the_terminal_gap(self):
        page = ["Line one of a paragraph", "line two of it.", "", "", "A new paragraph after a gap."]
        d = read(text_pdf([page], pitch=28))
        self.assertEqual(d.text, "Line one of a paragraph\nline two of it.\n\nA new paragraph after a gap.")

    def test_a_paragraph_runs_across_a_page_break(self):
        d = read(text_pdf([["The court held that a motion to"], ["dismiss was properly granted."]], header=HEADER, footer=FOOT))
        self.assertEqual(d.text, "The court held that a motion to\ndismiss was properly granted.")
        self.assertEqual([x for x in d.labels if x], ["p. 1:2", "p. 2:2"])

    def test_heading_lines_stand_alone_even_without_a_blank_line(self):
        page = [(None, "STATEMENT OF FACTS"), "On January 1, officers arrested Plaintiff.", "They did so at home.",
                (None, "ARGUMENT"), "The arrest was unlawful."]
        d = read(text_pdf([page]))
        self.assertEqual(d.text, "STATEMENT OF FACTS\n\nOn January 1, officers arrested Plaintiff.\nThey did so at home.\n\n"
                                 "ARGUMENT\n\nThe arrest was unlawful.")

    def test_a_numbered_heading_line_that_ends_a_sentence_stands_alone(self):
        d = read(text_pdf([["The motion was filed.", "II. THE COURT SHOULD DENY THE MOTION", "Plaintiff pleaded enough."]]))
        self.assertEqual(d.text, "The motion was filed.\n\nII. THE COURT SHOULD DENY THE MOTION\n\nPlaintiff pleaded enough.")

    def test_a_mid_sentence_line_that_looks_like_a_heading_does_not_split_the_paragraph(self):
        d = read(text_pdf([["The Court has said that a claim against", "THE CITY OF EXAMPLETON", "must plead a policy."]]))
        self.assertEqual(d.text, "The Court has said that a claim against\nTHE CITY OF EXAMPLETON\nmust plead a policy.")

    def test_wide_gaps_inside_a_line_collapse_and_ligatures_and_smart_quotes_normalize(self):
        d = read(text_pdf([["Plaintiff,            Civil No. 1", "See 42 U.S.C. § 1983 and “the file is closed.”"]]))
        self.assertIn("Plaintiff, Civil No. 1", d.text)
        self.assertIn("42 U.S.C. § 1983", d.text)
        self.assertIn("“the file is closed.”", d.text)

    def test_text_goes_to_pdftotext_on_stdin_and_nothing_is_written(self):
        calls = []
        real = subprocess.run

        def spy(cmd, *a, **kw):
            calls.append((cmd, kw.get("input")))
            return real(cmd, *a, **kw)
        data = text_pdf([["Alpha."]])
        with mock.patch.object(draftdocs.subprocess, "run", spy):
            read(data)
        layout = [c for c, _ in calls if "-layout" in c]
        self.assertEqual(layout[0][-2:], ["-", "-"])
        self.assertEqual([i for c, i in calls if "-layout" in c], [data])

    def test_a_pdf_with_no_text_layer_is_refused_not_passed(self):
        with self.assertRaisesRegex(draftdocs.DraftError, "no text.*OCR|image"):
            read(pdf([[]]))

    def test_a_file_that_is_not_a_pdf_is_refused(self):
        with self.assertRaisesRegex(draftdocs.DraftError, "not a PDF"):
            draftdocs.read_draft(b"just text", "draft.pdf")

    def test_a_damaged_pdf_is_refused_with_pdftotexts_complaint(self):
        with self.assertRaisesRegex(draftdocs.DraftError, "pdftotext could not read"):
            read(b"%PDF-1.4\nthis is not a pdf body\n")


class TestPdftotextMissing(unittest.TestCase):
    def test_missing_pdftotext_is_a_clear_refusal(self):
        with mock.patch.object(draftdocs.shutil, "which", return_value=None):
            with self.assertRaises(draftdocs.DraftError) as cm:
                draftdocs.read_pdf(b"%PDF-1.4\n")
        msg = str(cm.exception)
        self.assertIn("pdftotext", msg)
        self.assertIn("poppler", msg)
        self.assertIn(".docx", msg)

    def test_the_no_pdftotext_option_is_a_refusal_for_a_pdf_draft(self):
        with self.assertRaisesRegex(draftdocs.DraftError, "--no-pdftotext"):
            draftdocs.read_draft(b"%PDF-1.4\n", "draft.pdf", use_pdftotext=False)


@unittest.skipUnless(HAVE, "pdftotext not installed")
class TestPdfDraft(Base):
    def pdf_check(self, data, name="draft.pdf", **kw):
        path = os.path.join(os.path.dirname(self.m.dir), name)
        with open(path, "wb") as f:
            f.write(data)
        return briefcheck.check(self.m.dir, path, **kw)

    def test_a_finding_is_located_by_page_and_line(self):
        data = text_pdf([["Intro line one.", "Intro line two."],
                         ["Another page line.", "The Court entered the order on the extension at Doc. 99."]],
                        header=HEADER, footer=FOOT)
        r = self.pdf_check(data)
        self.assertEqual([(f.code, f.where) for f in r.findings if f.severity == "BLOCK"], [("record-not-in-store", "p. 2:3")])

    def test_a_cite_split_over_two_lines_and_a_page_is_read_whole(self):
        data = text_pdf([["Plaintiff relies on Dietz v. Bouldin, 579 U.S.", "40, 45 (2016). Defendants also cite"],
                         ["Fed. R. Civ. P. 15(a)(2)."]], header=HEADER, footer=FOOT)
        r = self.pdf_check(data)
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertEqual({a.display for a in r.authorities if "Dietz" in a.display or "15" in a.display} != set(), True)
        self.assertTrue(any("Dietz" in a.display for a in r.authorities))

    def test_running_header_digits_are_not_read_as_record_cites(self):
        r = self.pdf_check(text_pdf([["Alpha beta."], ["Gamma delta."]], header=HEADER, footer=FOOT))
        self.assertEqual(r.rows, [])
        self.assertNotIn("Document 16", r.render())

    def test_the_fact_section_opens_on_a_pdf_heading(self):
        page = [(None, "STATEMENT OF FACTS"), "The officer arrested the Plaintiff at home without a warrant.", (None, "ARGUMENT"),
                "The arrest was unlawful."]
        r = self.pdf_check(text_pdf([page]))
        self.assertEqual([(f.code, f.where) for f in r.findings], [("uncited-fact", "p. 1:2")])

    def test_the_report_says_where_and_how_pages_are_counted(self):
        text = self.pdf_check(text_pdf([["See Doc. 99."]])).render()
        self.assertIn("| Where |", text)
        self.assertIn("Draft read: PDF", text)
        self.assertIn("PDF page", text)

    def test_the_hash_is_of_the_pdf_bytes_and_the_matter_gains_no_file(self):
        data = text_pdf([["See Doc. 16."]])
        before = sorted(os.listdir(self.m.dir))
        r = self.pdf_check(data)
        self.assertEqual(r.draft_sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(sorted(os.listdir(self.m.dir)), before)

    def test_an_image_only_pdf_is_refused_by_check(self):
        with self.assertRaisesRegex(ValueError, "no text"):
            self.pdf_check(pdf([[]]))

    def test_no_pdftotext_option_refuses_a_pdf_draft(self):
        with self.assertRaisesRegex(ValueError, "--no-pdftotext"):
            self.pdf_check(text_pdf([["x"]]), use_pdftotext=False)


if __name__ == "__main__":
    unittest.main()
