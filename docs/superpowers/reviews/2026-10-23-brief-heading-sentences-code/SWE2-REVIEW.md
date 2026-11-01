I'll start by reading the handoff file.I'll review this branch. Let me read the diff and the code under review.Now let me read the code under review in `briefcheck.py` — the new classifier, `_heading()`, and supporting functions.Now let me read the classifier and `units()` sections.Now the `_Checker` class — `units()`, `unit()`, `run()`, `quotes()`, `facts()`.Now let me look at the test fixtures and the complaint-mode tests referenced in the handoff.Now let me check the spec, and look for other callers of `_heading` or anything else that could be affected.Let me check `citations.extract_citations` (called inside `_titled` via `scan_authorities`) and look at the review folder contents.Let me verify the on-disk test file matches the diff and check for any other briefcheck consumers.**Verdict: APPROVE WITH CHANGES** — the approach, tests and unit integration are sound, but two classifier regressions need small edits before this lands on `feat/matter-capture`: (1) `_titled`'s first-word dot check rejects one-word dotted titles like `"A. Background."`, which silently stops a fact section from opening; (2) `TITLE_MINOR`'s `"v."`/`"vs."` entries are dead code (the `rstrip(".?!:")` removes the dots first), so Title-Case case-name point headings become body text. Exact edits listed at the end.

## Findings

### P1-1 (top finding) — `"A. Background."` becomes body text; the fact section never opens, and every following factual sentence escapes `uncited-fact` — a silent regression vs the base
`matterkit/briefcheck.py:372` — `if first.endswith(".") and first not in HONORIFICS: return False`.

`_heading("A. Background.")`: `rest = "Background."`, `first = "Background."` ends in `.` and isn't an honorific → `_titled` returns False → `None` → body text. Because `_heading` returns `None`, `FACT_HEADING.search(block)` is never reached (briefcheck.py:907), so `fact_level` stays `None`.

Scenario — input:

```
A. Background.

The arrest happened at home on a holiday.
The clerk refused the request.
```

At `6ac2b01`: `"A. Background."` matched the marker regex → level-2 heading → `FACT_HEADING` matches "background" → fact section opens → both sentences get `UNCHECKED uncited-fact` → exit 2. At `e50de74`: the line is body text (2 tokens, under the 4-word floor, cite-free → no row) → the section never opens → **exit 0, no findings, no rows**. One orthographic variant of a fact heading turns the whole section's coverage off, silently — the same guarantee the base got right and this branch exists to strengthen. Same mechanism kills `"B. Facts."`, `"C. Allegations."` (any FACT_HEADING keyword), and any lettered Title heading whose first word is a non-honorific dotted abbreviation (`"A. U.S. Policy"`, `"B. St. Louis Records"`, `"A. Dept. Of Justice Records"` — C3 names the `U.S.` case but not the fact-opener consequence).

Supporting detail: the author's own test pins `"D. Statement of the Case."` (trailing period) as a level-2 heading — period-terminated titles are intended — yet `"D. Conclusion."` fails only because its first word happens to carry the dot. The check should fire on *initial/abbreviation-like* dotted first words, not on a lone dotted title word.

Also folded in here (design-inherent, document rather than fix): a fact heading that carries a cite — `"A. Background Doc. 16"`, `"I. STATEMENT OF FACTS [Doc. 16]"` (not all-caps, since `Doc` has lowercase) — now fails `_titled` via the cite check → same silent section-kill. That's the accepted C2 trade-off operating one level up; it deserves a line in the docstring or handoff notes because the failure is unbounded and silent.

### P2-1 — `TITLE_MINOR`'s `"v."`/`"vs."` entries are dead; Title-Case case-name point headings become body text → `uncited-fact` noise and lost structure inside fact sections
`matterkit/briefcheck.py:364–365` and `378–380`. Every candidate word is `rstrip(".?!:")`-ed before `w.lower() not in TITLE_MINOR`, so `"v."` arrives as `"v"`, which is never in `{"v.", "vs."}`. `"v"` is then treated as a major word and must be uppercase — it never is — so any lettered Title-Case heading containing ` v. ` that isn't saved by the ≤8-word short rule becomes body text.

Scenario — inside a fact section:

```
A. Background

The arrest happened at home. Doc. 16.

B. Smith v. Jones Controls The Outcome.

The motion should be denied for many reasons.
```

