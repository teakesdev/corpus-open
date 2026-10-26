I'll start by reading the handoff document.Now let me read the round-1 report and the review artifacts in parallel.I have the artifacts. Now let me read the code at 115f0ec — the relevant regions of `briefcheck.py` first.Now the extractor to understand fragment shapes and nested rows.I have the core mechanics. Now the Checker path — `Id.` binding, `full_statutes`, and `id-ambiguous` — to verify the pinned behavior.Let me check the test helpers and the §§ sibling-grammar test referenced in round 1.Let me verify there are no other callers of the changed symbols, and check the `codes`/`Base` helpers.I have complete mechanics. Let me verify a few remaining interaction details before writing the report — the `heads` set usage and whether any non-diff code path could reach the new claim loop.Analysis complete. Writing the report.

**Verdict: APPROVE**

All five round-1 findings are addressed at `115f0ec`. Design B is correctly implemented: the walk passes any ≤24-char parenthetical, members claim number+marks only, and per-member `free()` replaces the all-or-nothing region claim. The only new behavioral cost is the phantom-member pin-page read, which is documented, test-pinned, and founder-approved. No new P1/P2 breakage in `review.diff`.

## Round-1 findings

**P2 — non-mark paren collapses the xref into one junk-keyed cite → ADDRESSED.**
`XREF_TAIL_RE` (`matterkit/briefcheck.py:240–243`) now skips `(?:\s*\([^)]{1,24}\))*` before the separator, so "Section 21-23-7 (Rev. 2020) or 21-23-9" reaches the "or M" tail. `XREF_HEAD_RE` (:239) claims `num + _MARKS` only, and `scan_authorities` (:411–416) claims each member's own `(ma, mb)` span rather than the head-to-last-member region, leaving the paren free for its own cite. This is exactly the §§ walk/claim split I asked for (`LIST_MEMBER_RE` walks parens via `_SEC`, `LISTED_SEC_RE` trims the claim, :141–143, 421–431). Confirmed by `keys-differential.txt`: the eleven junk composites (`statute:section21-23-7(rev2020)or21-23-9`, `statute-short:section21-23-7(42usc1983)or21-23-9`, …) are replaced by the members' own keys plus the paren cite, and no genuine key is lost. Tests pin all three P2 shapes (head paren, lowercase and-form, mid-chain) at `tests/test_briefcheck_paren_cites.py:87–103`, plus the amplifier scenario (logged or-form + later "§ 21-23-7" passes) at :193–196.

**P3 — `_PAREN_TAIL_RE` can't see parens containing "(" → ADDRESSED.**
:144 now uses `[^)]*`, matching the extractor's own paren class. "Miss. Code Ann. § 21-23-9 (550 U.S. 544 (2007)" is cut as one group and the statute keys; pinned at :124–126.

**P3 — C5 broader than its rationale and unpinned → ADDRESSED (reversed).**
"Section 21-23-7 (550 U.S. 544), 21-23-9" now checks 21-23-9 (pinned :105–115, and the unlogged BLOCK at :198–204); "Section 21-23-7, 21-23-9 (Rev. 2020), 21-23-10" checks all three (:100–103, :198–201). The cost — a plain number after a cite paren reads as a member — is pinned at :117–120. Design B is the better trade than my option A, which would have kept a silent drop for the member after a cite paren.

**P3 — display/span trims undocumented → ADDRESSED.**
`_without_parentheticals`' docstring (:148–152) now states a mark after a non-mark is cut ("§ 1983 (Rev. 2019) (a)" → "§ 1983"); "(xviii)" and the ordering case are pinned with unchanged keys at :137–139.

**P3 — test gaps → ADDRESSED.**
All six gaps I named are covered: C5 both ways, the P2 shapes, "(n.3)" (:128–130), a rule in a paren (:132–135), "Id." after a paren cite (:211–216), "(xviii)" (:137–139).

## New breakage in `review.diff` only

**P3 (documented cost, accepted): phantom member after a cite paren on a plain head.** `XREF_TAIL_RE` :240–243 + `member()` :272–273. "Section 1983 (550 U.S. 544), 555" yields "Section 555" → `authority-not-logged` BLOCK. The surface is narrow: the follower must be a plain number (hyphenated/dotted followers fail `plain()`; "555-560" fails the hyphen count and lead; "544 F.2d"-style followers fail the `\s+[A-Z][\w'&]*\.` lookahead at :243), and the head must be plain. Real pin pages are usually *inside* the paren, making this rare. Fail-closed direction; pinned at :117–120 and approved per the handoff.

