I'll start by reading the handoff file.I'll read the review materials in parallel: the diff, the differential output, the code under review, the new test file, and the two permitted base-review files.Now let me read the rest of `briefcheck.py` to see the remainder of `_sentence_spans`, the checker loop, `resolve_authority`, and the uncited-fact rule.Reading the remainder of the file — the `_Checker` class, unit loop, and `resolve_authority`.Reading the file's tail, plus the differential script and the test fixture base.I have the full checker flow. Now I need `matterkit/citations.py` to judge what the extractor reads after a split (which missing `CITE_GOES_ON` entries cost a real binding vs. just a fragment), plus the cited spec and the sibling test file this branch claims to replace (A5).Let me read the base branch's splitter test file and the design spec to check A5 coverage claims and context.Now let me check for other call sites of `_sentence_spans`, `STATE_ABBREV`, and `CITE_GOES_ON` across the repo, and check whether the base test file's probes intersect this change.Let me check whether any other test pins behavior that intersects this change — drafts containing word-state abbreviations, and the complaint-mode test file.I have everything I need: the diff, both code files, the new and base test files, the fixture, the extractor patterns, the spec, and both permitted base-review files. `_sentence_spans` has a single call site (`units()`, line 905); `STATE_ABBREV` is used only at lines 233 and 409. Here is my report.

---

**VERDICT: APPROVE WITH CHANGES** — the mechanism is sound and correctly placed, and the commit is a strict improvement over the base for the 8 word-like states, but `CITE_GOES_ON` misses two systematic cite continuations (`C.` in "8 Del. C.", `Const.` in "Ill./Wash./Or./… Const.") — one of which reopens a same-section cross-jurisdiction bind in the class `fix/state-abbrev-cites` was built to kill — and the residual prose-starting allow-list entries go entirely unpinned. Required edits:

1. `matterkit/briefcheck.py:179` — add `C` to the dotted alternative: `(?:App|C|Ch|Ct|Cl|Dec|Dist|Fam|Hous|Prob|Sup|Super|Att'y|Regs|R|S)\.` (or the tighter `C\.\s*§`; see P2-1).
2. `matterkit/briefcheck.py:179` — add `Const` to the same group (see P2-2).
3. `tests/test_briefcheck_word_states.py` — pin the residual merges and the remaining over-splits as intended-or-known behavior (P2-3, P3-2).

Recommended (not blocking): tighten `Trial|Tax|Code|Laws|Acts` with a second-token lookahead (P2-3); fix the commit message's "56 subtests" claim to 72 if the branch is amended (P3-1).

## Findings

### P2-1 — `C\.` missing: `"8 Del. C. § 141"` splits after `"Del."` — WARN, Facts-mode `uncited-fact`, and a same-section cross-jurisdiction bind

**Where:** `matterkit/briefcheck.py:176-179` (the allow-list lacks `C`), `409` (the guard), exercised through `_citation_only` at `919-926` and the statute-short fallback at `1156-1165`.

**Failure scenarios:**

- Stored `N.Y. Bus. Corp. Law § 141` (source-checked), no Delaware row; draft `"Directors act under 8 Del. C. § 141."` At `74190c0`: the dedicated Delaware head (`citations.py:87`) extracts `"8 Del. C. § 141"` → `statute:8delc141` → `BLOCK authority-not-logged`, exit 1 — correct. At `c9804d7`: the unit cuts after `"Del."`; the `"C. § 141."` fragment yields only `"§ 141"` → `statute-short:c141` → the `endswith(_norm_key("141"))` fallback finds the NY row uniquely → binds to the wrong jurisdiction, `WARN short-form-without-full-cite`, exit 0 — the identical WARN-masked cross-state tie the base branch fixed for `"W. Va."`.
- Facts draft `"The company follows 8 Del. C. § 141."`: the head fragment `"The company follows 8 Del."` (5 words, no cite) → `UNCHECKED uncited-fact`, exit 2 — a correctly cited fact newly flagged, plus the WARN on the fragment.

`"Del. C."` is Delaware's principal statute form — the exact briefs this tool checks cite it constantly. Cheap fix above; `C\.\s*§` is the surgical variant if a bare `C.` initial-merging cost is unwanted (it is the same class as the already-accepted `R.`/`S.`).

### P2-2 — `Const\.` missing: all eight word-states' constitution cites split

**Where:** `matterkit/briefcheck.py:179`.

**Failure scenario:** Facts draft `"The right is in Ill. Const. art. I, § 2. Doc. 16 at 1."` → `["The right is in Ill.", "Const. art. I, § 2.", "Doc. 16 at 1."]`; the head fragment is ≥4 words and cite-free → `UNCHECKED uncited-fact`, exit 2. At `74190c0` the sentence stayed whole and passed as cited. Binding is unchanged (the extractor only ever read `"§ 2"` — pre-existing gap, so the differential shows no check-level delta in argument mode), but the Facts-mode flag is new and hits all eight: `Ill./Wash./Or./Ind./Mass./Del./Haw./Me. Const.` is the uniform state-constitution cite form.

