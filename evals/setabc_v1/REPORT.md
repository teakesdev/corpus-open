# Set A/B/C — CANONICAL RECORD (rev3, replaces rev2 tables)

**Date:** 2026-09-08 · **Standard:** exact source spans, item-level, both parsers. Scored against each parser's **original source offsets** (`text[start:end]`); normalized text (eyecite `corrected_citation()`) stored separately — **normalization is a different axis and never affects match status** (chair instruction). One canonical mode; hygiene (bounded startswith +2, same start) is a diagnostic only.

## Canonical numbers (canonical_run.json; scorer `score.py`; self-tests `test_scorer.py` 10/10)

| | kit stdlib v1 | eyecite 2.7.8 | union |
|---|---|---|---|
| Set A (n=12) | 12/12 | 12/12 | 12/12 |
| **Set B recall (n=180)** | **0.333 (60)** | **0.500 (90)** | **0.667 (120)** |
| False positives (unmatched predictions) | **45** | 18 | — |
| Junk tokens excluded upstream | 0 | 66 `UnknownCitation` | — |
| Duplicates collapsed | 0 | 0 | — |
| Hygiene diagnostic hits | 60 (= exact) | 102 | — |

Union computed from stored matched-ID sets: kit-only 30, eyecite-only 60, shared 30, neither 60.

## Why these numbers differ from rev2 (causes now proven, per Deepseek's reproduction)

1. **rev2 scored eyecite on `corrected_citation()`, not source spans.** Normalization had silently changed spans/text (`Cal. Civ. Code § 1500` → `Cal. Code § 1500`): 30 normalization mismatches on matched items, first-class in the record. Scored on true source offsets, eyecite's Set B recall is **0.500**, not 0.250. The chair's suspicion — the exact/hygiene discrepancy mixed boundary detection with formatting — confirmed and fixed structurally.
2. **The hygiene rule explained the 45-vs-60 gap** (trailing-period spans) — kept as a labeled diagnostic; with source-span scoring the diagnostic now equals exact for both parsers, and the discrepancy class is closed.
3. **eyecite false positives are first-class now:** 18, dominated by C.F.R. boundary truncation (`17 C.F.R. § 240` for gold `17 C.F.R. § 240.10b-5`). Kit FPs: 45, all sentence-prefix pollution (`Compare Cal. Civ. Code § 1500`). Both parsers therefore **detect more than they bound correctly** — FP hygiene is now a measured axis, not an anecdote.
4. The matcher itself had a double-consume bug (one prediction matching two gold items); the chair-mandated scorer self-tests caught it before any number shipped. Fixed; 10/10 self-tests cover prefix pollution, duplicates, overlapping spans, text-drift assertion, hygiene rules.

## Honest reading

- Still **far from the ≥ 0.95 acceptance bar** — best single parser 0.500, union 0.667 on v1's synthetic format-level corpus. The keep/fix/hybrid decision remains **deferred to v2**.
- Complementary weakness is real and now precisely characterized: kit owns `U.S.C.`/`Del. C.` families, eyecite owns `Code Ann.`+`Fla. Stat.`/`N.Y.`/C.F.R.-detects-but-truncates; both fail `Gov't`-apostrophe and `Comp. Stat.`.
- **Development vs acceptance data (chair, binding):** v1 examples (and any v2 additions motivated by these failures) are **development data**. The keep/fix/hybrid acceptance decision requires a **separately frozen, independently labeled holdout** — public-document excerpts plus harder negatives — scored once, after parser changes are complete.
- Set A stays a regression gate (n=12, both parsers 1.000, no discrimination).

## v2 dev-data result (parser changes applied 2026-09-08; STILL development data)

Kit parser extended (pattern-4 replaced by left-anchored dotted-chain patterns with `(?<![\w.])` mid-acronym guard; `Gov't`, `Fla. Stat.`-style, `Comp. Stat.`, C.F.R.-depth families added). Rescored with the same canonical scorer:

| Set B (dev deck) | kit v2 | eyecite | union |
|---|---|---|---|
| recall | **1.000 (180/180)** | 0.500 (90/180) | 1.000 |
| false positives | **0** | 18 | — |
| pollution telemetry | 2 (nested-string cross-talk: `…10b-1` ⊂ `…10b-10` across same-doc golds — telemetry artifact, spans correct) | — | — |

All 35 kit tests + 10 scorer self-tests green after the change.

**What this proves and what it does not:** it proves the v2 patterns cover every format family the dev deck contains — i.e., **regression coverage over the motivating examples** (chair's exact words). It does **not** establish general retrieval readiness: the patterns were built from these very examples, real-world text (OCR noise, footnotes, tables, odd spacing) is untested, and normalization/resolution remain unmeasured. **The keep/fix/hybrid acceptance decision still waits on the separately frozen, independently labeled holdout** (public-document excerpts + harder negatives), scored once with this same canonical pipeline.

## v2 queue (not started)

Kit parser: pattern-4 left-anchoring (45 FPs), `Gov't`-apostrophe, `Fla. Stat.`-style dotted states, `Comp. Stat.` multi-part, C.F.R. depth. eyecite path (if hybrid): only as optional extra, BSD-2 pin, source-span output already proven feasible here. Then: frozen holdout construction (public excerpts + harder negatives, independent labeling), single canonical scoring, acceptance decision.
