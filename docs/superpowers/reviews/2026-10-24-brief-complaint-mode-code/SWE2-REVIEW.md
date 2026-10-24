I'll start by reading the handoff document.Now let me look at the review folder contents and the diff.I have the diff. Now let me read the main file, `matterkit/briefcheck.py`, in the worktree at 7b78d9b.Now let me read the test base class the new tests build on, plus the MCP server file.Now the CLI plumbing in `matter.py` and the README section.Let me check how assertion statuses are validated/normalized — this affects whether the complaint-mode fact-tag check can miss statuses outside the known set.Let me grab precise line numbers in the new test file and double-check a couple of remaining details — whether the heading regex's `\d{1,3}` widening and `_sentence_spans` interact as I traced, and the PROMPT.txt for any extra instructions.I have everything I need. I've traced `_heading`, `units`, `unit`, `quotes`, `facts`, `check`, `render`, the CLI and MCP plumbing, the base test fixtures, and the store's `VALID_STATUS` set. Writing the report now.

---

**Verdict: APPROVE WITH CHANGES**

The complaint-mode plumbing, the notes mechanism, the exit contract, and the test coverage of the main paths are all sound. Two residual gaps in the `_heading` heuristic — one that silently swallows checkable cites/quotes in both modes (and is *widened* by the `\d{1,3}` change), and one that silently disables brief-mode fact-section tracking for punctuated numbered headings — warrant a follow-up commit. Neither blocks the merge.

## Findings

### P2 — Numbered lines ending in a cite or quote with no terminal punctuation are still swallowed as headings
`matterkit/briefcheck.py:327–331`

The new digit branch demotes a numbered line to a paragraph only when it ends in `[.?!:;,]`. A short numbered line that *contains* a checkable cite or quotation but lacks terminal sentence punctuation still returns heading level 3 and is skipped entirely — no row, no finding, no note:

- `"12. See Doc. 16 at 3"` — 6 words, ends in "3" → heading → the record cite is never verified.
- `"12. Doc. 16"` — same, even simpler.
- `'3. The captain said "stand down"'` — ends in `"`, which is `\W` but not in the punctuation class → heading → the alleged quotation never even reaches `notes`; it vanishes from the report.
- `"5. Statement (Doc. 4)"` — ends in `)` → same.

Synthetic failure: a complaint draft containing

```
12. See Doc. 99 at 3
```

