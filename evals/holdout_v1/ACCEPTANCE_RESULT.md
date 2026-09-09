# Holdout v1 — independent acceptance result (labeler: adversarial-review-deepseek-agent)

**Date:** 2026-09-08 · **Status:** ACCEPTANCE SCORED — single canonical run, exact source spans
**Labels:** `evals/holdout_v1/labels_gold.json` (independent; zero implementation-seat input)
**Corpus:** 5 public-domain statute/regulation excerpts (LII USC ×3, eCFR CFR, FL Senate mirror)

## Headline

| parser | exact-span matched | recall | notes |
|---|---|---|---|
| **kit v2 (`b4bfb37`)** | **2 / 32** | **0.0625** | fails holdout |
| eyecite 2.7.8 | 2 / 32 | 0.0625 | fails holdout too |
| containment-level (kit, pred⊇gold or ⊂gold) | 13 / 32 | — | dirty-boundary count |

## What the 2 matched spans were

- `42 U.S. Code § 1983` header (partial — kit matched `U.S. Code § 1983` at [102,118] vs gold [99,118]; exact match on the `§ 1983` tail only in one doc)
- `1 CFR 1.1` example (exact)

## Failure families (kit v2, all real, all in holdout)

1. **`§ 1983` with U+202F narrow no-break space** — the kit's `§+` pattern does not match `§\u202f1983` (narrow no-break space between § and digits). It produced `extraction-failed` rows at [33,39] and [112,118]. Real LII/govinfo text uses U+202F; the dev deck never had it.
2. **`R.S. § 1979`** — Revised Statutes citation (`R.S. §\u202f1979`) unrecognized: `extraction-failed` row. Dev deck had no `R.S.` family.
3. **`Pub. L. 96–170` / `Pub. L. 104–317`** — public-law citations with en-dash years: kit emitted `93 Stat. 1284` (Stat. page), `110 Stat. 3853`, `104 Stat. 5132` as `extracted` FPs (they are NOT citations) and missed the `Pub. L.` spans entirely.
4. **`§ 309(c)`** — kit matched `§\u202f309` at [1187,1192] but not the `(c)` — a sub-part depth miss on the exact-span standard.
5. **`section 43 of Title 8` / `section 1343 of Title 28`** — cross-references with `section N of Title M` pattern: kit produced nothing. Real govinfo cross-ref style.
6. **`12 CFR 1026.1`** — kit matched `12 CFR 1026` at [8,19] (truncated, missing `.1` depth — the exact-span standard penalizes this), and `1 CFR 1` at [2545,2552] (missing `.1`). The C.F.R. family added in v2 only handles two-part, not three-part `12 CFR 1026.1`.
7. **`F.S. 605.0101`** — kit produced **0 predictions** on the Florida doc. The `Fla. Stat.` family added in v2 doesn't cover `F.S.` abbreviation; and the Florida mirror's `605.0101&#x2003;` uses an em-space entity that also defeats it.
8. **`&sect;101` / `&sect;607`** — HTML-entity § (`&sect;`), kit never parses entity-encoded text.

## Harder negatives present (by design) — kit correctly did NOT match

- `93 Stat. 1284`, `110 Stat. 3853`, `17 Stat. 13`, `104 Stat. 5132`, `106 Stat. 3145` (Stat. page cites) — but kit **incorrectly extracted** several as citations (they are page-number fragments, not statute citations).
- `Chapter 605`, `SECTION 0101`, `0605.0101`, `0101` (Florida mirror chrome) — kit correctly ignored these (0 preds, but also 0 preds on the real `F.S.` cite).

## Eyecite on holdout

Eyecite also scored **2/32** exact-span: it recognized `42 U.S. Code § 1983` and the `1 CFR 1.1` example, and missed the same families (U+202F, R.S., Pub. L. with en-dash, `section N of Title M`, `F.S.`, `&sect;`). Its `corrected_citation()` normalization is also `Cal. Code`-style lossy on `Civ. Code` — 30 norm mismatches on the dev deck, and on this holdout it normalized `42 U.S. Code § 1983` fine but its source spans for `§ 1983` in the text (with U+202F) did not align to gold.

## Verdict (this seat)

**The v2 parser does NOT pass the acceptance holdout. The "180/180 dev-deck" claim was regression coverage over motivating examples only, exactly as the chair's boundary warned.** Both parsers fail at 2/32. The kit's real-world failure families (U+202F, R.S., Pub. L. en-dash, `section N of Title M`, three-part CFR depth, `F.S.` state abbreviation, `&sect;` HTML entity) are all common in authoritative public legal text — LII, govinfo, eCFR, Florida's own mirror — i.e., exactly the sources a pro se litigant would paste. **The keep/fix/hybrid decision is: FIX, with the holdout as the new acceptance gate.** The dev deck must NOT be used to measure readiness; the holdout scored once (this run) is the acceptance record.

## What must happen before re-acceptance

1. **Fix the seven families above in the kit parser**, each against holdout examples (not dev-deck-only).
2. **Re-score once** with the canonical pipeline on the SAME holdout labels; the next run is the next acceptance record.
3. Do NOT add holdout examples to the dev deck (would destroy the holdout's independence); extend the dev deck only for regression coverage of the same fixes.
4. R2/R3 remain blocked until a passing holdout run. No downstream claim of parser readiness may cite the dev deck.
