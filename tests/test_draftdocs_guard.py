"""draftdocs.read_docx: hostile and broken packages are refused, never read, never guessed at.

The archive bounds are RFC 0005 §4a of origin/feat/docx-structural-extraction, ported to read bytes instead of a
path; the tests below are adapted from that branch's tests/test_extract_docx.py (same fixtures, same cases). The
pre-open GUARD refuses on the file's tail records before zipfile.ZipFile is constructed; the post-open checks
(count reconciliation, aggregate limit) are labelled as what they are.

Synthetic data only. Run: python3 -m unittest discover -s tests -v
"""
import io
import os
import struct
import sys
import unittest
import zipfile
from unittest import mock

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.join(KIT, "tests", "fixtures"))

import zip_hostile_builders as hostile  # noqa: E402
from matterkit import draftdocs  # noqa: E402
from draft_builders import NS, docx, document_xml, notes_xml, para  # noqa: E402


class ZipFileSpy:
    """Stands in for zipfile.ZipFile as draftdocs sees it. `constructions` counts attempts, `completions`
    constructors that returned: a guard that fires pre-open leaves both at 0."""

    def __init__(self):
        self._real = zipfile.ZipFile
        self.constructions = 0
        self.completions = 0

    def __call__(self, *a, **kw):
        self.constructions += 1
        zf = self._real(*a, **kw)
        self.completions += 1
        return zf


def hand_eocd(entries, cd_size, cd_offset):
    return b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, entries, entries, cd_size, cd_offset, 0)


def hand_z64_locator(z64_offset):
    return b"PK\x06\x07" + struct.pack("<IQI", 0, z64_offset, 1)


def hand_z64_eocd(entries_total, cd_size, cd_offset, rec_size=44):
    return b"PK\x06\x06" + struct.pack("<QHHIIQQQQ", rec_size, 45, 45, 0, 0, entries_total, entries_total, cd_size, cd_offset)


def synthetic_zip(tail, filler_len):
    """A zip signature, filler, then hand-built tail records: all the pre-open reader ever looks at."""
    return b"PK\x03\x04" + b"\x00" * filler_len + tail


def many_member_zip(n):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for i in range(n):
            zf.writestr(f"m{i:05d}", b"x")
    return buf.getvalue()


def zip64_declared_sizes(raw, declared):
    """Rewrite every central-directory entry to declare `declared` uncompressed bytes the ZIP64 way."""
    eocd = raw.rfind(b"PK\x05\x06")
    count, = struct.unpack("<H", raw[eocd + 10:eocd + 12])
    cd_offset, = struct.unpack("<I", raw[eocd + 16:eocd + 20])
    cd, pos = bytearray(), cd_offset
    for _ in range(count):
        name_len, extra_len, comment_len = struct.unpack("<HHH", raw[pos + 28:pos + 34])
        head = bytearray(raw[pos:pos + 46])
        name = raw[pos + 46:pos + 46 + name_len]
        comment = raw[pos + 46 + name_len + extra_len:pos + 46 + name_len + extra_len + comment_len]
        head[24:28] = b"\xff\xff\xff\xff"
        extra = struct.pack("<HHQ", 0x0001, 8, declared)
        head[30:32] = struct.pack("<H", len(extra))
        cd += bytes(head) + name + extra + comment
        pos += 46 + name_len + extra_len + comment_len
    return raw[:cd_offset] + bytes(cd) + hand_eocd(count, len(cd), cd_offset)


def read(data, spy=None):
    if spy is None:
        return draftdocs.read_docx(data)
    with mock.patch.object(draftdocs.zipfile, "ZipFile", spy):
        return draftdocs.read_docx(data)


class Refuses(unittest.TestCase):
    def refused(self, data, needle, spy=None):
        with self.assertRaises(draftdocs.DraftError) as cm:
            read(data, spy)
        self.assertIn(needle.lower(), str(cm.exception).lower())
        return str(cm.exception)


