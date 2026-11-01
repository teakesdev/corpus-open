"""brief check reads a Word (.docx) draft: footnote cites, auto-numbered headings, tracked changes, locations.

Synthetic data only. Run: python3 -m unittest discover -s tests -v
"""
import hashlib
import os
import subprocess
import sys
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)

from matterkit import briefcheck  # noqa: E402
from draft_builders import (comments_xml, docx, fnref, lvl, numbering_xml, para, run, styles_xml)  # noqa: E402
from test_briefcheck import Base, codes  # noqa: E402

NUM = numbering_xml({0: [lvl(0, "upperRoman", "%1."), lvl(1, "upperLetter", "%2.")]}, {1: 0})
STYLES = styles_xml([("Heading1", "heading 1", None, ""), ("Heading2", "heading 2", None, "")])
FACTS = "The officer arrested the Plaintiff at home without a warrant."


class DocxBase(Base):
    def docx_check(self, data, name="draft.docx", **kw):
        path = os.path.join(os.path.dirname(self.m.dir), name)
        with open(path, "wb") as f:
            f.write(data)
        kw.setdefault("use_pdftotext", False)
        return briefcheck.check(self.m.dir, path, **kw)

    def where(self, report, code):
        return [f.where for f in report.findings if f.code == code]


class TestLocations(DocxBase):
    def test_a_body_finding_is_located_by_paragraph_number(self):
        r = self.docx_check(docx(para("The motion was filed.") + para("The order is at Doc. 99.")))
        self.assertEqual(self.where(r, "record-not-in-store"), ["para. 2"])

    def test_a_footnote_finding_is_located_by_footnote_number(self):
        r = self.docx_check(docx(para("The motion was filed.", fnref(1)) + para("Done."),
                                 footnotes={1: "The order is at Doc. 99."}))
        self.assertEqual(self.where(r, "record-not-in-store"), ["fn. 1"])
        self.assertIn("fn. 1", r.render())

    def test_the_report_says_where_for_a_word_draft_and_line_for_a_text_one(self):
        word = self.docx_check(docx(para("See Doc. 99."))).render()
        self.assertIn("| Where |", word)
        self.assertNotIn("| Line |", word)
        text = self.m.check("See Doc. 99.").render()
        self.assertIn("| Line |", text)
        self.assertNotIn("| Where |", text)

    def test_the_report_names_the_reader_and_what_it_did_not_read(self):
        r = self.docx_check(docx(para("A.", fnref(1)), footnotes={1: "One."}, parts={"word/header1.xml": "<x/>"})).render()
        self.assertIn("Draft read: Word (.docx)", r)
        self.assertIn("1 footnote", r)
        self.assertIn("headers and footers", r)

    def test_the_draft_hash_is_of_the_docx_bytes(self):
        data = docx(para("See Doc. 16."))
        r = self.docx_check(data)
        self.assertEqual(r.draft_sha256, hashlib.sha256(data).hexdigest())

    def test_word_cites_resolve_like_text_cites(self):
        r = self.docx_check(docx(para("Extension granted [Docs. 16 and 27].")))
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertEqual({c.target for row in r.rows for c in row.cites},
                         {"Filings/PACER/case-16-MotionDismiss.txt", "Filings/PACER/case-27-Order.txt"})

    def test_smart_quotes_nbsp_and_section_signs_still_resolve_authorities(self):
        r = self.docx_check(docx(para("Plaintiff sues under 42 U.S.C. § 1983 and Fed. R. Civ. P. 15(a)(2).")))
        self.assertEqual(codes(r, "BLOCK"), [])
        self.assertTrue(any("1983" in a.display for a in r.authorities), [a.display for a in r.authorities])


