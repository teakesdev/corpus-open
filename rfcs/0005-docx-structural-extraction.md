# RFC 0005 — DOCX text extraction with structural locators

**Date:** 2026-09-13 · **Repo:** `teakesdev/corpus-open` (`/Users/ty/dev/matter-kit`)
**Branches from:** `main` @ `ca9f0d6` · **Depends on:** RFC 0004 (`matterkit/extract.py`, `document_pages`, the honest-`extraction_failed` precedent)

Closes the last "recognized as blob" gap for text-bearing documents in the RFC 0004
inventory. `.docx` has been a first-class `EXT_KINDS` entry (`ingest.py`) since the
beginning; until now it was hashed and never read.

---

## 1. The pagination problem, and why this RFC does not solve it

WordprocessingML stores **no page numbers**. Pagination is produced by the rendering
engine at layout time from the font metrics, printer/page setup, field results and
hyphenation dictionary available on the machine doing the rendering. The same `.docx`
paginates differently in Word 2019 and Word 365, on macOS and Windows, and in
LibreOffice. An explicit `<w:br w:type="page"/>` is a *hint to the renderer*, not an
authoritative boundary: content still reflows around it.

So there is no page number to read out of the file. Any page number this kit attached
to DOCX content would be one **this tool invented** — a fabricated legal-grade
citation, which is precisely the failure mode the kit exists to refuse. RFC 0004
established the parallel principle for PDFs: never a silent empty string, because a
silent empty loses evidence. The same principle here forbids a confident-looking
locator that no one can check against the source.

**Decision: DOCX locators are structural, never paginal.** Concretely, and enforced in
three places rather than by convention:

| Enforcement | Where |
|---|---|
| `extract_docx` returns `pages == []` in every case, success and failure | `matterkit/extract.py` |
| `document_blocks` has **no** `page_number` column, and a CHECK constrains `locator_scheme` to `docx-structural` | `matterkit/store.py` |
| `record_blocks` raises on any non-`docx-structural` extraction, so the block store cannot be used to launder a page locator | `matterkit/extract.py` |
| `matter pages` routes `.docx` to `document_blocks` and writes no `document_pages` row | `matter.py` |

### 1a. Storage: why a sibling table, not a `locator_scheme` column on `document_pages`

The brief allowed either. A sibling table was chosen because it makes the invariant
*structural* instead of *procedural*: with a shared table, "never put a paragraph index
in `page_number`" is a rule someone has to keep remembering, and a single careless
`INSERT` breaks it silently. With a separate table there is no `page_number` column to
misuse, and the CHECK constraint means the database itself refuses the mistake.

The migration is therefore **purely additive**: `CREATE TABLE IF NOT EXISTS
document_blocks` runs on the next `store.connect()`. No existing row is read, rewritten
or backfilled; `document_pages` is untouched; an older kit reading a newer store simply
does not see the table.

---

## 2. The locator contract

A citation is the triple **`(document_sha256, part, locator)`**.

**`document_sha256`** — sha256 of the exact source bytes. A locator is valid only
against the byte-identical file. Editing a DOCX yields a different sha256 and therefore
a different citation space; a stale citation cannot silently re-point at moved content.
Sources are opened read-only and are never rewritten (RFC 0004's immutability
discipline, covered by a test that re-hashes the file after extraction).

**`part`** — the OOXML part the locator addresses. v1 addresses exactly one:
`word/document.xml`.

**`locator`** — `<part>#<segment>/<segment>/…`, a path of integer coordinates through
the part's *block tree*, with a one-letter type prefix cycling `b` (block child),
`r` (table row), `c` (table cell).

### 2a. Path construction

Within a container — the body, or a table cell — the recognised block-level children
(`w:p` and `w:tbl`, in document order, skipping property and bookmark elements that
carry no text) are numbered from 0.

- a `w:p` at index *i* is addressed `(…, i)`
- to reach content inside a `w:tbl` at index *i*, append the 0-based row and cell
  indexes and recurse into that cell: `(…, i, r, c)`

Every path therefore has length `3n + 1`, and **the addressed unit is always a
paragraph** — the smallest block that has a stable identity in the format:

```
word/document.xml#b3                    4th block child of the body
word/document.xml#b3/r1/c0/b2           3rd paragraph of row 1, cell 0
                                        of the table at body index 3
word/document.xml#b0/r0/c0/b1/r2/c1/b0  nested one table deeper
```

`container_type` is derived, not stored independently: `paragraph` when the path has
length 1, `table-cell` otherwise (the paragraph lives inside a cell), `part` for the
whole-part rows described in §3.