class TestArchiveMembersGuard(Refuses):
    def test_10001_tiny_members_refused_pre_open(self):
        data = many_member_zip(draftdocs.MAX_ARCHIVE_MEMBERS + 1)
        self.assertNotIn(b"PK\x06\x07", data[-64:])  # a classic EOCD, not ZIP64
        spy = ZipFileSpy()
        msg = self.refused(data, "size_limit:members", spy)
        self.assertIn("pre-open", msg)
        self.assertEqual(spy.constructions, 0)

    def test_a_zip64_tail_declaring_10001_is_refused_pre_open(self):
        filler = 460_000
        cd_size = (draftdocs.MAX_ARCHIVE_MEMBERS + 1) * 46
        tail = (hand_z64_eocd(draftdocs.MAX_ARCHIVE_MEMBERS + 1, cd_size, 4) + hand_z64_locator(4 + filler)
                + hand_eocd(0xFFFF, 0xFFFFFFFF, 4))
        spy = ZipFileSpy()
        self.refused(synthetic_zip(tail, filler), "size_limit:members", spy)
        self.assertEqual(spy.constructions, 0)

    def test_exactly_the_limit_passes_the_guard_and_then_fails_on_part_policy(self):
        spy = ZipFileSpy()
        msg = self.refused(many_member_zip(draftdocs.MAX_ARCHIVE_MEMBERS), "word/document.xml", spy)
        self.assertNotIn("size_limit", msg)
        self.assertEqual(spy.constructions, 1)

    def test_a_zip64_sentinel_with_no_locator_is_corrupt(self):
        data = bytearray(docx(para("x")))
        eocd = data.rfind(b"PK\x05\x06")
        data[eocd + 10:eocd + 12] = struct.pack("<H", 0xFFFF)
        spy = ZipFileSpy()
        msg = self.refused(bytes(data), "corrupt or truncated", spy)
        self.assertIn("locator", msg)
        self.assertEqual(spy.constructions, 0)

    def test_a_locator_pointing_at_garbage_is_corrupt(self):
        tail = b"\x00" * 56 + hand_z64_locator(4 + 2_000) + hand_eocd(0xFFFF, 0xFFFFFFFF, 4)
        spy = ZipFileSpy()
        self.refused(synthetic_zip(tail, 2_000), "corrupt or truncated", spy)
        self.assertEqual(spy.constructions, 0)

    def test_a_zip64_record_whose_size_does_not_end_at_the_locator_is_corrupt(self):
        tail = hand_z64_eocd(3, 138, 4, rec_size=999) + hand_z64_locator(4 + 2_000) + hand_eocd(0xFFFF, 0xFFFFFFFF, 4)
        spy = ZipFileSpy()
        self.refused(synthetic_zip(tail, 2_000), "locator", spy)
        self.assertEqual(spy.constructions, 0)

    def test_a_count_that_cannot_fit_in_the_directory_is_a_structural_lie(self):
        spy = ZipFileSpy()
        self.refused(synthetic_zip(hand_eocd(100, 1_000, 0), 3_000), "structural lie", spy)
        self.assertEqual(spy.constructions, 0)

    def test_a_directory_that_runs_past_the_end_of_the_file_is_corrupt(self):
        spy = ZipFileSpy()
        self.refused(synthetic_zip(hand_eocd(1, 100_000, 0), 100), "corrupt or truncated", spy)
        self.assertEqual(spy.constructions, 0)

    def test_a_comment_embedding_the_eocd_signature_is_refused_not_followed(self):
        spy = ZipFileSpy()
        self.refused(hostile.comment_signature_case(), "corrupt or truncated", spy)
        self.assertEqual(spy.constructions, 0)


class TestCentralDirectoryByteCap(Refuses):
    def test_an_honest_oversized_directory_is_refused_pre_open(self):
        data, real, cd_size = hostile.oversized_cd_honest_case(n_members=520, comment_bytes=65_535)
        self.assertLess(real, draftdocs.MAX_ARCHIVE_MEMBERS)
        self.assertGreater(cd_size, draftdocs.MAX_CD_BYTES)
        spy = ZipFileSpy()
        self.refused(data, "size_limit:central_directory", spy)
        self.assertEqual(spy.constructions, 0)


