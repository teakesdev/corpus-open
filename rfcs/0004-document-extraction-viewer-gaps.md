# Document extraction & read-only viewer — gap inventory (bounded)

**Date:** 2026-09-11 · **Owner:** @flash (chair assignment) · **Repo:** `teakesdev/corpus-open` (`/Users/ty/dev/matter-kit`, HEAD `f35efb2`)

## What exists today (verified against the tree)

| capability | state | evidence |
|---|---|---|
| Ingest hashes + classifies files | **yes** | `ingest.py`: `sha256_file`, `classify` (ext→kind + dir-hint), `ingest_paths`, `verify_integrity`. Sources never modified. |
| PDF/DOCX are *recognized* | **yes, as blobs** | `EXT_KINDS`: `.pdf→filing`, `.docx→filing` — registered and content-hashed only. |
| **PDF/DOCX text extraction** | **NO** | grep for pypdf/pdfplumber/fitz/mupdf/docx/tesseract → nothing except a comment in citations.py. `ingest.py` has no `extract`/`text`/`page` symbol. |
| Page locators on passages | **NO** | no `passages` table; `documents` is hash+path only. |
| Assertion → source pinning | **yes, coarse** | `assertion_sources(document_id, locator)` — locator is a free string, no page/offset schema, no click-through. |
| Read-only viewer | **NO** | no viewer/browse/inspect module; `packet.py` renders markdown from DB rows only. |
| Timeline / evidence matrix | **NO** | `events` + `issues` tables exist (empty scaffolds); no chronology renderer, no issue↔evidence join UI. |
| MCP surface | facts/deadlines/authorities/cite_extract + adapters | no document/read/view tools. |

## The smallest missing vertical slice (what I am implementing)

**Local PDF text extraction with page locators** — the one capability that turns the kit from "hash store" into "document intelligence," and the documented flagship workflow's first real step.

Design (zero-dependency, chair's "lightweight default"):
- **README/text paths unchanged.** `.txt`/`.md` already ingest as-is.
- **New module `matterkit/extract.py`** — stdlib-only primary path, **no pypdf dep by default**:
  - `extract_pdf(path) -> list[Page]`: a minimal, honest stdlib PDF text extractor covering the common text-stream/FlateDecode case (Tj/TJ operators, `/FlateDecode` via `zlib`). Returns per-page text + page number. **Not a full PDF engine** — streams using `/LZWDecode`, Type0/CID fonts, or scanned (image-only) pages return `extraction_failed` on that page, never silent empty text.
  - `extract_text(path)` dispatches by extension (`.pdf`, plain, else null -> caller decides).
- **Optional heavier deps** (pdfplumber/tesseract) are a documented *optional* install (`extras_require`) — not required, not imported unless present. Keeps default install stdlib.
- **Page-locator storage:** `cite_extract`-style provenance is already the pattern; extraction writes to a new `document_pages` table scoped by `document_sha256` + page number + `text_sha256` (offsets always relative to the exact per-page text).

## Out of scope (this slice)
OCR for scanned pages, docx unpacking, full tables/layout, the read-only viewer UI, timeline/matrix join, any model-produced facts.

## Vertical-slice acceptance (synthetic + public fixtures)
1. A synthetic 2-page PDF (text-stream/FlateDecode) extracts both pages, correct page numbers, non-empty, `text_sha256` stable.
2. A scanned/image-only PDF returns `extraction_failed` honestly (no silent empty).
3. A `.txt` file extracts identically to raw read.
4. Extracted page text reads back against source offsets (`text[start:end]` matches).
5. No new hard dependency: `import matterkit.extract` works with stdlib only.
6. Existing 88 tests stay green.