### 2b. Uniqueness and collision properties

*(this is the property the review gate is aimed at)*

- **Within a part:** a path is a coordinate in a tree. Two distinct blocks have distinct
  paths, because at the first index where their traversals diverge the integers differ.
  No two blocks in a part can collide.
- **Across parts:** impossible, because `part` is part of the citation.
- **Across documents:** impossible, because `document_sha256` is.
- **Identical text is not identical identity.** Two paragraphs reading "Same text." have
  the same `text_sha256` and two different locators. The locator identifies a *position*;
  the hash describes the *content*. Conflating them is what lets a citation drift.
- The storage primary key `(document_sha256, part, locator)` enforces all of the above,
  and makes re-extraction of the same bytes idempotent rather than duplicative.

The canonical string form is total and injective: `DocxLocator.parse(loc.canonical())
== loc` for every locator, and `parse` rejects malformed segments and any path whose
length is not `3n + 1`. Tests cover round-tripping, the four cells of a 2×2 table, a
nested table, and a whole mixed document (10 blocks, 10 distinct locators).

### 2c. Offsets

`DocumentText.text` is the extracted text of the part: block texts joined by a single
`\n` in reading order. Each block carries `char_start`/`char_end` into that string:

```
doc.text[block.char_start:block.char_end] == block.text
```

so a quoted passage re-derives exactly from the stored offsets. **The joiner is not a
delimiter**: a `w:br` inside a paragraph also renders as `\n`, so `doc.text` must not be
re-split on newlines to recover blocks — the stored offsets are the only authoritative
boundaries. Offsets index the extracted text, never the raw XML.

### 2d. Why character offsets rather than run indexes

The brief's sketch was `{part, container_type, container_index, run_start, run_end}`.
This implementation keeps the first three and replaces the run pair with character
offsets, because a `w:r` boundary is a formatting artefact, not a semantic one: Word
re-splits runs on spell-check, on revision tracking, and on edits elsewhere in the
paragraph. "Run 3 of paragraph 7" names a different substring after a trivial edit,
which is exactly the instability a citation must not have. Character offsets into the
extracted text are stable under the same conditions and are directly checkable by a
reader.

`run_count` is still recorded — it demonstrates the run traversal happened and is useful
when diagnosing a paragraph — but it is **never part of a citation**.

### 2e. What counts as text

Only `w:t` is visible text.

- `w:delText` (tracked-change deletions) is **never** extracted. Quoting struck-out text
  as document content misstates the evidence.
- `w:instrText` (field instruction codes, e.g. a `HYPERLINK` target) is never extracted;
  the field's visible result is a normal `w:t` and is.
- `w:ins` content *is* extracted — it is text present in this version of the document.
- `w:tab` → `\t`, `w:br`/`w:cr` → `\n`, `w:noBreakHyphen` → `-`, `w:softHyphen` → nothing.
- `w:pPr` is skipped whole, because `w:pPr/w:tabs/w:tab` is a tab-stop *definition* and
  a naive descendant walk would inject a tab character that is not in the document.
- Element *tails* are ignored, so pretty-printing whitespace in the XML never enters the
  extracted text.

An empty paragraph is an addressable block with empty text, not a dropped one — it holds
a real structural position that later blocks are numbered against.

---

## 3. Failure vocabulary

`status` is `extracted` or `extraction-failed` at the **document** level, with a
human-readable `reason` on failure. There is no partial success: a failure yields zero
blocks and is never a zero-length success.

| Input | Result | Reason contains |
|---|---|---|
| Not a zip (renamed `.txt`, `.pdf`, anything without a zip signature) | failed | `not a zip archive` |
| Polyglot — another format with a zip appended | failed | signature is checked on the leading bytes, before `zipfile` |
| Truncated / corrupt archive | failed | `corrupt or truncated zip archive` |
| CRC mismatch in the document part | failed | `corrupt or truncated zip archive: Bad CRC-32` |
| Encrypted member | failed | `is encrypted; refusing to guess at a password` |
| Part exceeds the decompressed cap (declared **or** actual) | failed | `too large` |
| No `word/document.xml` | failed | `has no word/document.xml part` |
| Two `word/document.xml` entries | failed | `duplicate … entries … ambiguous` |
| `word/document.xml` present but blank | failed | `present but empty` |
| Non-UTF-8 part (UTF-16/32, with or without a BOM) | failed | `has an unsupported encoding` |
| DOCTYPE present (including behind a UTF-8 BOM) | failed | `carries a DOCTYPE declaration` |
| Malformed XML | failed | `malformed xml … refusing to salvage a partial parse` |
| Strict-OOXML or other namespace | failed | `unsupported wordprocessingml namespace` |
| Root element is not `w:document` | failed | `unexpected root element` |
| No `w:body` | failed | `has no w:body element` |
| **Well-formed, no paragraphs** | **extracted**, 0 blocks, `reason is None` | — |

