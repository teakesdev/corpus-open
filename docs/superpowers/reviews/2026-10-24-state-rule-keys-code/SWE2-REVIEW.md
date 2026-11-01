I'll start by reading the handoff file to understand the assignment.Diff and evidence read. Now the code files at 8bbd9f6.Now the rest of `briefcheck.py` — the check flow, `_citation_only`, `_authority_index`, and `resolve_authority`.Now `authority.py` (`find`, `import_ledger`) and the test file at the pin.Now let me check every place `RULE_RE` is used and confirm there are no other callers affected.Now the spec, to check the invariants the decisions must not break.Let me check the base branch's state-abbrev tests and the state-ann-heads tests for interactions with the new `RULE_RE`.Let me check for other places that might hard-code rule-key expectations or docs mentioning the rule grammar.One more check — who else imports `briefcheck` (to be sure no other path consumes `rule:` keys differently).One thing to verify — `test_briefcheck_review_fixes.py:247` asserts keys appear in `briefcheck.__doc__`. Let me see what's pinned there.Analysis complete. I verified the regex semantics directly against `citations._STATE_DOTTED`/`_STATE_UNDOTTED`, traced every consumer of the keys, and cross-checked the differential. Writing the report.

**Verdict: APPROVE WITH CHANGES** — one small additive test to pin the `import_ledger` dedup consequence of the rekey; the implementation itself is correct.

---

# SWE-2 review: `fix/state-rule-keys` @ `8bbd9f6` (range `b32d490..8bbd9f6`)

## 1. Verdict

**APPROVE WITH CHANGES.** The `RULE_RE` change is correct, minimal, and matches D1–D5. I traced the regex myself against `matterkit/citations.py:60–63` and every consumer in `briefcheck.py`/`authority.py`; the red/green story, the differential, and the mutation checks are internally consistent with what the code must do. The one change I ask for is a test, not a code edit:

- **P2-1 (required):** pin the `import_ledger` dedup consequence of the rekey — a ledger listing both `Va. R. Civ. P. 12` and `W. Va. R. Civ. P. 12` must plan **both** rows. At `b32d490` the second row was silently skipped as a within-ledger duplicate (both keyed `rule:varcivp12`); at `8bbd9f6` it is not. This is the exact collision class the commit fixes, on the write path (`import --apply` inserts rows), and no test pins it. Suggested shape, in `tests/test_briefcheck_state_rules.py`:

```python
class TestStateRulesInLedgerImport(Base):
    def test_ledger_imports_virginia_and_west_virginia_rules(self):
        self.m.write("ledger.md",
                     "| Citation | Status |\n|---|---|\n"
                     "| Va. R. Civ. P. 12 | UNVERIFIED |\n"
                     "| W. Va. R. Civ. P. 12 | UNVERIFIED |\n")
        planned, skipped = authority.import_ledger(
            self.m.conn, self.m.dir, os.path.join(self.m.dir, "ledger.md"))
        self.assertEqual([r["citation"] for r in planned],
                         ["Va. R. Civ. P. 12", "W. Va. R. Civ. P. 12"])
        self.assertEqual(skipped, [])
```

(`authority` is already imported; `Base` supplies `self.m.write`, `self.m.conn`, `self.m.dir`. This fails at `b32d490` — the second row is skipped — and passes at `8bbd9f6`, so it is discriminating in the right direction.)

Everything else below is a note, not a requirement.

## 2. Findings

- **P1: none.** I could not construct an input where the new prefix keys another jurisdiction's cite, swallows non-cite text, or turns a base BLOCK into a silent pass.

- **P2-1: `import_ledger` dedup change is unpinned** — `matterkit/authority.py:239–244`. Scenario: a ledger containing `Va. R. Civ. P. 12` then `W. Va. R. Civ. P. 12`. At `b32d490`: second row skipped (`duplicate within the ledger`). At `8bbd9f6`: both planned. Correct behavior, unintended-looking to a reader who doesn't know the history, and unprotected against a future key collision regression.