### P2-3 — Prose-starting allow-list entries keep hiding the exact uncited fact this commit exists to catch

**Where:** `matterkit/briefcheck.py:177-179`.

**Failure scenarios (all exit 0, no findings, in a `STATEMENT OF FACTS` section — the differential confirms the first two):**

- `"On Sunday Plaintiff attended Mass. Trial began on Monday. Doc. 16 at 1."` — `Trial` (differential line 5-6). Most likely in Facts: trial narratives are the section's stock content.
- `"Plaintiff moved to Wash. 2 weeks later he left. Doc. 16 at 1."` — `[\d§]` (line 7-8). Any digit-initial sentence ("`2019`", "`3 officers`", "`5 days`") merges; unfixable without breaking `(Or. 2010)`/`100 Wash. 2d`.
- `"… attended Mass. Dec. 3, 2018, he left. Doc. 16."` — `Dec.` (for `Ill. Dec.`); the other eleven months correctly split — an inconsistency a drafter will never guess.
- `"… attended Mass. S. Smith testified. Doc. 16."` / `R. Jones` — `S.`/`R.` (differential lines 9-12); witness/party initials, including single-initial minor designations.
- `"… attended Mass. Code enforcement arrived. Doc. 16."` — `Code` (line 13-14); likewise `Laws`, `Acts`, `Tax` ("Tax records showed…").
- Lower: `App.` (appendix refs), `Ct|Cl|Ch|Dist|Fam|Hous|Prob|Sup|Super|Att'y|Regs` (rare as openers), and the middle alternative's residual (`"Rev. R. Smith"`).

All are strictly narrower than the base's unconditional merges — but `Trial`, digits and `Dec.` are common Facts-section openers. `Trial` and `Tax` can be tightened without losing their real uses: `Trial\s+(?:Ct\.|R\.)` still covers `"Ind. Trial R."` and `"(Mass. Trial Ct.)"`, `Tax\s+(?:Ct\.|\d)` covers `"(Or. Tax 2010)"`; `Code\s+(?:Ann\.|Regs?\.|tit\.|§|\d)`, `Laws\s+\d`, `Acts\s+\d` similarly keep `"Del. Code Ann."`, `"Or. Laws 2010"`, `"Mass. Acts 1969"` while releasing prose. Recommend the tightening plus pins; at minimum pin the merges as documented behavior.

### P3-1 — Commit message says "56 subtests red"; the committed test file produces 72

The handoff's red-step count at `74190c0` is 72 (64 splitter + 8 uncited-fact sub-tests), and `briefcheck.py` is byte-identical at `b32d490` and `74190c0`. Minor: amend on the next rebase.

### P3-2 — Many `CITE_GOES_ON` entries are unpinned; residual merges and over-splits are unpinned too

`Ann`, `Adm`, `Sess`, `Legis`, `Cl`, `Dist`, `Fam`, `Hous`, `Prob`, `Sup`, `Super`, `Att'y`, `Regs`, `S`, `Acts`, `Tax`, `Trial` appear in no test. The A1 negative side (a non-word state still merging: `"… in Richmond, Va. The clerk refused …"`), the P2-3 merges, and the remaining splits (`"Wash. State Dep't of Corr."`-class party names >4 non-signal words, `"Mass. L. Rptr."`, `"Ind. Stat."`, `"Or. Op. Att'y Gen."`, `"Or. Unif. Trial Ct. R."`) have no pins either — the differential documents them but the test file should too, whichever way the founder wants them read.

### P3-3 — `WORD_STATES` can never guard `Miss.`/`Ark.`; `_case_name` divergence is bounded

`word in ABBREV` is checked before `state`, so states whose lowercase form is in `ABBREV` (`miss`, `ark`, `ala`, `tex`, …) merge unconditionally — the guard's reachable universe is the 26 `STATE_ABBREV` entries only. Fine as scoped; worth a comment if guarding more states is ever revisited. On `_case_name` (A3): it sees only the unit text, so after the split it can no longer pull a word-state's preceding prose into a case name — for the 8 states this *reduces* the base review's P3-1 pollution. Divergence remains only in non-folding fragments (party names >4 words, where the display loses the `X v. State.` head); keys are never affected.

## Answers to points 1–6

**1. Hidden facts.** The guard works as designed — verified by the differential (`"attended Mass. The deputy"` now splits; the Facts draft went exit 0 → exit 2 with `uncited-fact`). What still merges, ranked by Facts-section plausibility: `Trial` (highest), `[\d§]` digit-initial sentences, `Dec.` (alone among months), `S.`/`R.` initials, `Code|Tax|Laws|Acts` prose, `App.`/court-abbrevs, and the tail-guarded `Rev|Gen|…` residual (low — the `(?:Stat|Code|Laws|Serv|Regs?|R)\b` tail makes `"Rev. Smith"`/`"Gen. Smith"` correctly split, which the tests pin). All are residual — narrower than base — but `Trial`/digits/`Dec.` deserve either the tightening above or explicit pins, since each silently passes an uncited fact.

