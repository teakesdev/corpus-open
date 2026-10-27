I'll start by reading the handoff document.I'll review this commit. Let me start by reading the diff and the key source files.Now let me read the rest of `briefcheck.py`, focusing on `_authority_index`, `resolve_authority`, and related sections.Now let me look at the supporting test files and `citations.py` for `_SEC_NUM`.The diff was truncated. Let me read the remaining lines of review.diff.Now let me grep for all `_norm_key` callers, `PARSER_ID`, and check `citations.py` for `_SEC_NUM`.Let me look at the relevant parts of `citations.py` to understand section extraction and the trailing-period handling.Let me check for other consumers of `authority_keys`/`_norm_key` across the package and tests, and look at the Base test helper.Let me verify the `authorities` table schema and check the `Matter` helper's `__init__` for completeness.I have everything I need. I've traced the key path end-to-end: `_norm_key` (:169), its callers in `scan_authorities`, `authority_keys`, `_authority_index`, the short-form fallback in `resolve_authority` (:1150-1162), plus `authority.py`'s `find` and `import_ledger`, and all three touched test files. Here is the review.

**Verdict: REQUEST CHANGES** — the `_norm_key` split itself is correct, but the same commit makes the short-form statute fallback at `briefcheck.py:1158` bind a wrong section where the base BLOCKed. Land only with a boundary guard, or strictly after `fix/brief-short-section`.

# SWE-2 review: `fix/brief-dotted-keys` @ `c1d83e1` (46a967f..c1d83e1)

## Findings

### P1 — `briefcheck.py:1158`: the section fallback now binds a suffix-extension section; a base BLOCK becomes exit 0

```python
cands = {k for k in self.auth_index if k.startswith("statute:") and sec and k.endswith(_norm_key(sec))}
```

The `endswith` test has no left boundary, so a logged statute whose normalized key *ends with* the sought section as a string counts as a candidate — including sections that merely share the suffix (`…15.1` for a `5.1` seek, `…151` for `51`). At the base this flaw was masked when a *second* logged row collided on the dot-stripped key, producing two candidates → no bind → `statute-short:…` → `authority-not-logged` BLOCK. At the tip the undotted row drops out of the candidate set, leaving exactly one candidate — the wrong one.

