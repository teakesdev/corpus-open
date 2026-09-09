# Set A/B/C v1 — CORRECTED RECORD (rev2)

**Date:** 2026-09-08 · **Supersedes:** the original `REPORT.md` claims (0.583 / 0.417 / 0.942), which are **retracted as unreproducible**.

## Correction history (adversarial finding, Deepseek 2026-09-08 — accepted in full)

1. **The v1 scorer was never committed** — gold, docs, and score JSONs landed (`3c0a2cb`) but the code producing the numbers did not. Process defect: an eval is not reproducible without its scorer. `score.py` is now committed alongside the artifacts it reads.
2. **The v1 matcher was too loose.** Containment matching counted polluted spans as hits: pattern 4 (`Code §`) over-matches `Cal. Civ. Code`/`Cal. Bus. & Prof.`/`Tex. Bus. & Orgs.` by swallowing the sentence prefix (`"Compare Cal. Civ. Code § 1500"`), and containment then scored the polluted fragment as a hit. Under exact-match spans those families are **0/15**, not 1.00.
3. **One sub-claim failed verification:** `Tex. Gov't Code` **is present** in the committed corpus (`stat_06_*.txt`, e.g. `Tex. Gov't Code § 0.005`) — the kit's `tx` misses are real measured failures, not an untested style.

## Numbers — exact-match spans (PRIMARY standard; committed `score.py`, default mode)

| measure | kit stdlib v1 | eyecite 2.7.8 | union |
|---|---|---|---|
| Set A recall (case cites, n=12) | 1.000 (12/12) | 1.000 (12/12) | 1.000 |
| **Set B recall (statutory, n=180)** | **0.333 (60/180)** | **0.250 (45/180)** | **0.417 (75/180)** |
| False positives on negative controls | 0 | 0 | — |

Exact = fragment equals the gold string, no normalization. Span hygiene is part of parser quality: a regex that eats the sentence prefix fails exact match **by design**.

## Secondary standard — bounded hygiene (tolerates a trailing period only, never prefixes)

| Set B | kit | eyecite | union |
|---|---|---|---|
| hygiene recall | 0.333 (60/180) | 0.333 (60/180) | 0.417 (75/180) |

The containment rule changes eyecite's number (60 vs 45 — trailing-period fragments) and nothing else; the union is 0.417 under both standards. Union breakdown (exact): kit-only 30 (`Del. C.`, `U.S.C.`), eyecite-only 15 (`Fla. Stat.`), shared 30 (`Code Ann.` families), **neither 105** (`Civ. Code`, `Bus. & Prof.`, `Tex. Gov't`, `Tex. Bus. & Orgs.`, `Comp. Stat.`, C.F.R., `Bus. Corp. Law`).

## Kit span-pollution finding (new, actionable)

Pattern 4 over-matches 15 Set B spans (representative: `"Compare Cal. Civ. Code § 1500"`). Root cause: the generic `…Code §` pattern has no left anchor. Fix path: require the code name to start a token boundary not preceded by sentence-leading words, or trim to the maximal `\d+ <Name> Code § \d+`-shaped suffix. Pollution is the reason `ca`/`ca_bp`/`tx_bo` score 0/15 exact despite detection.

## Honest conclusions (rev2)

- **No parser approaches the ≥ 0.95 Set-B acceptance bar**: kit 0.333, eyecite 0.250 exact; union 0.417. The v1 "complementary families, union ≈ 0.94" claim was an artifact of loose matching and is withdrawn.
- Real, reproducible format gaps — kit: `Civ. Code`-family anchoring, `Gov't`-apostrophe, `Fla. Stat.`-style dotted states without `Ann.`, `Comp. Stat.` multi-part, C.F.R. eyecite: `U.S.C.`/`Del. C.`/`Civ. Code`/`Bus. & Prof.`/`Bus. & Orgs.`/`Gov't`/`Comp. Stat.` (reporters-db coverage).
- Set A discriminates nothing at n=12 with both parsers at 1.000 (Deepseek: correct) — it stays a regression gate, not a discriminator, until a short-form/`id.` gold (eyecite-favorable) and harder case-cite variants are added in v2.
- Set A/B/C **v2 scope (proposed):** fix pattern-4 anchoring + add the five missing families kit-side; extend gold with short-form/`id.` expectations and messier contexts (footnote markers, line breaks); re-run under the same committed scorer. Decision (keep / fix / hybrid) deferred until v2 numbers exist.

## Reproducibility

```
python3 evals/setabc_v1/score.py            # exact (primary)
python3 evals/setabc_v1/score.py --hygiene  # secondary standard
```
Reads only committed `gold.json` + `manifest.json` + `docs/`; kit parser imported from the repo at HEAD; eyecite (optional comparison) runs from the local smoke venv if present. Rescored artifacts: `rescored_exact.json`, `rescored_hygiene.json`. Freeze discipline unchanged: gold frozen before scoring; synthetic material only; zero network.