class TestLyingTailRecords(Refuses):
    def test_an_understated_count_is_caught_after_open_by_reconciliation(self):
        data, real, _ = hostile.understated_count_case(n_members=300, comment_bytes=256, declared_count=5)
        msg = self.refused(data, "corrupt or truncated")
        self.assertIn("declares 5 entries", msg)

    def test_an_understated_directory_size_is_corrupt_not_a_cap_refusal(self):
        data, _, _ = hostile.understated_cd_size_case(n_members=300, comment_bytes=256, declared_count=5)
        msg = self.refused(data, "corrupt or truncated")
        self.assertNotIn("size_limit", msg)


class TestAggregateAcceptanceLimit(Refuses):
    def test_small_parts_with_a_huge_declared_total_are_refused_after_open(self):
        raw = docx(para("x"), parts={f"pad/{i}.bin": b"y" for i in range(10)})
        each = draftdocs.MAX_ARCHIVE_UNCOMPRESSED_BYTES // 10 + 1
        msg = self.refused(zip64_declared_sizes(raw, each), "size_limit:aggregate")
        self.assertIn("acceptance limit", msg)


class TestPreOpenTailReader(unittest.TestCase):
    def test_a_classic_eocd_reads_count_size_and_offset(self):
        data = docx(para("x"))
        got = draftdocs._read_zip_tail_records(data)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            self.assertEqual(got[0], len(zf.namelist()))

    def test_per_field_zip64_sentinels_take_only_the_saturated_field(self):
        filler = 2_000
        tail = hand_z64_eocd(7, 7 * 46, 99) + hand_z64_locator(4 + filler) + hand_eocd(0xFFFF, 7 * 46, 4)
        self.assertEqual(draftdocs._read_zip_tail_records(synthetic_zip(tail, filler)), (7, 7 * 46, 4))


class TestPackagePolicy(Refuses):
    def test_a_file_that_is_not_a_zip_is_refused(self):
        self.refused(b"Just some text, renamed .docx", "not a zip archive")
        self.refused(b"%PDF-1.4\n%synthetic\n", "not a zip archive")

    def test_a_pdf_with_a_zip_appended_is_refused_on_its_leading_bytes(self):
        self.refused(b"%PDF-1.4\n" + docx(para("x")), "not a zip archive")

    def test_a_truncated_archive_is_corrupt(self):
        self.refused(docx(para("x"))[:-30], "corrupt or truncated")

    def test_a_zip_without_the_main_part_is_refused(self):
        self.refused(docx(para("x"), parts={"word/document.xml": None}), "no word/document.xml")

    def test_two_main_parts_are_ambiguous(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", document_xml(para("one")))
            with self.assertWarns(UserWarning):  # zipfile itself warns about the duplicate name
                z.writestr("word/document.xml", document_xml(para("two")))
        self.refused(buf.getvalue(), "duplicate")

    def test_a_blank_main_part_is_refused(self):
        self.refused(docx(para("x"), parts={"word/document.xml": "  \n "}), "empty")

    def test_malformed_xml_is_refused_without_salvage(self):
        self.refused(docx(para("x"), parts={"word/document.xml": f"<w:document {NS}><w:body><w:p>"}), "malformed")

    def test_the_wrong_root_or_namespace_or_no_body_is_refused(self):
        self.refused(docx(para("x"), parts={"word/document.xml": f"<w:x {NS}><w:body/></w:x>"}), "unexpected root")
        self.refused(docx(para("x"), parts={"word/document.xml": '<w:document xmlns:w="urn:strict"><w:body/></w:document>'}),
                     "namespace")
        self.refused(docx(para("x"), parts={"word/document.xml": f"<w:document {NS}/>"}), "no w:body")

    def test_an_encrypted_member_is_refused(self):
        data = bytearray(docx(para("x"), compression=zipfile.ZIP_STORED))
        for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):  # general-purpose flag bit 0 in both headers
            i = 0
            while (i := data.find(sig, i)) >= 0:
                data[i + off] |= 1
                i += 4
        self.refused(bytes(data), "encrypted")

    def test_a_part_over_the_decompressed_cap_is_refused(self):
        with mock.patch.object(draftdocs, "MAX_PART_BYTES", 200):
            self.refused(docx(para("x" * 500)), "too large")

    def test_a_crc_mismatch_in_the_main_part_is_corrupt(self):
        data = bytearray(docx(para("Alpha beta gamma."), compression=zipfile.ZIP_STORED))
        i = data.find(b"Alpha beta")
        data[i] ^= 0x01
        self.refused(bytes(data), "corrupt")