### 3a. Extension-vs-magic mismatch is refused, not re-dispatched

A `.docx` whose bytes are a PDF fails. It is *not* re-routed to `extract_pdf`. A file
whose name contradicts its content is exactly the provenance problem the kit exists to
surface; quietly reading it under a different extractor would record a document that
does not match how anyone else on the matter refers to it.

### 3b. Three things the store must be able to tell apart

1. **never extracted** — no row.
2. **extracted, could not be read** — one row, `status='extraction-failed'`, `text IS NULL`,
   `reason` preserved.
3. **extracted, genuinely contains nothing** — one row, `status='extracted'`,
   `container_type='part'`, `text=''`, with a reason noting the document is well-formed
   and has no paragraphs.

Writing nothing in case 3 would collapse it into case 1; writing an empty string without
a row would collapse cases 2 and 3. Both are lies about the evidence, so both are tested.

---

## 4. Hostile-input policy

- **Extension/magic check first.** The leading bytes must be a zip signature before the
  file reaches `zipfile`, which otherwise locates a central directory anywhere in the
  file and would happily open a polyglot.
- **Zip-bomb guard, checked twice.** `MAX_PART_BYTES` (32 MiB, overridable per call via
  `max_part_bytes=`) is enforced against the *declared* decompressed size and then again
  against a bounded read, so a forged header does not get past it.
- **DOCTYPE refused.** `xml.etree.ElementTree` expands internal general entities — the
  billion-laughs vector — and the C parser in CPython exposes no expat handle to disable
  it. A conforming OOXML part has no DTD, so refusing one costs nothing and removes the
  class of attack. The check walks the XML prolog per the grammar
  (`prolog ::= XMLDecl? Misc* (doctypedecl Misc*)?`) and stops at the root element, so it
  cannot be fooled by the characters `<!DOCTYPE` appearing in the document's own text.
- **UTF-8 is a precondition of that guard.** The DOCTYPE check is a byte scan, so a
  UTF-16/32 part would scan as unrecognisable and slip past it. A UTF-8 BOM is stripped
  before scanning; anything that does not then begin as UTF-8 XML is refused rather than
  parsed blind. Word does not produce such a part. Both bypasses were verified to work
  against the un-guarded code before the guard was written.
- **Duplicate part names refused.** `zipfile` resolves a duplicated name to the last
  entry; another reader may take the first. Which bytes a citation pins to would be
  reader-dependent, so there is no honest answer.
- **Nesting bounded.** Table recursion stops at depth 24 and records the fact in
  `PartInventory.unhandled`; `RecursionError` anywhere in the walk becomes an explicit
  failure rather than a crash.
- **Nothing is written.** No part is extracted to disk, so archive path traversal is not
  reachable. The source is opened read-only, and a test re-hashes it after extraction.

### 4a. Resource bounds

`MAX_PART_BYTES` guards a single member. Checking `len(zf.namelist())` inside
`_extract_docx_open` is no guard at all: `ZipFile.__init__` has already read the
whole central directory and materialised one `ZipInfo` per entry before that line
runs (measured, CPython 3.11: 100,000 tiny members ≈ 55 MB of `ZipInfo` heap,
≈ 0.27 s of parsing). A real bound must run **before `ZipFile` is constructed**.

What the pre-open bound actually is: **honest-or-overstated tail records** (declared
entry count + declared directory bytes + file geometry). Lying records are a
**post-open detection**. Allocation in between is bounded only by file size. There is
no amplification — this is a local-file threat model. The declared-directory byte cap
bounds parser *input* and constrains allocation; it is **not** an exact RAM ceiling
and **not** proof of a pre-open actual-member limit.

#### The pre-open tail-record bound — the GUARD

Read the End Of Central Directory record straight off the file's tail: seek to EOF,
read at most `22 + 65,535 = 65,557` bytes (fixed EOCD length plus maximum comment
length), scan right-to-left for signature `0x06054b50`, and accept a candidate only if
its comment-length field puts the record's end exactly at EOF. Anything else fails as
corrupt/truncated — we do not go fishing for another record. Offsets are from the
record's first byte, little-endian (APPNOTE 4.3.16):

