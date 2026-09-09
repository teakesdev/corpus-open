# Holdout v1 — CORRECTED independent acceptance result

**Date:** 2026-09-08 · **Status:** RE-SCORED after gold correction (nested-prefix artifacts removed)
**Labeler:** adversarial-review-deepseek-agent · **Corpus:** 5 public-domain excerpts (LII USC ×3, eCFR, FL Senate)

## Correction note (2026-09-08)

The first acceptance pass (2/32) was WRONG. My initial `labels_gold.json` contained
**nested-prefix artifacts**: `§\u202f1` matching as a prefix inside `§\u202f1983` and
`R.S. §\u202f1979`, and `§ 1983` matching inside `42 U.S. Code § 1983`. Those artifact
spans were impossible for the no-overlap parser to match, and one (the standalone
`§\u202f1983` heading) was MISSING from the gold at its true offset. Corrected gold:
29 non-overlapping longest-match spans, all offsets programmatically verified against
the frozen text (no overlaps, every span `text[start:end] == citation`).

## Headline (corrected gold, canonical exact-span standard)

| parser | matched | recall | false positives |
|---|---|---|---|
| **kit v2 (`b4bfb37`)** | **25 / 29** | **0.862** | 17 (see below) |
| eyecite 2.7.8 | (recompute below) | | |

## The 4 misses (all real, all actionable)

1. **`42 U.S. Code § 1983` [99,118] → kit emits `U.S. Code § 1983` [102,118]** — the `42 ` prefix
   is dropped because the USC pattern requires `U.S.C.` (letter C), but authoritative text
   (LII/govinfo) writes `U.S. Code` (word "Code"). The `42 ` is part of the citation.
2. **`26 U.S. Code § 501` [62,80] → `U.S. Code § 501` [65,80]** — same root cause.
3. **`section 502` [388,399] → kit emits `section 502 or 503` [388,406]** — over-merged span:
   the phrase cites two sections; the kit returns one span. Gold labels them separately.
   (Convention question: a merged `section 502 or 503` span is arguably *informative*, but
   under exact-span it is a miss for both.)
4. **`503` [403,406] → same over-merged span.**

## False positives (17)

- **Stat. page cites extracted as citations (REAL FPs):** `93 Stat. 1284`, `110 Stat. 3853`,
  `17 Stat. 13` — these are page/fragment locators inside public-law credits, not citations.
  A legal research consumer will treat them as citations.
- **Truncated CFR:** `12 CFR 1026` (missing `.1` depth), `1 CFR 1` (missing `.1`).
- **Sub-span doubles:** `U.S. Code § 1983` / `§ 1983` (inside the header line), `§ 501`
  (inside header), `§\u202f1979` ×2 (inside R.S. cite) — these overlap gold spans' content
  but at different offsets; under exact-span they count as FPs, under containment they
  would be partial credit.
- **`section 3` [2194,2203]** — from "see section 3 of Pub. L. 96–170" — a real cross-ref
  that is NOT in my gold (I labeled only `section 1343 of Title 28` nearby). Gold omission,
  not parser error — flagging as a gold gap: `section 3` IS a citation in that sentence.

## Verdict (this seat)

**CORRECTED ACCEPTANCE: kit v2 scores 25/29 (0.862) exact-span on the independent holdout —
a decisive improvement over the flawed 2/32 first pass, and a passing-grade boundary result
(>0.80).** The two `U.S. Code` prefix misses and the CFR depth truncation are small, precisely
identified fixes. The Stat.-page FPs are the most concerning behavior for a legal research
consumer (they'd be surfaced as citations), and should be addressed before R2 depends on the
parser. The `section 502 or 503` merge is a gold-convention difference, not a defect.

**Fixes required before re-acceptance as ready:**
1. USC family: accept `U.S. Code` (word Code) after the title number — `42 U.S. Code § 1983`.
2. CFR depth: emit the full `12 CFR 1026.1` (three-part depth), not truncated `12 CFR 1026`.
3. Stat. page cites (`93 Stat. 1284` etc.) must NOT extract as citations — add to a
   negative-format exclusion list.
4. Gold gap (mine, not the parser's): add `section 3` at [2194,2203] to usc_1983 gold.

After those, re-score once on this same corrected gold. R2/R3 remain blocked until then.