class TestAutoNumberedHeadings(DocxBase):
    FACT_PARAS = [("STATEMENT OF FACTS", 0), (FACTS, None), ("ARGUMENT", 0), ("The officer acted unlawfully.", None)]

    def build(self, numbered=True):
        body = "".join(para(t, num=(1, lvl_)) if lvl_ is not None else para(t) for t, lvl_ in self.FACT_PARAS)
        return docx(body, numbering=NUM)

    def test_auto_numbered_headings_open_and_close_the_fact_section(self):
        r = self.docx_check(self.build())
        uncited = [f for f in r.findings if f.code == "uncited-fact"]
        self.assertEqual([f.cite for f in uncited], [FACTS])
        self.assertEqual([f.where for f in uncited], ["para. 2"])

    def test_the_word_draft_matches_the_same_draft_as_markdown(self):
        md = "I. STATEMENT OF FACTS\n\n" + FACTS + "\n\nII. ARGUMENT\n\nThe officer acted unlawfully. See Doc. 99.\n"
        word = docx(para("STATEMENT OF FACTS", num=(1, 0)) + para(FACTS) + para("ARGUMENT", num=(1, 0)) +
                    para("The officer acted unlawfully. See Doc. 99."), numbering=NUM)
        a, b = self.m.check(md), self.docx_check(word)
        self.assertEqual([(f.severity, f.code, f.cite) for f in a.findings], [(f.severity, f.code, f.cite) for f in b.findings])
        self.assertEqual(a.exit_code, b.exit_code)

    def test_a_heading_style_with_no_marker_and_no_caps_still_opens_the_section(self):
        body = para("Statement of Facts", style="Heading1") + para(FACTS) + para("Argument", style="Heading1") + para("Law applies.")
        r = self.docx_check(docx(body, styles=STYLES))
        self.assertEqual([f.cite for f in r.findings if f.code == "uncited-fact"], [FACTS])
        r = self.docx_check(docx(para("Statement of Facts") + para(FACTS)))
        self.assertEqual([f for f in r.findings if f.code == "uncited-fact"], [])  # unstyled: nothing marks it a heading

    def test_a_style_hint_does_not_override_the_numerals(self):
        # an author who styles every heading "Heading 1": "A." is still a subsection of the facts, not the next section
        body = (para("STATEMENT OF FACTS", style="Heading1", num=(1, 0)) + para("Arrest", style="Heading1", num=(1, 1)) +
                para(FACTS) + para("ARGUMENT", style="Heading1", num=(1, 0)) + para("Law applies."))
        r = self.docx_check(docx(body, numbering=NUM, styles=STYLES))
        self.assertEqual([f.cite for f in r.findings if f.code == "uncited-fact"], [FACTS])

    def test_the_report_lists_the_headings_it_read_and_marks_the_fact_sections(self):
        text = self.docx_check(self.build()).render()
        section = text.split("## Headings read")[1].split("## Proposition")[0]
        self.assertIn("para. 1 (numbered I.) · I. STATEMENT OF FACTS ← fact section", section)
        self.assertIn("· II. ARGUMENT", section)
        self.assertNotIn("II. ARGUMENT ←", section)
        none = self.docx_check(docx(para("Statement of Facts") + para(FACTS))).render()
        self.assertIn("no heading was recognized", none)
        self.assertNotIn("## Headings read", self.m.check("I. FACTS\n\nText.").render())

    def test_complaint_mode_reads_auto_numbered_allegations(self):
        num = numbering_xml({0: [lvl(0, "decimal", "%1.")]}, {1: 0})
        body = "".join(para(t, num=(1, 0)) for t in ["Plaintiff lives in Exampleton.", "Defendant arrested Plaintiff at home."])
        r = self.docx_check(docx(body, numbering=num), mode="complaint")
        self.assertEqual([f for f in r.findings if f.code == "uncited-fact"], [])


class TestFootnoteFlows(DocxBase):
    def test_a_fact_sentence_in_a_footnote_under_the_facts_heading_needs_a_cite(self):
        body = para("STATEMENT OF FACTS", num=(1, 0)) + para("The arrest was on January 1. See Doc. 16 at 1.", fnref(1)) + \
            para("ARGUMENT", num=(1, 0)) + para("Law.", fnref(2))
        r = self.docx_check(docx(body, numbering=NUM, footnotes={1: FACTS, 2: FACTS}))
        self.assertEqual([f.where for f in r.findings if f.code == "uncited-fact"], ["fn. 1"])

    def test_id_in_a_footnote_never_binds_to_a_cite_in_the_body(self):
        body = para("Plaintiff relies on Dietz v. Bouldin, 579 U.S. 40, 45 (2016).", fnref(1))
        r = self.docx_check(docx(body, footnotes={1: "Id. at 46."}))
        self.assertEqual(self.where(r, "id-unbound"), ["fn. 1"])

    def test_id_in_a_footnote_binds_to_the_footnote_before_it(self):
        body = para("First point.", fnref(1)) + para("Second point.", fnref(2))
        r = self.docx_check(docx(body, footnotes={1: "See Dietz v. Bouldin, 579 U.S. 40, 45 (2016).", 2: "Id. at 46."}))
        self.assertEqual(self.where(r, "id-unbound"), [])
        dietz = [a for a in r.authorities if "Dietz" in a.display]
        self.assertEqual(sorted(dietz[0].pins), ["45", "46"])

    def test_id_in_the_body_after_a_footnote_still_binds_to_the_body_cite(self):
        body = (para("Plaintiff relies on Dietz v. Bouldin, 579 U.S. 40, 45 (2016).", fnref(1)) + para("Id. at 47."))
        r = self.docx_check(docx(body, footnotes={1: "See Fed. R. Civ. P. 15(a)(2)."}))
        self.assertEqual(self.where(r, "id-unbound"), [])
        dietz = [a for a in r.authorities if "Dietz" in a.display]
        self.assertEqual(sorted(dietz[0].pins), ["45", "47"])


