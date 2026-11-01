I'll start by reading the handoff document to understand the scope.Now let me read the code under review at `bcbf173` and the round-1 handoff for the C/D decision context.Now let me read the rest of `briefcheck.py`, particularly `units()`, `unit()`, and the cite/fact/quote checking paths.I have the core code. Now let me check the test fixture (`test_briefcheck.py` `Base`/`Matter`) and skim the complaint-mode test file referenced by the round-1 notes.Let me quickly check the existing `SWE2-REVIEW.md` placeholder in the r2 folder, and confirm there are no other `_heading`/`_titled` callers or related spec text I should reconcile.All the evidence is in hand. I've traced every change in `review.diff` against `briefcheck.py` at `bcbf173`, the fixture (`Matter`/`Base`), and the base-tip design. Writing the report now.

**Verdict: APPROVE WITH CHANGES** — every round-1 finding is addressed, kept-by-decision and pinned, or consciously deferred to the founder, and the recorded evidence (24-test red step with 16 failures, `Ran 36 tests OK` focused, `Ran 453 tests OK` gate) is consistent with what the code does. One required change, all documentation/pinning — no code change needed: the new fact-heading rule's stated invariant ("a misread can only open or keep a fact section, never close one") is not quite true — a misread *narrows* an already-open shallower fact section (`fact_level` 1→2), which lets a later same-level heading close it early and silently drop `uncited-fact` coverage. Pin the corner and correct the comment at `briefcheck.py:376–377`; details below.

## Round-1 findings

**P1-1 (`"A. Background."` is body text → fact section never opens) — ADDRESSED.** Two mechanisms at `briefcheck.py`:
- Line 374: the abbreviation-opener check now requires `len(words) > 1`, so a lone dotted title word no longer kills the line. `"D. Conclusion."` reaches the Title Case path at 383–384 and returns heading level 2 (pinned at `tests/test_briefcheck_heading_sentences.py:146`).
- Lines 376–377: the new fact rule returns True for a ≤7-word rest matching `FACT_HEADING` — this is what rescues the cases edit 1 alone could not: sentence-case headings (`"A. Factual background."`, `"B. Procedural history."`, `"D. Statement of facts."`, pins at 147–149) where a lowercase fact word would fail Title Case. Traced: `_titled("Factual background.", False)` → `first="Factual"` (no dot) → 2 words ≤7, `FACT_HEADING` matches → True → level 2 → `fact_level=2` → the following uncited sentence is flagged (`test_dotted_fact_heading_opens_its_section`, 166–169; `test_sentence_case_fact_heading_opens_its_section`, 171–173).

**P1-1's folded-in cite case (`"A. Background Doc. 16"`) — ADDRESSED.** The fact rule runs at 376–377, *before* the cite check at 378, so a cite-carrying fact heading is a heading again and `units()` opens the section at 911–912; its `Doc. 16` is still extracted and judged in `unit()` (pinned at 150 and end-to-end at 175–178: `record-not-in-store` on `Doc. 1`, `uncited-fact` on the arrest sentence).