- **P3-1: several newly keyed forms are covered only by the differential, not the suite.** `N. D.` (spaced two-part), `Ohio  R.` (double space), `W.Va.R.` (fully glued), `P.R.`/`V.I.`/`N. Mar. I.` (territories), and `E.D. Va.` keeping `rule:varcivp12` all appear in `rule-differential.txt` but in no `unittest`. Any is one line in `TestStateRuleKeys`; the spaced/double-space forms are the most worth pinning because they exercise `N\.\s?D` and the undotted `\s+`.

- **P3-2: a `;` rule list inside a draft still scans only the head.** "…under W. Va. R. Civ. P. 5(b); 6(a); 12." yields only `rule:wvarcivp5` — the `;` continuation lives in `authority_keys` (`briefcheck.py:347–355`), i.e., the stored side only. Pre-existing and symmetric with `Fed. R. Civ. P.` lists, so out of scope for this commit, but it is a gap a draft can still fall through: sub-rules 6(a) and 12 are never checked. Report to the founder if not already tracked.

- **P3-3: the undotted `\s+` can join across a paragraph break.** `"Ohio\n\nR. Civ. P. 12"` keys `rule:ohiorcivp12` (`briefcheck.py:132`). A paragraph ending in a bare undotted state name followed by one opening with `R. Civ. P.` is contrived, and the result still must be a real rule cite that BLOCKs when unlogged — it fails safe. It also matches `citations.py:93`, which uses the same `(?:{_STATE_UNDOTTED})\s+` convention. Keep as is; narrowing to `[ \t]+` would break hard-wrapped drafts.

- **P3-4: `_citation_only` now folds short state-rule sentences.** In a fact section, "Service was defective under N.D. R. Civ. P. 12." strips to a ≤4-word remainder and folds into the previous unit (`briefcheck.py:917–924`); at `b32d490` the same sentence was an `uncited-fact` (UNCHECKED) because nothing scanned. The identical draft citing `Fed. R. Civ. P.` already folded at base — the trade-off is designed, just now reachable for more prefixes. Consistent; noted for the record.

- **P3-5: remaining open items are upstream, not this commit.** "W.  Va." (two spaces) still keys `rule:varcivp12` — your P2-1 `\s?`→`\s*` on `fix/state-abbrev-cites`, which propagates here through C1 if applied. "W. VA." all-caps scans nothing — consistent with the codebase's case-sensitive convention (`STATE_ABBREV` docstring, `test_briefcheck_state_abbrev.py:42–46`).

- **P3-6 (cosmetic):** the module docstring's authority list (`briefcheck.py:9–11`) still shows only `Fed. R. Civ. P.`/`L.U.Civ.R.`/`Rule 15`. An illustrative list needn't be exhaustive; adding `W. Va. R. Civ. P.` would document the new reach.

## 3. Answers to the six checkpoints

**(1) False passes.** Leftmost-start works as claimed: at position 0 of "W. Va. R. Civ. P. 12", `W\.\s?Va` is the only `_STATE_DOTTED` alternative that can match at "W" (`Va` requires "V"), the match consumes the whole cite, and no inner "Va." row survives — the differential confirms zero non-W.Va. rekeys and zero lost rows. I probed the dangerous cousins by hand: `E.D.N.Y.`/`S.D.N.Y.`/`W.D.C.` before "R. Civ. P." scan nothing (the `N`/`D` are dot-preceded, and the one-letter fallbacks cannot match); `U.S.C.`'s inner `S.C` is blocked by the lookbehind while `W.D.Tex.R.`/`N.D.Tex.R.` still key `tex…` via the unguarded fallback — exactly the C2 design. Two-part class alternatives are exhaustive of the real T1 set (`N.`+`CDHJMY`, `S.`+`CD`, `R.I`, `D.C`, `P.R`, `V.I`, `N. Mar. I`, `Am. Samoa`); prefix-of-prefix cases (`Mo` vs `Mont`, `N. M` vs `N. Mar. I`) are saved by backtracking. `_norm_key` collisions: none found — `wva`, `nd`, `sc`, `dc`, `ohio` etc. are produced only by their own state spellings.