class TestNothingIsAcceptedSilently(DocxBase):
    INS = '<w:ins w:id="1" w:author="Pat Reviewer">' + run("a new sentence") + "</w:ins>"

    def test_tracked_changes_refuse_the_whole_check(self):
        with self.assertRaisesRegex(ValueError, "tracked changes"):
            self.docx_check(docx(para("Text ", self.INS, ".")))

    def test_comments_hidden_text_and_text_boxes_are_warnings_in_the_report(self):
        box = "<w:r><w:pict><x:t xmlns:x='urn:x'><w:txbxContent>" + para("Doc. 99") + "</w:txbxContent></x:t></w:pict></w:r>"
        r = self.docx_check(docx(para("Body ", run("hid", vanish=True), box, "text."), comments=comments_xml(2)))
        self.assertTrue({"draft-comments", "draft-hidden-text", "draft-text-box"} <= set(codes(r, "WARN")), codes(r, "WARN"))
        self.assertEqual(self.where(r, "draft-comments"), ["document"])
        self.assertEqual(r.exit_code, 0)  # warnings do not change the exit code, but they are printed

    def test_an_unresolvable_list_is_a_warning(self):
        r = self.docx_check(docx(para("ARGUMENT", num=(1, 0))))
        self.assertIn("draft-numbering-unresolved", codes(r, "WARN"))


class TestDispatch(DocxBase):
    def write(self, name, data):
        path = os.path.join(os.path.dirname(self.m.dir), name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def refused(self, name, data, needle):
        with self.assertRaises(ValueError) as cm:
            briefcheck.check(self.m.dir, self.write(name, data), use_pdftotext=False)
        self.assertIn(needle, str(cm.exception))

    def test_a_docx_that_is_not_a_zip_is_refused(self):
        self.refused("d.docx", b"plain text saved as .docx", "not a zip archive")

    def test_old_word_files_are_refused_with_a_way_forward(self):
        self.refused("d.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 old word", "Save As")
        self.refused("d.doc", b"anything", ".docx")

    def test_a_docx_saved_under_a_text_name_is_refused_not_read_as_garbage(self):
        self.refused("d.txt", docx(para("See Doc. 99.")), "rename")
        self.refused("d.md", b"%PDF-1.4\n...", "rename")
        self.refused("d.txt", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 old word", "Save As")
        self.refused("d.txt", b"{\\rtf1\\ansi hello}", ".rtf")

    def test_rtf_and_odt_by_name_are_refused(self):
        self.refused("d.rtf", b"{\\rtf1\\ansi hello}", ".rtf")
        self.refused("d.odt", docx(para("x")), ".odt")

    def test_markdown_and_text_drafts_are_read_exactly_as_before(self):
        r = self.m.check("The order is at Doc. 99.")
        self.assertEqual(r.findings[0].where, "")
        self.assertEqual(r.findings[0].line, 1)
        self.assertNotIn("Draft read", r.render())


class TestCli(DocxBase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, os.path.join(KIT, "matter.py"), *args], capture_output=True, text=True)

    def test_matter_brief_check_reads_a_docx_and_reports_paragraphs_and_footnotes(self):
        path = os.path.join(os.path.dirname(self.m.dir), "brief.docx")
        with open(path, "wb") as f:
            f.write(docx(para("A fact.", fnref(1)) + para("See Doc. 99."), footnotes={1: "Also Doc. 98."}))
        p = self.run_cli("brief", "check", self.m.dir, path)
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn("para. 2", p.stdout)
        self.assertIn("fn. 1", p.stdout)

    def test_the_cli_says_why_it_refused(self):
        path = os.path.join(os.path.dirname(self.m.dir), "old.doc")
        with open(path, "wb") as f:
            f.write(b"x")
        p = self.run_cli("brief", "check", self.m.dir, path)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("brief check refused", p.stderr)
        self.assertIn("Save As", p.stderr)


if __name__ == "__main__":
    unittest.main()