class TestXmlSafety(Refuses):
    BOMB = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
            '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>'
            f'<w:document {NS}><w:body><w:p><w:r><w:t>&lol3;</w:t></w:r></w:p></w:body></w:document>')
    XXE = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/hosts">]>'
           f'<w:document {NS}><w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>')

    def test_entity_expansion_and_external_entities_are_refused(self):
        self.refused(docx(para("x"), parts={"word/document.xml": self.BOMB}), "doctype")
        self.refused(docx(para("x"), parts={"word/document.xml": self.XXE}), "doctype")

    def test_a_doctype_behind_a_utf8_bom_is_still_refused_and_a_plain_bom_is_read(self):
        self.refused(docx(para("x"), parts={"word/document.xml": b"\xef\xbb\xbf" + self.XXE.encode()}), "doctype")
        d = draftdocs.read_docx(docx(para("x"), parts={"word/document.xml": b"\xef\xbb\xbf" + document_xml(para("Fine.")).encode()}))
        self.assertEqual(d.text, "Fine.")

    def test_utf16_parts_are_refused_rather_than_scanned_blind(self):
        for enc in ("utf-16", "utf-16-le", "utf-16-be", "utf-32"):
            with self.subTest(enc=enc):
                self.refused(docx(para("x"), parts={"word/document.xml": document_xml(para("x")).encode(enc)}),
                             "unsupported encoding")

    def test_every_part_that_is_read_gets_the_same_scrutiny(self):
        foot = notes_xml("footnote", {1: "One."})
        for part, xml in (("word/footnotes.xml", foot), ("word/endnotes.xml", foot.replace("footnote", "endnote")),
                          ("word/numbering.xml", f"<w:numbering {NS}/>"), ("word/styles.xml", f"<w:styles {NS}/>"),
                          ("word/settings.xml", f"<w:settings {NS}/>")):
            with self.subTest(part=part):
                bad_doctype = xml.replace("<w:", "<!DOCTYPE x [<!ENTITY y 'z'>]><w:", 1)
                msg = self.refused(docx(para("x"), parts={part: bad_doctype}), "doctype")
                self.assertIn(part, msg)
                msg = self.refused(docx(para("x"), parts={part: xml[:-5]}), "malformed")
                self.assertIn(part, msg)
                self.refused(docx(para("x"), parts={part: xml.encode("utf-16")}), "unsupported encoding")

    def test_a_duplicated_optional_part_is_ambiguous(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", document_xml(para("x")))
            z.writestr("word/footnotes.xml", notes_xml("footnote", {1: "One."}))
            with self.assertWarns(UserWarning):
                z.writestr("word/footnotes.xml", notes_xml("footnote", {1: "Two."}))
        self.refused(buf.getvalue(), "duplicate")


class TestMalformedContent(Refuses):
    def test_a_part_with_a_nonsense_attribute_is_a_refusal_not_a_crash(self):
        bad = f'<w:numbering {NS}><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="x"/></w:abstractNum></w:numbering>'
        msg = self.refused(docx(para("x"), parts={"word/numbering.xml": bad}), "could not read this Word draft")
        self.assertIn("ValueError", msg)


class TestStdlibOnlyFloor(unittest.TestCase):
    def test_the_reader_imports_with_third_party_paths_removed(self):
        import subprocess
        code = ("import sys, site\n"
                "sys.path[:] = [p for p in sys.path if 'site-packages' not in p and 'dist-packages' not in p]\n"
                f"sys.path.insert(0, {KIT!r})\n"
                "from matterkit import draftdocs\n"
                "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('lxml', 'docx', 'defusedxml'))\n"
                "assert not bad, bad\n")
        p = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)


if __name__ == "__main__":
    unittest.main()
