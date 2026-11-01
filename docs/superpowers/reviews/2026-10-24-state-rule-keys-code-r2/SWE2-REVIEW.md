I'll start by reading the handoff document to understand the exact scope.The handoff is clear. Now let me read the round-1 report and the review diff in parallel.Now let me read the code files at `cbf2ede` — briefcheck.py, the test file, authority.py, and citations.py — to verify the fix against the claims.Code and test files confirmed. Now I need `authority.py` (for `import_ledger` signature, `parse_ledger` status mapping, and the dedup path), `citations.py` (the state alternatives the new tests exercise), and the `Base` fixture in `test_briefcheck.py`.`import_ledger` and `parse_ledger` confirmed: UNVERIFIED maps to `"unverified"` (`authority.py:163–173`), dedup via `keys & seen` at `authority.py:239–244`. Now let me verify the `Base` fixture API (`self.m.write`) and check whether any test pins `briefcheck.__doc__` content that the docstring edit could break.`Matter.write` writes into `self.dir` — the test's `os.path.join(self.m.dir, "ledger.md")` matches. Let me check what keys the docstring-pin test requires.Docstring pin test only asserts key names — unaffected. Now let me read the rest of briefcheck.py to verify the paragraph-splitting claim for P3-3 and the `_citation_only`/`check` flow.The `units()` block regex at line 893 confirms the handoff's P3-3 claim: `(?:[^\n]|\n(?![ \t]*\n))+` splits on blank lines, so `\n\n` never reaches the scan inside `check`, but a single `\n` does — and `m["base"]` containing `\n` makes line 272's `re.match` return `None`, crashing on `.group(1)`. Let me run a few last sanity greps: other tests touching `import_ledger`, anything pinning the module docstring text, and other callers of `scan_authorities`.All verification complete. I traced every new assertion through the actual regexes and `import_ledger` semantics, confirmed the P3-3 crash claim via the `units()` block regex at `briefcheck.py:893` and the `.group(1)` on `None` at line 272, and confirmed no test pins the module docstring text beyond `test_n5`'s key names (all still present). The diff is docstring + tests only — no new breakage.

**Verdict: APPROVE**

---

# SWE-2 re-review, round 2: `fix/state-rule-keys` @ `cbf2ede` (range `8bbd9f6..cbf2ede`)

## 1. Verdict

**APPROVE.** The fix commit is docstring + tests only; the required P2-1 pin is delivered and discriminating in the right direction, every P3-1 pin traces correctly through `RULE_RE`/`_STATE_DOTTED`/`_STATE_UNDOTTED`, and the P3-3 dispute holds — my round-1 claim was wrong: the undotted `\s+` does not silently key across a break, it crashes. No new breakage in this diff.

## 2. Round-1 findings

- **P2-1 (`import_ledger` dedup unpinned): ADDRESSED.** `test_ledger_imports_virginia_and_west_virginia_rules` at `tests/test_briefcheck_state_rules.py:164–171` writes a two-row ledger (`Va. R. Civ. P. 12`, `W. Va. R. Civ. P. 12`, both `UNVERIFIED`) and asserts both rows planned with `skipped == []`. This exercises exactly `authority.py:239–244` (`keys & seen` → "duplicate within the ledger"). At `b32d490` the second row's keys (`rule:varcivp12`, `full:wvarcivp12`) intersect `seen`; at `cbf2ede` they don't. The red-step record (fails at `b32d490`, passes at `cbf2ede`) matches what the code predicts.

- **P3-1 (differential-only forms): ADDRESSED — all five pinned.**
  - `W.Va.R.Civ.P. 12` fully glued → `rule:wvarcivp12` (`test_briefcheck_state_rules.py:47`). Traced: `W\.\s?Va` + `\.` + `\s?` + `R\.` + `Civ\.` + `P\.` + `\s*\d+` all accept zero-width whitespace. ✓
  - `N. D.` spaced two-part → `rule:ndrcivp12` (line 50): `N\.\s?[CDHJMY]` matches `N. D`. ✓
  - `Ohio  R.` double space → `rule:ohiorcivp12` (line 51): undotted `\s+` absorbs both spaces; `_norm_key` collapses them. ✓
  - `P.R.`/`V.I.`/`N. Mar. I.` → `prrcivp12`/`vircivp12`/`nmarircivp12` (lines 53–56). For `N. Mar. I.` I verified the backtrack: `N\.\s?[CDHJMY]` matches `N. M` but fails on the required `\.` (next char is `a`), so alternation falls through to `N\.\s?Mar\.\s?I`. ✓
  - `E.D. Va.` keeps `rule:varcivp12` (lines 58–59). Traced: `E.` matches no alternative; `D.` at position 2 is blocked by `(?<![\w.])` (preceded by `.`); `Va.` starts the single match → display `Va. R. Civ. P. 12`. Exactly as asserted. ✓

- **P3-2 (`;` list scans only its head): NOT ADDRESSED — correctly deferred.** Unchanged at `briefcheck.py:347–356`; pre-existing, founder informed.

