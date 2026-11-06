"""Draft readers for brief check: a Word (.docx) or PDF draft becomes Markdown-shaped text plus a location for
every line, so the checks written for .md and .txt drafts run on it unchanged.

  .docx   body paragraphs, footnotes and endnotes (each placed right after the paragraph that cites it), with
          auto-numbered headings and lists rendered ("II." "A." "1.") from word/numbering.xml. Tracked changes are
          REFUSED (brief check will not guess which version you mean); comments, hidden text, text boxes,
          formatting-only tracked changes and list settings not reproduced are WARNINGS; headers and footers are
          listed as not read.
  .pdf    `pdftotext -layout`, run transiently on stdin and never stored: page numbers and running headers/footers
          dropped, paragraphs rebuilt, every line located as page:line.

Locations are strings a person can find: "para. 14", "fn. 3", "endnote ii", "p. 3:12". Word stores no page
numbers, so a Word draft never gets one (RFC 0005 §1: a page number the tool invented would be a fabricated
cite location). A PDF page is the file's page order, not the printed folio.

The archive and XML defenses are RFC 0005 §4a of origin/feat/docx-structural-extraction (3f390e5), ported to read
bytes instead of a path (so the sha256 brief check reports is of the bytes parsed) and applied to every part read,
not only word/document.xml. Stdlib only. Nothing is written anywhere; the text lives in memory for one check.
"""
import io
import re
import shutil
import subprocess
import unicodedata
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass, field
from xml.etree import ElementTree

from . import rules


class DraftError(ValueError):
    """The draft was refused. The message says why and what to do instead."""


@dataclass
class Block:
    flow: str = "body"   # "body", or "fn" for a footnote or endnote (it keeps its own Id. antecedent)
    heading: int = 0     # heading level from the Word style or outline level; 0 if none


@dataclass
class Draft:
    kind: str                                        # "text" | "docx" | "pdf"
    text: str
    labels: list = field(default_factory=list)       # labels[i] is the location of text line i+1
    blocks: dict = field(default_factory=dict)       # number of a block's first line -> Block
    reader: str = ""                                 # "Word (.docx)", "PDF (pdftotext 26.06.0)"
    counts: str = ""                                 # "12 paragraphs, 3 footnotes"
    notes: list = field(default_factory=list)        # what was read, dropped or not read; printed in the report
    warnings: list = field(default_factory=list)     # (code, subject, message): WARN findings tied to no line

    def where(self, line: int) -> str:
        """The location a person can find for a text line; "" for a plain-text draft."""
        if not self.labels:
            return ""
        if line == 0:
            return "document"
        return self.labels[line - 1] if 0 < line <= len(self.labels) else ""


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


# --------------------------------------------------------------------------
# Characters: Word and PDF text carry spaces, hyphens and quotes the cite patterns do not expect
# --------------------------------------------------------------------------
_SPACES = "\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000"
_TRANS = {**{ord(c): " " for c in _SPACES},
          **{ord(c): None for c in "\u00ad\u200b\u200c\u200d\u2060\ufeff"},   # soft hyphen, zero-width, BOM
          0x2010: "-", 0x2011: "-",                                           # hyphen, non-breaking hyphen
          0x2018: "'", 0x2019: "'", 0x02bc: "'"}                              # curly apostrophes: "Gov't", "App'x"
_LIGATURES = {ord(c): unicodedata.normalize("NFKC", c) for c in "ﬀﬁﬂﬃﬄﬅﬆ"}


def normalize(s: str) -> str:
    """One line of draft text: non-breaking and thin spaces to spaces, soft hyphens and zero-width characters
    gone, ligatures spelled out, curly apostrophes straight, runs of white space collapsed. Curly double quotes
    stay (the quotation patterns read them) and "§" and "¶" are untouched."""
    s = s.translate(_TRANS).translate(_LIGATURES)
    return re.sub(r"[ \t\r\n\f\v]+", " ", s).strip()


# --------------------------------------------------------------------------
# The package: RFC 0005 §4a archive bounds, ported to bytes
# --------------------------------------------------------------------------
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % W_NS

MAIN_PART = "word/document.xml"

#: Largest draft file brief check will read in one piece (a brief is kilobytes; this is a ceiling, not an expectation).
MAX_DRAFT_BYTES = 64 * 1024 * 1024
#: Hard ceiling on one decompressed part. Checked against the declared size and again on a bounded read.
MAX_PART_BYTES = 32 * 1024 * 1024
#: GUARD (pre-open): the declared entry count and declared central-directory size, read from the archive's tail
#: records before zipfile.ZipFile is constructed. A real .docx has tens of parts and a directory of kilobytes.
MAX_ARCHIVE_MEMBERS = 10_000
MAX_CD_BYTES = 32 * 1024 * 1024
#: ACCEPTANCE LIMIT (post-open, not a guard): the sum of every member's declared uncompressed size.
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 256 * 1024 * 1024

ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06")
_SIG_EOCD, _SIG_Z64_LOCATOR, _SIG_Z64_EOCD = b"PK\x05\x06", b"PK\x06\x07", b"PK\x06\x06"
_EOCD_LEN, _Z64_LOCATOR_LEN, _Z64_EOCD_FIXED_LEN = 22, 20, 56
_TAIL_WINDOW = 65_535 + _EOCD_LEN + _Z64_LOCATOR_LEN
_CD_HEADER_MIN = 46


def _corrupt(detail: str) -> str:
    return f"corrupt or truncated zip archive: {detail}"