**(2) Key agreement.** Draft scan, `RULE_TAIL_RE` lists ("W. Va. R. Civ. P. 12 and 15" → `wvarcivp12`/`wvarcivp15`, prefix extracted by `re.match(r"(.*?)\d+(?:\.\d+)?$", base)` which correctly keeps "W. Va. R. Civ. P. "), stored `authority_keys` including the `;` continuation (`re.sub(r"\d+$",…)` on `rule:wvarcivp5` → `rule:wvarcivp`), the docket `bare_rule` pass, `authority.find` disambiguation, and `import_ledger` all compute through the same scan/keys — they agree. The only unpinned one is P2-1 above.

**(3) Side effects.** `_citation_only` strips more, which can fold a sentence that at base was an `uncited-fact` (P3-4) — design-consistent, not a hole this commit opens. Rule display is `m.group(0)` — the cite as written, including the state; `resolve_authority`'s bare-rule display is untouched. Nothing else consumes `RULE_RE` (the ledger pin-extractor at `authority.py:208` filters `p` — rule rows carry `pin=None`).

**(4) C1–C6.** C1: acceptable and right — `authority.py` already calls `briefcheck._find`/`_fragments` (`authority.py:111`), so cross-module private reuse is house style, a duplicated T1 list would silently diverge from `74190c0`/P2-1, and a rename in `citations.py` fails loudly at `re.compile` during import. C2: the asymmetry is *necessary*, not just acceptable — I verified both mutants' logic (whole-prefix lookbehind drops `W.D.Tex.R.` → silent pass; no lookbehind lets `U.S.C.` read as `S.C.`). For one-word states the lookbehind is redundant (the fallback reproduces the match), which is exactly why it must sit on the state alternatives only. C3: `\.\s?`/`\s+` mirrors the `citations.py` head grammar; keep `\s+` (P3-3). C4: correct — the as-written state normalizes identically across spacing; the two-space residue is the known upstream item. C5: verified, including the glued `W.Va.R.` case which now correctly keys `wva…` (base keyed `va…` — an additional quiet improvement). C6: clean — diff touches nothing but `RULE_RE` and its comment.

**(5) Tests.** D1 pinned both directions at scan, `check`, and `find` levels; D5 pinned across all ten two-part and six undotted forms plus the Iowa decimal; C2 pinned on both mutants — each negative discriminates (I checked: mutant A fails only the `U.S.C.` test, mutant B fails only the `W.D.Tex.` test). Missing: the P2-1 ledger case, and cheap P3-1 pins.

**(6) Gaps.** The comment at `briefcheck.py:127–129` is accurate on both claims (whole-state prefix; token-start guard with the `Fed.`/fallback carve-out). The commit message's claims hold — it says "state or territory" and the list does carry P.R./V.I./N. Mar. I./Am. Samoa. Test-file docstring's base-bug description is accurate (bare `Rule 12` → `wva…` vs logged `va…` at `b32d490`).

## 4. Readiness

Ready to land behind `fix/state-abbrev-cites`, after the P2-1 test. The commit touches only `briefcheck.py:127–134` and adds one test file; `6301a9f` edited `citations.py` state lists and docs — no textual overlap, and the recorded merge-tree runs (clean, 471/479 tests OK) match what the code predicts. Two semantic hand-offs arrive through C1 by design — "Am. Samoa R. Civ. P." will begin keying `rule:amsamoarcivp…` on rebase (today it scans nothing), and P2-1 would extend `W.  Va.` coverage — both worth one line in the rebase commit message so the behavior delta is intentional-looking. None of the conflicts listed under Target involve this branch's files.

## 5. Model

SWE-2 Max.
