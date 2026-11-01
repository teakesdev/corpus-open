I'll start by reading the handoff file.Handoff read. This is a read-only review of a one-commit port (`a5c8efb..762a2d9`). Constraints: file read, grep, glob only — no exec/shell/python, no servers, no other worktrees. Let me survey the evidence files first.The review.diff is 849 lines; let me read the rest.I have the full picture of both diffs. Now let me read the actual code at the review tip: `matterkit/briefcheck.py` (RULE_RE, `_norm_key`, `scan_authorities`, `authority_keys`, `_rule_list`, `RULE_CONT_RE`, `_citation_only`, `units`, `resolve_authority`) and `matterkit/citations.py` (`_STATE_DOTTED`, `_STATE_UNDOTTED`).Now I have the merged `RULE_RE` and the public-line `citations._STATE_DOTTED`/`_STATE_UNDOTTED` (which now use `\s*` inside two-part abbreviations — the P2-1 fix landed, and `Am. Samoa` moved to undotted). Let me read the rest of `briefcheck.py` — `scan_authorities`, `authority_keys`, the `;` continuation, `_citation_only`, `units`, `resolve_authority`.Key machinery located: the merged `RULE_RE` (lines 133–137), `_norm_key` keeping dots between digits (265–268), `scan_authorities` rule pass with `re.S` prefix split (442–455), `_rule_list`, `RULE_CONT_RE`, and the `;` continuation in `authority_keys` (530–553). Now I need the check path: `units`, `_citation_only`, the bare-Rule binding, `resolve_authority`, and `_authority_index`.I have the core logic. Now I need the test fixture — `Base`, `codes`, `Matter.check` in `test_briefcheck.py` — since the DRAFT strings contain "Doc. 16" and the tests assert `codes(r, "BLOCK") == []`. Let me check how that's handled.Fixture confirmed: Doc. 16 is registered in the docket, so `codes(r, "BLOCK") == []` is sound. Now let me read the ported test file and sweep for any test still assuming dropped dots in rule keys.All the key machinery is confirmed. The dotted-keys convention (`rule:fedrcivp5.1`) is pinned across `test_briefcheck_dotted_keys.py` and `test_briefcheck_false_blocks.py`, and the `;` strip `\d+(?:\.\d+)*$` is the dotted-keys-aware version. Now the leak sweep (point 7) and a few remaining cross-checks: `authority.find`/`import_ledger` in `authority.py`, plus grepping the diff for home paths/emails.No leaks found — only `~/` redactions and constraint text mentioning `~/dev/Cursus/` in the ported handoffs. `authority.find` and `import_ledger` both compute keys via `briefcheck.authority_keys` at run time, so the ported scan covers both sides. One loose end: `test_briefcheck_dotted_keys.py:158` expects both `rule:fedrcivp5.1` and `rule:fedrcivp51` — let me see what that's pinning, and check `DEFAULT_RULES`/`find` and `RULE_CONT_RE` usage.Let me verify the on-disk layout of the new docs folders matches the diff, and check `import_ledger`'s status mapping for the UNVERIFIED ledger test.The SWE2-REVIEW.md file is the launcher's live log of my session — that's the save mechanism, not something I write. The history-rewrite note confirms all the prerequisite fixes (linebreaks `495ef74`, state-abbrev-cites `dd2778f`, dotted-keys `05646ad`, stored rule lists `e023ceb`) landed on the public line before `a5c8efb`. One last sweep: any test expecting a dotted rule cite to key without the dot, and the `_citation_only` fold behavior.One entry to verify: `test_briefcheck_rule_lists.py:56` expects `{rule:fedrcivp56}` alone — let me check that context.Let me verify the docstring-pin test the r2 report cited (`test_n5_docstring_documents_every_honoured_key`) still holds with the modified docstring.I have everything I need. All traces verified, the merge resolution confirmed character-for-character against `branch.diff`, and the public-line interactions cross-checked. Writing the report.

APPROVE WITH CHANGES — one additive test required; the ported code itself is correct.

---

# SWE-2 review: port of `fix/state-rule-keys` onto the public line (`a5c8efb..762a2d9`)

## 1. Verdict

**APPROVE WITH CHANGES.** The port is faithful: every line that differs from `branch.diff` is one of the three documented resolutions, and the merged `RULE_RE` behaves correctly in every scenario I traced, including all four handoff probes. The one thing the commit's only semantic novelty — the state-alternation × `\s*`/`re.S` seam — has no test on, anywhere in the suite. One small additive test, same shape as round 1's P2-1 ask.

**Required edit** (in `tests/test_briefcheck_state_rules.py`, inside `TestStateRuleKeys` — e.g. after `test_spacing_inside_and_after_the_state_keys_the_same` at line 51):

```python
    def test_a_line_break_inside_the_state_rule_still_keys_the_whole_state(self):
        self.assertEqual(rules("W. Va.\nR. Civ. P. 12"), [("rule:wvarcivp12", "W. Va.\nR. Civ. P. 12")])
        self.assertEqual(rules("N.D.\nR. Civ. P. 12"), [("rule:ndrcivp12", "N.D.\nR. Civ. P. 12")])
        self.assertEqual(rules("Ohio\nR. Civ. P. 12"), [("rule:ohiorcivp12", "Ohio\nR. Civ. P. 12")])
```