- **P3-3: the dispute HOLDS — I was wrong.** Round-1 said `"Ohio\n\nR. Civ. P. 12"` keys `rule:ohiorcivp12`. The truth is worse: the generator yields that row once (`briefcheck.py:271`), then `re.match(r"(.*?)\d+(?:\.\d+)?$", m["base"])` at `briefcheck.py:272` returns `None` because `.` can't cross `\n`, and `.group(1)` raises `AttributeError`. Inside `check`, `units()` splits on `\n\n` (`briefcheck.py:893`, `\n(?![ \t]*\n)`), so a blank-line break is unreachable — but a single hard-wrap `\n` is, so `"Ohio\nR. Civ. P. 12"` crashes `check` mid-iteration. The same crash pre-exists at `b32d490` for `"Fed.\nR. Civ. P. 12"` via `Fed\.\s?`. Keeping `\s+` is right (parity with `citations.py:93`; `[ \t]+` would drop hard-wrapped drafts without fixing the pre-existing crash class); the crash fix belongs to `fix/brief-rule-linebreaks@bebaaac`.

- **P3-4 (`_citation_only` folding): NOT ADDRESSED — by design.** Unchanged at `briefcheck.py:918–925`; probe-confirmed consistent with `Fed. R. Civ. P.` behavior.

- **P3-5 (`W.  Va.` two-space, `W. VA.` all-caps): upstream, unchanged.**

- **P3-6 (docstring reach): ADDRESSED.** `briefcheck.py:10` now lists `W. Va. R. Civ. P. 12`; all original examples retained through the rewrap. `test_n5_docstring_documents_every_honoured_key` (`test_briefcheck_review_fixes.py:245–247`) only asserts key names — all still present at `briefcheck.py:21–24`.

## 3. New breakage in the fix diff

**None.** The diff is one docstring rewrap (`briefcheck.py:9–12`) and additive test content. No production code path changes; no assertion contradicts traced behavior; no shared fixture is mutated (`Matter.write` at `test_briefcheck.py:67` writes a fresh `ledger.md` inside each test's own matter dir).

One nit, not breakage: the docstring-only merge conflicts with `fix/brief-false-blocks` and `fix/brief-short-section` recorded in the handoff are real but trivial — resolution is "keep both lists' additions."

## 4. Answers to points 1–3

**(1) Is the P2-1 test discriminating as written?** Yes, in both directions.
- *File shape:* `| Citation | Status |` + `|---|---|` satisfies `parse_ledger`'s header/separator requirement (`authority.py:181–184`); `ci=0`, `si=1`, no Source column.
- *Status mapping:* `UNVERIFIED` → `"unverified"` via `_LEDGER_STATUS`/`_map_status` (`authority.py:163–173`); with no source column `has_source=False` and the row still lands `status="unverified"`, not `None` — so it's planned, not skipped as a non-authority row. UNVERIFIED is the right choice: it's the weakest-status path and avoids the pins/`V-LEAD` confirmed-check machinery, isolating the dedup logic under test.
- *Assertions:* `[r["citation"] for r in planned]` checks both rows in ledger order; `skipped == []` directly catches the "duplicate within the ledger" regression. Signature `import_ledger(conn, matter_dir, ledger_path)` matches `authority.py:218`; `self.m.write("ledger.md", …)` + `os.path.join(self.m.dir, "ledger.md")` agree (`test_briefcheck.py:67–69`).

**(2) Are the P3-1 pins right, and is the E.D. Va. guard worth keeping?** All pinned keys and displays are correct per my traces above. The E.D. Va. guard is **worth keeping** even though "E.D. Va. R. Civ. P." is contrived: it pins the C2 lookbehind contract — that a dotted token ending in `.` before a state can't be absorbed or shadow the state's own match. If `_STATE_DOTTED` ever gains a `E.D./S.D.` alternative or the lookbehind is loosened, this is the test that catches a silent rekey of `Va.` cites. One line, real intent.

**(3) Docstring wording accurate; conflict acceptable?** Accurate — `W. Va. R. Civ. P. 12` is a genuinely resolvable form, and the rewrap keeps every prior example. The docstring-only conflict is acceptable: it's the cheapest conflict class (disjoint additions to an example list, resolved by keeping both). Dropping the line is a legitimate alternative if the founder wants zero-conflict rebases, but I'd keep it — it directly answers P3-6.

## 5. Readiness

Ready to land behind `fix/state-abbrev-cites`, together with `fix/brief-rule-linebreaks`. The newline-`AttributeError` class stays live at `cbf2ede` (reachable on a single `\n` inside a paragraph) — it is inherited, pre-existing at the base for `Fed.\nR.`, and fixed at `bebaaac`; the recorded merge-tree evidence (clean merge, 490 tests OK, zero fuzz crashes) is consistent with the code I traced. Land `state-rule-keys` and `brief-rule-linebreaks` as a pair, and take the "keep both additions" resolution on the two docstring conflicts when rebasing past `brief-false-blocks`/`brief-short-section`.

## 6. Model

SWE-2 Max.