**2. Over-splitting.** Missing continuations ranked by cost: `C.` (P2-1 — WARN + Facts flag + the same-section fallback bind; the only false-pass path I found, and it is WARN-visible, not fully silent), `Const.` (P2-2 — Facts flag; binding unchanged), then `Stat.` (`"Ind. Stat."`), `Op.` (`"Or. Op. Att'y Gen."`), `Unif.` (`"Or. Unif. Trial Ct. R."`), `Evid.`, `Post-Conviction`, `Terr.`, `L.`/`J.` journal families (`"Mass. L. Rptr."`, `"Del. J. Corp. L."`), and the unbounded party-name class (`"Wash. State Dep't of …"`). Costs: truncated case-name display only when the fragment exceeds the ≤4-word fold (`"Dep't of Social & Health Servs."`-length names — real); `uncited-fact` on the ≥4-word head in Facts sections; a statute-short WARN where the extractor had read the full head (`Del. C.` is the only missing entry whose head the extractor claims whole). No fully silent false pass besides P2-1's WARN-visible wrong bind.

**3. Mechanism.** The lookahead allow-list is a reasonable 80/20 and its placement is exactly right (single call site; `pos`/`endpos` math verified — `start + m.end()` is the lookahead character, and the boundary regex's own lookahead guarantees every `CITE_GOES_ON` alternative is reachable). Two better designs exist for the follow-up: (a) keep the sentence whole when an `extract_citations`/`scan_authorities` span *crosses* the boundary — fixes `Del. C.` for free, but loses `Rev. Stat. Ann. tit.` (extractor gap) unless OR-ed with the list; (b) fold §/statute fragments in `_citation_only` (it currently recognizes only case/rule/record/Id patterns, so `"C. § 141."` and `"Const. art. I, § 2."` never rejoin their head — fixing that removes the Facts-mode flag for every split class at once); and (c) a `" v. "`-earlier-plus-`CASE_RE`-ahead heuristic for party names, which is what guarding *all* states (A1) would need. As implemented, extending the guard to all 26 would over-split `"Smith v. Pa. Dep't of Corr."` and `"(Pa. 2010)"` is safe only because `(Pa.` strips to `Pa` — do not widen `WORD_STATES` under this mechanism.

**4. Consistency (A3).** Covered in P3-3 — the two can disagree only where the fragment doesn't fold, and only cosmetically. `_case_name` keys come from `vol rep page`; display truncation is bounded.

**5. Tests (A1–A5).** The file pins the core behavior well: the 8×8 follower grid is genuinely discriminating (it proves `Rev./Gen.` can't over-keep via the tail requirement), the lowercase control pins A4, the cited-fact fold test pins the `Doc.`-follower path, and the check-level `STATE_CITES` tests pin end-to-end binding and blocking. Gaps: no pin for A1's residual (non-word merge), none for P2-3's merges, none for the remaining over-splits, and `Del.`/`Ill.`/`Mich.` statute cites are absent from `STATE_CITES` (they're only splitter-tested). A5's claim to replace `fix/brief-va-abbrev` is plausible — `STATE_CITES` exercises 23 state forms logged and unlogged — but I could not diff that branch's tests directly (worktree off-limits), so treat "can be dropped" as probable, not verified.

**6. Gaps.** Comments and the module docstring are mostly accurate; the docstring's "only before a cite's next part" slightly overclaims (digits/`Trial`/`Dec.` admit non-cite prose) — worth one clause naming the residual. The "56 subtests" claim is wrong (P3-1). As a base: sound — this range does not touch `citations.py`, so it neither helps nor worsens the open P2-1 (`\s?` joins) or the `kit-cite-0.5` collision; both are orthogonal.

**A1–A5:** A1 correct under this mechanism (see point 3 — widening needs the smarter continuation test, not a bigger list). A2 well-designed with two systematic misses (P2-1, P2-2). A3 acceptable. A4 verified — lowercase forms of all 8 are absent from `ABBREV`, so the guard can't be subverted (but it would silently no-op for `Miss`/`Ark`). A5 probable, partially unverifiable.

## Readiness

Ready to land on `feat/matter-capture` with `fix/state-abbrev-cites` **after the `C.`/`Const.` additions and the pins** — or now if ty accepts the Del. C. residual as knowingly traded (I'd take the one-token fix; it is cheaper than the review that found it). Everything else is follow-up. Merge notes: `fix/brief-va-abbrev` can drop per A5 (which also dissolves its add/add conflict on `test_briefcheck_state_abbrev.py`); `fix/brief-dotted-section` still needs the `kit-cite-0.6` renumber from the base review's plan plus the mechanical `briefcheck.py` conflict resolution; the reported clean `merge-tree` with `feat/matter-capture@e77da0c` is consistent with this diff touching only two small regions.

## Model

SWE-2 Max, as my runtime reports it.