- `10` — total entries in the central directory (2 bytes) ← member-count field
- `12` — size of central directory (4 bytes) ← directory-byte field
- `16` — offset of central directory (4 bytes)
- `20` — comment length (2 bytes)

A saturated classic field holds the sentinel `0xFFFF` (2-byte) or `0xFFFFFFFF`
(4-byte), meaning "consult ZIP64" (APPNOTE 4.4.1.4). Sentinels fire **independently
per field**: an archive can saturate the count while leaving `cd_size` a normal
32-bit value. The ZIP64 EOCD **locator** (`0x07064b50`, 20 bytes) sits immediately
before the EOCD and gives the ZIP64 EOCD record's offset at locator offset `8`
(8 bytes). That **record** (`0x06064b50`, 56-byte fixed portion, APPNOTE 4.3.14)
carries:

- `32` — total entries in the central directory (8 bytes)
- `40` — size of central directory (8 bytes)
- `48` — offset of central directory (8 bytes)
- `4` — size of this record (8 bytes); `value + 12` must equal the fixed portion plus
  any extensible-data sector

**Forged EOCDs.** What is provable pre-open is structural, and all of it is refused as
corrupt/truncated before `ZipFile` exists: a sentinel with no locator at
`eocd_start − 20`; a locator pointing at bytes that are not `0x06064b50`; a ZIP64
record whose declared size does not end at the locator; `cd_offset + cd_size` beyond
EOF; a count that cannot fit in `cd_size` (a central-directory entry is ≥ 46 bytes, so
`count × 46 > cd_size` is a lie). What is *not* provable pre-open: an internally
consistent but false count, or a downward lie about `cd_size` that still fits
`count × 46`. No field is authenticated, and we will not claim otherwise. So one
cheap post-open reconciliation is added — `len(zf.namelist())` must equal the
pre-open count; a mismatch is refused as corrupt, never silently trusted — labelled
as what it is: an honesty check, not a resource guard. The guard already ran.

**`MAX_ARCHIVE_MEMBERS = 10_000`.** A real Word `.docx` is ~10–40 parts (a minimal
package is ~10; media- and annotation-heavy documents reach the hundreds). 10,000 is
two to three orders of magnitude of headroom. It bounds the declared count on
honest-or-overstated tails; a hostile archive that *understates* the count still
reaches `ZipFile`.

**`MAX_CD_BYTES = 32 MiB`.** Same order as `MAX_PART_BYTES`. A real Word directory is
kilobytes; 32 MiB is tens of megabytes of headroom and is the number CPython 3.11
`zipfile._RealGetContents` will `fp.read()` if we let construction start. It bounds
parser input for honest-or-overstated `cd_size`. It does not bound RAM, and it does
not bound the actual member count. A 6 MB directory under a lying count of 5 is
under this cap and is detected post-open.

#### Aggregate uncompressed bytes — an acceptance limit, not a guard

**No: an aggregate-uncompressed bound is not achievable pre-open.** Per-member
uncompressed sizes live in the central-directory entries; the EOCD gives only the
directory's *size* and *count*, and no tail record sums member sizes. Obtaining the sum
requires parsing the directory — the very cost the guard exists to avoid. The honest
split, and the labels must not be blurred:

- **GUARD (pre-open):** the member-count bound and the declared-directory byte cap
  above. Refuses before any `ZipFile` exists, and only for honest-or-overstated tails.
- **ACCEPTANCE LIMIT (post-open):** `MAX_ARCHIVE_UNCOMPRESSED_BYTES = 256 MiB`
  (8 × `MAX_PART_BYTES`), a checked sum of every member's declared `file_size` in
  `_extract_docx_open`, before any part read. It refuses *after* the directory was
  already parsed, and it is a declared-size sanity check: we never decompress members
  other than `word/document.xml`, so their real inflation is never realised
  in-process, and the existing declared-plus-bounded-read pair stays the only
  per-member enforcement.

#### Residual exposure

- A `members` or `central_directory` refusal reads ≤ `4` head bytes + ≤ `65,557` tail
  bytes, plus ≤ `20` (locator — widen the tail window to `65,577` so it is always
  inside) and ≤ `56` (ZIP64 EOCD) when sentinels are present: **worst case ≤ 65,637
  bytes, zero central-directory bytes, zero `ZipFile` constructor attempts.**