def _read_zip_tail_records(data: bytes):
    """(entries_total, cd_size, cd_offset) from the End Of Central Directory record, or a reason string.

    Reads only the tail of `data`, scanning right to left for the EOCD and accepting a candidate only if its
    comment length puts the record's end exactly at EOF. A saturated classic field (0xFFFF / 0xFFFFFFFF) sends
    the reader to the ZIP64 record, per field (APPNOTE 4.4.1.4). Every structural lie is returned as a
    corrupt/truncated reason, before any ZipFile exists."""
    file_size = len(data)
    if file_size < _EOCD_LEN:
        return _corrupt(f"file is {file_size} bytes, too short to hold a {_EOCD_LEN}-byte end-of-central-directory record")
    tail_start = max(0, file_size - _TAIL_WINDOW)
    tail = data[tail_start:]
    eocd_off = tail.rfind(_SIG_EOCD)
    if (eocd_off < 0 or eocd_off + _EOCD_LEN > len(tail)
            or tail_start + eocd_off + _EOCD_LEN + int.from_bytes(tail[eocd_off + 20:eocd_off + 22], "little") != file_size):
        return _corrupt("no end-of-central-directory record ends exactly at EOF")
    entries = int.from_bytes(tail[eocd_off + 10:eocd_off + 12], "little")
    cd_size = int.from_bytes(tail[eocd_off + 12:eocd_off + 16], "little")
    cd_offset = int.from_bytes(tail[eocd_off + 16:eocd_off + 20], "little")
    e_sent, s_sent, o_sent = entries == 0xFFFF, cd_size == 0xFFFFFFFF, cd_offset == 0xFFFFFFFF
    if e_sent or s_sent or o_sent:
        loc_off = eocd_off - _Z64_LOCATOR_LEN
        if loc_off < 0 or tail[loc_off:loc_off + 4] != _SIG_Z64_LOCATOR:
            return _corrupt("zip64 sentinel field in the EOCD but no zip64 EOCD locator immediately before it")
        locator_start = tail_start + loc_off
        z64_offset = int.from_bytes(tail[loc_off + 8:loc_off + 16], "little")
        if z64_offset + _Z64_EOCD_FIXED_LEN > file_size:
            return _corrupt(f"zip64 EOCD locator points at offset {z64_offset}, outside the archive")
        rec = data[z64_offset:z64_offset + _Z64_EOCD_FIXED_LEN]
        if rec[:4] != _SIG_Z64_EOCD:
            return _corrupt(f"zip64 EOCD locator points at offset {z64_offset}, which does not begin a zip64 EOCD record")
        rec_size = int.from_bytes(rec[4:12], "little")
        if z64_offset + rec_size + 12 != locator_start:
            return _corrupt(f"zip64 EOCD record declares size {rec_size}; size+12 does not end at the locator")
        if e_sent:
            entries = int.from_bytes(rec[32:40], "little")
        if s_sent:
            cd_size = int.from_bytes(rec[40:48], "little")
        if o_sent:
            cd_offset = int.from_bytes(rec[48:56], "little")
    if cd_offset + cd_size > file_size:
        return _corrupt(f"central directory claims offset {cd_offset} + size {cd_size}, past end of file ({file_size} bytes)")
    if entries * _CD_HEADER_MIN > cd_size:
        return _corrupt(f"central directory claims {entries} entries but is only {cd_size} bytes; at {_CD_HEADER_MIN} "
                        f"bytes minimum per entry the count is a structural lie")
    return entries, cd_size, cd_offset


def _prolog_has_doctype(raw: bytes) -> bool:
    """True if a DOCTYPE appears in the XML prolog (prolog ::= XMLDecl? Misc* (doctypedecl Misc*)?). ElementTree
    expands internal entities (billion laughs) and exposes no way to stop it; a conforming OOXML part has no DTD,
    so one is refused. The scan stops at the root element, so '<!DOCTYPE' inside the text cannot trip it."""
    i, n = 0, len(raw)
    while i < n:
        while i < n and raw[i] in b" \t\r\n":
            i += 1
        if i >= n or raw[i] != 0x3C:
            return False
        if raw.startswith(b"<!DOCTYPE", i):
            return True
        if raw.startswith(b"<?", i):
            j = raw.find(b"?>", i)
            if j < 0:
                return False
            i = j + 2
        elif raw.startswith(b"<!--", i):
            j = raw.find(b"-->", i)
            if j < 0:
                return False
            i = j + 3
        else:
            return False
    return False


class _Package:
    """An opened, bounded .docx. `part(name)` returns a parsed root element, None if the part is absent."""

    def __init__(self, data: bytes):
        if data[:4] not in ZIP_MAGICS:
            raise DraftError(f"not a zip archive: leading bytes {data[:4]!r} are not a zip signature, so the .docx "
                             "extension does not match the content")
        tail = _read_zip_tail_records(data)
        if isinstance(tail, str):
            raise DraftError(tail)
        self.preopen_entries, cd_size, _ = tail
        # THE GUARD: before zipfile.ZipFile exists, because its constructor reads the whole declared central
        # directory and builds one ZipInfo per entry. Honest-or-overstated tails only; a lie is caught post-open.
        if self.preopen_entries > MAX_ARCHIVE_MEMBERS:
            raise DraftError(f"size_limit:members — archive declares {self.preopen_entries} entries, over the "
                             f"{MAX_ARCHIVE_MEMBERS}-member guard; refused pre-open, before any ZipFile was constructed")
        if cd_size > MAX_CD_BYTES:
            raise DraftError(f"size_limit:central_directory — archive declares a {cd_size}-byte central directory, over "
                             f"the {MAX_CD_BYTES}-byte cap; refused pre-open, before any ZipFile was constructed")
        try:
            self.zf = zipfile.ZipFile(io.BytesIO(data), "r")
        except zipfile.BadZipFile as exc:
            raise DraftError(_corrupt(str(exc))) from None
        self.names = self.zf.namelist()
        if len(self.names) != self.preopen_entries:   # an honesty check, not a resource guard (the guard already ran)
            raise DraftError(_corrupt(f"end-of-central-directory declares {self.preopen_entries} entries but the central "
                                      f"directory holds {len(self.names)}; the tail records lie about the archive they describe"))
        total = sum(zi.file_size for zi in self.zf.infolist())   # an ACCEPTANCE LIMIT: the directory is already parsed
        if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise DraftError(f"size_limit:aggregate — members declare {total} uncompressed bytes in total, over the "
                             f"{MAX_ARCHIVE_UNCOMPRESSED_BYTES}-byte acceptance limit; refused after the directory was parsed, "
                             "which is why this is an acceptance limit and not a guard")

    def has(self, pattern: str) -> list:
        """Member names matching `pattern` in full, whatever their case (OPC part names are case-insensitive)."""
        return [n for n in self.names if re.fullmatch(pattern, n, re.I)]

    def part(self, name: str, required: bool = False):
        count = self.names.count(name)
        if count == 0:
            if required:
                raise DraftError(f"archive is a zip but has no {name} part ({len(self.names)} member(s) seen)")
            return None
        if count > 1:
            raise DraftError(f"archive contains {count} duplicate {name} entries; which one a reader sees is ambiguous, "
                             "so the content cannot be pinned")
        info = self.zf.getinfo(name)
        if info.flag_bits & 0x1:
            raise DraftError(f"{name} is encrypted; refusing to guess at a password")
        if info.file_size > MAX_PART_BYTES:
            raise DraftError(f"{name} declares {info.file_size} decompressed bytes, too large (cap {MAX_PART_BYTES})")
        try:
            with self.zf.open(info) as fh:
                raw = fh.read(MAX_PART_BYTES + 1)
        except RuntimeError as exc:      # zipfile signals some encrypted members this way
            raise DraftError(f"cannot read {name}: {exc}") from None
        except NotImplementedError as exc:
            raise DraftError(f"cannot read {name}: unsupported compression ({exc})") from None
        if len(raw) > MAX_PART_BYTES:
            raise DraftError(f"{name} decompressed past the {MAX_PART_BYTES}-byte cap, too large (the declared size was understated)")
        if not raw.strip():
            raise DraftError(f"{name} is present but empty")
        # The DOCTYPE guard is a byte scan, so the part must really be UTF-8 (Word writes UTF-8). A UTF-16/32 part
        # would scan as unrecognisable and slip past it.
        scan = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
        lead = scan.lstrip(b" \t\r\n")
        if lead[:1] != b"<" or lead[1:2] == b"\x00":
            raise DraftError(f"{name} has an unsupported encoding: it does not begin as UTF-8 XML (leading bytes "
                             f"{raw[:4]!r}); only UTF-8 parts can be checked for a DOCTYPE before parsing")
        if _prolog_has_doctype(scan):
            raise DraftError(f"{name} carries a DOCTYPE declaration; a conforming OOXML part has no DTD, and entity "
                             "expansion is refused rather than evaluated")
        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError as exc:
            raise DraftError(f"malformed xml in {name}: {exc}; refusing to salvage a partial parse into a success") from None
        ns, local = root.tag[1:].split("}", 1) if root.tag.startswith("{") else ("", root.tag)
        if ns != W_NS:
            raise DraftError(f"unsupported wordprocessingml namespace {ns!r} in {name} (expected transitional OOXML "
                             f"{W_NS!r}); strict OOXML and non-Word packages are not supported")
        want = "document" if name == MAIN_PART else name.rsplit("/", 1)[1][:-4]
        if local != want:
            raise DraftError(f"unexpected root element {local!r} in {name} (expected {want!r})")
        return root