The first two rows pin the merged `\.\s*` on dotted states; the third is the round-2 crash input itself (`"Ohio\nR. Civ. P."` raised `AttributeError` at the old base — at `762a2d9` it keys `ohiorcivp12` because the prefix split at `briefcheck.py:447` uses `re.S`). Optional additions to the same method: `"W.  Va. R. Civ. P. 12"` → `wvarcivp12`, `"N.  D. R. Civ. P. 12"` → `ndrcivp12`, `"Am. Samoa R. Civ. P. 12"` → `amsamoarcivp12` (the inherited `_STATE_*` reach, see P3-1).

## 2. Findings

**P1: none.**

- **P2-1 (the required edit above): the merge seam is unpinned** — `matterkit/briefcheck.py:134–136`, `tests/test_briefcheck_state_rules.py:40–102`. The port's only semantic novelty is the state alternation running under the public line's `\s*` separators and `re.S` prefix split. Every ported test uses single/double *spaces* on one line; `test_briefcheck_rule_linebreaks.py:26–35` and `test_briefcheck_wrapped_cites.py:24–30` wrap only `Fed.`/`Tex.`/`L.U.Civ.R.` — no state prefix. Failure scenario: a future edit reverting `\s*`→`\s?` on the state path, or dropping `re.S` at `briefcheck.py:447`, leaves all ported tests green while `"W. Va.\nR. Civ. P. 12"` silently unscans or `"Ohio\nR. Civ. P. 12"` resurrects the `AttributeError` (base containing `\n`, `.group()` on `None`) that the round-2 dispute documented.

- **P3-1: the port note understates the delta** — `docs/superpowers/reviews/2026-11-01-port-state-rule-keys.md:14`. "Everything else is the branch's net diff" is true line-by-line, but `citations._STATE_DOTTED`/`_STATE_UNDOTTED` changed under the port (`citations.py:70–73`): two-part states now allow `\s*` internally (the P2-1 fix the branch's handoff said was undecided — it landed) and `Am. Samoa` moved to undotted. So the ported `RULE_RE` additionally keys `"W.  Va."`, `"N.  D."` and `"Am. Samoa R. Civ. P."` — forms the reviewed branch did not (round-1's P3-5 predicted exactly this inheritance via C1). Benign and intended, but worth one line in the note, ideally alongside the fact that the round-2 `AttributeError` class is now dead on this line.

- **P3-2: `\.\s*` joins a dotted-state cite across `\n\n` in `authority_keys`** — `briefcheck.py:134`. A hand-stored or typed citation `"W. Va.\n\nR. Civ. P. 12"` keys `rule:wvarcivp12`. Drafts cannot reach this (`units()` splits on blank lines at `briefcheck.py:1170`) and ledger cells are per-line, so the exposure is the stored-citation side only — identical to what `"Fed.\n\nR. Civ. P."` already does on this line. Consistent and fails safe; noted for the record, not a defect.

- **P3-3 (nit): two empty `swe2.log` files carried into the port** — `docs/superpowers/reviews/2026-10-24-state-rule-keys-code{,-r2}/swe2.log`. Harmless placeholders, consistent with every other review folder on this line.

## 3. Answers to points 1–8

**(1) Fidelity.** Verified line by line. Exactly three differences from `branch.diff`, all documented: (a) the docstring keeps the public line's `Miss. Code (Ann.) § 95-1-5 · Section 95-1-5 · Section 552.101` and adds `W. Va. R. Civ. P. 12` (`briefcheck.py:10–12`); (b) `RULE_RE` is the branch's alternation with `\s?`→`\s*` at every separator the public line widened (`briefcheck.py:134–136`); (c) one test line, `iowarcivp1421`→`iowarcivp1.421` (`test_briefcheck_state_rules.py:76`). The other 174 test lines, both review folders (r1: 6 files, r2: 4, incl. `rule-differential.*` and empty logs) match after `~` redaction. No added, dropped or altered line I cannot explain.

**(2) `RULE_RE` as merged.** Correct.
- *State + line break before `R.`:* `"N.D.\nR. Civ. P. 12"` → `ndrcivp12`; `"W. Va.\nR. …"` → `wvarcivp12`; `"Ohio\nR. …"` → `ohiorcivp12` (undotted `\s+` already crossed `\n` at the branch — the crash was downstream in the prefix split, now dead via `re.S`).
- *`Fed.` / fallback:* unchanged and unguarded, by design (C2): `Fed.R.Civ.P.12`, `Tex.`, `Miss.`, `Pa.`, `Mont.` all key as before; mid-token `W.D.Tex.R.` still reaches `texrcivp12` through the fallback.
- *Lookbehind:* `(?<![\w.])` on the state alternatives only — `"the U.S.C. R. Civ. P. 3"` scans `[]` (the `S.C` is dot-preceded); `"S.D. Ind. R. Civ. P. 12"` yields `indrcivp12`, display `"Ind. R. Civ. P. 12"` — `S\.\s*D` fails at `R\.` (next is "Ind."), `D` is dot-blocked, and `Ind` reads as the state, exactly the `E.D. Va.` semantics. `"S.D.N.Y. R. Civ. P."` scans `[]`.
- *Backtracking:* all alternatives are literal-bounded; `\s*`/`\s+` are single-class quantifiers; `[a-z]{1,5}` capped; prefix-of-prefix cases (`N. M` vs `N. Mar. I`, `Mo` vs `Mont`) resolve by ordinary backtracking. No catastrophic vector.

