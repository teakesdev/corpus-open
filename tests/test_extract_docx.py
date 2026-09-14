"""RFC 0005 — DOCX text extraction with STRUCTURAL locators.

Every fixture is synthesised in-process from XML strings + `zipfile`; nothing
binary is committed and no client document is touched.

The central invariant under test: a DOCX extraction never produces, stores, or
implies a page number. DOCX pagination is a property of the rendering engine,
not of the file, so a page number here would be a fabricated citation.
"""
import hashlib
import io
import os
import sqlite3
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matterkit import extract  # noqa: E402

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
STRICT_NS = "http://purl.oclc.org/ooxml/wordprocessingml/main"

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Target="word/document.xml" Type="http://schemas.'
    'openxmlformats.org/officeDocument/2006/relationships/officeDocument"/>'
    "</Relationships>"
)


def document_xml(body_inner: str, ns: str = W_NS) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{ns}"><w:body>{body_inner}</w:body></w:document>'
    )


def para(text: str) -> str:
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def build_docx_bytes(parts: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return buf.getvalue()


def standard_parts(body_inner: str, ns: str = W_NS) -> dict:
    return {
        "[Content_Types].xml": CONTENT_TYPES,
        "_rels/.rels": RELS,
        "word/document.xml": document_xml(body_inner, ns=ns),
    }


class DocxFixture:
    """Context manager writing raw bytes to a temp file with a chosen suffix."""

    def __init__(self, data: bytes, suffix: str = ".docx"):
        self.data = data
        self.suffix = suffix

    def __enter__(self) -> str:
        fd, self.path = tempfile.mkstemp(suffix=self.suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(self.data)
        return self.path

    def __exit__(self, *exc):
        os.unlink(self.path)
        return False


def docx_file(body_inner: str, ns: str = W_NS) -> DocxFixture:
    return DocxFixture(build_docx_bytes(standard_parts(body_inner, ns=ns)))


# --------------------------------------------------------------------------
# Locator contract
# --------------------------------------------------------------------------
class TestLocatorContract(unittest.TestCase):
    def test_body_paragraph_locators_carry_structural_path(self):
        with docx_file(para("First paragraph.") + para("Second paragraph.")) as p:
            doc = extract.extract_docx(p)
        self.assertEqual(doc.status, "extracted")
        self.assertEqual(doc.locator_scheme, extract.LOCATOR_SCHEME_DOCX)
        self.assertEqual(len(doc.blocks), 2)
        first, second = doc.blocks
        self.assertEqual(first.text, "First paragraph.")
        self.assertEqual(second.text, "Second paragraph.")
        self.assertEqual(first.locator.part, "word/document.xml")
        self.assertEqual(first.locator.container_type, "paragraph")
        self.assertEqual(first.locator.path, (0,))
        self.assertEqual(second.locator.path, (1,))
        self.assertEqual(first.index, 0)
        self.assertEqual(second.index, 1)

    def test_canonical_locator_round_trips_through_parse(self):
        with docx_file(para("Alpha") + para("Beta")) as p:
            doc = extract.extract_docx(p)
        for block in doc.blocks:
            canonical = block.locator.canonical()
            self.assertEqual(extract.DocxLocator.parse(canonical), block.locator)
            self.assertEqual(extract.DocxLocator.parse(canonical).canonical(), canonical)

    def test_text_sha256_is_the_sha_of_the_block_text(self):
        with docx_file(para("Alpha") + para("Beta")) as p:
            doc = extract.extract_docx(p)
        for block in doc.blocks:
            self.assertEqual(
                block.text_sha256,
                hashlib.sha256(block.text.encode("utf-8")).hexdigest(),
            )

    def test_run_count_reflects_the_runs_traversed_in_the_paragraph(self):
        body = (
            "<w:p>"
            "<w:r><w:t>Split </w:t></w:r>"
            "<w:r><w:t>across </w:t></w:r>"
            "<w:r><w:t>three runs.</w:t></w:r>"
            "</w:p>"
        )
        with docx_file(body) as p:
            doc = extract.extract_docx(p)
        self.assertEqual(doc.blocks[0].text, "Split across three runs.")
        self.assertEqual(doc.blocks[0].run_count, 3)
        # run indexes are diagnostic only -- the citable position is the char span
        self.assertEqual(doc.blocks[0].char_end - doc.blocks[0].char_start,
                         len("Split across three runs."))

    def test_identical_paragraph_text_still_yields_distinct_locators(self):
        with docx_file(para("Same text.") * 1 + para("Same text.")) as p:
            doc = extract.extract_docx(p)
        self.assertEqual(len(doc.blocks), 2)
        self.assertEqual(doc.blocks[0].text, doc.blocks[1].text)
        self.assertEqual(doc.blocks[0].text_sha256, doc.blocks[1].text_sha256)
        self.assertNotEqual(
            doc.blocks[0].locator.canonical(), doc.blocks[1].locator.canonical()
        )


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------
TABLE_2X2 = (
    "<w:tbl><w:tblPr/><w:tblGrid/>"
    "<w:tr><w:trPr/>"
    f"<w:tc><w:tcPr/>{para('R0C0')}</w:tc>"
    f"<w:tc><w:tcPr/>{para('R0C1')}</w:tc>"
    "</w:tr>"
    "<w:tr>"
    f"<w:tc>{para('R1C0')}</w:tc>"
    f"<w:tc>{para('R1C1')}</w:tc>"
    "</w:tr></w:tbl>"
)


class TestTableLocators(unittest.TestCase):
    def test_two_by_two_table_addresses_four_cells_distinctly(self):
        with docx_file(TABLE_2X2) as p:
            doc = extract.extract_docx(p)
        self.assertEqual(doc.status, "extracted")
        self.assertEqual(len(doc.blocks), 4)
        by_text = {b.text: b.locator for b in doc.blocks}
        self.assertEqual(sorted(by_text), ["R0C0", "R0C1", "R1C0", "R1C1"])
        self.assertEqual(by_text["R0C0"].path, (0, 0, 0, 0))
        self.assertEqual(by_text["R0C1"].path, (0, 0, 1, 0))
        self.assertEqual(by_text["R1C0"].path, (0, 1, 0, 0))
        self.assertEqual(by_text["R1C1"].path, (0, 1, 1, 0))
        for loc in by_text.values():
            self.assertEqual(loc.container_type, "table-cell")
        self.assertEqual(len({loc.canonical() for loc in by_text.values()}), 4)

    def test_multi_paragraph_cell_addresses_each_paragraph(self):
        tbl = (
            "<w:tbl><w:tr><w:tc>"
            + para("Cell line one")
            + para("Cell line two")
            + "</w:tc></w:tr></w:tbl>"
        )
        with docx_file(tbl) as p:
            doc = extract.extract_docx(p)
        self.assertEqual([b.text for b in doc.blocks], ["Cell line one", "Cell line two"])
        self.assertEqual(doc.blocks[0].locator.path, (0, 0, 0, 0))
        self.assertEqual(doc.blocks[1].locator.path, (0, 0, 0, 1))

    def test_nested_table_paths_remain_unique_and_typed(self):
        inner = "<w:tbl><w:tr><w:tc>" + para("Nested") + "</w:tc></w:tr></w:tbl>"
        outer = "<w:tbl><w:tr><w:tc>" + para("Outer") + inner + "</w:tc></w:tr></w:tbl>"
        with docx_file(outer) as p:
            doc = extract.extract_docx(p)
        paths = [b.locator.path for b in doc.blocks]
        self.assertEqual(paths, [(0, 0, 0, 0), (0, 0, 0, 1, 0, 0, 0)])
        self.assertEqual(len(set(paths)), len(paths))
        self.assertTrue(all(len(pth) % 3 == 1 for pth in paths))

    def test_every_locator_in_a_mixed_document_is_unique(self):
        body = para("Intro") + TABLE_2X2 + para("Outro") + TABLE_2X2
        with docx_file(body) as p:
            doc = extract.extract_docx(p)
        canonicals = [b.locator.canonical() for b in doc.blocks]
        self.assertEqual(len(canonicals), 10)
        self.assertEqual(len(set(canonicals)), len(canonicals))


# --------------------------------------------------------------------------
# Offsets
# --------------------------------------------------------------------------
class TestOffsetRoundTrip(unittest.TestCase):
    def test_char_offsets_slice_the_exact_block_text(self):
        body = para("Intro") + TABLE_2X2 + para("Outro")
        with docx_file(body) as p:
            doc = extract.extract_docx(p)
        self.assertTrue(doc.text)
        for block in doc.blocks:
            self.assertEqual(doc.text[block.char_start:block.char_end], block.text)
        self.assertEqual(
            doc.text_sha256, hashlib.sha256(doc.text.encode("utf-8")).hexdigest()
        )

    def test_offsets_are_monotonic_and_non_overlapping(self):
        body = para("One") + para("Two") + para("Three")
        with docx_file(body) as p:
            doc = extract.extract_docx(p)
        prev_end = -1
        for block in doc.blocks:
            self.assertGreater(block.char_start, prev_end)
            self.assertEqual(block.char_end, block.char_start + len(block.text))
            prev_end = block.char_end

    def test_tabs_and_breaks_render_into_the_block_text(self):
        body = (
            "<w:p><w:r><w:t>A</w:t><w:tab/><w:t>B</w:t><w:br/><w:t>C</w:t></w:r></w:p>"
        )
        with docx_file(body) as p:
            doc = extract.extract_docx(p)
        self.assertEqual(doc.blocks[0].text, "A\tB\nC")
        self.assertEqual(doc.text[doc.blocks[0].char_start:doc.blocks[0].char_end],
                         "A\tB\nC")

    def test_tracked_deletion_text_is_not_extracted(self):
        body = (
            "<w:p>"
            "<w:r><w:t>Kept. </w:t></w:r>"
            "<w:del><w:r><w:delText>Struck out.</w:delText></w:r></w:del>"
            "<w:ins><w:r><w:t>Inserted.</w:t></w:r></w:ins>"
            "</w:p>"
        )
        with docx_file(body) as p:
            doc = extract.extract_docx(p)
        self.assertEqual(doc.blocks[0].text, "Kept. Inserted.")

    def test_field_instruction_codes_are_not_extracted(self):
        body = (
            "<w:p>"
            "<w:r><w:instrText> HYPERLINK \"http://example.test\" </w:instrText></w:r>"
            "<w:r><w:t>Visible label</w:t></w:r>"
            "</w:p>"
        )
        with docx_file(body) as p:
            doc = extract.extract_docx(p)
        self.assertEqual(doc.blocks[0].text, "Visible label")


# --------------------------------------------------------------------------
# Honest failure vocabulary
# --------------------------------------------------------------------------
class TestHonestFailure(unittest.TestCase):
    def assert_failed(self, doc, needle):
        self.assertEqual(doc.status, "extraction-failed")
        self.assertEqual(doc.blocks, [])
        self.assertEqual(doc.pages, [])
        self.assertIsNotNone(doc.reason)
        self.assertIn(needle, doc.reason.lower())

    def test_truncated_zip_fails(self):
        data = build_docx_bytes(standard_parts(para("Hello")))
        with DocxFixture(data[: len(data) // 2]) as p:
            self.assert_failed(extract.extract_docx(p), "zip")

    def test_renamed_text_file_is_refused_not_redispatched(self):
        with DocxFixture(b"This is plain text, not a docx.\n") as p:
            doc = extract.extract_docx(p)
            self.assert_failed(doc, "zip")
            self.assertEqual(extract.extract_text(p).status, "extraction-failed")

    def test_renamed_pdf_is_refused(self):
        with DocxFixture(b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\n%%EOF\n") as p:
            self.assert_failed(extract.extract_docx(p), "zip")

    def test_zip_without_document_xml_fails(self):
        data = build_docx_bytes({"[Content_Types].xml": CONTENT_TYPES, "_rels/.rels": RELS})
        with DocxFixture(data) as p:
            self.assert_failed(extract.extract_docx(p), "word/document.xml")

    def test_blank_document_xml_fails(self):
        parts = standard_parts(para("x"))
        parts["word/document.xml"] = "   \n  "
        with DocxFixture(build_docx_bytes(parts)) as p:
            self.assert_failed(extract.extract_docx(p), "empty")

    def test_malformed_xml_fails_without_partial_salvage(self):
        parts = standard_parts(para("x"))
        parts["word/document.xml"] = (
            '<?xml version="1.0"?>'
            f'<w:document xmlns:w="{W_NS}"><w:body>'
            "<w:p><w:r><w:t>Salvageable looking text</w:t></w:r></w:p>"
        )  # never closed
        with DocxFixture(build_docx_bytes(parts)) as p:
            doc = extract.extract_docx(p)
            self.assert_failed(doc, "xml")
            self.assertNotIn("Salvageable", doc.text)

    def test_wrong_root_element_fails(self):
        parts = standard_parts(para("x"))
        parts["word/document.xml"] = f'<w:styles xmlns:w="{W_NS}"/>'
        with DocxFixture(build_docx_bytes(parts)) as p:
            self.assert_failed(extract.extract_docx(p), "root")

    def test_missing_body_fails(self):
        parts = standard_parts(para("x"))
        parts["word/document.xml"] = f'<w:document xmlns:w="{W_NS}"/>'
        with DocxFixture(build_docx_bytes(parts)) as p:
            self.assert_failed(extract.extract_docx(p), "body")

    def test_ooxml_strict_namespace_fails_rather_than_returning_empty(self):
        with docx_file(para("Strict namespace text"), ns=STRICT_NS) as p:
            self.assert_failed(extract.extract_docx(p), "namespace")

    def test_encrypted_part_fails(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("word/document.xml", document_xml(para("secret")))
        raw = bytearray(buf.getvalue())
        # set the "encrypted" general-purpose bit on the local header + central dir
        for magic in (b"PK\x03\x04", b"PK\x01\x02"):
            idx = raw.find(magic)
            while idx != -1:
                flag_at = idx + (6 if magic == b"PK\x03\x04" else 8)
                raw[flag_at] |= 0x01
                idx = raw.find(magic, idx + 1)
        with DocxFixture(bytes(raw)) as p:
            self.assert_failed(extract.extract_docx(p), "encrypt")

    def test_oversized_part_is_refused(self):
        body = "".join(para("padding " * 64) for _ in range(200))
        with docx_file(body) as p:
            doc = extract.extract_docx(p, max_part_bytes=1024)
            self.assert_failed(doc, "too large")

    def test_empty_but_wellformed_document_succeeds_with_zero_blocks(self):
        with docx_file("<w:sectPr/>") as p:
            doc = extract.extract_docx(p)
        self.assertEqual(doc.status, "extracted")
        self.assertEqual(doc.blocks, [])
        self.assertEqual(doc.text, "")
        self.assertIsNone(doc.reason)

    def test_empty_paragraph_is_an_addressable_block_not_a_dropped_one(self):
        with docx_file(para("Before") + "<w:p/>" + para("After")) as p:
            doc = extract.extract_docx(p)
        self.assertEqual([b.text for b in doc.blocks], ["Before", "", "After"])
        self.assertEqual(doc.blocks[1].locator.path, (1,))
        self.assertEqual(doc.blocks[1].status, "extracted")


# --------------------------------------------------------------------------
# No fabricated pagination, anywhere
# --------------------------------------------------------------------------
class TestNoFabricatedPagination(unittest.TestCase):
    def test_docx_extraction_yields_no_pages(self):
        body = para("Intro") + TABLE_2X2 + "<w:p><w:r><w:br w:type=\"page\"/></w:r></w:p>"
        with docx_file(body) as p:
            doc = extract.extract_docx(p)
        self.assertEqual(doc.pages, [])
        self.assertEqual(doc.locator_scheme, extract.LOCATOR_SCHEME_DOCX)
        for block in doc.blocks:
            self.assertFalse(hasattr(block.locator, "page_number"))
            self.assertNotIn("page", block.locator.canonical().lower())

    def test_explicit_page_break_does_not_create_a_page_index(self):
        body = (
            para("Before break")
            + "<w:p><w:r><w:br w:type=\"page\"/><w:t>After break</w:t></w:r></w:p>"
        )
        with docx_file(body) as p:
            doc = extract.extract_docx(p)
        self.assertEqual(len(doc.blocks), 2)
        self.assertEqual(doc.blocks[1].locator.path, (1,))
        self.assertEqual(doc.blocks[1].text, "\nAfter break")


# --------------------------------------------------------------------------
# Part-set policy
# --------------------------------------------------------------------------
class TestPartInventory(unittest.TestCase):
    def test_inventory_separates_extracted_skipped_and_seen(self):
        parts = standard_parts(para("Body text"))
        parts["word/header1.xml"] = document_xml(para("Header text"))
        parts["word/footer1.xml"] = document_xml(para("Footer text"))
        parts["word/footnotes.xml"] = document_xml(para("Footnote text"))
        parts["word/theme/theme1.xml"] = "<a/>"
        with DocxFixture(build_docx_bytes(parts)) as p:
            doc = extract.extract_docx(p)
        self.assertEqual(doc.status, "extracted")
        self.assertEqual(doc.parts.extracted, ("word/document.xml",))
        self.assertEqual(
            sorted(doc.parts.skipped),
            ["word/footer1.xml", "word/footnotes.xml", "word/header1.xml"],
        )
        self.assertIn("word/theme/theme1.xml", doc.parts.seen)
        self.assertEqual([b.text for b in doc.blocks], ["Body text"])

    def test_unhandled_block_level_elements_are_named_not_silently_dropped(self):
        body = para("Visible") + "<w:sdt><w:sdtContent>" + para("Inside control") + "</w:sdtContent></w:sdt>"
        with docx_file(body) as p:
            doc = extract.extract_docx(p)
        self.assertEqual([b.text for b in doc.blocks], ["Visible"])
        self.assertIn("sdt", doc.parts.unhandled)

    def test_clean_document_reports_no_unhandled_elements(self):
        with docx_file(para("Just a paragraph.") + TABLE_2X2) as p:
            doc = extract.extract_docx(p)
        self.assertEqual(doc.parts.unhandled, ())


# --------------------------------------------------------------------------
# Immutability + dispatch + dependency floor
# --------------------------------------------------------------------------
class TestSourceImmutability(unittest.TestCase):
    def test_source_bytes_unchanged_by_extraction(self):
        data = build_docx_bytes(standard_parts(para("Hello") + TABLE_2X2))
        before = hashlib.sha256(data).hexdigest()
        with DocxFixture(data) as p:
            extract.extract_docx(p)
            extract.extract_text(p)
            after = hashlib.sha256(Path(p).read_bytes()).hexdigest()
            mtime_stable = os.path.getsize(p)
        self.assertEqual(before, after)
        self.assertEqual(mtime_stable, len(data))


class TestDispatch(unittest.TestCase):
    def test_extract_text_routes_docx_to_the_structural_extractor(self):
        with docx_file(para("Dispatched.")) as p:
            doc = extract.extract_text(p)
        self.assertEqual(doc.kind, "docx")
        self.assertEqual(doc.locator_scheme, extract.LOCATOR_SCHEME_DOCX)
        self.assertEqual([b.text for b in doc.blocks], ["Dispatched."])

    def test_uppercase_extension_still_routes(self):
        data = build_docx_bytes(standard_parts(para("Shouty.")))
        with DocxFixture(data, suffix=".DOCX") as p:
            doc = extract.extract_text(p)
        self.assertEqual(doc.kind, "docx")

    def test_legacy_doc_is_not_treated_as_docx(self):
        data = build_docx_bytes(standard_parts(para("Not legacy.")))
        with DocxFixture(data, suffix=".doc") as p:
            doc = extract.extract_text(p)
        self.assertEqual(doc.status, "extraction-failed")
        self.assertNotEqual(doc.kind, "docx")

    def test_unknown_extension_uses_the_whole_file_scheme(self):
        doc = extract.extract_text("nonexistent/whatever.bin")
        self.assertEqual(doc.locator_scheme, extract.LOCATOR_SCHEME_WHOLE_FILE)
        self.assertEqual(doc.status, "extraction-failed")

    def test_pdf_path_keeps_the_page_locator_scheme(self):
        import zlib

        stream = zlib.compress(b"BT (Page one text.) Tj ET")
        pdf = (
            b"%PDF-1.4\n1 0 obj\n<< /Length "
            + str(len(stream)).encode()
            + b" /Filter /FlateDecode >>\nstream\n"
            + stream
            + b"\nendstream\nendobj\n%%EOF\n"
        )
        with DocxFixture(pdf, suffix=".pdf") as p:
            doc = extract.extract_text(p)
        self.assertEqual(doc.kind, "pdf")
        self.assertEqual(doc.locator_scheme, extract.LOCATOR_SCHEME_PDF_PAGE)
        self.assertEqual(doc.blocks, [])


class TestStdlibOnlyFloor(unittest.TestCase):
    def test_no_third_party_module_is_imported(self):
        self.assertNotIn("docx", sys.modules)
        self.assertNotIn("lxml", sys.modules)
        self.assertNotIn("pypdf", sys.modules)
        self.assertNotIn("pdfplumber", sys.modules)

    def test_module_imports_with_third_party_paths_removed(self):
        import subprocess

        repo = str(Path(__file__).resolve().parent.parent)
        code = (
            "import sys, sysconfig\n"
            f"sys.path = [p for p in sys.path if 'site-packages' not in p]\n"
            f"sys.path.insert(0, {repo!r})\n"
            "import matterkit.extract as e\n"
            "assert hasattr(e, 'extract_docx')\n"
            "print('ok')\n"
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("ok", out.stdout)


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------
class TestBlockStorage(unittest.TestCase):
    def setUp(self):
        from matterkit import store

        self.tmp = tempfile.mkdtemp()
        self.conn = store.connect(self.tmp)

    def tearDown(self):
        self.conn.close()

    def test_document_blocks_table_has_no_page_number_column(self):
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(document_blocks)")]
        self.assertTrue(cols, "document_blocks table missing")
        self.assertNotIn("page_number", cols)
        self.assertIn("locator", cols)
        self.assertIn("locator_scheme", cols)
        self.assertIn("char_start", cols)

    def test_record_blocks_persists_sliceable_locators(self):
        body = para("Intro") + TABLE_2X2 + para("Outro")
        with docx_file(body) as p:
            doc = extract.extract_docx(p)
            sha = hashlib.sha256(Path(p).read_bytes()).hexdigest()
        n = extract.record_blocks(self.conn, sha, doc)
        self.assertEqual(n, len(doc.blocks))
        rows = self.conn.execute(
            "SELECT * FROM document_blocks WHERE document_sha256=? ORDER BY block_index",
            (sha,),
        ).fetchall()
        self.assertEqual(len(rows), 6)
        for row, block in zip(rows, doc.blocks):
            self.assertEqual(row["locator"], block.locator.canonical())
            self.assertEqual(row["locator_scheme"], extract.LOCATOR_SCHEME_DOCX)
            self.assertEqual(row["text"], block.text)
            self.assertEqual(row["text_sha256"], block.text_sha256)
            self.assertEqual(doc.text[row["char_start"]:row["char_end"]], row["text"])

    def test_record_blocks_is_idempotent_on_reextraction(self):
        with docx_file(para("A") + para("B")) as p:
            doc = extract.extract_docx(p)
            sha = hashlib.sha256(Path(p).read_bytes()).hexdigest()
        extract.record_blocks(self.conn, sha, doc)
        extract.record_blocks(self.conn, sha, doc)
        count = self.conn.execute(
            "SELECT COUNT(*) FROM document_blocks WHERE document_sha256=?", (sha,)
        ).fetchone()[0]
        self.assertEqual(count, 2)

    def test_record_blocks_does_not_commit_caller_transaction(self):
        """The caller owns the transaction: record_blocks never commits."""
        with docx_file(para("A") + para("B")) as p:
            doc = extract.extract_docx(p)
            sha = hashlib.sha256(Path(p).read_bytes()).hexdigest()
        self.conn.execute("BEGIN")
        n = extract.record_blocks(self.conn, sha, doc)
        self.assertEqual(n, len(doc.blocks))
        self.conn.rollback()
        count = self.conn.execute(
            "SELECT COUNT(*) FROM document_blocks"
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_failed_extraction_records_one_failure_row_and_no_content(self):
        with DocxFixture(b"not a zip at all") as p:
            doc = extract.extract_docx(p)
            sha = hashlib.sha256(Path(p).read_bytes()).hexdigest()
        extract.record_blocks(self.conn, sha, doc)
        rows = self.conn.execute(
            "SELECT * FROM document_blocks WHERE document_sha256=?", (sha,)
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "extraction-failed")
        self.assertIsNone(rows[0]["text"])
        self.assertIsNotNone(rows[0]["reason"])

    def test_record_blocks_refuses_a_page_scheme_extraction(self):
        """The block store cannot be used to launder a page locator."""
        doc = extract.DocumentText(
            path="x.pdf", kind="pdf",
            locator_scheme=extract.LOCATOR_SCHEME_PDF_PAGE)
        with self.assertRaises(ValueError):
            extract.record_blocks(self.conn, "deadbeef", doc)

    def test_status_column_rejects_invented_statuses(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO document_blocks (document_sha256, locator_scheme, part, "
                "locator, block_index, container_type, path, status) "
                "VALUES ('x','docx-structural','word/document.xml','w#b0',0,"
                "'paragraph','[0]','probably-fine')"
            )

    def test_locator_scheme_column_rejects_a_page_scheme_for_blocks(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO document_blocks (document_sha256, locator_scheme, part, "
                "locator, block_index, container_type, path, status) "
                "VALUES ('x','pdf-page','word/document.xml','w#b0',0,"
                "'paragraph','[0]','extracted')"
            )


# --------------------------------------------------------------------------
# Hostile archives and hostile XML
# --------------------------------------------------------------------------
class TestHostileInput(unittest.TestCase):
    def assert_failed(self, doc, needle):
        self.assertEqual(doc.status, "extraction-failed")
        self.assertEqual(doc.blocks, [])
        self.assertEqual(doc.pages, [])
        self.assertIn(needle, doc.reason.lower())

    def test_doctype_declaration_is_refused(self):
        """An entity-expansion bomb never reaches the parser: OOXML has no DTD."""
        parts = standard_parts(para("x"))
        parts["word/document.xml"] = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE w:document ['
            '<!ENTITY a "aaaaaaaaaa">'
            '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
            ']>'
            f'<w:document xmlns:w="{W_NS}"><w:body>'
            "<w:p><w:r><w:t>&c;</w:t></w:r></w:p>"
            "</w:body></w:document>"
        )
        with DocxFixture(build_docx_bytes(parts)) as p:
            self.assert_failed(extract.extract_docx(p), "doctype")

    def test_doctype_behind_a_utf8_bom_is_still_refused(self):
        """The prolog scan must not be defeated by a byte-order mark."""
        parts = standard_parts(para("x"))
        xml = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE w:document [<!ENTITY a "aaaaaaaaaa">]>'
            f'<w:document xmlns:w="{W_NS}"><w:body>'
            "<w:p><w:r><w:t>&a;</w:t></w:r></w:p>"
            "</w:body></w:document>"
        )
        parts["word/document.xml"] = b"\xef\xbb\xbf" + xml.encode("utf-8")
        with DocxFixture(build_docx_bytes(parts)) as p:
            self.assert_failed(extract.extract_docx(p), "doctype")

    def test_plain_utf8_bom_document_still_extracts(self):
        parts = standard_parts(para("Byte order marked."))
        parts["word/document.xml"] = (
            b"\xef\xbb\xbf" + parts["word/document.xml"].encode("utf-8"))
        with DocxFixture(build_docx_bytes(parts)) as p:
            doc = extract.extract_docx(p)
        self.assertEqual(doc.status, "extracted")
        self.assertEqual([b.text for b in doc.blocks], ["Byte order marked."])

    def test_utf16_document_part_is_refused_rather_than_scanned_blind(self):
        """A UTF-16 part cannot be byte-scanned for a DOCTYPE, so it is refused."""
        parts = standard_parts(para("x"))
        parts["word/document.xml"] = document_xml(para("x")).replace(
            'encoding="UTF-8"', 'encoding="UTF-16"').encode("utf-16")
        with DocxFixture(build_docx_bytes(parts)) as p:
            self.assert_failed(extract.extract_docx(p), "encoding")

    def test_duplicate_document_part_is_refused_as_ambiguous(self):
        import warnings

        buf = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("word/document.xml", document_xml(para("Decoy")))
                zf.writestr("word/document.xml", document_xml(para("Payload")))
        with DocxFixture(buf.getvalue()) as p:
            self.assert_failed(extract.extract_docx(p), "duplicate")

    def test_crc_mismatch_in_the_document_part_fails(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            for name, data in standard_parts(para("AAAAAAAA")).items():
                zf.writestr(name, data)
        raw = buf.getvalue()
        self.assertIn(b"AAAAAAAA", raw)
        tampered = raw.replace(b"AAAAAAAA", b"BBBBBBBB")   # same length, wrong CRC
        with DocxFixture(tampered) as p:
            doc = extract.extract_docx(p)
        self.assertEqual(doc.status, "extraction-failed")
        self.assertEqual(doc.blocks, [])
        self.assertNotIn("BBBBBBBB", doc.text)

    def test_zip_signature_check_precedes_any_parsing(self):
        """A polyglot that only *ends* in a zip is still refused on its head."""
        inner = build_docx_bytes(standard_parts(para("Hidden")))
        with DocxFixture(b"%PDF-1.4\n" + inner) as p:
            self.assert_failed(extract.extract_docx(p), "zip")


# --------------------------------------------------------------------------
# Resource bounds (RFC 0005 §4a): the pre-open GUARD vs the post-open
# ACCEPTANCE LIMIT. The spy below is the whole point of these tests:
# `size_limit:members` must refuse with ZERO ZipFile constructions (the guard
# ran before `ZipFile.__init__` materialised a ZipInfo per entry), while
# `size_limit:aggregate` must refuse with AT LEAST ONE (the acceptance limit
# runs post-open, after the directory was already parsed). If the two labels
# were swapped, the construction counts would swap with them.
# --------------------------------------------------------------------------
class ZipFileSpy:
    """Replaces `zipfile.ZipFile` AS SEEN BY `matterkit.extract` and counts
    constructions, delegating to the real class so post-open paths still run.

    Captures the real class at construction time -- build the spy BEFORE the
    patch is active, and build any fixture bytes with `zipfile` before too.
    """

    def __init__(self):
        self._real = zipfile.ZipFile
        self.constructions = 0

    def __call__(self, *args, **kwargs):
        self.constructions += 1
        return self._real(*args, **kwargs)


def hand_eocd(entries: int, cd_size: int, cd_offset: int) -> bytes:
    """Hand-built End Of Central Directory record, 22 bytes (APPNOTE 4.3.16).
    Entries total at +10 (2B), cd_size at +12 (4B), cd_offset at +16 (4B)."""
    return (b"PK\x05\x06"
            + struct.pack("<HHHHIIH", 0, 0, entries, entries,
                          cd_size, cd_offset, 0))


def hand_z64_locator(z64_offset: int) -> bytes:
    """Hand-built ZIP64 EOCD locator, 20 bytes; record offset at +8 (8B)."""
    return b"PK\x06\x07" + struct.pack("<IQI", 0, z64_offset, 1)


def hand_z64_eocd(entries_total: int, cd_size: int, cd_offset: int,
                  rec_size: int = 44) -> bytes:
    """Hand-built ZIP64 EOCD record, 56-byte fixed portion (APPNOTE 4.3.14).
    Record size at +4 (8B), total entries at +32 (8B), cd_size at +40 (8B),
    cd_offset at +48 (8B)."""
    return (b"PK\x06\x06"
            + struct.pack("<QHHIIQQQQ", rec_size, 45, 45, 0, 0,
                          entries_total, entries_total, cd_size, cd_offset))


def synthetic_zip(tail: bytes, filler_len: int) -> bytes:
    """A file whose head is a zip signature and whose tail is hand-built.

    The pre-open reader sees only head magic + tail records, so synthetic
    bytes test the guard faithfully -- no need for 65,536+ real members
    (`zipfile` will not emit ZIP64 for few entries, and a real ZIP64 fixture
    would be ~6 MB and slow). Every fixture here is refused pre-open or
    parsed by the reader directly, so the filler is never interpreted.
    """
    return b"PK\x03\x04" + b"\x00" * filler_len + tail


def many_member_zip(n: int) -> bytes:
    """A real (non-ZIP64) zip with `n` tiny members. ~0.05 s for 10k."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for i in range(n):
            zf.writestr(f"m{i:05d}", b"x")
    return buf.getvalue()


def zip64_declared_sizes(raw: bytes, declared: int) -> bytes:
    """Rewrite every central-directory entry to declare `declared` uncompressed
    bytes the ZIP64 way: the classic 4-byte field saturated to 0xFFFFFFFF and
    the true 64-bit size carried in an extra field (header 0x0001, APPNOTE
    4.5.3) -- exactly how a member over 4 GiB declares itself.

    Only the directory is rewritten; no member is remotely that large, which is
    the point: the acceptance limit sums DECLARED sizes. A reader that took the
    saturated 32-bit field at face value would compute a different (smaller)
    total, so the asserted total is what proves the ZIP64 field was honoured.
    """
    eocd = raw.rfind(b"PK\x05\x06")
    count, = struct.unpack("<H", raw[eocd + 10:eocd + 12])
    cd_offset, = struct.unpack("<I", raw[eocd + 16:eocd + 20])
    cd, pos = bytearray(), cd_offset
    for _ in range(count):
        assert raw[pos:pos + 4] == b"PK\x01\x02", "not a central-directory entry"
        name_len, extra_len, comment_len = struct.unpack("<HHH", raw[pos + 28:pos + 34])
        head = bytearray(raw[pos:pos + 46])
        name = raw[pos + 46:pos + 46 + name_len]
        comment = raw[pos + 46 + name_len + extra_len:
                      pos + 46 + name_len + extra_len + comment_len]
        head[24:28] = b"\xff\xff\xff\xff"          # uncompressed size -> sentinel
        extra = struct.pack("<HHQ", 0x0001, 8, declared)
        head[30:32] = struct.pack("<H", len(extra))  # extra field length
        cd += bytes(head) + name + extra + comment
        pos += 46 + name_len + extra_len + comment_len
    # The directory grew, so the EOCD's cd_size must follow it; cd_offset is
    # unchanged (the directory still starts where the member data ends).
    return raw[:cd_offset] + bytes(cd) + hand_eocd(count, len(cd), cd_offset)


class TestArchiveMembersGuard(unittest.TestCase):
    """The GUARD: `size_limit:members`, pre-open, no ZipFile constructed."""

    def assert_failed(self, doc, needle):
        self.assertEqual(doc.status, "extraction-failed")
        self.assertEqual(doc.blocks, [])
        self.assertEqual(doc.pages, [])
        self.assertIsNotNone(doc.reason)
        self.assertIn(needle, doc.reason.lower())

    def test_10001_tiny_members_refused_pre_open_spy_untouched(self):
        data = many_member_zip(extract.MAX_ARCHIVE_MEMBERS + 1)
        # sanity: a real 10,001-member zip is NOT ZIP64, so this exercises
        # the classic EOCD path (entries total at EOCD+10).
        self.assertNotIn(b"PK\x06\x07", data[-64:])
        spy = ZipFileSpy()
        with DocxFixture(data) as p:
            with mock.patch.object(extract.zipfile, "ZipFile", spy):
                doc = extract.extract_docx(p)
        self.assertIsNotNone(doc.reason)
        self.assertTrue(doc.reason.startswith("size_limit:members"), doc.reason)
        self.assert_failed(doc, "size_limit:members")
        # THE POINT: the guard fired before `zipfile.ZipFile` was constructed.
        self.assertEqual(spy.constructions, 0)

    def test_zip64_handbuilt_tail_declaring_10001_refused_spy_untouched(self):
        # Sentinel EOCD (entries 0xFFFF, cd_size 0xFFFFFFFF; cd_offset a
        # perfectly normal 32-bit value -- sentinels fire per field) +
        # locator + ZIP64 EOCD record declaring count 10,001.
        filler = 460_000
        z64_off = 4 + filler
        cd_size = (extract.MAX_ARCHIVE_MEMBERS + 1) * 46
        tail = (hand_z64_eocd(extract.MAX_ARCHIVE_MEMBERS + 1, cd_size, 4)
                + hand_z64_locator(z64_off)
                + hand_eocd(0xFFFF, 0xFFFFFFFF, 4))
        spy = ZipFileSpy()
        with DocxFixture(synthetic_zip(tail, filler)) as p:
            with mock.patch.object(extract.zipfile, "ZipFile", spy):
                doc = extract.extract_docx(p)
        self.assertIsNotNone(doc.reason)
        self.assertTrue(doc.reason.startswith("size_limit:members"), doc.reason)
        self.assert_failed(doc, "size_limit:members")
        self.assertEqual(spy.constructions, 0)

    def test_exactly_max_archive_members_passes_the_guard_then_fails_part_policy(self):
        data = many_member_zip(extract.MAX_ARCHIVE_MEMBERS)
        spy = ZipFileSpy()
        with DocxFixture(data) as p:
            with mock.patch.object(extract.zipfile, "ZipFile", spy):
                doc = extract.extract_docx(p)
        # The guard does NOT fire at exactly the limit; ZipFile IS constructed,
        # and the archive then fails on part policy (no word/document.xml).
        self.assertEqual(spy.constructions, 1)
        self.assert_failed(doc, "word/document.xml")
        self.assertFalse(doc.reason.startswith("size_limit:"))

    def test_sentinel_without_locator_is_corrupt_spy_untouched(self):
        data = bytearray(build_docx_bytes(standard_parts(para("x"))))
        eocd = data.rfind(b"PK\x05\x06")
        data[eocd + 10:eocd + 12] = struct.pack("<H", 0xFFFF)  # lie: sentinel
        spy = ZipFileSpy()
        with DocxFixture(bytes(data)) as p:
            with mock.patch.object(extract.zipfile, "ZipFile", spy):
                doc = extract.extract_docx(p)
        self.assert_failed(doc, "corrupt or truncated")
        self.assert_failed(doc, "locator")
        self.assertEqual(spy.constructions, 0)

    def test_locator_pointing_at_garbage_is_corrupt_spy_untouched(self):
        filler = 2_000
        z64_off = 4 + filler
        tail = (b"\x00" * 56                      # locator target: garbage
                + hand_z64_locator(z64_off)
                + hand_eocd(0xFFFF, 0xFFFFFFFF, 4))
        spy = ZipFileSpy()
        with DocxFixture(synthetic_zip(tail, filler)) as p:
            with mock.patch.object(extract.zipfile, "ZipFile", spy):
                doc = extract.extract_docx(p)
        self.assert_failed(doc, "corrupt or truncated")
        self.assertEqual(spy.constructions, 0)

    def test_zip64_record_size_arithmetic_failure_is_corrupt_spy_untouched(self):
        filler = 2_000
        z64_off = 4 + filler
        # Valid signature and fields, but the declared record size does not
        # put the record's end at the locator (rec_size 999 != 44).
        tail = (hand_z64_eocd(3, 138, 4, rec_size=999)
                + hand_z64_locator(z64_off)
                + hand_eocd(0xFFFF, 0xFFFFFFFF, 4))
        spy = ZipFileSpy()
        with DocxFixture(synthetic_zip(tail, filler)) as p:
            with mock.patch.object(extract.zipfile, "ZipFile", spy):
                doc = extract.extract_docx(p)
        self.assert_failed(doc, "corrupt or truncated")
        self.assert_failed(doc, "locator")
        self.assertEqual(spy.constructions, 0)

    def test_count_times_46_over_cd_size_is_a_provable_lie_spy_untouched(self):
        # 100 entries cannot live in 1,000 bytes: a central-directory file
        # header is >= 46 bytes, so the count is provably false.
        tail = hand_eocd(100, 1_000, 0)
        spy = ZipFileSpy()
        with DocxFixture(synthetic_zip(tail, 3_000)) as p:
            with mock.patch.object(extract.zipfile, "ZipFile", spy):
                doc = extract.extract_docx(p)
        self.assert_failed(doc, "corrupt or truncated")
        self.assert_failed(doc, "structural lie")
        self.assertEqual(spy.constructions, 0)

    def test_cd_offset_plus_size_past_eof_is_corrupt_spy_untouched(self):
        tail = hand_eocd(1, 100_000, 0)           # 0 + 100,000 > file size
        spy = ZipFileSpy()
        with DocxFixture(synthetic_zip(tail, 100)) as p:
            with mock.patch.object(extract.zipfile, "ZipFile", spy):
                doc = extract.extract_docx(p)
        self.assert_failed(doc, "corrupt or truncated")
        self.assertEqual(spy.constructions, 0)


class TestAggregateAcceptanceLimit(unittest.TestCase):
    """The ACCEPTANCE LIMIT: `size_limit:aggregate`, post-open. ZipFile HAS
    been constructed by the time it fires -- that is the honest difference
    from the guard, and the spy asserts it."""

    def assert_failed(self, doc, needle):
        self.assertEqual(doc.status, "extraction-failed")
        self.assertEqual(doc.blocks, [])
        self.assertEqual(doc.pages, [])
        self.assertIsNotNone(doc.reason)
        self.assertIn(needle, doc.reason.lower())

    def test_small_per_part_big_aggregate_refused_after_open(self):
        # 9 members, each DECLARED 31 MiB (< the 32 MiB per-part cap), sum
        # 279 MiB > the 256 MiB aggregate limit. The members on disk are
        # tiny: only the declared uncompressed-size fields are forged, which
        # is exactly what the acceptance limit sums.
        declared = 31 * 1024 * 1024
        parts = {
            "[Content_Types].xml": "<Types/>",
            "_rels/.rels": "<Relationships/>",
            "word/document.xml": document_xml(para("tiny")),
        }
        for i in range(6):
            parts[f"word/media/img{i}.bin"] = "x"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in parts.items():
                zf.writestr(name, data)
        raw = bytearray(buf.getvalue())
        for sig, size_off in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
            idx = raw.find(sig)
            while idx != -1:
                raw[idx + size_off:idx + size_off + 4] = struct.pack("<I", declared)
                idx = raw.find(sig, idx + 1)
        self.assertEqual(len(parts) * declared, 9 * declared)
        self.assertLess(declared, extract.MAX_PART_BYTES)
        self.assertGreater(len(parts) * declared, extract.MAX_ARCHIVE_UNCOMPRESSED_BYTES)

        spy = ZipFileSpy()
        with DocxFixture(bytes(raw)) as p:
            with mock.patch.object(extract.zipfile, "ZipFile", spy):
                doc = extract.extract_docx(p)
        self.assertIsNotNone(doc.reason)
        self.assertTrue(doc.reason.startswith("size_limit:aggregate"), doc.reason)
        self.assert_failed(doc, "size_limit:aggregate")
        # THE POINT: post-open by necessity -- the directory was already
        # parsed, which is why this is an acceptance limit and not a guard.
        self.assertGreaterEqual(spy.constructions, 1)


    def test_zip64_declared_sizes_are_summed_from_the_64_bit_field(self):
        # The ZIP64 half of the pair: each member declares 5 GiB through a
        # ZIP64 extra field, over the 32-bit field's whole range. The exact
        # total in the reason is the assertion that matters -- a reader that
        # summed the 0xFFFFFFFF sentinels would report 12,884,901,885, not
        # this, and would still have "refused", which is the silent failure
        # this test exists to catch.
        declared = 5 * 1024 ** 3
        base = build_docx_bytes(standard_parts(para("tiny")))
        data = zip64_declared_sizes(base, declared)
        total = 3 * declared                      # 3 parts in standard_parts
        self.assertGreater(declared, 0xFFFFFFFF)
        self.assertGreater(total, extract.MAX_ARCHIVE_UNCOMPRESSED_BYTES)

        spy = ZipFileSpy()
        with DocxFixture(data) as p:
            with mock.patch.object(extract.zipfile, "ZipFile", spy):
                doc = extract.extract_docx(p)
        self.assertIsNotNone(doc.reason)
        self.assertTrue(doc.reason.startswith("size_limit:aggregate"), doc.reason)
        self.assertIn(str(total), doc.reason)
        self.assert_failed(doc, "size_limit:aggregate")
        self.assertGreaterEqual(spy.constructions, 1)


class TestPostOpenCountReconciliation(unittest.TestCase):
    """The honesty check: a structurally valid but false pre-open count is
    reconciled against the directory `zipfile` actually materialised."""

    def test_eocd_claiming_fewer_entries_than_the_directory_holds_is_corrupt(self):
        data = bytearray(build_docx_bytes(standard_parts(para("Hello"))))
        eocd = data.rfind(b"PK\x05\x06")
        # 3 real members, EOCD claims 2: internally consistent enough to pass
        # every pre-open structural check, and false. Never silently trusted.
        data[eocd + 10:eocd + 12] = struct.pack("<H", 2)
        spy = ZipFileSpy()
        with DocxFixture(bytes(data)) as p:
            with mock.patch.object(extract.zipfile, "ZipFile", spy):
                doc = extract.extract_docx(p)
        self.assertEqual(doc.status, "extraction-failed")
        self.assertEqual(doc.blocks, [])
        self.assertIsNotNone(doc.reason)
        self.assertIn("corrupt or truncated", doc.reason.lower())
        self.assertIn("declares 2", doc.reason)
        self.assertIn("holds 3", doc.reason)
        # A post-open refusal: the guard passed, ZipFile was constructed, and
        # the reconciliation caught the lie.
        self.assertGreaterEqual(spy.constructions, 1)


class TestPreOpenTailReaderOffsets(unittest.TestCase):
    """Offset regression on the reader itself: distinctive values at EOCD+10
    /+12 and ZIP64+32/+40 must come back from exactly those offsets, and the
    per-field sentinels must fire independently."""

    def read_tail(self, data: bytes):
        with DocxFixture(data) as p:
            return extract._read_zip_tail_records(Path(p), os.path.getsize(p))

    def test_classic_eocd_reads_count_at_plus10_and_cd_size_at_plus12(self):
        # 1,234 * 46 = 56,764 <= 56,789, so the pair is structurally sound
        # and both distinctive values must survive verbatim.
        data = synthetic_zip(hand_eocd(1234, 56_789, 0), 57_000)
        self.assertEqual(self.read_tail(data), (1234, 56_789))

    def test_entries_sentinel_alone_takes_count_from_zip64_plus32_only(self):
        # entries16 == 0xFFFF but cd_size is a perfectly normal 32-bit value:
        # the count comes from ZIP64+32 and cd_size stays the EOCD+12 value.
        # The ZIP64 cd_size field holds a poison value that would blow the
        # EOF check if the reader wrongly consulted it.
        filler = 600_100
        z64_off = 4 + filler
        tail = (hand_z64_eocd(12_345, 0xFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF)
                + hand_z64_locator(z64_off)
                + hand_eocd(0xFFFF, 600_000, 0))
        self.assertEqual(self.read_tail(synthetic_zip(tail, filler)),
                         (12_345, 600_000))

    def test_cd_size_sentinel_alone_takes_size_from_zip64_plus40_only(self):
        # cd_size == 0xFFFFFFFF but the entry count is a normal 16-bit value:
        # cd_size comes from ZIP64+40 and the count stays the EOCD+10 value.
        # The ZIP64 entries field holds a poison count that would fail the
        # count*46 check if the reader wrongly consulted it.
        filler = 5_000
        z64_off = 4 + filler
        tail = (hand_z64_eocd(20_000, 4_600, 0)
                + hand_z64_locator(z64_off)
                + hand_eocd(100, 0xFFFFFFFF, 0))
        self.assertEqual(self.read_tail(synthetic_zip(tail, filler)),
                         (100, 4_600))


class TestEmptyDocumentIsRecordedExplicitly(unittest.TestCase):
    def setUp(self):
        from matterkit import store

        self.tmp = tempfile.mkdtemp()
        self.conn = store.connect(self.tmp)

    def tearDown(self):
        self.conn.close()

    def test_valid_empty_docx_records_an_extracted_zero_content_row(self):
        """'Extracted, nothing in it' must be distinguishable from 'never extracted'."""
        with docx_file("<w:sectPr/>") as p:
            doc = extract.extract_docx(p)
            sha = hashlib.sha256(Path(p).read_bytes()).hexdigest()
        self.assertEqual(doc.status, "extracted")
        self.assertEqual(doc.blocks, [])

        n = extract.record_blocks(self.conn, sha, doc)
        self.assertEqual(n, 1)
        rows = self.conn.execute(
            "SELECT * FROM document_blocks WHERE document_sha256=?", (sha,)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "extracted")
        self.assertEqual(rows[0]["container_type"], "part")
        self.assertEqual(rows[0]["text"], "")
        self.assertIsNotNone(rows[0]["reason"])


# --------------------------------------------------------------------------
# CLI: `matter pages` must never write a page row for a DOCX
# --------------------------------------------------------------------------
KIT = str(Path(__file__).resolve().parent.parent)


class TestPagesCLIOnDocx(unittest.TestCase):
    def setUp(self):
        import shutil
        import subprocess

        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        subprocess.run(
            [sys.executable, os.path.join(KIT, "matter.py"), "init", self.dir],
            capture_output=True, text=True, check=True)

    def _run(self, *args):
        import subprocess

        return subprocess.run(
            [sys.executable, os.path.join(KIT, "matter.py"), *args],
            capture_output=True, text=True, cwd=self.dir)

    def test_docx_writes_blocks_and_leaves_document_pages_untouched(self):
        from matterkit import store

        src = os.path.join(self.dir, "exhibit.docx")
        Path(src).write_bytes(build_docx_bytes(standard_parts(para("Intro") + TABLE_2X2)))
        sha = hashlib.sha256(Path(src).read_bytes()).hexdigest()

        r = self._run("pages", self.dir, src)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("block", r.stdout.lower())

        conn = store.connect(self.dir)
        blocks = conn.execute(
            "SELECT * FROM document_blocks WHERE document_sha256=? ORDER BY block_index",
            (sha,)).fetchall()
        pages = conn.execute(
            "SELECT * FROM document_pages WHERE document_sha256=?", (sha,)).fetchall()
        conn.close()
        self.assertEqual(len(blocks), 5)
        self.assertEqual(pages, [], "a DOCX must never produce a document_pages row")
        self.assertEqual(blocks[0]["locator"], "word/document.xml#b0")

    def test_corrupt_docx_reports_failure_and_writes_no_content(self):
        from matterkit import store

        src = os.path.join(self.dir, "broken.docx")
        Path(src).write_bytes(b"definitely not a zip")
        sha = hashlib.sha256(Path(src).read_bytes()).hexdigest()

        r = self._run("pages", self.dir, src)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("failed", r.stdout.lower())

        conn = store.connect(self.dir)
        rows = conn.execute(
            "SELECT * FROM document_blocks WHERE document_sha256=?", (sha,)).fetchall()
        pages = conn.execute(
            "SELECT * FROM document_pages WHERE document_sha256=?", (sha,)).fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "extraction-failed")
        self.assertIsNone(rows[0]["text"])
        self.assertEqual(pages, [])


if __name__ == "__main__":
    unittest.main()
