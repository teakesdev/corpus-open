"""Document text extraction with source locators (RFC 0004 + RFC 0005).

Stdlib-only by default. Honest failure: a PDF whose text stream we cannot
decode (LZW, CID/Type0 fonts, image-only scans) returns an explicit
`extraction_failed` on that page — never silently empty text, which is the
single failure mode that loses evidence.

Heavier deps (pdfplumber, tesseract OCR) are OPTIONAL: importable only when
installed, never required. This module must stay importable with stdlib only.

========================================================================
LOCATOR SCHEMES
========================================================================

Every extraction declares exactly one `locator_scheme`, and the scheme
determines both what a locator means and where it is stored. The schemes are
never mixed and one is never coerced into another.

``pdf-page``   (RFC 0004)  PDF. Locator is a 1-based page number recovered
                           from the file's own page/content-stream structure.
                           Stored in ``document_pages``.
``docx-structural`` (RFC 0005) DOCX. Locator is a structural path through the
                           OOXML block tree. Stored in ``document_blocks``.
``whole-file`` (RFC 0004)  ``.txt``/``.md`` and unextractable kinds. The whole
                           file is one unit; there is no intra-file locator.

**A DOCX never carries a page number.** Pagination in WordprocessingML is
produced by the rendering engine at layout time (fonts, printer metrics,
field results, hyphenation) and is not present in the file. An explicit
``w:br w:type="page"`` is a *hint to the renderer*, not an authoritative page
boundary: content still reflows around it. Any page number attached to DOCX
content would therefore be manufactured by this tool, not read from the
source — a fabricated legal-grade citation. So:

* ``extract_docx`` returns ``pages == []`` in every case, success or failure.
* ``document_blocks`` has no ``page_number`` column, and a CHECK constraint
  refuses any ``locator_scheme`` other than ``docx-structural``.
* the ``matter pages`` CLI writes no ``document_pages`` row for a ``.docx``.

========================================================================
THE DOCX LOCATOR CONTRACT (RFC 0005)
========================================================================

A citation is the triple ``(document_sha256, part, locator)``.

``document_sha256``  The sha256 of the exact source bytes. Locators are only
                     valid against the byte-identical file; editing a DOCX
                     produces a different sha256 and thus a different
                     citation space. Sources are opened read-only and are
                     never rewritten.
``part``             The OOXML part the locator addresses. v1 addresses
                     exactly one: ``word/document.xml``.
``locator``          ``<part>#<segment>/<segment>/...`` — a path of integer
                     coordinates through the part's *block tree*, rendered
                     with a one-letter type prefix that cycles ``b`` (block
                     child), ``r`` (table row), ``c`` (table cell).

Path construction. Within a container (the body, or a table cell) the
recognised block-level children — ``w:p`` and ``w:tbl``, in document order,
ignoring property/bookmark elements that carry no text — are numbered from 0.
A ``w:p`` at index *i* is addressed ``(…, i)``. To reach content inside a
``w:tbl`` at index *i*, append the 0-based row and cell indexes and recurse
into that cell: ``(…, i, r, c)``. Hence every locator path has length
``3n + 1`` and the addressed unit is always a paragraph:

    word/document.xml#b3                  4th block child of the body
    word/document.xml#b3/r1/c0/b2         3rd paragraph of row 1, cell 0 of
                                          the table at body index 3
    word/document.xml#b0/r0/c0/b1/r2/c1/b0  nested one table deeper

**Uniqueness.** A path is a coordinate in a tree: two distinct blocks differ
at their first divergent index, so no two blocks in a part can share a path.
Collisions across parts are impossible because ``part`` is part of the
citation, and collisions across documents are impossible because
``document_sha256`` is. Identical *text* in two places is therefore still two
distinct citations — the locator identifies a position, not a string. The
storage primary key ``(document_sha256, part, locator)`` enforces this.

**Offsets.** ``DocumentText.text`` is the extracted text of the part: the
block texts joined by a single ``\n`` in reading order. Each block records
``char_start``/``char_end`` into that string, with the invariant

    doc.text[block.char_start:block.char_end] == block.text

so a quoted passage re-derives from the stored offsets exactly. Offsets index
the *extracted text*, never the raw XML. The joiner is not a delimiter: a
``w:br`` inside a paragraph also renders as ``\n``, so ``doc.text`` must not
be re-split on newlines to recover blocks. The stored offsets are the only
authoritative block boundaries.

**Why character offsets and not run indexes.** A ``w:r`` run boundary is a
formatting artefact: Word re-splits runs on spell-check, revision tracking,
or an unrelated edit, and a run carries no semantic boundary. Run indexes are
therefore unstable identifiers for a passage. ``run_count`` is recorded as a
diagnostic (it shows the run traversal happened) and is never part of a
citation.

**What counts as text.** Only ``w:t`` is visible text. ``w:delText``
(tracked-change deletions) and ``w:instrText`` (field instruction codes) are
never extracted — quoting struck-out or machine-internal text as document
content would misstate the evidence. ``w:tab``, ``w:br``/``w:cr`` and
``w:noBreakHyphen`` render as ``\t``, ``\n`` and ``-``. Element *tails* are
ignored, so pretty-printing whitespace in the XML never enters the text.

**Hostile input.** The extension is checked against the leading bytes before
`zipfile` is handed the file, so a renamed ``.txt``/``.pdf`` — and a polyglot
whose zip is appended to another format — is refused rather than re-routed.
A single part is capped twice (declared size, then a bounded read) so a
forged header cannot get past the zip-bomb guard. A DOCTYPE is refused
outright: ``xml.etree.ElementTree`` expands internal entities and the C
parser exposes no expat handle to turn that off, and a conforming OOXML part
has no DTD. That check is a byte scan, so a UTF-8 BOM is stripped first and a
part that does not then begin as UTF-8 XML is refused rather than parsed
blind. Two entries under the same part name are refused as ambiguous,
because which one a reader resolves to is reader-dependent and the citation
could not be pinned.

**Failure vocabulary.** ``status`` is ``extracted`` or ``extraction-failed``
at the document level, with a human-readable ``reason`` on failure. Failure
yields zero blocks; it is never a zero-length success. A well-formed document
containing no paragraphs is the distinct case ``extracted`` with zero blocks
and ``reason is None``.

**Part-set policy (v1).** Only ``word/document.xml`` is read.
Headers, footers, footnotes, endnotes and comments are text-bearing parts
that v1 deliberately does not extract; they are listed in
``PartInventory.skipped`` so a reviewer can distinguish "skipped by design"
from "missed". Every name in the archive is listed in ``PartInventory.seen``.
Block-level elements encountered in a container but not descended into (for
example ``w:sdt`` content controls) are named in ``PartInventory.unhandled``
so no content is dropped silently.

v1 addresses the main part by its conventional name rather than by resolving
the ``officeDocument`` package relationship, so a producer that names the
main part something other than ``word/document.xml`` fails with "no
word/document.xml part" instead of being read. That is a capability gap, not
a silent one; see RFC 0005 for the v2 item.
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

LOCATOR_SCHEME_PDF_PAGE = "pdf-page"
LOCATOR_SCHEME_DOCX = "docx-structural"
LOCATOR_SCHEME_WHOLE_FILE = "whole-file"


@dataclass
class Page:
    number: int
    text: str
    status: str = "extracted"  # extracted | extraction-failed
    reason: str | None = None
    text_sha256: str | None = None


@dataclass(frozen=True)
class DocxLocator:
    """A structural coordinate inside one OOXML part. See the module docstring."""

    part: str
    path: tuple[int, ...]

    _PREFIXES = "brc"

    @property
    def container_type(self) -> str:
        if not self.path:
            return "part"          # the whole part; used only for failure rows
        return "paragraph" if len(self.path) == 1 else "table-cell"

    def canonical(self) -> str:
        segs = [f"{self._PREFIXES[i % 3]}{n}" for i, n in enumerate(self.path)]
        return f"{self.part}#{'/'.join(segs)}"

    @classmethod
    def parse(cls, text: str) -> "DocxLocator":
        part, sep, rest = text.partition("#")
        if not sep:
            raise ValueError(f"not a docx locator (no '#'): {text!r}")
        path: list[int] = []
        if rest:
            for i, seg in enumerate(rest.split("/")):
                want = cls._PREFIXES[i % 3]
                if len(seg) < 2 or seg[0] != want or not seg[1:].isdigit():
                    raise ValueError(f"bad locator segment {seg!r} in {text!r}")
                path.append(int(seg[1:]))
            if len(path) % 3 != 1:
                raise ValueError(f"locator path must have length 3n+1: {text!r}")
        return cls(part=part, path=tuple(path))


@dataclass
class Block:
    """One addressable paragraph of DOCX content."""

    locator: DocxLocator
    index: int                 # reading-order ordinal within the part
    text: str
    char_start: int            # offsets into DocumentText.text
    char_end: int
    run_count: int             # diagnostic only, never part of a citation
    text_sha256: str
    status: str = "extracted"


@dataclass(frozen=True)
class PartInventory:
    """What the archive held, what we read, and what we knowingly did not."""

    seen: tuple[str, ...] = ()
    extracted: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    unhandled: tuple[str, ...] = ()


@dataclass
class DocumentText:
    path: str
    kind: str  # pdf | text | docx | unknown
    pages: list[Page] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    locator_scheme: str = LOCATOR_SCHEME_WHOLE_FILE
    status: str = "extracted"  # extracted | extraction-failed
    reason: str | None = None
    text: str = ""             # full extracted part text; block offsets index this
    text_sha256: str | None = None
    parts: PartInventory | None = None


# --- minimal PDF text extraction --------------------------------------------
# Extracts `(...)Tj` / `[...] TJ` text-showing operators out of FlateDecode
# content streams. Handles the common plain-text PDF; everything else fails
# honestly per-page rather than guessing.

_OBJ_DELIMS = "()<>[]{}/%"
_WS = b" \t\r\n\f\x00"


def _skip_ws(b: bytes, i: int) -> int:
    while i < len(b) and b[i] in _WS:
        i += 1
    return i


def _parse_number(b: bytes, i: int):
    i = _skip_ws(b, i)
    s = i
    while i < len(b) and b[i] in b"0123456789+-.":
        i += 1
    return b[s:i], i


def _parse_name(b: bytes, i: int):
    i = _skip_ws(b, i)
    if i < len(b) and b[i] == ord("/"):
        s = i + 1
        i += 1
        while i < len(b) and b[i] not in _WS and b[i] not in _OBJ_DELIMS.encode():
            i += 1
        return b[s:i], i
    return None, i


def _parse_string(b: bytes, i: int):
    """Literal string; returns decoded bytes (rough latin-1 for text)."""
    i = _skip_ws(b, i)
    if i >= len(b) or b[i] != ord("("):
        return None, i
    depth = 1
    j = i + 1
    out = bytearray()
    while j < len(b) and depth:
        c = b[j]
        if c == ord("\\"):
            j += 1
            if j < len(b):
                nxt = b[j]
                if nxt == ord("n"):
                    out.append(ord("\n"))
                elif nxt == ord("r"):
                    out.append(ord("\r"))
                elif nxt == ord("t"):
                    out.append(ord("\t"))
                elif nxt in b"()\\":
                    out.append(nxt)
                elif nxt in b"01234567":
                    octv = b[j:j + 3]
                    out.append(int(octv, 8) & 0xFF)
                    j += 2
                else:
                    out.append(nxt)
                j += 1
        elif c == ord("("):
            depth += 1
            out.append(c)
            j += 1
        elif c == ord(")"):
            depth -= 1
            if depth:
                out.append(c)
            j += 1
        else:
            out.append(c)
            j += 1
    return out, j


def _extract_text_from_content(stream: bytes) -> str:
    i = 0
    parts: list[str] = []
    last_text = ""
    while i < len(stream):
        c = stream[i]
        if c == ord("("):
            s, i2 = _parse_string(stream, i)
            if s is None:
                i += 1
                continue
            i = i2
            try:
                parts.append(s.decode("latin-1"))
            except UnicodeDecodeError:
                pass
        elif c == ord("T"):
            # "Tj" / "ET" text-show end; also literal `TJ` array operator
            if i + 1 < len(stream) and stream[i + 1:] and stream[i + 1:i + 2] == b"j":
                i += 2
                parts.append("\n")  # Tj = show text; line break after
                continue
            if stream[i:i + 2] == b"TJ":
                i += 2
                continue
            i += 1
        elif c == ord("'"):
            parts.append("\n")
            i += 1
        elif c == ord('"'):
            # double-quote = newline then show
            parts.append("\n")
            i += 1
        else:
            i += 1
    text = "".join(parts)
    return text


def _obj_offsets(b: bytes):
    """Yield (start,end) of each `N M obj ... endobj`."""
    i = 0
    out = []
    while i < len(b):
        idx = b.find(b"obj", i)
        if idx < 0:
            break
        # back up to a boundary
        s = idx
        # find preceding number(s): sequence "N G obj" or "N G R obj"
        # scan backwards for start of line
        ls = b.rfind(b"\n", 0, idx) + 1
        head = b[ls:idx].split()
        if len(head) >= 2 and head[-2].isdigit() and head[-1].isdigit():
            # obj num = head[-2]
            end = b.find(b"endobj", idx)
            if end < 0:
                break
            out.append((idx, end))
            i = end + 6
        else:
            i = idx + 3
    return out


def _streams_in_obj(obj: bytes) -> list[bytes]:
    """Return decoded data of each `stream ... endstream` in an object."""
    out = []
    i = 0
    while True:
        s = obj.find(b"stream", i)
        if s < 0:
            break
        # skip to EOL after 'stream'
        j = obj.find(b"\n", s)
        if j < 0:
            j = obj.find(b"\r", s)
        if j < 0:
            break
        start = j + 1
        # account for \r\n
        if obj[start:start + 1] == b"\n":
            start += 1
        e = obj.find(b"endstream", start)
        if e < 0:
            break
        raw = obj[start:e]
        if raw.endswith(b"\n"):
            raw = raw[:-1]
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        out.append(raw)
        i = e + len(b"endstream")
    return out


def extract_pdf(path: str) -> DocumentText:
    data = Path(path).read_bytes()
    out = DocumentText(path=path, kind="pdf",
                       locator_scheme=LOCATOR_SCHEME_PDF_PAGE)
    # Split content text per page: crude page-boundary detection by page objects.
    # We first decode FlateDecode streams globally (many simple PDFs use one
    # stream per page but we err honest: if no /FlateDecode filter, mark pages
    # failed rather than dump binary).
    text_blocks = []
    for _s, _e in _obj_offsets(data):
        obj = data[_s:_e]
        # only content streams that are FlateDecode
        if b"/FlateDecode" in obj and b"stream" in obj:
            for raw in _streams_in_obj(obj):
                try:
                    decoded = zlib.decompress(raw)
                except Exception:
                    continue
                t = _extract_text_from_content(decoded)
                if t.strip():
                    text_blocks.append(t)
    # Assign blocks to pages 1..N as a best-effort; without a real page tree we
    # report the count and page numbers we CAN see. If no text at all -> one
    # failed page (honest) rather than zero pages.
    if not text_blocks:
        out.pages.append(Page(number=1, text="", status="extraction-failed",
                              reason="no decodable text stream (scanned / LZW / CID font)"))
        out.status = "extraction-failed"
        out.reason = out.pages[0].reason
        return out
    for idx, t in enumerate(text_blocks, start=1):
        out.pages.append(Page(
            number=idx, text=t, status="extracted",
            text_sha256=hashlib.sha256(t.encode()).hexdigest()))
    return out


def extract_text(path: str) -> DocumentText:
    """Dispatch by extension. Each branch declares its own locator scheme.

    Extension chooses the extractor; the extractor still verifies the content.
    A ``.docx`` that is not actually a DOCX fails here — it is never re-routed
    to another extractor on the basis of its bytes, because a file whose name
    contradicts its content is exactly the provenance problem the kit exists
    to refuse.
    """
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".pdf":
        return extract_pdf(path)
    if ext == ".docx":
        return extract_docx(path)
    if ext in (".txt", ".md"):
        raw = p.read_text(encoding="utf-8", errors="replace")
        digest = hashlib.sha256(raw.encode()).hexdigest()
        return DocumentText(
            path=path, kind="text",
            locator_scheme=LOCATOR_SCHEME_WHOLE_FILE,
            text=raw, text_sha256=digest,
            pages=[Page(number=1, text=raw, status="extracted",
                        text_sha256=digest)])
    return DocumentText(
        path=path, kind="unknown",
        locator_scheme=LOCATOR_SCHEME_WHOLE_FILE,
        status="extraction-failed", reason=f"no extractor for {ext}",
        pages=[Page(number=1, text="", status="extraction-failed",
                    reason=f"no extractor for {ext}")])


# --- DOCX structural extraction (RFC 0005) ----------------------------------

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % W_NS

PRIMARY_PART = "word/document.xml"

#: Hard ceiling on a single decompressed part. Guards a zip bomb: the declared
#: size is checked first, then the actual read is bounded, so a forged header
#: cannot get past it either.
MAX_PART_BYTES = 32 * 1024 * 1024

#: ============================================================================
#: ARCHIVE BOUNDS (RFC 0005 §4a). Two limits, two labels, and the labels are
#: load-bearing -- do not blur them:
#:
#:   MAX_ARCHIVE_MEMBERS is the GUARD. It is enforced PRE-OPEN, straight off
#:   the archive's tail records, BEFORE `zipfile.ZipFile` is constructed --
#:   because `ZipFile.__init__` reads the whole central directory and
#:   materialises one `ZipInfo` per entry before any in-process check could
#:   run. A refusal here has allocated no `ZipInfo` objects and read no
#:   central-directory bytes. `size_limit:members` failures fire before a
#:   ZipFile object exists.
#:
#:   MAX_ARCHIVE_UNCOMPRESSED_BYTES is the ACCEPTANCE LIMIT, NOT a guard. It
#:   is enforced POST-OPEN, inside `_extract_docx_open`, after `ZipFile` has
#:   already parsed the directory -- per-member uncompressed sizes live in
#:   central-directory entries and no tail record sums them, so the sum is
#:   unobtainable without paying the parse the guard exists to avoid. It is a
#:   declared-size sanity check; nothing post-open can undo the parse.
#: ============================================================================
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 256 * 1024 * 1024

#: Local-file-header and end-of-central-directory signatures. Checked before
#: handing the file to `zipfile`, so a renamed .txt/.pdf is refused on its own
#: bytes rather than on a downstream parse error.
ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06")

#: Text-bearing parts v1 deliberately does not extract. Recorded, not silent.
_SKIPPED_PART_RE = re.compile(
    r"^word/(header\d*\.xml|footer\d*\.xml|footnotes\.xml|endnotes\.xml"
    r"|comments\w*\.xml)$")

#: Elements that may appear at a block position and carry no citable text.
#: Anything at a block position that is neither recognised nor listed here is
#: reported in PartInventory.unhandled rather than dropped silently.
_INERT_BLOCK_TAGS = frozenset({
    "pPr", "sectPr", "tblPr", "tblPrEx", "tblGrid", "trPr", "tcPr",
    "bookmarkStart", "bookmarkEnd", "proofErr", "permStart", "permEnd",
    "commentRangeStart", "commentRangeEnd",
    "customXmlInsRangeStart", "customXmlInsRangeEnd",
    "customXmlDelRangeStart", "customXmlDelRangeEnd",
    "customXmlMoveFromRangeStart", "customXmlMoveFromRangeEnd",
    "customXmlMoveToRangeStart", "customXmlMoveToRangeEnd",
    "moveFromRangeStart", "moveFromRangeEnd",
    "moveToRangeStart", "moveToRangeEnd",
})

#: Table nesting beyond this is pathological rather than a real document;
#: descending stops and the fact is recorded instead of silently dropped.
_MAX_TABLE_DEPTH = 24

#: Zip tail-record signatures (APPNOTE): End Of Central Directory, ZIP64 EOCD
#: locator, ZIP64 EOCD record.
_SIG_EOCD = b"PK\x05\x06"
_SIG_Z64_LOCATOR = b"PK\x06\x07"
_SIG_Z64_EOCD = b"PK\x06\x06"

#: Fixed record lengths, in bytes.
_EOCD_LEN = 22
_Z64_LOCATOR_LEN = 20
_Z64_EOCD_FIXED_LEN = 56

#: Tail window read by the GUARD: the widest comment the EOCD can declare
#: (65,535) plus the EOCD itself (22) plus the ZIP64 locator (20), so the
#: locator always sits inside the single read. The guard's whole worst case is
#: 4 head bytes + this window + one 56-byte ZIP64-EOCD read = 65,637 bytes;
#: not one central-directory byte is touched.
_TAIL_WINDOW = 65_535 + _EOCD_LEN + _Z64_LOCATOR_LEN   # 65,577

#: Minimum fixed length of one central-directory file header (APPNOTE 4.3.12).
#: An entry can carry extra name/extra/comment bytes on top of this, never
#: fewer, so `entries_total * 46 > cd_size` is a provable structural lie.
_CD_HEADER_MIN = 46


def _tail_record_error(detail: str) -> str:
    """Every pre-open structural lie exits as corrupt/truncated (§3 style)."""
    return f"corrupt or truncated zip archive: {detail}"


def _read_zip_tail_records(p: Path, file_size: int):
    """Pre-open reader for the GUARD. Returns (entries_total, cd_size) or a
    reason string; a str return means refuse, before any ZipFile exists.

    Reads only the file's tail: at most _TAIL_WINDOW (65,577) bytes, scanned
    right-to-left for the EOCD signature. A candidate is accepted only if its
    comment-length field puts the record's end exactly at EOF -- we do not go
    fishing for another record. A saturated classic field (0xFFFF / 0xFFFFFFFF)
    means "consult ZIP64" (APPNOTE 4.4.1.4); the sentinels fire INDEPENDENTLY
    PER FIELD, so an archive with entries_total == 0xFFFF and a perfectly
    normal 32-bit cd_size is a real case, not a hypothetical one -- each
    saturated field is replaced from the ZIP64 record and each unsaturated
    field is kept as-is. Every structural lie found here is refused as
    corrupt/truncated rather than passed on to `zipfile` to interpret.
    """
    if file_size < _EOCD_LEN:
        return _tail_record_error(
            f"file is {file_size} bytes, too short to hold a {_EOCD_LEN}-byte "
            f"end-of-central-directory record")
    try:
        with p.open("rb") as fh:
            fh.seek(file_size - _TAIL_WINDOW if file_size > _TAIL_WINDOW else 0)
            tail = fh.read(_TAIL_WINDOW)
            tail_start = file_size - len(tail)
            # One candidate, the rightmost: if its comment length does not put
            # the record's end exactly at EOF, the archive is corrupt and we
            # refuse -- we do not go fishing for another record.
            eocd_off = tail.rfind(_SIG_EOCD)
            if (eocd_off < 0 or eocd_off + _EOCD_LEN > len(tail)
                    or (tail_start + eocd_off + _EOCD_LEN
                        + int.from_bytes(tail[eocd_off + 20:eocd_off + 22],
                                         "little")) != file_size):
                return _tail_record_error(
                    "no end-of-central-directory record ends exactly at EOF")
            entries16 = int.from_bytes(tail[eocd_off + 10:eocd_off + 12], "little")
            cd_size = int.from_bytes(tail[eocd_off + 12:eocd_off + 16], "little")
            cd_offset = int.from_bytes(tail[eocd_off + 16:eocd_off + 20], "little")

            # Sentinels fire INDEPENDENTLY PER FIELD: any relevant field
            # saturated on its own forces the ZIP64 consult (an archive can
            # have entries_total == 0xFFFF while cd_offset is a perfectly
            # normal 32-bit value). Once consulted, the ZIP64 record carries
            # the actual values: total entries at +32, cd_size at +40; a
            # saturated cd_offset is replaced from +48, an unsaturated
            # 32-bit cd_offset stays as the EOCD declared it.
            entries_sentinel = entries16 == 0xFFFF
            size_sentinel = cd_size == 0xFFFFFFFF
            offset_sentinel = cd_offset == 0xFFFFFFFF
            entries_total = entries16
            if entries_sentinel or size_sentinel or offset_sentinel:
                loc_off = eocd_off - _Z64_LOCATOR_LEN
                if loc_off < 0 or tail[loc_off:loc_off + 4] != _SIG_Z64_LOCATOR:
                    return _tail_record_error(
                        "zip64 sentinel field in the EOCD but no zip64 EOCD "
                        "locator immediately before it")
                locator_start = tail_start + loc_off
                z64_offset = int.from_bytes(tail[loc_off + 8:loc_off + 16], "little")
                if z64_offset < 0 or z64_offset + _Z64_EOCD_FIXED_LEN > file_size:
                    return _tail_record_error(
                        f"zip64 EOCD locator points at offset {z64_offset}, "
                        f"outside the archive")
                z64_off = z64_offset - tail_start
                if 0 <= z64_off and z64_off + _Z64_EOCD_FIXED_LEN <= len(tail):
                    rec = tail[z64_off:z64_off + _Z64_EOCD_FIXED_LEN]
                else:
                    # ZIP64 record sits before the tail window (only possible
                    # behind a near-maximal EOCD comment): one extra bounded
                    # read of exactly the 56-byte fixed portion.
                    fh.seek(z64_offset)
                    rec = fh.read(_Z64_EOCD_FIXED_LEN)
                if rec[:4] != _SIG_Z64_EOCD:
                    return _tail_record_error(
                        f"zip64 EOCD locator points at offset {z64_offset}, "
                        f"which does not begin a zip64 EOCD record")
                # record-size field (ZIP64 EOCD +4): `value + 12` is the whole
                # record, and the record must end exactly at the locator.
                rec_size = int.from_bytes(rec[4:12], "little")
                if z64_offset + rec_size + 12 != locator_start:
                    return _tail_record_error(
                        f"zip64 EOCD record declares size {rec_size}; size+12 "
                        f"does not end at the locator")
                if entries_sentinel:
                    entries_total = int.from_bytes(rec[32:40], "little")
                if size_sentinel:
                    cd_size = int.from_bytes(rec[40:48], "little")
                if offset_sentinel:
                    cd_offset = int.from_bytes(rec[48:56], "little")

            if cd_offset + cd_size > file_size:
                return _tail_record_error(
                    f"central directory claims offset {cd_offset} + size "
                    f"{cd_size}, past end of file ({file_size} bytes)")
            if entries_total * _CD_HEADER_MIN > cd_size:
                return _tail_record_error(
                    f"central directory claims {entries_total} entries but is "
                    f"only {cd_size} bytes; at {_CD_HEADER_MIN} bytes minimum "
                    f"per entry the count is a structural lie")
            return entries_total, cd_size
    except OSError as exc:
        return _tail_record_error(f"cannot read archive tail: {exc}")


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _block_children(container, unhandled: set):
    """Yield (block_index, element, localname) for w:p / w:tbl in document order."""
    idx = 0
    for child in container:
        if child.tag in (W + "p", W + "tbl"):
            yield idx, child, _local(child.tag)
            idx += 1
        elif _local(child.tag) not in _INERT_BLOCK_TAGS:
            unhandled.add(_local(child.tag))


def _named_children(container, name: str, unhandled: set):
    want = W + name
    for child in container:
        if child.tag == want:
            yield child
        elif _local(child.tag) not in _INERT_BLOCK_TAGS:
            unhandled.add(_local(child.tag))


def _collect_paragraphs(container, prefix: tuple, unhandled: set, out: list,
                        depth: int = 0) -> None:
    """Depth-first walk collecting (path, w:p element) pairs in reading order."""
    for idx, child, local in _block_children(container, unhandled):
        path = prefix + (idx,)
        if local == "p":
            out.append((path, child))
            continue
        if depth >= _MAX_TABLE_DEPTH:
            unhandled.add("tbl(nesting-depth-exceeded)")
            continue
        for r, row in enumerate(_named_children(child, "tr", unhandled)):
            for c, cell in enumerate(_named_children(row, "tc", unhandled)):
                _collect_paragraphs(cell, path + (r, c), unhandled, out, depth + 1)


def _paragraph_text(el, acc: list, runs: list) -> None:
    """Append a paragraph's visible text to `acc`; count w:r runs into `runs`.

    Only ``w:t`` is visible text. ``w:delText`` (tracked deletion) and
    ``w:instrText`` (field instruction codes such as a HYPERLINK target) are
    not visible content and are never extracted. ``w:pPr`` is skipped whole,
    because ``w:pPr/w:tabs/w:tab`` is a tab-stop *definition* and would
    otherwise inject a tab character that is not in the document.
    """
    for child in el:
        tag = child.tag
        if tag in (W + "pPr", W + "rPr", W + "del", W + "moveFrom",
                   W + "instrText", W + "delText", W + "delInstrText",
                   W + "fldChar"):
            continue
        if tag == W + "r":
            runs[0] += 1
        elif tag == W + "t":
            acc.append(child.text or "")
            continue
        elif tag == W + "tab":
            acc.append("\t")
            continue
        elif tag in (W + "br", W + "cr"):
            acc.append("\n")
            continue
        elif tag == W + "noBreakHyphen":
            acc.append("-")
            continue
        elif tag == W + "softHyphen":
            continue
        _paragraph_text(child, acc, runs)


def _prolog_has_doctype(raw: bytes) -> bool:
    """True if a DOCTYPE appears in the XML prolog.

    `xml.etree.ElementTree` expands internal general entities, so a DTD is an
    entity-expansion (billion-laughs) vector, and the C parser exposes no expat
    handle to disable it. A conforming OOXML part has no DTD at all, so the
    honest guard is to refuse one.

    The scan walks the prolog per the XML grammar --
    ``prolog ::= XMLDecl? Misc* (doctypedecl Misc*)?`` -- stopping at the root
    element's ``<``. It therefore cannot be fooled by the characters
    ``<!DOCTYPE`` appearing inside the document's own text, which is escaped
    content and lives after the prolog.
    """
    i = 0
    n = len(raw)
    while i < n:
        while i < n and raw[i] in b" \t\r\n":
            i += 1
        if i >= n or raw[i] != 0x3C:            # not a '<' -> prolog is over
            return False
        if raw.startswith(b"<!DOCTYPE", i):
            return True
        if raw.startswith(b"<?", i):            # XML declaration / PI
            j = raw.find(b"?>", i)
            if j < 0:
                return False
            i = j + 2
            continue
        if raw.startswith(b"<!--", i):
            j = raw.find(b"-->", i)
            if j < 0:
                return False
            i = j + 3
            continue
        return False                            # root element reached
    return False


def _docx_failed(path: str, reason: str,
                 parts: PartInventory | None = None) -> DocumentText:
    """Every DOCX failure exit goes through here: zero blocks, zero pages, a reason."""
    return DocumentText(path=path, kind="docx",
                        locator_scheme=LOCATOR_SCHEME_DOCX,
                        status="extraction-failed", reason=reason,
                        parts=parts or PartInventory())


def extract_docx(path: str, *, max_part_bytes: int = MAX_PART_BYTES) -> DocumentText:
    """Extract `word/document.xml` text with structural locators. Never paginates.

    Returns a DocumentText whose `blocks` carry `DocxLocator`s and whose
    `pages` is empty in every case, success or failure. Every zip/XML defect
    is an explicit `extraction-failed` with a reason — never an empty-string
    success. The source is opened read-only and is never modified.
    """
    p = Path(path)
    try:
        with p.open("rb") as fh:
            head = fh.read(4)
        file_size = p.stat().st_size
    except OSError as exc:
        return _docx_failed(path, f"cannot read file: {exc}")
    if head not in ZIP_MAGICS:
        return _docx_failed(
            path, f"not a zip archive: leading bytes {head!r} are not a zip "
                  f"signature, so the .docx extension does not match the content")

    # THE GUARD (RFC 0005 §4a): the member bound runs HERE, before
    # `zipfile.ZipFile` is constructed, because `ZipFile.__init__` reads the
    # whole central directory and materialises one `ZipInfo` per entry before
    # any in-process check could run. A `size_limit:members` refusal means no
    # ZipFile object ever existed. Do not move this below the `with`.
    tail = _read_zip_tail_records(p, file_size)
    if isinstance(tail, str):                       # a structural lie: refuse
        return _docx_failed(path, tail)
    preopen_entries, _cd_size = tail
    if preopen_entries > MAX_ARCHIVE_MEMBERS:
        return _docx_failed(
            path, f"size_limit:members — archive declares {preopen_entries} "
                  f"entries, over the {MAX_ARCHIVE_MEMBERS}-member guard; "
                  f"refused pre-open, before any ZipFile was constructed")

    try:
        with zipfile.ZipFile(p, "r") as zf:
            return _extract_docx_open(path, zf, max_part_bytes, preopen_entries)
    except zipfile.BadZipFile as exc:
        return _docx_failed(path, f"corrupt or truncated zip archive: {exc}")
    except (EOFError, zlib.error) as exc:
        return _docx_failed(path, f"corrupt zip member data: {exc}")
    except RecursionError:
        return _docx_failed(path, "document nesting too deep to traverse safely")
    except OSError as exc:
        return _docx_failed(path, f"cannot read zip archive: {exc}")


def _extract_docx_open(path: str, zf: zipfile.ZipFile,
                       max_part_bytes: int, preopen_entries: int) -> DocumentText:
    names = tuple(zf.namelist())
    skipped = tuple(n for n in names if _SKIPPED_PART_RE.match(n))
    inv = PartInventory(seen=names, skipped=skipped)

    # Honesty check, NOT a resource guard — the guard already ran pre-open
    # (`size_limit:members`, before ZipFile existed). No field of the tail
    # records is authenticated, so an internally consistent but false count is
    # not provable pre-open; this reconciliation is the honest follow-up. The
    # directory `ZipFile` actually materialised must equal the pre-open count.
    if len(names) != preopen_entries:
        return _docx_failed(
            path, f"corrupt or truncated zip archive: end-of-central-directory "
                  f"declares {preopen_entries} entries but the central "
                  f"directory holds {len(names)}; the tail records lie about "
                  f"the archive they describe", inv)

    # ACCEPTANCE LIMIT, not a guard (RFC 0005 §4a): `ZipFile.__init__` has
    # already parsed the whole directory before this line runs, and nothing
    # post-open can undo that — the pre-open guard is what bounded it. This is
    # a checked sum of every member's declared uncompressed size, before any
    # part is read; a declared-size sanity check, not per-member enforcement
    # (the declared-plus-bounded-read pair on the one part we open stays that).
    total_declared = sum(zi.file_size for zi in zf.infolist())
    if total_declared > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        return _docx_failed(
            path, f"size_limit:aggregate — members declare {total_declared} "
                  f"uncompressed bytes in total, over the "
                  f"{MAX_ARCHIVE_UNCOMPRESSED_BYTES}-byte acceptance limit; "
                  f"refused after the directory was parsed, which is why "
                  f"this is an acceptance limit and not a guard", inv)

    if PRIMARY_PART not in names:
        return _docx_failed(
            path, f"archive is a zip but has no {PRIMARY_PART} part "
                  f"({len(names)} member(s) seen)", inv)
    if names.count(PRIMARY_PART) > 1:
        # Two entries under one name: `zipfile` resolves to the last, another
        # reader may take the first. Which bytes the citation pins to would be
        # reader-dependent, so there is no honest answer -- refuse.
        return _docx_failed(
            path, f"archive contains {names.count(PRIMARY_PART)} duplicate "
                  f"{PRIMARY_PART} entries; which one a reader sees is "
                  f"ambiguous, so the content cannot be pinned", inv)

    info = zf.getinfo(PRIMARY_PART)
    if info.flag_bits & 0x1:
        return _docx_failed(
            path, f"{PRIMARY_PART} is encrypted; refusing to guess at a password",
            inv)
    if info.file_size > max_part_bytes:
        return _docx_failed(
            path, f"{PRIMARY_PART} declares {info.file_size} decompressed bytes, "
                  f"too large (cap {max_part_bytes})", inv)
    try:
        with zf.open(info) as fh:
            raw = fh.read(max_part_bytes + 1)
    except RuntimeError as exc:      # zipfile signals encrypted members this way
        return _docx_failed(path, f"cannot read {PRIMARY_PART}: {exc}", inv)
    if len(raw) > max_part_bytes:
        return _docx_failed(
            path, f"{PRIMARY_PART} decompressed past the {max_part_bytes}-byte cap, "
                  f"too large (the declared size was understated)", inv)
    if not raw.strip():
        return _docx_failed(path, f"{PRIMARY_PART} is present but empty", inv)

    # The DOCTYPE guard below is a byte scan, so the part must actually be
    # UTF-8. A UTF-16/32 part would scan as unrecognisable and slip past it,
    # and Word does not produce one; refuse rather than parse blind.
    scan = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
    lead = scan.lstrip(b" \t\r\n")
    if lead[:1] != b"<" or lead[1:2] == b"\x00":
        return _docx_failed(
            path, f"{PRIMARY_PART} has an unsupported encoding: it does not "
                  f"begin as UTF-8 XML (leading bytes {raw[:4]!r}); only UTF-8 "
                  f"parts can be checked for a DOCTYPE before parsing", inv)

    if _prolog_has_doctype(scan):
        return _docx_failed(
            path, f"{PRIMARY_PART} carries a DOCTYPE declaration; a conforming "
                  f"OOXML part has no DTD, and entity expansion is refused "
                  f"rather than evaluated", inv)

    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        return _docx_failed(
            path, f"malformed xml in {PRIMARY_PART}: {exc}; refusing to salvage a "
                  f"partial parse into a success", inv)

    ns, local = (root.tag[1:].split("}", 1) if root.tag.startswith("{")
                 else ("", root.tag))
    if ns != W_NS:
        return _docx_failed(
            path, f"unsupported wordprocessingml namespace {ns!r} in {PRIMARY_PART} "
                  f"(expected transitional OOXML {W_NS!r}); strict OOXML and "
                  f"non-Word packages are not supported", inv)
    if local != "document":
        return _docx_failed(
            path, f"unexpected root element {local!r} in {PRIMARY_PART} "
                  f"(expected 'document')", inv)
    body = root.find(W + "body")
    if body is None:
        return _docx_failed(path, f"{PRIMARY_PART} has no w:body element", inv)

    unhandled: set = set()
    found: list = []
    _collect_paragraphs(body, (), unhandled, found)

    blocks: list[Block] = []
    pieces: list[str] = []
    cursor = 0
    for i, (bpath, p_el) in enumerate(found):
        acc: list[str] = []
        runs = [0]
        _paragraph_text(p_el, acc, runs)
        text = "".join(acc)
        blocks.append(Block(
            locator=DocxLocator(part=PRIMARY_PART, path=bpath),
            index=i, text=text,
            char_start=cursor, char_end=cursor + len(text),
            run_count=runs[0],
            text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest()))
        pieces.append(text)
        cursor += len(text) + 1          # +1 for the "\n" joiner used below

    full = "\n".join(pieces)
    return DocumentText(
        path=path, kind="docx",
        locator_scheme=LOCATOR_SCHEME_DOCX,
        status="extracted", reason=None,
        blocks=blocks, text=full,
        text_sha256=hashlib.sha256(full.encode("utf-8")).hexdigest(),
        parts=PartInventory(seen=names, extracted=(PRIMARY_PART,),
                            skipped=skipped, unhandled=tuple(sorted(unhandled))))


_BLOCK_COLUMNS = (
    "document_sha256", "locator_scheme", "part", "locator", "block_index",
    "container_type", "path", "char_start", "char_end", "text", "text_sha256",
    "status", "reason",
)


def record_blocks(conn, document_sha256: str, doc: DocumentText) -> int:
    """Write a DOCX extraction into `document_blocks` on `conn`. Returns rows written.

    The caller owns the transaction — this function never commits, so the
    write can be rolled back with the rest of the caller's unit of work.

    A failed extraction writes exactly one row — status `extraction-failed`,
    no text, the reason kept — so the store records that the document was
    examined and could not be read. Writing nothing would read as "not yet
    extracted"; writing an empty string would read as "no content". Both are
    lies about the evidence.

    Re-extracting the same bytes rewrites the same rows: the primary key is
    (document_sha256, part, locator).
    """
    if doc.locator_scheme != LOCATOR_SCHEME_DOCX:
        raise ValueError(
            f"record_blocks handles {LOCATOR_SCHEME_DOCX} only, got "
            f"{doc.locator_scheme!r}; page-scheme extractions belong in "
            f"document_pages")
    rows = []
    whole_part = DocxLocator(PRIMARY_PART, ()).canonical()
    if doc.status == "extraction-failed":
        rows.append((document_sha256, LOCATOR_SCHEME_DOCX, PRIMARY_PART,
                     whole_part, -1, "part", "[]",
                     None, None, None, None, "extraction-failed", doc.reason))
    elif not doc.blocks:
        # A well-formed document with no paragraphs. Writing nothing would be
        # indistinguishable from "never extracted", so record the result.
        rows.append((document_sha256, LOCATOR_SCHEME_DOCX, PRIMARY_PART,
                     whole_part, -1, "part", "[]", 0, 0, "",
                     hashlib.sha256(b"").hexdigest(), "extracted",
                     "document is well-formed and contains no paragraphs"))
    else:
        for b in doc.blocks:
            rows.append((document_sha256, LOCATOR_SCHEME_DOCX, b.locator.part,
                         b.locator.canonical(), b.index, b.locator.container_type,
                         json.dumps(list(b.locator.path)),
                         b.char_start, b.char_end, b.text, b.text_sha256,
                         b.status, None))
    placeholders = ",".join("?" * len(_BLOCK_COLUMNS))
    conn.executemany(
        f"INSERT OR REPLACE INTO document_blocks ({','.join(_BLOCK_COLUMNS)}) "
        f"VALUES ({placeholders})", rows)
    return len(rows)