**P2-1 (`TITLE_MINOR`'s `"v."`/`"vs."` dead) — ADDRESSED.** 364–366: entries are now `"v"`, `"vs"` (unstripped forms, matching the `rstrip(".?!:")` at 382), plus the legal minors `re ex rel et al de la per sub nom en banc`; the comment correctly states the comparison order. Traced: `"B. Smith v. Jones Controls The Outcome."` → not short (ends `.`) → Title path → `"v"` minor → level 2 (pin 151; `test_case_name_heading_closes_the_fact_section`, 180–183, discriminating — if `"v"` regresses, the line is body and draws `uncited-fact` itself, and the argument sentence stays in-section and also flags).

**P3-1 (heading cite as `last_target` hijacks a following `Id.`) — KEPT BY DECISION, pinned.** Behavior unchanged (1076–1078 still update `last_target`/`last_unit_targets` for heading units). `test_id_after_a_heading_cite_binds_to_the_heading_cite` (190–195) pins my exact scenario: `Id. at 45` binds to the heading's `Doc. 16` (3 pages) → BLOCK `pin-out-of-range` → exit 1. A wrong guess BLOCKs loudly — the stated rationale is defensible; documented.

**P3-2 (cite-less quotation in a heading → `quote-uncited`) — KEPT per D3, pinned.** `test_uncited_quotation_in_a_heading_is_flagged` (197–200) pins `## The "File Is Closed" Call` → UNCHECKED `quote-uncited`, exit 2. Real-noise caveat from round 1 stands but is the founder's call.

**P3-3 (`first` checked unstripped) — ADDRESSED.** `words` is quote/bracket-stripped once at 372, `first = words[0]` at 373 feeds both the abbrev check (374) and `first[:1].isupper()` (383). `"B. (Optional) The Standard For Review Of Denial Motions"` → `first="Optional"` → Title passes → level 2 (pin 154).

**P3-4 (vocabulary) — ADDRESSED.** `HONORIFICS` (367) adds `Hon. Prof. Sgt. Lt. Capt. Det. Gov. Sen.`; `St.`, `Rev.`, `Gen.` deliberately excluded (code-name collisions: `Rev. Stat.`, `Gen. Laws`) — the exclusions are reasoned and run in the safe direction (a misread is noise, not silence). `Rep.`, `Pres.`, `Esq.` from my round-1 list are also out — acceptable; `Rep.` collides with "Report", the other two are rare openers. Honorifics bypass *only* the abbrev check; a real cite in the line is still caught at 378. Pinned: 153, 185–188.

**P3-5 (cosmetics) — PARTIALLY ADDRESSED, acceptably.** The `units()` comment (915) now names fact tags — accurate (`facts()` runs on heading units; the tag loop at 926–929 hosts tags inside heading blocks). The C8 test-name fix (`test_roman_numeral_heading_with_period_is_still_a_heading`, `tests/test_briefcheck_complaint_mode.py:109`) is unchanged pending the founder's decision — noted, not a defect.

**Round-1 exact edits 1–3:** edit 1 done (as `len(words) > 1`, which is the same scoping — `rest.split()` and the stripped list have equal length); edit 2 done plus the legal minors; edit 3 done except the deferred rename. All six missing pins I listed are now present.

## New breakage in the fix diff (`e50de74..bcbf173`), ranked

**P2 — a fact-rule misread *narrows* an already-open fact section, so a later same-level heading closes it early; the stated invariant is false.** `briefcheck.py:376–377` interacting with `units()` at 911–914. The rule's reasoning — "a `FACT_HEADING` heading only ever sets `fact_level`" — is literally true but incomplete: when a fact section is open at a shallower level, `fact_level = lvl` *demotes* the close threshold, so a subsequent heading that previously couldn't close now does. Input:

```
I. FACTS

J. Smith described the facts.

A. The Arrest

The officer arrived late on a holiday.
```

- At `e50de74`: the "J." line is body text (Title fails on "described") → `uncited-fact`; `"A. The Arrest"` (level 2) cannot close a level-1 section (`2 <= 1` false) → the officer sentence is also flagged → **exit 2, two findings**.
- At `bcbf173`: the "J." line is a level-2 heading via the fact rule → `FACT_HEADING` matches the block → `fact_level` demotes 1→2 → `"A. The Arrest"` (2 ≤ 2) closes the section → the officer sentence escapes → **exit 0, zero findings, zero rows**. Same class of silent, section-tail coverage loss as P1-1 — though a much rarer shape (needs a level-1 fact opener + a ≤7-word fact-word lettered line + a later level-2 heading). This is parity with `6ac2b01`, not a regression against the base — but it is new relative to the reviewed tip `e50de74`, and it is enabled solely by the new rule.
- No clean code fix exists: the demote is load-bearing for genuine nested fact headings ("I. FACTS" → "A. Background" → "B. Argument" relies on `fact_level=2` to let B. close). **Required change:** pin the corner end-to-end in `test_known_limits_are_pinned` (or its own test) and correct the comment at 377 — "can open, keep, or *narrow* a fact section" — so the approved trade-off is stated accurately.

**P3 — lone dotted fragments become level-2 headings and can close a level-2 fact section.** `briefcheck.py:374` (the `len(words) > 1` relaxation) → Title path at 382–384. `"W. Va."`, `"J. R."`, `"A. Doc."`, `"A. (Exh.)"` — a one-word dotted rest skips the abbrev check, carries no cite, and passes Title (`"Va"`[:1] upper). Under `"A. Background"` (fact_level 2) a stray `"W. Va."` line closes the section silently. Requires a truncated-heading fragment on its own line inside a fact section — exotic; no change required, could be pinned alongside the P2 note.

**P3 — the fact rule ignores terminal punctuation entirely, folding a second "sentence" into the heading unit.** `briefcheck.py:376`. `"A. Facts: the clerk lied."` (rest: 4 words, first word `"Facts:"` ends in `:` not `.`, so it passes the abbrev check) is a level-2 heading — "the clerk lied" is inside the heading unit and is never asked for `uncited-fact`. Bounded to the line, and it opens rather than closes coverage — loud direction. Same accepted-cost envelope as the pinned "J. Smith described the facts."; worth one line in the class docstring.

Everything else in the diff verified safe: the vocab additions only rescue already-Title-shaped lines (see point 3 below); `first[:1]` on an all-punctuation token yields `""` → `isupper()` False, no crash; `words` rebind at 382 does not corrupt `first` (first char unaffected by `rstrip`); cite/quote/tag checking inside heading units is unchanged and identical to body units.

## Answers to the handoff's points 1–6

**1. The fact-heading rule (376–377).**
- *Close a fact section or hide a cite?* It cannot hide a cite — heading units run the same `unit()` extraction (record/pleading/authority/Id./quotes/tags; only `uncited-fact` and `_sentence_spans` splitting are skipped). It cannot *directly* close a section — a `FACT_HEADING`-matching heading always takes the `fact_level = lvl` branch. But it can *narrow* one — the P2 above, so the "never close" claim needs qualification. The misread sentence itself escapes only `uncited-fact`, bounded to its own line.
- *7-word rest limit?* Defensible — it matches the ≤8-word short-rule vocabulary (7 rest words + marker = 8 line words). Residual: a *longer* sentence-case fact heading (`"A. Statement of facts in support of the motion."`, 8-word rest) still loses its section — bounded, and the Title-Case spelling of the same heading survives. Running before the cite check is *required* — otherwise `"A. Background Doc. 16"` returns False at 378 and the folded P1-1 case is unfixed.
- *FACT_HEADING reach:* `"J. Smith denied the allegations."` (4-word rest) and `"B. The background check was clean."` (5-word rest) both become headings. The pinned cost is acceptable *as disclosed* — each such line still gets full cite/quote/tag checks and opens coverage for what follows; the disclosure just needs the narrowing caveat.

**2. Edit 1 (374).** `"W. Va."`, `"J. R."` alone do become level-2 headings via the Title path — they can close a level-2 fact section (the P3 above). A one-word abbreviation followed by a cite cannot arise: a one-token rest containing a cite pattern still hits the cite check at 378 (e.g. `"A. §1983"` → `"§" in rest` → False; `"A. Doc.16"` → `RECORD_RE` matches `Doc.16` with `\s*` empty → False). The abbrev check keeps precedence over the fact rule — pinned by `"J. R. Smith described the facts."` → None (164).

**3. Vocabulary (364–367).**
- *New minors passing a real sentence:* only if every other alpha word is already uppercase-initial — i.e., the line is already Title-shaped. Ordinary sentences fail on their lowercase verbs/particles regardless of `"per"/"de"/"et"/"al"`. The sharpest addition is `"per"` (a normal English preposition), but a line like `"B. Relief Sought per the Mandate"` reads as a title anyway. Low risk.
- *New honorifics opening a real cite:* an honorific bypasses only the abbrev check; `"§"`, `RECORD_RE`, `PLEADING_RE`, `ID_RE` and `scan_authorities` still run at 378 — `"A. Gov. Code § 123"` is body text. `"Sen. Rep."`-shaped lines aren't cite patterns at all, so heading-vs-body changes nothing about what's checked. Exclusions sound.

**4. Stripped first word (372–373, 383).** Safe. `rest` is `\S.*` so `words` is non-empty; a token that strips to `""` (e.g. `"()"`) gives `first=""` → `endswith(".")` False, `[:1].isupper()` False — handled gracefully, and `[:1]` is the right defensive form for a possibly-empty string (the old `first[0]` never saw an empty token only because it was unstripped). Empty tokens are also skipped by the `w and w[0].isalpha()` guard in `all()`.

**5. Tests.** The 9 new tests are discriminating — each mechanism has a positive pin and a bounding negative (7-word limit at 163, abbrev precedence at 164), and the end-to-end tests fail on regression of the specific mechanism, not just on "something changed". All six pins I asked for exist. Still missing: the demote-narrow pin (the required change), and optionally a lone-dotted-fragment pin. The `e50de74`-era suite's own pins all still hold by inspection.

**6. Integration preview (from `base-tip-heading.txt` only).**
- `e77da0c` has, this branch lacks: the `mode` parameter and complaint-mode lettered rule; the 12-word numbered limit and the Title-Case/`.`-or-`:`-ended numbered title rule (`"1. Statement of Facts."` is a heading there, body here — the P1-1 shape for numbered styles); cite-bearing numbered/complaint-lettered lines forced to text.
- This branch has, it lacks: checked heading units (its `units()` still skips heading blocks whole); `_titled`'s sentence detection (abbrev opener, short, Title Case, fact rule); a wider notion of "carried cite" for lettered lines — `§`, `ID_RE`, and rule/bare-rule/statute hits vs. its record/pleading/case/short/WL only.
- Opposite answers for the same line: `"J. Smith testified that the clerk refused the request."` (no cite) — body here, heading there (unchecked); `"W. Va. Code § 21-23-7 controls."` — body+checked here, heading+skipped there; `"A. Background Doc. 16"` — heading, opens the fact section, here; body, section never opens, there; `"B. Rule 15(a) Permits Amendment"` — body here, heading there; `"1. Statement of Facts."` — body here, level-3 heading opening a fact section there; `'A. The "File Is Closed" Call'` — heading + `quote-uncited` here, heading + silent there; `"I. The Court may set … Rule 15(a)(2)."` — body here (bare rule counts as a cite), level-1 heading there.

## Readiness

Ready to land on `feat/matter-capture` once the `5c0a42c` conflict is resolved, with the required pin/comment fix being landable before or alongside the merge (it's documentation of already-approved behavior, so it does not need to block). Whichever way the founder resolves the `briefcheck.py` conflict, the resolution must preserve, at minimum:

1. **Coverage of every line classified as a heading** — either this branch's checked-heading-unit (909–917, `Unit(...)` + `unit()`), or a `_carries_cite` widened to count `§`, `ID_RE`, `RULE_RE`/`LOCAL_RE`/`BARE_RULE_RE`/`LOCAL_WORDS_RE`, and `QUOTE_RE` for *all* marker kinds — otherwise `"W. Va. Code § 21-23-7 controls."` and quoted headings go silent again (the D1 bug).
2. **Fact-section openers that survive punctuation and cites** — the fact rule or an equivalent; the base design still loses `"A. Background Doc. 16"`'s section.
3. **The base's `mode` parameter and complaint-mode lettered/numbered rule** — this branch has no counterpart and must not be allowed to clobber them.
4. **Both test files' pinned behaviors**, with a re-run of the full suite on the merged tree (no suite has run on the conflict-resolved tree — `merge-tree` exits 1).

## Model

SWE-2 Max (`swe-2-max`), per the runtime's own report.