**(3) The key change.** `rule:iowarcivp1.421` is right — `_norm_key` (`briefcheck.py:268`) keeps a dot between digits; `Fed. R. Civ. P. 5.1` → `fedrcivp5.1` (pinned at `test_briefcheck_dotted_keys.py:139,185`, `test_briefcheck_false_blocks.py:95`). `_rule_list` members key `_norm_key(prefix + n)` (`briefcheck.py:454`); `RULE_CONT_RE`/the `;` continuation strips `\d+(?:\.\d+)*$` then re-normalizes the segment number (`briefcheck.py:548–550`), so dotted numbers work on both sides (`5.1; 6` → `{5.1, 6}`; `5.1; 6.1` → `{5.1, 6.1}` — `test_briefcheck_dotted_keys.py:318–321`). The old `iowarcivp1421` would be *wrong* here; the reviewer's judgment call is correct. Nothing else assumes dropped dots — `fedrcivp51`/`fedrcivp56` in the suite are literal rule numbers 51/56.

**(4) Interaction with the public line.** All four traced clean:
- `authority_keys("W. Va. R. Civ. P. 5.1; 6")` → `{rule:wvarcivp5.1, rule:wvarcivp6, full:…}`: `inside[-1]` = `rule:wvarcivp5.1`, `\d+(?:\.\d+)*$` strips `5.1` → `rule:wvarcivp` + `6`. State survives.
- `authority_keys("N.D. R. Civ. P. 5(b); 12")` → `{rule:ndrcivp5, rule:ndrcivp12, full:…}` (sub-marks ride `RULE_CONT_RE`'s trailing group).
- `"W. Va. R. Civ. P. 12 and 15"` → `wvarcivp12` + `wvarcivp15` via `RULE_TAIL_RE` and `prefix + n` (`briefcheck.py:452–455`).
- Bare `Rule 12` after a state cite → `resolve_authority` (`briefcheck.py:1497–1507`) binds to the most recent earlier `full_rules[num]` → `c.target` = the state key; with none, the docket `bare_rule`. `full_rules` keys on the number string, so `5.1` and `51` stay distinct (dotted-keys compatible). The ported docket-map test pins the fallback; the earlier-cite path is pinned on this line for one-word states (`test_briefcheck_dotted_keys.py:146–151`).

**(5) Tests.** The 175 lines test what they claim. I traced every expected key and display through the merged `RULE_RE`, `_STATE_*` and `_norm_key`; `Doc. 16` is registered in the `Base` fixture (`test_briefcheck.py:45`), so the `BLOCK == []` assertions are sound; the UNVERIFIED ledger exercises `keys & seen` (`authority.py:163–173, 239–244`); both negative guards are discriminating (mutant-checked in round 1). No vacuous or stale assertion found. The only hole is P2-1.

**(6) The port note.** Accurate on every checkable claim — all three deltas verified, the `~` redaction is applied, the `swe2.log`s are empty (consistent with "the branch never committed its transcripts"), and the hash table matches the two rounds' handoffs (`8bbd9f6` fix, `522e2bc`/`06e8dbf` r1 handoff/record, `cbf2ede` r2 fix, `789c4df`/`1f22ab1` r2 handoff/record). Two omissions, not errors: the inherited `_STATE_*` delta and the dead crash class (P3-1).

**(7) Leaks.** None found. No `/Users/` or `/home/` literal; all paths are `~/`; `~/dev/Cursus/` appears only inside the ported "do not open" constraint text; `https://example.org/rules` is the reserved test domain; no transcript or session text in the diff.

**(8) Gaps.** Only the two already given: the unpinned seam (P2-1) and the note's under-described delta (P3-1). The port is otherwise minimal, correctly scoped, and preserves the review provenance (both rounds' folders plus a hash-mapped note).

## 4. Readiness

Ready to land after the one additive test in §1. The merge resolution is exactly what the handoff claims, the one-line test change is required by the public line's key convention and was made correctly, and the recorded 1207-test pass at `762a2d9` (Python 3.14) is consistent with everything I traced. The unreviewed combination — state alternation under `\s*`/`re.S` — behaves as intended on every probe; it also silently inherits the `W.  Va.`/`N.  D.`/`Am. Samoa` reach from the landed `state-abbrev-cites` P2-1 and `74190c0` changes, all improvements worth one line in the port note.

## 5. Model

SWE-2 Max.
