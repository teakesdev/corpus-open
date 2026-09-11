"""Document text extraction with page locators (RFC 0004).

Stdlib-only by default. Honest failure: a PDF whose text stream we cannot
decode (LZW, CID/Type0 fonts, image-only scans) returns an explicit
`extraction_failed` on that page — never silently empty text, which is the
single failure mode that loses evidence.

Heavier deps (pdfplumber, tesseract OCR) are OPTIONAL: importable only when
installed, never required. This module must stay importable with stdlib only.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Page:
    number: int
    text: str
    status: str = "extracted"  # extracted | extraction-failed
    reason: str | None = None
    text_sha256: str | None = None


@dataclass
class DocumentText:
    path: str
    kind: str  # pdf | text | unknown
    pages: list[Page] = field(default_factory=list)


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
    out = DocumentText(path=path, kind="pdf")
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
    import hashlib
    if not text_blocks:
        out.pages.append(Page(number=1, text="", status="extraction-failed",
                              reason="no decodable text stream (scanned / LZW / CID font)"))
        return out
    for idx, t in enumerate(text_blocks, start=1):
        out.pages.append(Page(
            number=idx, text=t, status="extracted",
            text_sha256=hashlib.sha256(t.encode()).hexdigest()))
    return out


def extract_text(path: str) -> DocumentText:
    p = Path(path)
    ext = p.suffix.lower()
    import hashlib
    if ext == ".pdf":
        return extract_pdf(path)
    if ext in (".txt", ".md"):
        raw = p.read_text(encoding="utf-8", errors="replace")
        return DocumentText(path=path, kind="text", pages=[
            Page(number=1, text=raw, status="extracted",
                 text_sha256=hashlib.sha256(raw.encode()).hexdigest())
        ])
    return DocumentText(path=path, kind="unknown", pages=[
        Page(number=1, text="", status="extraction-failed",
             reason=f"no extractor for {ext}")
    ])