# --------------------------------------------------------------------------
# Word: styles, numbering, notes
# --------------------------------------------------------------------------
def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _val(el, child: str):
    c = el.find(W + child) if el is not None else None
    return None if c is None else c.get(W + "val")


_HEADING_NAME = re.compile(r"heading\s*([1-9])", re.I)
_TOC_NAME = re.compile(r"(toc \d|toc heading|table of authorities|toa heading|table of figures|index \d|index heading)", re.I)
_TOC_ID = re.compile(r"(TOC\d|TOCHeading|TableofAuthorities|TOAHeading|TableofFigures)")


class _Styles:
    """Paragraph styles: the numbering a style carries, its heading level, and whether it is a TOC/TOA line."""

    def __init__(self, root):
        self.s = {}
        if root is None:
            return
        for st in root.findall(W + "style"):
            sid = st.get(W + "styleId")
            ppr = st.find(W + "pPr")
            numpr = ppr.find(W + "numPr") if ppr is not None else None
            self.s[sid] = {"name": _val(st, "name") or "", "based": _val(st, "basedOn"),
                           "numId": _val(numpr, "numId") if numpr is not None else None,
                           "ilvl": _val(numpr, "ilvl") if numpr is not None else None,
                           "outline": _val(ppr, "outlineLvl") if ppr is not None else None}

    def chain(self, sid):
        seen = []
        while sid in self.s and sid not in seen and len(seen) < 20:
            seen.append(sid)
            yield sid, self.s[sid]
            sid = self.s[sid]["based"]

    def numbering(self, sid):
        """(numId, ilvl or None, style that defines it) from a style or the styles it is based on."""
        for s, d in self.chain(sid):
            if d["numId"] is not None:
                return d["numId"], d["ilvl"], s
        return None

    def heading_level(self, sid, direct_outline=None) -> int:
        if direct_outline is not None and direct_outline.isdigit() and int(direct_outline) < 9:
            return int(direct_outline) + 1
        for s, d in self.chain(sid):
            m = _HEADING_NAME.fullmatch(d["name"].strip()) or re.fullmatch(r"Heading([1-9])", s)
            if m:
                return int(m.group(1))
            if d["outline"] is not None and d["outline"].isdigit() and int(d["outline"]) < 9:
                return int(d["outline"]) + 1
        m = re.fullmatch(r"Heading([1-9])", sid or "")
        return int(m.group(1)) if m else 0

    def is_toc(self, sid) -> bool:
        if sid and _TOC_ID.fullmatch(sid):
            return True
        return any(_TOC_NAME.fullmatch(d["name"].strip()) for _, d in self.chain(sid))


def _roman(n: int, upper: bool) -> str:
    r = rules.ROMANS[n - 1] if 1 <= n <= len(rules.ROMANS) else str(n)
    return r.upper() if upper else r


