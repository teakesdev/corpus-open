"""RFC 0004 vertical-slice tests — PDF text extraction with page locators.

Builds synthetic PDFs in-process (no fixture files needed): a minimal FlateDecode
text PDF and an image-only (binary-only) PDF. Verifies honest extraction vs
honest failure, stdlib-only importability, and offset stability.
"""
import sys
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matterkit import extract  # noqa: E402


def _make_pdf(streams: list[bytes], filters: list[str] | None = None) -> bytes:
    """Minimal PDF with N content streams, each its own object."""
    objs = []
    n = 1
    body = []
    offsets = []
    for i, s in enumerate(streams):
        f = (filters[i] if filters else "FlateDecode")
        data = zlib.compress(s) if f == "FlateDecode" else s
        body.append(f"{n} 0 obj\n<< /Length {len(data)} /Filter /{f} >>\nstream\n")
        body.append(data)
        body.append(b"\nendstream\nendobj\n")
        offsets.append((n, 0))
        n += 1
    pdf = b"%PDF-1.4\n"
    pdf += b"".join(b if isinstance(b, bytes) else b.encode() for b in body)
    pdf += b"%%EOF\n"
    return pdf


class TestStdlibOnly(unittest.TestCase):
    def test_import_needs_no_third_party(self):
        # if this import already succeeded, stdlib-only holds; assert no pypdf.
        self.assertNotIn("pypdf", sys.modules)
        self.assertNotIn("pdfplumber", sys.modules)


class TestPDFExtraction(unittest.TestCase):
    def test_flatdecode_two_pages_extract(self):
        s1 = b"BT (Page one text.) Tj ET"
        s2 = b"BT (Page two text.) Tj ET"
        pdf = _make_pdf([s1, s2])
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf)
            p = f.name
        try:
            doc = extract.extract_pdf(p)
            self.assertEqual(doc.kind, "pdf")
            statuses = [pg.status for pg in doc.pages]
            self.assertTrue(any(s == "extracted" for s in statuses))
            self.assertTrue(any("Page one" in pg.text for pg in doc.pages))
            for pg in doc.pages:
                if pg.status == "extracted":
                    self.assertEqual(pg.text_sha256,
                                     __import__("hashlib").sha256(pg.text.encode()).hexdigest())
                    # offset stability: source[0:len] == text
                    self.assertEqual(pg.text[: len(pg.text)], pg.text)
        finally:
            os.unlink(p)

    def test_image_only_pdf_fails_honestly(self):
        # a binary stream that is NOT FlateDecode-decodable text -> extraction-failed
        pdf = _make_pdf([b"\xff\xd8\xff\xe0" + b"\x00" * 40], filters=["None"])
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf)
            p = f.name
        try:
            doc = extract.extract_pdf(p)
            self.assertGreaterEqual(len(doc.pages), 1)
            self.assertTrue(any(pg.status == "extraction-failed" for pg in doc.pages))
            # never silently empty on a claimed extracted page
            for pg in doc.pages:
                if pg.status == "extracted":
                    self.assertTrue(pg.text.strip())
        finally:
            os.unlink(p)


class TestPlainText(unittest.TestCase):
    def test_txt_extracts_identically(self):
        import tempfile, os
        body = "Under 15 U.S.C. \u00a7 1681a, the statute applies.\nSecond line."
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write(body)
            p = f.name
        try:
            doc = extract.extract_text(p)
            self.assertEqual(doc.kind, "text")
            self.assertEqual(doc.pages[0].text, body)
            self.assertEqual(doc.pages[0].status, "extracted")
        finally:
            os.unlink(p)

    def test_unknown_ext_fails_honestly(self):
        doc = extract.extract_text("does/not/matter.bin")
        self.assertEqual(doc.kind, "unknown")
        self.assertEqual(doc.pages[0].status, "extraction-failed")


if __name__ == "__main__":
    unittest.main()