between blank lines, run with `--mode complaint`, exits 0 with no trace that `Doc. 99` was ever cited — contradicting D2 ("every record cite is still checked") and README.md:174–176. This existed at `766440e` for ≤2-digit numbers, but the `\d{1,3}` widening at line 327 extends it to paragraphs numbered 100–999 (a long complaint's `105. See Doc. 16 at 3` was checked before and is skipped now — a genuine regression).

**Suggested edit:** inside the `tok.isdigit()` branch, veto headings whose line contains checkable content, e.g. `return None if RECORD_RE.search(line) or PLEADING_RE.search(line) or QUOTE_RE.search(line) or CASE_RE.search(line)` — these patterns can't match a real short title like "1. Background". Alternatively, or additionally, require the line to end in an alphanumeric/`)`/`"` *and* have no internal sentence punctuation.

### P2 — Punctuated numbered headings demote to paragraphs, silently disabling brief-mode fact tracking
`matterkit/briefcheck.py:330–331` interacting with `units()` at `briefcheck.py:836–839`

`FACT_HEADING` is only consulted when `_heading` returns non-`None`. A title-case numbered section heading ending in punctuation — `"1. Factual Allegations."`, `"1. Statement of the Case:"` — is now a paragraph, so `fact_level` is never set and every following uncited sentence in that section goes unflagged **in brief mode** — a regression in the mode the diff was supposed to leave alone. (All-caps headings are unaffected because the earlier all-caps branch catches them regardless of trailing punctuation; roman-numeral headings too.) The symmetric noise case: a demoted ≥4-word numbered subheading *inside* an active fact section gets a spurious `uncited-fact` flag.

Synthetic failure: brief draft `"1. Statement of Facts.\n\nThe arrest happened on a holiday.\n"` — previously `uncited-fact` fired on the second line; now zero findings, exit 0.

**Suggested edit:** the demotion was made for complaints, where numbered paragraphs dominate; briefs are where numbered point headings live. Pass the mode into `_heading` and apply the `≤8 words / no terminal punctuation` restriction only in complaint mode, keeping the pre-`7b78d9b` digit-heading behavior in brief mode. That eliminates this regression entirely without touching the complaint fix.

### P3 — `summary()` and the `--out` console line don't mention listed alleged quotations
`matterkit/briefcheck.py:706–712`, `matter.py:290`

Exit 0 in complaint mode can print `ALL TIED TO SOURCES: 0 blocking, 0 unchecked, 0 warnings` while `notes` holds unconfirmed quotations. The rendered report mitigates this with the dedicated section ("The pleader vouches for these by signing…") — acceptable — but with `--out`, stdout shows *only* the summary. Suggest appending `"; N alleged quotations listed (see report)"` when `mode == "complaint" and self.notes`.

### P3 — Residual false-heading surface for `[A-Z]` letter tokens (pre-existing, same class)
`matterkit/briefcheck.py:332`

Letter and roman tokens keep the old rule: ≤25 words, no punctuation guard. `"A. Plaintiff lives in Exampleton."` (a capital-letter-numbered allegation or caption line) is heading level 2 → skipped, cites unchecked. The same guard applied to digits (or the cite/quote veto suggested above) could cover `A.`/`B.` tokens cheaply. Pre-existing, but squarely inside D2's promise.

### P3 — MCP `mode` property lacks `"type": "string"`
`matter-mcp.py:58`

`"mode": {"enum": [...]}` unlike the sibling `"type"` properties. Harmless — see point 3 below — but worth normalizing while touching the file.

### P3 — Test gaps
`tests/test_briefcheck_complaint_mode.py`

- No test pins either heading edge above (`"12. Doc. 16"`, a `"`-terminated numbered quote line, or a punctuated numbered facts heading in brief mode).
- `test_numbered_sentence_is_checked_not_skipped` (line 100) uses a 9-word line — it would pass under the old ≤25-word rule too, so it doesn't exercise the new guard's boundary. A ≤8-word line ending in a period (e.g. `"1. Doc. 1."`) would.
- `test_roman_numeral_heading_with_period_is_still_a_heading` (lines 109–111) is vacuous: `Rule 15` resolves to the logged `Fed. R. Civ. P. 15`, so `authority-not-logged` is absent whether or not the line is treated as a heading.
- No MCP invalid-mode test; the isError path (see below) is only verified by inspection.

### P3 — README overstates the guarantee
`README.md:176–178`

"Numbered paragraphs … are always checked as text; only a short, unpunctuated '1. Background' line counts as a heading." `"12. See Doc. 16 at 3"` is internally punctuated yet still counts as a heading — the actual rule is about *terminal* punctuation. Suggest rewording alongside the P2 fix.

## Answers to the five points

**1. Does complaint mode skip anything it should still check?**
The mode gates are correctly placed: `uncited-fact` (`unit`, line 1006) is the only skipped check; record, pleading, authority and `Id.` paths are mode-blind; `fact-unknown` still blocks; `fact-unsupported` is skipped only for `ALLEGATION`; `fact-not-documented` fires for `HYPOTHESIS/INFERENCE/UNKNOWN` while `DOCUMENTED FACT`/`ALLEGATION` pass — exhaustive over `store.VALID_STATUS` (store.py:111). In `quotes`, a quotation lands in `notes` only when the unit has *no* cite at all (line 1123); a cite in the same sentence or a folded citation-only sentence is found via `after`/`before` and fully checked (confirmed by `test_sourced_quotation_is_still_checked`). The remaining ways an attributed quote or a cite escapes checking are the two heading findings above, not the mode logic. (Caveat, pre-existing: MCP `fact_add` doesn't validate `status` server-side — `matter-mcp.py:112` — so a non-taxonomy label would slip past both `fact-not-documented` branches.)

**2. The heading rule.** Both failure directions exist — see P2 findings. Roman-numeral and letter levels are unchanged (`return 1 … else 2`, line 332), and fact-section tracking still keys off heading levels correctly — except that demoted numbered headings can no longer set `fact_level` (the mechanism behind the second P2).

**3. Mode plumbing.** `check()` validates `mode` before any I/O (`briefcheck.py:782–783`); CLI `choices=` rejects bad values via argparse (`matter.py:553–554`); an invalid MCP `mode` reaches `check()`, raises `ValueError`, and `handle()` wraps it as a clean `isError` result (`matter-mcp.py:185–187`). `Report.mode`/`notes` defaults are set in `__init__` and overridden only inside `check`. `render` names the mode accurately for both modes.

**4. Exit codes and wording.** Exit 0 with unconfirmed `notes` is reachable by design (D3) and the report section is explicit; the only leak is the summary-only console path — the P3 above. I'd call the wording adequate after that one edit.

**5. Gaps.** Covered by the P3s: README wording, missing edge tests, the letter-token residual.

## Readiness

It can stay on `feat/matter-capture` as-is. The dominant complaint shape — long numbered paragraphs ending in periods — is handled correctly, and the P2s are edge-case coverage losses rather than wrong verdicts on checked material. The follow-up commit should fix the two heading gaps (both in `briefcheck.py:327–331`), tighten the README sentence, and add regression tests for the four line shapes above.

## Reviewing model

SWE-2 Max.
