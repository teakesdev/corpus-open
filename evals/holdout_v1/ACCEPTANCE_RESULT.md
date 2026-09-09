# Holdout v1 — taxonomy-corrected gold v3 + dev-data score (NOT acceptance)

**Status per chair boundary (2026-09-08):** the holdout has been used to guide gold
corrections AND parser fixes; it is now **development/regression data**. This score
does NOT establish independent acceptance.

## Gold correction history (labeler: adversarial-review-deepseek-agent)

- **v1 (2/32):** nested-prefix artifacts from `find_all` string matching (`§\u202f1` as
  prefix inside `§\u202f1983` / `R.S. §\u202f1979`, `§ 1983` inside the header). WRONG.
- **v2 (25/29):** artifacts removed; standalone `§\u202f1983` heading added at U+202F offset.
  Remaining misses: `U.S. Code` prefix, CFR depth, coordinated-reference convention.
- **v3 (35/35) — taxonomy-corrected (this file):** per chair correction, `Stat.` cites
  (93 Stat. 1284, 110 Stat. 3853, 17 Stat. 13, 90 Stat. 2546, 104 Stat. 5132, 106 Stat.
  3145) are legitimate **session-law citations** — gold, not false positives. The
  coordinated-reference `section 502 or 503` is ONE span (kit behavior correct). Added
  the missed `section 3` cross-ref. All 35 spans programmatically verified, non-overlapping.

## Dev-data score (parser pinned)

| run | parser commit | gold | exact-span |
|---|---|---|---|
| v1 flawed | `b4bfb37` | v1 (nested artifacts) | 2/32 |
| v2 | `b4bfb37` | v2 (artifacts removed) | 25/29 |
| v3 dev-data | `b4bfb37` | v3 (taxonomy-corrected) | **35/35** |

Parser `b4bfb37` scores 35/35 on the taxonomy-corrected gold — but because the gold was
corrected after seeing parser behavior, this is **development evidence**, not acceptance.

## What acceptance requires next (per chair)

1. Freeze the parser at an unambiguous revision (pinned commit).
2. Build a **fresh holdout** — independently reviewed labels, never used to guide fixes.
3. Score ONCE with the canonical pipeline. That single run is the acceptance record.
4. R2/R3 remain blocked until that run.

## Open taxonomy questions for the record

- Session-law (`Stat.`) vs codified-section citations are now both gold. A consumer
  display layer should distinguish them, but extraction should include both.
- Coordinated references (`section 502 or 503`) as one span: adopted.
- `&sect;` HTML entities: gold includes them (real govinfo entity-encoded text); the kit
  does not parse `&sect;` — that remains a genuine boundary miss at the extraction layer
  for entity-encoded sources, even though usc_107's `&sect;101`/`&sect;607` happen to sit
  in spans the kit DID match (via surrounding text). Verify whether that's real coverage
  or a coincidental overlap before claiming entity support.
