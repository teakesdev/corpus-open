I'll read the handoff document first.I'll now read the diff and the code under review.I have the diff. Now let me read the rest of `briefcheck.py`, focusing on the index builder and `resolve_authority`.Now let me read the rest of the file, the test file, and the test fixture it builds on.Now let me examine the test fixture (`Base`, `codes`, the `Matter` helper) in `test_briefcheck.py`.Let me check `citations.py` to understand `extract_citations` and what `raw_fragment` looks like.Let me check a few remaining details: other callers of the fallback path, the spec referenced in the handoff, and whether other tests exercise this code.Let me check the related test files for overlapping coverage, and skim the spec.Let me check the spec for the intended semantics of this fallback.I have everything I need. I traced the fallback end-to-end: `_statute_base` (briefcheck.py:185-196) strips trailing parens and keys the citation; `_authority_index` (:880-886) maps every `authority_keys` key to full rows; `_statute_keys_for_section` (:888-896) then requires the key's normalized tail plus a whole-token regex hit anywhere in the raw `r["citation"]`. I verified the regex semantics and the extractor's fragment shapes in `citations.py`, and checked the fixture and neighboring tests. Here is the report.

---

**Verdict: REQUEST CHANGES** — the fix correctly closes the tail/dot false binds it targets and keeps every required bind, but the whole-token test still searches the entire stored citation string rather than the section that produced the key, so a short form can still bind to a different logged statute on realistic forms (P1).

## Findings

### P1 — the whole-token test is scoped to the row's citation text, not the key's section
`matterkit/briefcheck.py:893–896`

`whole` is searched against the full `r["citation"]`, while the `statute:` key was computed by `_statute_base` from a single extracted fragment *with trailing parentheticals removed* (`_statute_base`, :188). Any number anywhere else in the stored string — a subsection parenthetical, an edition year, the title numeral, or a second cite in the same row — counts as a whole-token `sec` whenever the key happens to end with `_norm_key(sec)`. `(` is not in `[\w.\-]` and `)` is not in `[\w\-]` nor `.\w`, so parenthesized digits always pass the boundary test.

Failure scenarios (each binds → only `short-form-without-full-cite` WARN → exit 0, a false pass — exactly the class this branch exists to kill, and a violation of D1 "match only a whole section number"):