**P3 (optional polish, non-blocking): doc coverage.** `_xref_members`' docstring (:246–256) describes member shapes but not the paren-skip walk — the rule is documented at :236–238 instead, which is adequate. The `(`-inside-paren behavior lives in the :144 regex comment rather than the docstring; adequate.

No other new issues found. The all-or-nothing → per-member claim change cannot drop checked text: every entry in `spans` accompanies a yielded cite, so a member skipped by `free(ma, mb)` is text already judged under another cite.

## Answers to points 1–6

**1. Design B false passes.** None found. Unclaimed gap text is scanned only through extractor rows; cases/WL/rules always scanned it anyway (passes run before statutes, :341–393). Rows starting in a gap yield real cites — statutes in parens, nested "Sec." xrefs, even the stray second-§ "§ N" row — which is the intended unhiding, and `keys-differential.txt` shows only genuine keys appear. Members can't attach to a wrong head: the walk is anchored at the row's own `XREF_HEAD_RE.match(s, a)` (:257), a member num must start with a digit, and the lookahead rejects "§" and dotted-word followers, so a following "Section 9" or "§ 9" can never be absorbed. Separators alone can't form statute rows. One pre-existing residual (not diff-introduced): a "§§" list *inside* a skipped paren yields only its stray "§ N" row and drops later members — identical at `a6885cb`.

**2. Pin-page cost.** Narrow and acceptable, as bounded above: plain head + immediately-following plain number after a skipped ≤24 paren. Alternative phrasings (ranges, hyphenated numbers, reporter-shaped numbers, "at 555", footnote "n.4") all fail `member()` or the separator. The alternative (stop at cite parens) reintroduces a silent drop — worse for a fail-closed checker.

**3. `[^)]` in `_PAREN_TAIL_RE`.** Correct. It diverges from `[^()]` only on fragments whose trailing paren contains "(", which is precisely the unbalanced shape the extractor produces (its `[^)]{1,24}` requires a closing ")"). `[^)]*` cannot span two groups — it stops at the first ")" — and a group not reaching `\s*$` cannot match, so nothing is over- or under-cut. Bounded; no backtracking risk.

**4. Interaction with `f7119bd` and C3 `taken`.** Clean. `member()` (:272–273) is evaluated on the num after the skip; unchanged. `taken` (:261–262) excludes only rows *starting* inside `[a, b)` — rows in the gap start ≥ the cut `b`, so they remain blockers: a member whose cite span overlaps them still kills the walk → fallback, preserving C3 ("Section 7 or 550 U.S. 544" collapses rather than stealing the case's span). For comma xrefs, bare member numbers produce no extractor rows, so `taken` can't block them; for lowercase and/or xrefs, nested member rows start inside `[a,b)` and are correctly excluded — the nested rows' own re-walks then dedup via `free()`.

**5. Tests.** The 10 red tests are discriminating: I traced each against `a6885cb` semantics — the walk tests fail for absent members, the BLOCK tests fail for absent or junk-keyed cites, the nested-"(" test fails under `[^()]`. The pin-page cost is pinned, not hidden. The `id-ambiguous` pin (:211–216) is the right thing: positional binding can't apply the Bluebook's explanatory-parenthetical rule, so a WARN beats silently binding to the parenthetical's cite.

**6. Readiness.** Ready. `f7119bd` is an ancestor of `115f0ec`; landing on `feat/matter-capture` is a fast-forward. Recorded gate: 653 tests OK on the identical tree; the recorded red step is internally consistent with the code I read.

## The two reported issues

- **"Id." binding to the paren cite.** Pre-existing positional semantics (bind by max `start`, :1220–1222). The cross-sentence case now WARNs `id-ambiguous`, which the test pins. Should not block; worth a follow-up issue for excluding parenthetical sources from Id. binding.
- **`CASE_RE` year-paren hiding an inner statute** ("…550 U.S. 544 (citing 42 U.S.C. § 1983)"). `CASE_RE` (:124–127) is untouched by this diff and predates the branch. Should not block; follow-up candidate.

## Readiness

Ready to land on `feat/matter-capture` as a fast-forward.

## Model

SWE-2 Max (as reported by my runtime).