- A lying tail (understated count with an honest `cd_size` still under `MAX_CD_BYTES`;
  or a downward `cd_size` lie that still passes `count × 46`) is not preventable
  pre-open. `ZipFile.__init__` then `fp.read`s the declared `cd_size` (CPython 3.11
  `_RealGetContents`) and either materialises the directory or raises `BadZipFile`
  mid-construction. On the measured downward-`cd_size` fixture the message is
  `"Bad magic number for central directory"`: patching size but not offset shifts
  CPython's `concat`, so the bounded read is taken from the wrong place. A short
  file can instead raise `"Truncated central directory"`. Either way the declared
  size bounds parser input; zero successful constructor returns is not proof of
  zero allocation. Allocation on that path is typical, bounded by file size, not
  an enforced ceiling. Post-open detection (count reconciliation, or the
  `except BadZipFile` handler) does not undo it.
- An `aggregate` refusal cannot prevent what already happened: the directory parse
  the guard allowed. The acceptance limit does not remove it; nothing post-open can.
- Unavoidable on every accepted path: the tail window itself, and `zipfile`'s own
  re-parse of the EOCD after our guard passes.

#### Parser agreement (tests, not a cross-version fact)

The pre-open reader and CPython `zipfile`'s `_EndRecData` / `_EndRecData64` are
checked against each other by test: honest EOCD, and ZIP64 locator+record with every
classic field saturated. Agreement means the same effective
`(entries_total, cd_size, cd_offset)`. These checks are pinned to the CPython 3.11
implementation they were read against (`_EndRecData` takes `rfind(stringEndArchive)`
— rightmost — and assumes the magic does not appear in the comment). They carry
forward as tests, not as a settled property of every Python.

Documented divergences, all fail-closed on our side, none a defect:

- **Magic in the EOCD comment (`PK\x05\x06`).** CPython selects the in-comment
  occurrence. No right-to-left repair scan restores agreement, and none is
  implemented (P3-1: withdrawn). We refuse because that occurrence's comment-length
  field does not put a record at EOF. Tests assert *our refusal only*.
- **Comment-length field that does not land on EOF.** We refuse. CPython still
  returns the classic count/size/offset and `ZipFile` opens. Same class as P3-1.
- **Per-field sentinels vs CPython's all-fields overwrite.** APPNOTE 4.4.1.4
  saturates independently per field; we keep unsaturated classic values. CPython
  `_EndRecData64`, once a locator is present, replaces count, size, and offset from
  the ZIP64 record together. Agreement tests use the common all-sentinel ZIP64
  shape; mixed-sentinel fixtures pin *our* APPNOTE behaviour, not CPython's.

#### Failure vocabulary (§3 style)

| Condition | Reason starts | Fires |
|---|---|---|
| total-entry count > `MAX_ARCHIVE_MEMBERS` | `size_limit:members` | pre-open — the GUARD; no `ZipFile` constructed |
| declared `cd_size` > `MAX_CD_BYTES` | `size_limit:central_directory` | pre-open — the GUARD; no `ZipFile` constructed |
| Σ declared `file_size` > `MAX_ARCHIVE_UNCOMPRESSED_BYTES` | `size_limit:aggregate` | post-open — acceptance limit; directory already parsed |

All three exit through `_docx_failed`: `status='extraction-failed'`, zero blocks, zero
pages, reason preserved. A downward `cd_size` lie that reaches `ZipFile` and raises
`BadZipFile` is `corrupt or truncated zip archive: …`, distinguishable from
`size_limit:central_directory`.

---

## 5. Part-set policy

Only `word/document.xml` is read. `PartInventory` makes the rest legible rather than
leaving a reviewer to guess whether a part was skipped by design or missed:

| field | meaning |
|---|---|
| `seen` | every member name in the archive |
| `extracted` | `("word/document.xml",)` |
| `skipped` | text-bearing parts present and deliberately not read in v1: `word/header*.xml`, `word/footer*.xml`, `word/footnotes.xml`, `word/endnotes.xml`, `word/comments*.xml` |
| `unhandled` | local names of elements found at a block position and not descended into — e.g. `sdt` (content controls) — so no content is dropped silently |

`matter pages` prints both `skipped` and `unhandled` for each DOCX.

---

## 6. Storage