- Logged `5 U.S.C. § 552(b)(2)` → `_statute_base` strips `(b)(2)` → key `statute:5usc552`, which ends with `"2"`. Draft `"Under § 2, the board may act."` → token `2` found inside `(2)` → binds to `statute:5usc552`. `§ 2` is a different statute than § 552. Subsection cites like `(a)(1)`, `(b)(2)` are ubiquitous in real logged authorities; any section number whose digits end with a parenthetical's ordinal is a landmine. Also `42 U.S.C. § 2000e-2(a)(2)` + `§ 2` → binds to `statute:42usc2000e-2`.
- Logged `8 U.S.C. § 1108` → key ends with `"8"`; the title numeral `8` at position 0 of the citation is a whole token (lookbehind vacuously passes at string start, followed by a space) → draft `§ 8` binds to `statute:8usc1108`. Same for `5 U.S.C. § 555` + `§ 5`, `15 U.S.C. § 1115` + `§ 15` — real sections in those titles exist.
- Logged `Miss. Code Ann. § 41-1983 (1983)` → key `statute:misscode41-1983` ends with `"1983"`; `1983` inside `(1983)` is a whole token → draft `§ 1983` binds to a statute whose section is `41-1983`. This is the C5 "known limit" — but contrary to the handoff's "I found no realistic logged form that does this," an edition-year parenthetical is a documented logged form (the probe table itself uses `42 U.S.C. § 1983 (2018)`).
- Same mechanism via a second cite in one stored string: `42 U.S.C. § 11983; see also 5 U.S.C. § 1983` — the `1983` token in the second cite makes `statute:42usc11983` a false candidate (in this exact example it creates a second candidate → ambiguity BLOCK, unchanged from base; without the second cite's own key it would be a false bind).

Fix direction (any equivalent is fine): tie the token to the span that produced the key. Cleanest: for each row, re-run `self._scan_authorities(r["citation"])` and consider only `statute:` fragments whose key ends with `_norm_key(sec)`; for each such fragment, strip trailing parens as `_statute_base` does and compare the fragment's *trailing* section token to `sec` (exact, case-insensitive — preserves the `552a`/`552A` fold and the `1.01`≠`101` distinction). The producing fragment's section is always at its end after paren-stripping under the current extractor patterns, and this covers the no-`§` form (`42 U.S.C. 1983` → trailing token `1983`), so C1's regression stays fixed. It also removes the title numeral (never trailing) and the sibling-fragment tokens (P3 below). Applying the current regex to a paren-stripped, fragment-scoped string is an acceptable minimal variant, but the trailing-token compare is simpler and immune to further boundary edge cases.

### P2 — tests don't cover the token-scope hole
`tests/test_briefcheck_short_section.py`

The suite pins D1–D3 well for the key-tail class, but nothing exercises a token that is not the key's section. Required additions (all BLOCK `authority-not-logged`): logged `5 U.S.C. § 552(b)(2)` + draft `§ 2`; logged `8 U.S.C. § 1108` + `§ 8`; logged `Miss. Code Ann. § 41-1983 (1983)` + `§ 1983`. Also worth pinning while here (each currently relies on probe evidence, not tests): logged `5 U.S.C. § 552a` + `§ 2a` must not bind and `§ 552A` must bind (lettered sections, case fold); `§ 101` must not bind to logged `§ 1.01` (mirror of `test_section_that_differs_only_by_a_dot`); binds for logged `42 U.S.C. § 1983 et seq.` and `(2018)`.

### P3 — shared-row ambiguity across keys
`briefcheck.py:894–896`

A stored string that keys two statutes puts one row under both keys; `cands` counts keys, not rows. Logged `42 U.S.C. §§ 11983, 1983` (once `fix/brief-statute-lists` lands; or `42 U.S.C. § 11983; 5 U.S.C. § 1983` today) → draft `§ 1983` gets two candidates → unbound → BLOCK, even though § 1983 is genuinely logged. Not a regression — base produced the same two candidates — but the fragment-scoped token test in P1 resolves it naturally (the `11983` fragment has no `1983` token). Flagging so the merge order is aware.

### P3 — docstring understates the lookahead
`briefcheck.py:891`

"no … `".<digit>"` after it" — the code rejects `.\w` (dot + any word char, including letters and `_`), not only `.<digit>`. Minor.

### P3 — bisect hazard
`028d412` is a red commit (`test_binds_to_logged_cite_written_without_section_sign` fails there, per the handoff). If the branch lands unsquashed, bisection can land on a false-BLOCK state. Recommend squash-landing the two commits; otherwise fine.

### P3 — nits
`briefcheck.py:895`: `_norm_key(sec)` is recomputed per index key — hoist (also relevant if P1's rescan is adopted). Commit messages could not be verified — the constraints forbid git — but the final tree matches the described intent.

## Answers to the handoff's points

**1. False passes.** Yes — P1. The key-suffix test is still needed: without it, any token hit anywhere makes *every* `statute:` key on that row a candidate (e.g., a row logging `5 U.S.C. § 552 note (discussing 42 U.S.C. § 1983)` would make `statute:5usc552` a candidate for `§ 1983`). But the conjunction is necessary, not sufficient: suffix ties the key tail to `sec`, the token proves `sec` exists *somewhere* in the same string — neither proves the token is the section. I verified the intended negatives all hold: `§ 983`/`§ 3-8`/`§ 23-8`/`§ 1.01`/`§ 2`/`§ 552`/`§ 10b-5`/`§ 2a`/`§ 2.101` correctly fail the boundary or literal-dot test; `§ 21–23–8` (en dash) is a pre-existing extractor truncation, unchanged. Lettered sections behave (`552a` binds, `2a` does not). CFR-style tails (`240.10b-5` vs `10b-5`) are correctly rejected by the dot-lookbehind.

**2. False BLOCKs.** None for genuinely whole sections. The token test only *removes* candidates (conjunction), so `cands(809f305) ⊆ cands(267ab37)`; the only binds lost are the intended partial/dot-diff ones, and every probe row that bound before still binds (`(2018)`, `et seq.`, trailing period, `§1983`, no-`§` `42 U.S.C. 1983`, `§§`-headed single cites). Multi-candidate BLOCKs are identical to base. A section inside a subsection paren still binds when it's the real section (`§ 1983(a)` → `statute:42usc1983`) because `(` is a valid left boundary — the same boundary that enables the P1 hole, which is why scoping the *text* (not the boundary) is the right fix.

**3. Merge safety.** The `auth_index` seam composes with `fix/brief-statute-lists`: list-member keys produced inside `authority_keys` land in `auth_index`, which is exactly what `028d412`'s `scan_authorities`-map approach could not see — C1's reasoning is sound and the final approach answers it. Nothing of `028d412` remains in `809f305`'s final code. The residual is the P3 shared-row ambiguity (pre-existing) and the red intermediate commit.

**4. Tests.** `assert_unbound` is properly discriminating (`assertNotEqual` + `unlogged == [raw]` + BLOCK + WARN + exit 1 catches a bind to any logged wrong key); `assert_bound` likewise; the no-`§` control is exactly what catches C1's regression; `TestSharedSectionStaysUnbound` pins C4. Missing: the P1 negatives and the unpinned probe behaviors listed under P2.

**5. Gaps.** Docstring nit (P3); C6's per-call compile is fine for the store sizes here — if the P1 rescan is adopted, build the key→fragment map once in `_authority_index` instead. Adjacent pre-existing quirk, unchanged by this range but worth a note: `"Section 552.101"` parses as § 552 (the Section-form regex has no dots), which the fallback then resolves as § 552 — a different range's problem.

**C1–C6.** C1: correct call — `auth_index` is the right composition seam; squash to remove the red commit. C2: sound intent, under-scoped token (P1). C3: the boundary regex itself is well-designed (dot-lookahead vs. closing period, `re.I` preserving the `_norm_key` fold); the flaw is *where* it searches. C4: unchanged, correct. C5: disproven — realistic forms exist in three flavors (subsection parens, edition years, title numerals). C6: acceptable as stated.

## Readiness

Not ready to land as-is — P1 leaves a realistic false-pass path in the tool whose job is catching exactly that, and it is the same failure class the founder asked to eliminate (D1). After the token-scope fix and the P2 tests, it is ready to land after `fix/brief-false-blocks` on `feat/matter-capture`; land it squashed so `028d412`'s red state can't surface. Composition with `fix/brief-statute-lists` is sound in structure (P3 aside).

## Model

SWE-2 Max, as reported by my runtime.