`"B. Smith v. Jones Controls The Outcome."` → 7 words but ends `.` → not short → Title check fails at `"v"` → body text → cite-free, ≥4 words → `UNCHECKED uncited-fact` on the heading itself; it also fails to close the level-2 fact section → the argument sentence draws a second `uncited-fact` → exit 2 on a correct document. The author clearly *intended* `"v."` to be a minor word (it's in the set); the intent is defeated by the strip order. Case-name point headings ending in a period are a standard briefing style.

### P3-1 — a cited heading becomes `last_target` and can hijack a following `Id.` into a blocking finding
`matterkit/briefcheck.py:912` + `1072–1074`. Example:

```
The court has inherent power. Dietz v. Bouldin, 579 U.S. 40, 45 (2016).

## Procedural History Doc. 16

The record was silent. Id. at 45.
```

`Id. at 45` binds to the heading's `Doc. 16` → `check_pin` → Doc 16 has 3 pages → `BLOCK pin-out-of-range`. At the base the same `Id.` bound to Dietz (pin 45 inside the recorded 45–47) → exit 0. Binding to the nearest cite is defensible — arguably more correct — but it is a behavior change worth documenting; writers who mean "the case above the heading" now get BLOCK.

### P3-2 — a cite-less quotation in a heading now draws `quote-uncited` (exit 2)
`matterkit/briefcheck.py:1186–1200`. `## The "File Is Closed" Call` alone above a cited paragraph → `UNCHECKED quote-uncited` → exit 2; at the base it was skipped → exit 0. This is per-spec (D3 part 1 checks heading quotations "like any sentence"), but section titles that quote a phrase without a cite are common — expect real noise; consider noting it in the report/docs.

### P3-3 — `first` is checked unstripped while `words` are stripped
`matterkit/briefcheck.py:371,379`. `first[0].isupper()` sees the raw token, so a >8-word lettered title that opens with a quoted or parenthesized word — `"B. (Optional) The Standard For Review Of Denial Motions"` — becomes body text even though the stripped `words[0]` would pass `isupper`. Narrow; inconsistent with the line-378 normalization.

### P3-4 — `HONORIFICS` and `TITLE_MINOR` vocab gaps
`matterkit/briefcheck.py:366, 364`. `HONORIFICS` lacks `Hon.`, `St.`, `Prof.`, `Rev.`, `Sgt.`, `Lt.`, `Capt.`, `Gen.`, `Sen.`, `Rep.`, `Gov.`, `Pres.`, `Det.`, `Esq.` — `"A. Hon. Lee's Evidentiary Rulings"` → body text → `uncited-fact` inside a fact section. `TITLE_MINOR` lacks legal-title minors: `re` (In re), `ex`, `rel`, `et`, `al`, `de`, `la`, `per`, `sub`, `nom`, `en`, `banc` — `"B. In re Smith"` (correct lowercase `re`) → body.

### P3-5 — cosmetic: comment and test-name drift
- `briefcheck.py:911` comment says cites and quotations are checked; tags are too (facts()) — one word short of complete.
- `tests/test_briefcheck_complaint_mode.py:109` `test_roman_numeral_heading_with_period_is_still_a_heading` now passes via the body-text path (the "I." line carries a `Rule 15(a)(2)` cite → `_titled` False). The test still guards "a one-letter line with a logged cite produces no `authority-not-logged`" — fine to keep, but the name now asserts a falsehood; rename or comment it.

## Answers to the handoff's points 1–6

**1. False passes.** The change strictly narrows heading classification (only single-letter markers got harder to be headings), so most residual escapes are inherited or accepted trade-offs: `"J. Smith testified"` (≤8 words, no terminal `.?!:;,` — or a terminal period hidden by trailing non-word chars like `[16]`) is still a heading (C4, explicitly chosen); Title-Case'd sentences after a letter; `C.`/`L.` Title lines closing fact sections at level 1; the lenient multi-letter Roman rule (`"II. <any ≤25-word sentence>"` stays a level-1 heading, escapes `uncited-fact`, closes sections — unchanged, defensible since such lines are almost never real sentences); the unchanged all-caps rule. The one *new* false pass — and the one worse than the bug fixed in its corner — is P1-1: a misread fact heading disables `uncited-fact` for an entire section, silently.

**2. False blocks / new noise from part (1).** Real: case-name headings (P2-1), abbrev-led headings (P3-4/P1-1's non-fact variants draw `uncited-fact` when ≥4 words inside a fact section), the `Id.` hijack (P3-1), cite-less heading quotations (P3-2). Non-issues verified: a cite-free heading never updates `last_target`/`last_unit_targets` (the `if cites:` gate), so `Id.` after a plain heading still binds to the earlier body cite; `id-ambiguous` still warns on multi-target units; heading cites feed `uses`, coverage rows and `full_cases`/`full_statutes` identically to body cites — which is the point. `fact_section=False` is the only thing that *should* differ for a heading unit — that gate is correct; the open question is whether a heading should participate in `last_target` at all (P3-1), and that's a judgment call, not a defect. Side benefit: heading units skip `_sentence_spans`, so `"W. Va. Code § 21-23-7"` inside a heading extracts whole instead of splitting at `"Va."` — heading cites actually get *better* extraction than body cites today.

**3. Classifier details.** C2's citation test is adequate: `scan_authorities`' `bare_rule`/`local_rule` parameters only affect emitted *keys*, never detection (BARE_RULE_RE and LOCAL_WORDS_RE match regardless), so the module defaults change nothing observable in `_titled` — correct choice. C3 is too broad (P1-1, P3-4). C5: hyphens/apostrophes/`§`-led words/numbers are handled acceptably (non-alpha first chars are skipped; `§` never reaches the check), the stripped-vs-unstripped `first` asymmetry is P3-3. `TITLE_MINOR`/`HONORIFICS` gaps per P2-1/P3-4.

**4. Fact-section structure.** Traced `fact_level` through the new paths: a lettered *sentence* is now body text — under `"I. FACTS"` it never closed anyway (level 2 > 1); under `"A. Background"` (level-2 fact heading) it previously *did* close the section and now doesn't — that's the C7 trade-off, and it runs in the safe direction (more sentences flagged, not fewer). A Roman-letter sentence (`"I. The Court may…"`) likewise no longer closes. Nothing in the range makes a *non-heading* open or close a section, so the only way to silently lose coverage is the misread fact opener — P1-1. The C7 trade-offs are all acceptable except that one, which is undisclosed and worse than the fixed bug in its corner.

**5. Tests.** The 15 tests pin D1–D3 and most of C1–C6, and the negatives discriminate: `test_initial_sentence_does_not_close_the_fact_section` fails if `"J. Smith…"` is misread (a level-2 heading would close `"A. Background"`'s section), and `test_lettered_title_heading_still_opens_a_fact_section` fails if `"B. Argument"` loses heading status (the argument sentence would then draw `uncited-fact`). C9's loosened assertion is acceptable — it still proves the cite is *checked*, which is what regressed; the `"Va."` split belongs to `fix/state-abbrev-cites`. C8 unedited is defensible as a guard; rename it (P3-5). Missing pins, in importance order: `_heading("A. Background.")` (P1-1), a ` v. ` Title heading >8 words or period-ended (P2-1), a fact heading carrying a cite (documents the C2 residual), a record-cited heading followed by `Id.` (documents P3-1), a cite-less quoted heading (documents P3-2), and `"J. Smith testified"` without a period (documents the accepted C4 corner).

**6. Gaps.** Comments and docstrings are accurate modulo P3-5; the spec (`docs/superpowers/specs/2026-10-23-brief-check-and-subcases-design.md`) says nothing about heading classification, so nothing is contradicted. The commit message isn't in `review.diff` and I can't run git in this lane; the handoff's account of it is consistent with the code. The merge-conflict note for `fix/brief-false-blocks`/`fix/brief-short-section` is plausible — both touch adjacent `briefcheck.py` hunks — and is a landing-order question, not a defect in this range.

## Views on C1–C9

- **C1** (one-letter markers only): sound. `"II. <sentence>"` is vanishingly rare while sentence-form Roman point headings are common; part (1) now checks their cites anyway. Justified.
- **C2** (citation definition): right for classification; prefixes don't matter to detection (above). A case name without a reporter correctly doesn't count — `"A. Smith v. Jones controls."` already fails via the `v`/lowercase paths.
- **C3** (abbrev opener): too broad — see P1-1/P3-4. The fix preserves its intent.
- **C4** (short wins): accepted trade-off; note it also swallows `"J. Smith testified. [16]"`-shaped lines where trailing brackets hide the terminal period.
- **C5** (Title Case): mechanism is right; `TITLE_MINOR` is broken for `v.`/`vs.` (P2-1) and thin for legal minors (P3-4).
- **C6** (heading units): correct except the `last_target` participation deserves a documented decision (P3-1).
- **C7** (known limits): accurate and honestly stated, but incomplete — it misses the fact-opener regression (P1-1) and the `uncited-fact`-on-real-heading noise (P2-1).
- **C8**: keep the test, fix the name.
- **C9**: acceptable; tighten to an exact cite once `fix/state-abbrev-cites` lands.

## Exact edits required before landing

1. `briefcheck.py:372` — scope the initial/abbreviation rejection to multi-word rests so one-word dotted titles survive:
   `if len(rest.split()) > 1 and first.endswith(".") and first not in HONORIFICS:` and add pins `_heading("A. Background.") == 2` plus an end-to-end facts-section test.
2. `briefcheck.py:365` — make the dead entries reachable: replace `"v.", "vs."` with `"v", "vs"` in `TITLE_MINOR` (and consider adding `re`, `ex`, `rel`, `et`, `al`, `de`, `la`, `per`, `sub`, `nom`, `en`, `banc`); pin `"B. Smith v. Jones Controls The Outcome."` as a level-2 heading.
3. Non-blocking but recommended: widen `HONORIFICS` (`Hon.`, `St.`, `Prof.`, `Rev.`, …); decide and document heading-`last_target` behavior; rename the C8 test; add the missing pins listed under point 5.

## Readiness

Not ready to land as-is — P1-1 is a silent, section-wide coverage regression with a one-line fix. After edits 1–2 and their pinning tests, it is ready for `feat/matter-capture`; the sibling-branch conflicts noted in the handoff are unchanged by this range and remain a landing-order decision for the founder. The recorded 444-test gate and the red-step evidence are consistent with what the code does.

## Model

SWE-2 Max (`swe-2-max`), per the runtime's own report.