```sql
CREATE TABLE IF NOT EXISTS document_blocks (
  document_sha256 TEXT NOT NULL,
  locator_scheme TEXT NOT NULL CHECK (locator_scheme IN ('docx-structural')),
  part TEXT NOT NULL,
  locator TEXT NOT NULL,
  block_index INTEGER NOT NULL,      -- reading-order ordinal; -1 on a whole-part row
  container_type TEXT NOT NULL
    CHECK (container_type IN ('paragraph','table-cell','part')),
  path TEXT NOT NULL,                -- JSON array of the locator's integer path
  char_start INTEGER, char_end INTEGER,
  text TEXT,
  text_sha256 TEXT,
  status TEXT NOT NULL CHECK (status IN ('extracted','extraction-failed')),
  reason TEXT,
  PRIMARY KEY (document_sha256, part, locator)
);
```

No `page_number`. The `locator_scheme` CHECK is deliberately single-valued: adding a
scheme is a deliberate migration with its own review, not an insert someone can make by
accident.

### Locator schemes in the kit

| scheme | format | locator means | table |
|---|---|---|---|
| `pdf-page` (RFC 0004) | PDF | 1-based page number read from the file's own structure | `document_pages` |
| `docx-structural` (this RFC) | DOCX | structural path through the OOXML block tree | `document_blocks` |
| `whole-file` (RFC 0004) | `.txt`/`.md`, unextractable kinds | the whole file is one unit | `document_pages` |

---

## 7. Dependencies

Stdlib only: `zipfile`, `xml.etree.ElementTree`, `hashlib`, `json`, `re`, `zlib`,
`pathlib`. No `python-docx`, no `lxml`. The floor is asserted by a test that re-imports
the module in a subprocess with every `site-packages` path stripped from `sys.path`, not
by a comment. RFC 0004's optional heavier paths (pdfplumber, tesseract) stay optional and
remain unimported.

---

## 8. Acceptance

57 new tests in `tests/test_extract_docx.py`; 150 total, green under
`python -m unittest discover -s tests` (the CI runner) on Python 3.11 and 3.14. Every fixture is
synthesised in-process from XML strings plus `zipfile` — no binary fixtures committed, no
client documents.

Mapping to the brief's acceptance list:

| brief | covered by |
|---|---|
| (a) 2-paragraph DOCX, correct indexes, stable `text_sha256` | `TestLocatorContract` |
| (b) 2×2 table, four cells addressed distinctly | `TestTableLocators` (plus multi-paragraph cells, nesting, a 10-block mixed document) |
| (c) truncated/corrupt zip → failed | `TestHonestFailure`, `TestHostileInput` |
| (d) zip without `word/document.xml` → failed | `TestHonestFailure` |
| (e) renamed `.txt`/PDF refused, not re-dispatched | `TestHonestFailure` (and the polyglot case in `TestHostileInput`) |
| (f) offset round-trip | `TestOffsetRoundTrip`, `TestBlockStorage` (re-sliced out of SQLite) |
| (g) 93 existing tests green; stdlib-only asserted by import check | full suite; `TestStdlibOnlyFloor` |

Beyond the list: zero-fabricated-pagination assertions at the object, schema and CLI
levels; the empty-vs-failed distinction; tracked-deletion and field-code exclusion;
DOCTYPE (including behind a BOM), non-UTF-8, duplicate-part, CRC and zip-bomb
refusals; source-immutability re-hash.

---

## 9. Out of scope (inherited from RFC 0004 unless noted)

OCR; rendered-page claims for any format; header/footer/footnote/endnote/comment
extraction; style and formatting preservation; `.doc` (legacy binary — explicitly *not*
routed to the DOCX extractor); full OOXML schema coverage; the viewer UI; timeline and
evidence matrix; any model-produced facts. `matter-mcp.py` is untouched.

## 10. Named gaps for v2

Each of these is visible in the output rather than silent:

1. **Main-part resolution.** v1 addresses `word/document.xml` by its conventional name
   instead of resolving the `officeDocument` package relationship in `_rels/.rels`. A
   producer that names the main part otherwise fails with "no word/document.xml part"
   rather than being read.
2. **`w:sdt` content controls** are not descended into; they appear in
   `PartInventory.unhandled`. Descending needs a path segment for the SDT wrapper, which
   changes the locator grammar and so wants its own review.
3. **Headers, footers, footnotes, endnotes, comments** are listed in
   `PartInventory.skipped`. Extracting them needs a second `part` value per document,
   which the schema already supports (`part` is in the primary key) but the extractor
   does not yet produce.
4. **`document_pages` and `.txt`/`.md`.** A plain-text file is stored as
   `document_pages` page 1, which is a whole-file locator wearing a page number. It
   predates this RFC and is left alone here rather than widened into it, but it is the
   same category of claim and should be reconciled when the viewer lands.