def _letters(n: int, upper: bool) -> str:
    s = chr(65 + (n - 1) % 26) * ((n - 1) // 26 + 1)    # Word runs past Z as AA, BB, CC
    return s if upper else s.lower()


FORMATS = {"decimal": lambda n: str(n), "decimalZero": lambda n: f"{n:02d}",
           "upperRoman": lambda n: _roman(n, True), "lowerRoman": lambda n: _roman(n, False),
           "upperLetter": lambda n: _letters(n, True), "lowerLetter": lambda n: _letters(n, False)}


class _Numbering:
    """Renders the marker Word prints for an auto-numbered paragraph ("II." "A." "(a)" "1.1") from numbering.xml."""

    def __init__(self, root, warn, styles=None):
        self.abstract, self.nums, self.links, self.counters, self.warn = {}, {}, {}, {}, warn
        self.styles = styles
        self.scope = "body"  # "footnote" and "endnote" while reading a note: its lists count apart from the body's
        self.bad_formats = set()
        self.style_link = {}    # abstract list -> the list style it takes its numbers from (w:numStyleLink), not followed
        self.flags, self.flagged = [], set()   # (kind, why) for lists whose numbers may differ from Word's; see take()
        self.linked_styles = set()   # styles a list level names (w:lvl/w:pStyle)
        self.present = root is not None
        if root is None:
            return
        for a in root.findall(W + "abstractNum"):
            aid = a.get(W + "abstractNumId")
            levels = {}
            for lv in a.findall(W + "lvl"):
                i = int(lv.get(W + "ilvl", "0"))
                restart = _val(lv, "lvlRestart")
                levels[i] = {"fmt": _val(lv, "numFmt") or "decimal", "text": _val(lv, "lvlText") or "",
                             "start": int(_val(lv, "start") or 1), "legal": lv.find(W + "isLgl") is not None,
                             # restarts other than after the level above it (0 = never); not reproduced, only warned
                             "odd_restart": i > 0 and restart is not None and restart.isdigit() and int(restart) < i}
                if _val(lv, "pStyle"):
                    self.links[(aid, _val(lv, "pStyle"))] = i
            self.abstract[aid] = levels
            if _val(a, "numStyleLink"):
                self.style_link[aid] = _val(a, "numStyleLink")
        for n in root.findall(W + "num"):
            ov = {int(o.get(W + "ilvl", "0")): int(_val(o, "startOverride"))
                  for o in n.findall(W + "lvlOverride") if _val(o, "startOverride") is not None}
            # a level redefined for this list only (w:lvl inside w:lvlOverride): not applied, only warned
            replaced = {int(o.get(W + "ilvl", "0")) for o in n.findall(W + "lvlOverride") if o.find(W + "lvl") is not None}
            self.nums[n.get(W + "numId")] = {"abs": _val(n, "abstractNumId"), "ov": ov, "replaced": replaced}
        self.linked_styles = {s for _, s in self.links}

    def level_for_style(self, numId, style_id):
        num = self.nums.get(numId)
        return self.links.get((num["abs"], style_id)) if num else None

    def style_name(self, sid):
        d = self.styles.s.get(sid) if self.styles else None
        return (d["name"] if d else "") or sid

    def flag(self, key, kind, why):
        """Note, once per list and reason, that the next paragraph's list number may not be what Word prints."""
        if (key, why) not in self.flagged:
            self.flagged.add((key, why))
            self.flags.append((kind, why))

    def take(self):
        """The flags raised since the last call, for the caller to warn about at the paragraph it can name."""
        out, self.flags = self.flags, []
        return out

    def _fmt(self, n, fmt):
        f = FORMATS.get(fmt)
        if f is None:
            self.bad_formats.add(fmt)
            f = FORMATS["decimal"]
        return f(n)

    def marker(self, numId, ilvl):
        """The marker for the next paragraph in this list at this level, or None (bullet, no list, unresolved)."""
        if numId in (None, "0"):
            return None
        num = self.nums.get(numId)
        levels = self.abstract.get(num["abs"]) if num else None
        link = self.style_link.get(num["abs"]) if num else None
        if not levels or ilvl not in levels:
            self.warn("numbering-unresolved", f"list {numId}",
                      f"a paragraph is in a list that takes its numbers from the list style '{self.style_name(link)}', which "
                      "brief check does not follow; its number was not rendered, so a heading marker such as 'II.' may be "
                      "missing from the text brief check read" if link else
                      "a paragraph is in a numbered list that numbering.xml does not define; its number was not rendered, so "
                      "a heading marker such as 'II.' may be missing from the text brief check read" if self.present else
                      "the draft has numbered lists but no numbering part; their numbers were not rendered, so a heading "
                      "marker such as 'II.' may be missing from the text brief check read")
            return None
        # Settings rendered the plain way, not reproduced: flag them for a warning at this paragraph (P3-5, P3-6, P3-7).
        shown = {ilvl} | {int(j) - 1 for j in re.findall(r"%([1-9])", levels[ilvl]["text"])}   # levels this number shows
        if link:
            self.flag(("abs", num["abs"]), "list", f"the list takes its numbers from the list style '{self.style_name(link)}', "
                      "which brief check does not follow")
        if shown & num["replaced"]:
            self.flag(("num", numId), "list", "the list redefines a level's numbering for this list only, which brief check "
                      "does not apply")
        if any(levels.get(j, {}).get("odd_restart") for j in shown):
            self.flag(("abs", num["abs"]), "list", "the list changes when a sub-level starts counting again (Word's 'Restart "
                      "list after' setting), which brief check does not reproduce")
        # Nums of one abstract list share a count (Word continues the list) unless a num restarts a level.
        key = (self.scope, "num", numId) if num["ov"] else (self.scope, "abs", num["abs"])
        ctr = self.counters.setdefault(key, [None] * 9)
        start = lambda j: num["ov"].get(j, levels.get(j, {}).get("start", 1))  # noqa: E731
        ctr[ilvl] = start(ilvl) if ctr[ilvl] is None else ctr[ilvl] + 1
        for j in range(ilvl + 1, 9):
            ctr[j] = None
        lv = levels[ilvl]
        if lv["fmt"] in ("bullet", "none") or not lv["text"]:
            return None

        def sub(m):
            j = int(m.group(1)) - 1
            lj = levels.get(j, lv)
            n = ctr[j] if j < 9 and ctr[j] is not None else start(j)
            return self._fmt(n, "decimal" if lv["legal"] else lj["fmt"])
        return re.sub(r"%([1-9])", sub, lv["text"]).strip() or None


# --------------------------------------------------------------------------
# Word: paragraphs
# --------------------------------------------------------------------------
_TRACKED = {"ins": "insertion", "del": "deletion", "moveFrom": "move", "moveTo": "move", "cellIns": "cell insertion",
            "cellDel": "cell deletion", "cellMerge": "cell merge"}
_FORMAT_CHANGES = {"rPrChange", "pPrChange", "sectPrChange", "tblPrChange", "tblPrExChange", "trPrChange", "tcPrChange",
                   "tblGridChange", "numberingChange"}
_INERT = {"pPr", "rPr", "sectPr", "tblPr", "tblPrEx", "tblGrid", "trPr", "tcPr", "bookmarkStart", "bookmarkEnd", "proofErr",
          "permStart", "permEnd", "commentRangeStart", "commentRangeEnd", "moveFromRangeStart", "moveFromRangeEnd",
          "moveToRangeStart", "moveToRangeEnd", "customXmlInsRangeStart", "customXmlInsRangeEnd", "customXmlDelRangeStart",
          "customXmlDelRangeEnd", "customXmlMoveFromRangeStart", "customXmlMoveFromRangeEnd", "customXmlMoveToRangeStart",
          "customXmlMoveToRangeEnd", "sdtPr", "sdtEndPr"}
_RUN_INERT = {"rPr", "instrText", "fldChar", "delText", "delInstrText", "footnoteRef", "endnoteRef", "separator",
              "continuationSeparator", "continuationNotice", "lastRenderedPageBreak", "annotationRef",
              "commentReference", "pgNum", "ruby"}
_CONTAINERS = {"hyperlink", "fldSimple", "smartTag", "customXml", "dir", "bdo"}
_MAX_TABLE_DEPTH = 24
#: What the unread-content warning says for an element a pro se reader would not recognise by its XML name.
_UNREAD_WORDS = {"sym": "symbol characters (sym)", "oMath": "equations (oMath)", "altChunk": "embedded content (altChunk)",
                 "ruby": "phonetic guide text (ruby)"}


_MATH_T = "{http://schemas.openxmlformats.org/officeDocument/2006/math}t"


def _has_text(el) -> bool:
    return any((t.text or "").strip() for t in el.iter() if t.tag in (W + "t", _MATH_T))


class _State:
    """What a walk of paragraphs saw besides their text."""

    def __init__(self):
        self.refs = []           # (kind, id) of footnote/endnote references, in order, for the current paragraph
        self.hidden = 0
        self.boxes = 0
        self.unhandled = set()


def _run(r, out, st):
    rpr = r.find(W + "rPr")
    if rpr is not None and rpr.find(W + "vanish") is not None:
        if _has_text(r):
            st.hidden += 1
        return
    for c in r:
        t, loc = c.tag, _local(c.tag)
        if t == W + "t":
            out.append(c.text or "")
        elif t in (W + "tab", W + "br", W + "cr", W + "ptab"):
            out.append(" ")
        elif t == W + "noBreakHyphen":
            out.append("-")
        elif t == W + "footnoteReference":
            st.refs.append(("footnote", c.get(W + "id")))
        elif t == W + "endnoteReference":
            st.refs.append(("endnote", c.get(W + "id")))
        elif loc == "sym":
            st.unhandled.add("sym")  # a symbol-font glyph: its code means nothing outside that font, so it is not guessed at
        elif loc in _RUN_INERT or loc == "softHyphen":
            continue
        elif any(_local(e.tag) == "txbxContent" and _has_text(e) for e in c.iter()):
            st.boxes += 1            # drawings and text boxes: their text is not read (and not read twice)
        elif loc not in ("drawing", "pict", "object", "AlternateContent") and _has_text(c):
            st.unhandled.add(loc)


def _walk(el, out, st):
    for c in el:
        loc = _local(c.tag)
        if c.tag == W + "r":
            _run(c, out, st)
        elif loc in _CONTAINERS:
            _walk(c, out, st)
        elif loc == "sdt":
            content = c.find(W + "sdtContent")
            if content is not None:
                _walk(content, out, st)
        elif loc in _INERT:
            continue
        elif _has_text(c):
            st.unhandled.add(loc)


def _table_parts(parent, want, st):
    """The `want` (tr or tc) children of a table or row, looking through content controls that wrap them. A child
    that is neither that, an inert property element nor a content control is flagged (a table directly inside a
    table or row, say), never dropped without a word."""
    for c in parent:
        if c.tag == W + want:
            yield c
        elif c.tag == W + "sdt":
            content = c.find(W + "sdtContent")
            if content is not None:
                yield from _table_parts(content, want, st)
        elif st is not None and _local(c.tag) not in _INERT and (c.tag == W + "tbl" or _has_text(c)):
            st.unhandled.add(f"{_local(c.tag)} (directly inside a table or row)")


def _paragraphs(container, tables=(), st=None, depth=0, counter=None):
    """(paragraph element, table path) for every w:p under `container` in reading order, descending into tables
    and block-level content controls. The table path is ((table no., row, cell), ...), outermost first; a table
    is numbered among the tables of its own container (the body, or a cell)."""
    counter = counter if counter is not None else [0]   # shared with content controls that wrap tables
    for c in container:
        if c.tag == W + "p":
            yield c, tables
        elif c.tag == W + "tbl":
            counter[0] += 1
            n_tables = counter[0]
            if depth >= _MAX_TABLE_DEPTH:
                if st is not None:
                    st.unhandled.add("tbl (nested too deep)")
                continue
            for ri, row in enumerate(_table_parts(c, "tr", st), 1):
                for ci, cell in enumerate(_table_parts(row, "tc", st), 1):
                    yield from _paragraphs(cell, tables + ((n_tables, ri, ci),), st, depth + 1)
        elif c.tag == W + "sdt":
            content = c.find(W + "sdtContent")
            if content is not None:
                yield from _paragraphs(content, tables, st, depth, counter)
        elif st is not None and (_local(c.tag) == "altChunk" or (_local(c.tag) not in _INERT and _has_text(c))):
            st.unhandled.add(_local(c.tag))   # embedded content or an element holding text that is not read


def _table_label(tables) -> str:
    return " > ".join(f"table {t}, row {r}, cell {c}" for t, r, c in tables)


def _list_authors(root_elems, names):
    authors = Counter()
    for root in root_elems:
        for el in root.iter():
            if _local(el.tag) in names and el.get(W + "author"):
                authors[el.get(W + "author")] += 1
    return authors


def read_docx(data: bytes) -> Draft:
    """A Word draft as text with locations. Raises DraftError if it cannot be read, or has tracked changes."""
    try:
        return _read_docx(data)
    except DraftError:
        raise
    except zipfile.BadZipFile as exc:
        raise DraftError(_corrupt(str(exc))) from None
    except (EOFError, zlib.error) as exc:
        raise DraftError(f"corrupt zip member data: {exc}") from None
    except (OverflowError, zipfile.LargeZipFile, NotImplementedError) as exc:
        raise DraftError(_corrupt(str(exc))) from None
    except RecursionError:
        raise DraftError("document nesting too deep to traverse safely") from None
    except Exception as exc:  # a malformed attribute or structure in a part: refuse it, never crash the check
        raise DraftError(f"could not read this Word draft ({type(exc).__name__}: {exc}); it may be damaged or use a "
                         "feature brief check does not read") from None


def _read_docx(data: bytes) -> Draft:
    pkg = _Package(data)
    doc = pkg.part(MAIN_PART, required=True)
    body = doc.find(W + "body")
    if body is None:
        raise DraftError(f"{MAIN_PART} has no w:body element")
    notes_roots = {k: pkg.part(f"word/{k}s.xml") for k in ("footnote", "endnote")}
    styles_root, numbering_root = pkg.part("word/styles.xml"), pkg.part("word/numbering.xml")
    styles = _Styles(styles_root)
    settings = pkg.part("word/settings.xml")
    comments = pkg.part("word/comments.xml")
    warnings, seen_warnings = [], set()

    def warn(code, subject, message):
        if (code, subject) not in seen_warnings:
            seen_warnings.add((code, subject))
            warnings.append((code, subject, message))

    numbering = _Numbering(numbering_root, warn, styles)

    def warn_numbering(at, subject=None):
        """Warn about list numbers that may differ from Word's, at the first paragraph they touch (see _Numbering.flag)."""
        for kind, why in numbering.take():
            message = (f"{at} may have a list number in Word that brief check did not render: {why}. The 'para. N' count "
                       "is not affected" if kind == "style" else
                       f"list numbers from {at} on may not match what Word shows: {why}. The 'para. N' count is not "
                       "affected; only the list number at the start of the line and after 'numbered' may be off")
            warnings.append(("numbering-may-differ", subject or at, message))

    # ---- tracked changes: refuse. Nothing is accepted or rejected for the author.
    parts = [r for r in (doc, notes_roots["footnote"], notes_roots["endnote"]) if r is not None]
    tally = Counter()
    for root in parts:
        for el in root.iter():
            kind = _TRACKED.get(_local(el.tag))
            if kind:
                tally[kind] += 1
    if tally:
        # a move is a moveFrom and a moveTo; count the pair once
        if "move" in tally:
            tally["move"] = (tally["move"] + 1) // 2
        authors = _list_authors(parts, set(_TRACKED))
        what = ", ".join(_plural(n, k) for k, n in sorted(tally.items()))
        by = f"; by {', '.join(a for a, _ in authors.most_common(5))}" if authors else ""
        raise DraftError(f"this Word draft has tracked changes ({what}{by}). It holds two versions of the text and brief "
                         "check will not guess which one you mean. In Word, review them with Review > Accept or Reject "
                         "(Accept All Changes), save a copy, and check that copy.")
    if any(_local(el.tag) in _FORMAT_CHANGES for root in parts for el in root.iter()):
        warn("formatting-changes", "tracked formatting", "the draft has tracked formatting changes (not text changes); a "
             "heading style or list number may differ from what you see after accepting them")
    # Styles, numbering and settings hold no text brief check reads, so a tracked change there is a warning, not a refusal.
    if any(_local(el.tag) in _FORMAT_CHANGES or _local(el.tag) in _TRACKED
           for root in (styles_root, numbering_root, settings) if root is not None for el in root.iter()):
        warn("formatting-changes", "styles, numbering or settings", "the draft has tracked changes in its styles, numbering or "
             "settings (not in the text); a heading style, list number or footnote setting may differ from what you see after "
             "accepting them")

    # ---- footnote and endnote numbering
    fmt_of = {"footnote": "decimal", "endnote": "lowerRoman"}
    odd = False
    scan = [r for r in (settings, doc) if r is not None]
    for kind in ("footnote", "endnote"):
        for root in scan:
            for pr in root.iter(W + f"{kind}Pr"):
                f = _val(pr, "numFmt")
                if f:
                    fmt_of[kind] = f
                if (f and f != ("decimal" if kind == "footnote" else "lowerRoman")) or _val(pr, "numStart") not in (None, "1") \
                        or _val(pr, "numRestart") not in (None, "continuous"):
                    odd = True
    if odd:
        warn("footnote-numbering", "footnotes", "footnotes or endnotes restart numbering, start elsewhere or use a "
             "different number style; 'fn. N' counts them in order from the start of the document, so it may not match "
             "the number Word prints")

    # ---- walk the body
    st = _State()
    blocks, n_para, n_toc, counts = [], 0, 0, Counter()
    used = {"footnote": set(), "endnote": set()}
    seq = {"footnote": 0, "endnote": 0}
    labelled, repeated, missing = {}, {}, set()   # (kind, id) -> first label; label -> extra references; absent notes

    def note_blocks(kind, nid):
        if (kind, nid) in labelled:   # the same note referenced again: read once, under the first label
            if (kind, nid) not in missing:   # a note that is not there was warned about once and was not read
                repeated[labelled[(kind, nid)]] = repeated.get(labelled[(kind, nid)], 0) + 1
            return
        seq[kind] += 1
        label_no = FORMATS.get(fmt_of[kind], FORMATS["decimal"])(seq[kind])
        label = ("fn. " if kind == "footnote" else "endnote ") + label_no
        labelled[(kind, nid)] = label
        root = notes_roots[kind]
        el = None
        if root is not None:
            for n in root.findall(W + kind):
                if n.get(W + "id") == nid and n.get(W + "type") in (None, "normal"):
                    el = n
                    break
        if el is None:
            missing.add((kind, nid))
            warn("footnote-missing", label, f"the text cites {label} but the draft has no such {kind}; it was not read")
            return
        used[kind].add(nid)
        counts[kind] += 1
        nst = _State()
        numbering.scope = kind   # a footnote's lists and an endnote's count apart, as Word's stories do
        for p, _ in _paragraphs(el, st=nst):
            out = []
            _walk(p, out, nst)
            ppr = p.find(W + "pPr")
            marker = _paragraph_marker(ppr, numbering, styles)
            text = normalize("".join(out))
            if text:
                blocks.append((label, (marker + " " if marker else "") + text, "fn", 0))
                warn_numbering(label)
        numbering.scope = "body"
        _absorb(nst)

    def _absorb(s):
        st.hidden += s.hidden
        st.boxes += s.boxes
        st.unhandled |= s.unhandled

    for p, tables in _paragraphs(body, st=st):
        pst = _State()
        out = []
        _walk(p, out, pst)
        ppr = p.find(W + "pPr")
        marker = _paragraph_marker(ppr, numbering, styles)   # advances the list even if the paragraph is empty
        text = normalize("".join(out))
        _absorb(pst)
        if not text:
            for kind, nid in pst.refs:   # a paragraph that is only a note reference still has its note
                note_blocks(kind, nid)
            continue
        n_para += 1
        sid = _val(ppr, "pStyle")
        if styles.is_toc(sid):
            n_toc += 1
            for kind, nid in pst.refs:   # the line itself is not checked; a note it cites still is
                note_blocks(kind, nid)
            continue
        detail = ([f"numbered {marker}"] if marker else []) + ([_table_label(tables)] if tables else [])
        label = f"para. {n_para}" + (f" ({', '.join(detail)})" if detail else "")
        blocks.append((label, (marker + " " if marker else "") + text, "body",
                       styles.heading_level(sid, _val(ppr, "outlineLvl"))))
        warn_numbering(label)   # flags from an empty or table-of-contents paragraph wait for the next one with a label
        counts["paragraph"] += 1
        for kind, nid in pst.refs:
            note_blocks(kind, nid)

    if not blocks:
        raise DraftError("this Word draft has no text to check (no body paragraphs with words in them)")
    warn_numbering("the end of the document", subject="document")

    # ---- what was not read, said out loud
    notes = []
    for kind, root in notes_roots.items():
        if root is None:
            continue
        orphans = [n for n in root.findall(W + kind) if n.get(W + "type") in (None, "normal") and n.get(W + "id") not in used[kind]]
        if orphans:
            notes.append(f"{_plural(len(orphans), kind)} not referenced from the text was not read")
    for label, extra in repeated.items():
        notes.append(f"{label} is referenced from more than one place ({_plural(extra, 'extra reference')}); it was read once, "
                     "at the first reference")
    if n_toc:
        notes.append(f"{_plural(n_toc, 'table-of-contents or authorities paragraph')} (by style) not checked; they still count in "
                     "'para.' numbers")
    unread = []
    if pkg.has(r"word/(header|footer)\d*\.xml"):
        unread.append("headers and footers")
    if comments is not None:
        unread.append("comments")
    notes.append("not read: " + ", ".join(unread + ["text boxes"]))
    if st.unhandled:
        warn("unread-content", ", ".join(sorted(st.unhandled)), "content in these Word elements was not read: "
             + ", ".join(_UNREAD_WORDS.get(u, u) for u in sorted(st.unhandled)))
    if st.hidden:
        warn("hidden-text", "hidden text", f"{_plural(st.hidden, 'run')} of hidden text was not read (Word does not show or "
             "print it); unhide it and check again if it is part of the draft")
    if st.boxes:
        warn("text-box", "text boxes", f"text boxes or drawings with text ({st.boxes}) were not read; cites inside them are "
             "unchecked")
    n_comments = len(comments.findall(W + "comment")) if comments is not None else 0
    n_comments = n_comments or sum(1 for _ in doc.iter(W + "commentReference"))
    if n_comments:
        warn("comments", "comments", f"{_plural(n_comments, 'comment')} in the draft were not read or checked; comments are "
             "not part of the filing")
    for f in sorted(numbering.bad_formats):
        warn("numbering-format", f, f"a list uses the number style '{f}', which was rendered as plain numbers")

    # ---- assemble: one block per line, blank line between blocks
    text_blocks, labels, meta = [], [], {}
    for i, (label, text, flow, heading) in enumerate(blocks):
        meta[2 * i + 1] = Block(flow, heading)
        text_blocks.append(text)
        labels += [label] if i == len(blocks) - 1 else [label, ""]
    count_bits = [_plural(counts["paragraph"], "paragraph")]
    for kind in ("footnote", "endnote"):
        if counts[kind]:
            count_bits.append(_plural(counts[kind], kind))
    return Draft(kind="docx", text="\n\n".join(text_blocks), labels=labels, blocks=meta, reader="Word (.docx)",
                 counts=", ".join(count_bits), notes=notes, warnings=warnings)


def _paragraph_marker(ppr, numbering, styles):
    """The list marker for a paragraph ("II." "A." "1."), or None. Direct numbering wins over the style's."""
    if ppr is None:
        return None
    sid = _val(ppr, "pStyle")
    numpr = ppr.find(W + "numPr")
    num_id = ilvl = None
    link_style = sid
    if numpr is not None:
        num_id, ilvl = _val(numpr, "numId"), _val(numpr, "ilvl")
    if num_id is None:
        via = styles.numbering(sid)
        if via is None:
            if sid in numbering.linked_styles:   # a list level names this style, but the style carries no number
                numbering.flag(("style", sid), "style", f"its style '{numbering.style_name(sid)}' is tied to a numbered "
                               "list but carries no number itself")
            return None
        num_id, style_ilvl, link_style = via
        ilvl = ilvl if ilvl is not None else style_ilvl
    if num_id == "0":
        return None
    if ilvl is None:
        ilvl = numbering.level_for_style(num_id, link_style)
    return numbering.marker(num_id, int(ilvl) if ilvl is not None else 0)


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------
_TERMINAL = re.compile(r"[.?!:;][\"”’')\]]*$")
_NUMBERED = re.compile(r"(?:[IVXLC]+|[A-Z]|\d{1,3})\.\s+\S")
_STARTS_NEW = re.compile(r"[A-Z0-9\"“(\[§¶*]")


def _pdftotext_version(exe: str) -> str:
    try:
        p = subprocess.run([exe, "-v"], capture_output=True, text=True, timeout=10)
        return ((p.stderr or p.stdout).splitlines() or ["pdftotext"])[0].strip()
    except (OSError, subprocess.SubprocessError):
        return "pdftotext (version unknown)"


def _signature(line: str) -> str:
    return re.sub(r"\d+", "#", " ".join(line.lower().split()))


def read_pdf(data: bytes) -> Draft:
    """A PDF draft as text with page:line locations, through `pdftotext -layout` run on stdin. Nothing is stored."""
    exe = shutil.which("pdftotext")
    if not exe:
        raise DraftError("brief check reads a PDF draft with pdftotext (part of poppler), which is not installed. Install "
                         "poppler (macOS: brew install poppler; Debian/Ubuntu: apt install poppler-utils), or save the "
                         "draft from Word as a .docx and check that.")
    try:
        p = subprocess.run([exe, "-layout", "-enc", "UTF-8", "-", "-"], input=data, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise DraftError("pdftotext took longer than 120 seconds to read the PDF; check a .docx or .txt copy instead") from None
    except OSError as exc:
        raise DraftError(f"pdftotext could not be run: {exc}") from None
    if p.returncode != 0:
        why = (p.stderr.decode("utf-8", "replace").strip().splitlines() or ["no message"])[0]
        raise DraftError(f"pdftotext could not read the PDF: {why}")
    pages = p.stdout.decode("utf-8", "replace").split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    # (page, raw line) for every line, as rules.build_text keeps them, so its page-number logic applies as is
    lines = [(pno, raw) for pno, ptext in enumerate(pages, 1) for raw in ptext.split("\n")]
    if not any(raw.strip() for _, raw in lines):
        raise DraftError("the PDF has no text to read (it looks like a scan or images only); brief check does not do OCR. "
                         "Save the draft from Word as a .docx, or export a PDF that has a text layer.")
    notes = []

    # non-blank lines, numbered per page from the top (the number a person counts), with their gaps and indents
    rows, by_page = [], {}
    for idx, (pno, raw) in enumerate(lines):
        if raw.strip():
            k = len(by_page.setdefault(pno, [])) + 1
            by_page[pno].append(idx)
            rows.append({"idx": idx, "page": pno, "line": k, "indent": len(raw) - len(raw.lstrip(" ")),
                         "text": normalize(raw)})
    drop = set()

    # page numbers: a lone number at a page's first or last line that follows the page sequence (rules.py)
    edges = {i for ids in by_page.values() for i in (ids[0], ids[-1])}
    cand = [i for i in sorted(edges) if (m := rules.PAGE_NO.fullmatch(lines[i][1].strip())) and rules._page_value(m["n"]) is not None]
    try:
        for w in rules._check_page_sequence(lines, cand):
            notes.append(w.replace("page ", "PDF page ", 1) if w.startswith("page ") else w)
        drop |= set(cand)
        if cand:
            notes.append(f"dropped {_plural(len(cand), 'page number')} at the top or bottom of pages")
    except rules.RulesError:
        notes.append(f"kept {_plural(len(cand), 'lone number')} at page edges: they do not follow the page number sequence, "
                     "so they were not dropped as page numbers")

    # running headers and footers: the same line (digits aside) at the top or bottom of most pages
    ids_by_page = {p_: [i for i in ids if i not in drop] for p_, ids in by_page.items()}
    n_pages = len(ids_by_page)
    sig_pages, where = {}, {}
    for p_, ids in ids_by_page.items():
        for i in ids[:3] + ids[-3:]:
            s = _signature(lines[i][1])
            if re.search(r"[a-z]", s):
                sig_pages.setdefault(s, set()).add(p_)
                where.setdefault((p_, s), []).append(i)
    need = max(2, -(-n_pages * 6 // 10))
    repeated = {s for s, ps in sig_pages.items() if n_pages >= 2 and len(ps) >= need}
    dropped_hf = [i for (p_, s), ids in where.items() if s in repeated for i in ids]
    if dropped_hf:
        drop |= set(dropped_hf)
        notes.append(f"dropped {_plural(len(set(dropped_hf)), 'running header or footer line')} that repeat on most pages")

    kept = [r for r in rows if r["idx"] not in drop]
    if not kept:
        raise DraftError("the PDF has no text left to check after dropping page numbers and running headers and footers")

    # spacing: a double-spaced page has a blank line between every line, so a gap only signals a paragraph
    # break when it is wider than the document's usual gap
    gap_counts = Counter(b["idx"] - a["idx"] - 1 for a, b in zip(kept, kept[1:]) if a["page"] == b["page"])
    usual = min((g for g, c in gap_counts.items() if c == max(gap_counts.values())), default=0)
    wide = [r["indent"] for r in kept if len(r["text"]) >= 40]
    margin = Counter(wide).most_common(1)[0][0] if wide else min(r["indent"] for r in kept)

    def centered(r):
        return r["indent"] - margin >= 8

    def heading_like(r):
        words = r["text"].split()
        if not 0 < len(words) <= 14 or r["text"].endswith(","):
            return False
        letters = [c for c in r["text"] if c.isalpha()]
        if len(letters) >= 3 and all(c.isupper() for c in letters):
            return True
        if _NUMBERED.match(r["text"]) and "," not in r["text"] and (not r["text"].endswith(".") or len(words) <= 6):
            return True
        return centered(r) and not r["text"].endswith(".")

    # A new paragraph starts at a gap wider than usual, at an indent change after a sentence end (a line that
    # starts a sentence), and at a heading-like line that follows a sentence end; a heading-like line also
    # ends its own paragraph. A line that only looks like a heading in the middle of a sentence is left alone.
    paras, cur, after_heading = [], [], False
    for i, r in enumerate(kept):
        brk = i == 0 or after_heading
        if not brk:
            a = kept[i - 1]
            ended = bool(_TERMINAL.search(a["text"]))
            if a["page"] == r["page"] and r["idx"] - a["idx"] - 1 > usual:
                brk = True
            elif ended and abs(r["indent"] - a["indent"]) >= 3 and _STARTS_NEW.match(r["text"]):
                brk = True
            elif heading_like(r) and (ended or heading_like(a) or centered(r)):
                brk = True
        after_heading = brk and heading_like(r)
        if brk and cur:
            paras.append(cur)
            cur = []
        cur.append(r)
    paras.append(cur)

    text_lines, labels, meta = [], [], {}
    for pi, para_rows in enumerate(paras):
        if pi:
            text_lines.append("")
            labels.append("")
        meta[len(text_lines) + 1] = Block()
        for r in para_rows:
            text_lines.append(r["text"])
            labels.append(f"p. {r['page']}:{r['line']}")
    notes.append("locations are PDF page order (the first page is 1, not the printed page number) and the line is the "
                 "text line counted from the top of that page, running header included")
    notes.append("paragraphs are rebuilt from spacing and indentation; footnote text in a PDF is read where it sits on the "
                 "page, and pleading-paper margin numbers are not recognized. A .docx gives a more reliable check")
    return Draft(kind="pdf", text="\n".join(text_lines), labels=labels, blocks=meta,
                 reader=f"PDF via {_pdftotext_version(exe)}",
                 counts=f"{_plural(len(pages), 'page')}, {_plural(len(kept), 'text line')}", notes=notes)


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_WORD_FIX = "In Word, use File > Save As > Word Document (.docx) and check that copy."


def read_draft(raw: bytes, name: str, use_pdftotext: bool = True) -> Draft:
    """Read a draft by its extension, refusing a file whose bytes say something else (RFC 0005 §3a: a name that
    contradicts the content is a provenance problem, so it is refused, not re-dispatched)."""
    ext = name.lower().rsplit(".", 1)[-1] if "." in name.rsplit("/", 1)[-1] else ""
    if ext == "docx":
        if len(raw) > MAX_DRAFT_BYTES:
            raise DraftError(f"the draft is {len(raw)} bytes, over the {MAX_DRAFT_BYTES}-byte limit")
        return read_docx(raw)
    if ext == "pdf":
        if b"%PDF-" not in raw[:1024]:
            raise DraftError("not a PDF: the file has no %PDF- header, so the .pdf extension does not match the content")
        if len(raw) > MAX_DRAFT_BYTES:
            raise DraftError(f"the draft is {len(raw)} bytes, over the {MAX_DRAFT_BYTES}-byte limit")
        if not use_pdftotext:
            raise DraftError("a PDF draft needs pdftotext, but --no-pdftotext (or pdftotext=False) was given")
        return read_pdf(raw)
    if ext == "doc":
        raise DraftError(f".doc is Word's old binary format, which brief check cannot read. {_WORD_FIX}")
    if ext in ("rtf", "odt", "pages"):
        raise DraftError(f".{ext} drafts are not supported. {_WORD_FIX}")
    # a text name: refuse a file whose bytes are some other format, instead of reading it as garbage text
    head = raw[:16]
    if head[:4] in ZIP_MAGICS:
        raise DraftError(f"{name} is a zip archive (a Word .docx?) under a text name; rename it to .docx to check it. "
                         "Brief check does not guess a format from the contents.")
    if raw[:5] == b"%PDF-":
        raise DraftError(f"{name} is a PDF under a text name; rename it to .pdf to check it.")
    if head[:8] == _OLE:
        raise DraftError(f"{name} is an old Word (.doc) file under a text name. {_WORD_FIX}")
    if head[:5] == b"{\\rtf":
        raise DraftError(f"{name} is an RTF (.rtf) file under a text name; .rtf drafts are not supported. {_WORD_FIX}")
    return Draft(kind="text", text=raw.decode("utf-8", "replace"))
