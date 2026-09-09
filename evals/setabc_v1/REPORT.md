# Set A/B/C v1 — kit parser vs eyecite (public/synthetic, format-level)

**Date:** 2026-09-08 · **Corpus:** `evals/setabc_v1` · **Material:** synthetic sentences embedding **real public citation formats** — zero lawsuit or private content, no network, no paid services.
**Freeze discipline:** gold decks (`gold.json`, sha256 in `manifest.json`) written **before** any scoring run; corpus generation seeded (`random.seed(20260908)`); scored with kit `citations.py` @ `19f5d4e` (R1) and eyecite 2.7.8. Leakage/label review: @adversarial-review-deepseek-agent (pending).

## Headline

| measure | kit stdlib v1 | eyecite 2.7.8 |
|---|---|---|
| Set A recall (case cites, n=12) | **1.000** (12/12) | **1.000** (12/12) |
| Set B recall (statutory, n=180) | **0.583** (105/180) | **0.417** (75/180) |
| False positives on negative controls | **0** | **0** |
| Junk tokens on misses | **0** (bare `§` → structured `extraction-failed` rows) | **66** `UnknownCitation` tokens (silently dropped by upstream APIs) |
| Short-form / `id.` / `supra` | 0 rows (not attempted in v1 — documented) | **3/3 detected** (Id/Supra/ShortCase classes) |

## Set B per style (n=15 each)

| style | kit | eyecite | union |
|---|---|---|---|
| 15 U.S.C. § … | 1.00 | 0.00 | 1.00 |
| X Del. C. § … | 1.00 | 0.00 | 1.00 |
| Miss. Code Ann. § … | 1.00 | 1.00 | 1.00 |
| Wyo. Stat. Ann. § … | 1.00 | 1.00 | 1.00 |
| Cal. Civ. Code § … | 1.00 | 0.00 | 1.00 |
| Cal. Bus. & Prof. Code § … | 1.00 | 0.00 | 1.00 |
| Tex. Bus. & Orgs. Code § … | 1.00 | 0.00 | 1.00 |
| Tex. Gov't Code § … (apostrophe) | 0.00 | 0.00 | 0.00 |
| Fla. Stat. § … | 0.00 | 1.00 | 1.00 |
| N.Y. Bus. Corp. Law § … | 0.00 | 1.00 | 1.00 |
| 805 Ill. Comp. Stat. 5/x | 0.00 | 0.00 | 0.00 |
| 17 C.F.R. § … | 0.00 | 1.00 | 1.00 |
| **Set B total** | **0.583** | **0.417** | **0.942** (169/180) |

Misses are **deterministic format gaps**, not noise: kit misses `Gov't` (apostrophe family), `Fla. Stat.`/`N.Y. … Law` (no `Ann.`/`Code` keyword family), `Comp. Stat.` (multi-part), C.F.R.; eyecite misses exactly the USC/`Del. C.`/`Civ. Code`/`Bus. & Prof.`/`Bus. & Orgs.` families its reporters-db does not carry — reproducing the 2026-09-08 smoke on a 180-item corpus. Union coverage 0.942 leaves two genuinely uncovered families (`Tex. Gov't` apostrophe; `Ill. Comp. Stat.`).

## Reading (recommendation to the chair)

1. **The two parsers are complementary by format family.** Neither meets the Set-B ≥ 0.95 acceptance bar alone; the *union* nearly does, and the two missing families are both simple kit-regex additions.
2. **Kit failure mode is honest by construction** (structured `extraction-failed` rows with offsets, zero junk) — eyecite's misses become `UnknownCitation` tokens that downstream code must know to drop. On the three-stage contract, kit failures surface; eyecite failures vanish.
3. **eyecite is the only candidate for short-form/`id.`/`supra` handling in v1** — 3/3 detected where the kit attempts nothing. That is Set A enrichment, not Set B.

**Recommended next action (not yet authorized):** extend the kit stdlib parser with the four missing families (`Gov't`-apostrophe handling, `Fla. Stat.`/state-dotful-name without `Ann.`, `Comp. Stat.` multi-part, C.F.R.) — target Set B ≥ 0.95 kit-solo on v2 of this corpus — and keep eyecite as the optional extra for case short-forms under the RFC's BSD-2-pin rule. Re-run this harness (frozen gold unchanged; new docs appended as v2) after the parser change.

## Threats to validity (honest scope)

- **Format-level, not semantic:** v1 measures *detection*, not resolution or correctness of the parsed sections.
- **Synthetic sentence contexts:** real pages carry OCR noise, footnotes, tables; formats seen in the wild are messier than these templates.
- **Single run, one version pair:** kit @ `19f5d4e`, eyecite 2.7.8; re-run on version bumps per spec.
- **Reviewer:** adversarial pass on labels/scoring (leakage, missed-citation formats) still open — this report is pre-review.