Confirmed by hand-trace (the handoff's evidence bullet is accurate):

- **Scenario A.** Store "Tex. Bus. Orgs. Code § 15.1" and "Tex. Gov't Code § 51" (source-checked); draft `"The name must be distinct under Section 5.1."` → `statute-short:section5.1`, `sec="5.1"`, `_norm_key(sec)="5.1"`. Base: both `…code151` and `…code51` end with `51` → 2 candidates → BLOCK, exit 1. Tip: only `statute:texbusorgscode15.1` ends with `5.1` → binds § 15.1 → only `short-form-without-full-cite` WARN → **exit 0, judged by the wrong section**.
- **Scenario B (symmetric, undotted seek).** Store "Tex. Gov't Code § 5.1" and "Tex. Bus. Orgs. Code § 151"; draft `"… under Section 51."` → `_norm_key("51")="51"`; tip: `…code5.1` no longer ends with `51`, `…code151` does → binds § 151 → exit 0. Base: two candidates → BLOCK.

Same mechanism is reachable via coordinated members (each `Section N` member is a `statute-short:` cite) and `Id.` (inherits the misbound target at :1047-1056). Also note the pre-existing half of this: a single logged `§ 15.1` binds "Section 5.1" at *both* base and tip — the guard below fixes that too.

**Fix (preferred — makes the commit correct in any landing order):** require the matched suffix to start a number, i.e. the char before it must not be a digit or hyphen (`.` must stay allowed so `Section 10b-5` still binds `statute:…240.10b-5` as it does at base):

```python
cands = {k for k in self.auth_index if k.startswith("statute:") and sec
         and re.search(r"(?<![\d-])" + re.escape(_norm_key(sec)) + r"$", k)}
```

This rejects `…15.1`/`…151`/`…13-1-4.1`-style suffix matches while keeping every legitimate `…code5.1` bind. Residual known limitation: `…3.5.1` still suffix-matches a `5.1` seek (dot boundary) — pre-existing ambiguity, properly owned by design B in `fix/brief-short-section`.

**Alternative:** land strictly after `fix/brief-short-section` (design B removes this path). The merge evidence supports that ordering, but the stack is mid-rebase; the one-line guard is cheap insurance and also repairs the pre-existing single-row wrong bind.

### P2 — `tests/test_briefcheck_dotted_keys.py`: the fallback path is never exercised

No draft in the new file contains a short-form statute cite (`§ N` or `Section N`); every statute test uses a full cite, and the rule tests hit the `full_rules` path (raw-number keyed, unaffected). The one code path this commit can regress went untested, which is why P1 slipped through. With the guard, add:

- Store `§ 15.1` + `§ 51`; draft `Section 5.1` → assert `authority-not-logged` BLOCK, exit 1.
- Store `§ 5.1` + `§ 151`; draft `Section 51` → same.
- Optionally pin intent for single-row suffixes: `§ 15.1` alone vs `Section 5.1` (must not bind `§15.1`).

### P3 — `_norm_key` splits on spacing typos (`briefcheck.py:169-172`)

Neighbors are judged on the raw string, so `"§ 5. 1"`/`"§ 5 .1"`/`"5..1"` key as `"51"` — a new false BLOCK against a logged `§ 5.1` (base merged them) while still binding a logged `§ 51` (pre-existing wrong-authority pass). Edge-casey; acceptable as-is, but worth a one-line docstring note or a pin test.

### P3 — `TOOL_ID` unchanged (`briefcheck.py:48`)

Defensible per C2: keys are recomputed from stored citation text each run, extraction is unchanged, the report prints displays. Exit-code semantics did change — if the series treats the verdict contract as part of the tool id, a `0.2` bump would document it. Consistent with the series either way.

## Answers to the handoff's points

**1. The wrong-section bind — confirmed (P1).** The split-never-merge argument is sound: `_norm_key_tip(s)` with its `.`s deleted is exactly `_norm_key_base(s)`, so every tip candidate `k.endswith(_norm_key(sec))` was a base candidate — the set can only shrink. Shrinking to 0 gives the intended new BLOCKs (logged `§ 51`, draft `Section 5.1`); shrinking to exactly 1 gives the regression above when the survivor is a suffix-extension. Ordering after `fix/brief-short-section` eliminates it (the fallback is replaced), but the commit is not safe standalone. Other paths checked: full cites (key split only — tip match ⇒ base match, so no new pass), `Id.` (only via the same misbound target), rule lists/`RULE_TAIL` (key split only), bare rules (`full_rules` keyed by raw number — unaffected), local rules (`extra=None`, direct key path), `§§`/coordinated members (same P1 root), `authority-duplicate` (dup set ⊆ base; fewer spurious WARNs, and the right row now judges — an improvement: at base a collided `§ 51` row could win ranking and pass a `§ 5.1` cite).

**2. The regex (C1) — sound for the intended rule.** A dot survives iff digit-flanked in the raw string. Verified: `U.S.C.`/`Fed. R. Civ. P.`/`L.U.Civ.R.` letter dots drop; sentence-final and `5.1.` trailing dots drop; `5.1.2` keeps both; `Fed.R.Civ.P.5.1` and `P.5` normalize identically to spaced forms; `29 CFR 1630.2` and `29 C.F.R. § 1630.2` produce identical `statute:` and `full:` keys (`29cfr1630.2`); `5.1(b)`, years and parentheticals unaffected. No realistic reporter/docket decimal reaches `full:` inconsistently — a `full:` split needs the two spellings to differ somewhere anyway. The only new split is the spacing-typo family (P3). Corpus spot-check examples (`12cfr1026.1`, `240.10b-5`, `3.005`) are exactly what the regex produces.

**3. Callers — consistent.** `_norm_key` reaches only `statute:`/`statute-short:`/`rule:`/`full:` keys and the :1158 suffix test; `_rep_key` (case keys) is separate; `full_rules`/`full_statutes`/`full_cases` are keyed by raw numbers. `find` and `import_ledger` use set intersection only — ambiguity can strictly decrease (e.g. `find("§ 5.1")` now resolves between logged `§ 5.1`/`§ 51` instead of raising), never increase; no suffix path there, so no wrong-bind surface. `import_ledger`'s `seen` dedup likewise only un-merges.

**4. Tests — D5 covered, P1 missing.** Required coverage present: 5.1 vs 51 (statute/rule/find/ledger), 5.1 vs 5.10 both directions, hyphenated `21-23-8` unchanged, Ann./Annotated/plain spellings, controls labeled. All 33 negatives are discriminating (each asserts the specific unlogged cite + exit 1). The 12 changed expected strings in the two existing files are exactly the forced updates — 8 strings in 7 tests in `test_briefcheck_dotted_section.py`, 4 in 3 tests in `test_briefcheck_false_blocks.py`; no BLOCK/binding/exit-code assertion weakened (verified hunk by hunk). Missing: the fallback bind cases under P1/P2, and optionally a `_norm_key("5. 1")` pin.

**5. C2 and C3 — agree.** TOOL_ID may stay (see P3 for the caveat). The Ann. spellings (`Fla. Stat. Ann.`, `Miss. Code Ann.`/`Annotated`) are a fair test of D5; the multi-word "Tex. Gov't Code Ann." extractor limit is documented, pre-existing, and not made worse by this commit — `_ANNOTATED.sub` still strips the head's "Ann." whenever the fragment reaches `_statute_base` whole.

**6. Gaps.** `_norm_key`'s docstring is accurate; the test file's failure-mode map matches its classes. I cannot see the commit message (diff contains none) — unverifiable, no flag. The real gap is the missing short-form coverage (P2) and the missing boundary in the suffix test (P1).

## Readiness

Not ready to land as the tip of the stack on `46a967f`. With the P1 guard + the P2 regression tests, it is correct standalone and order-independent. Without them it is correct only if it lands after `fix/brief-short-section` (per the founder's landing path A and the clean `merge-tree` evidence). Order relative to `fix/brief-dotted-section` is free — the merge is clean modulo one expected-key string.

## Model

SWE-2 Max (as reported by my runtime).
