I'll start by reading the handoff document.Now let me read the review diff and the files under review.Now let me read briefcheck.py, focusing on the changed areas and the resolve/fallback logic.I have the core files. Now let me check what else references `PARSER_ID` / `kit-cite`, look at the test file, and check related docs.Let me check the false-blocks tests (base branch conventions), the cite_v4_gaps test, and the holdout_v1 Florida doc.Let me verify the eval-doc claim by grepping the eval docs for section-word + dotted-number text, and check whether anything else reads `parser`.Let me check the design spec for the intended short-form semantics and the base review's recommendation.Let me get exact line numbers in the new test file for citations.**Verdict: APPROVE WITH CHANGES** — the range correctly implements D1 (dotted "Section N.N" read whole) and D2 (`kit-cite-0.5`), and every new failure direction I found is a visible BLOCK except one real regression: the fix makes the pre-existing dot-stripped suffix fallback newly reachable from the word form, creating fresh false passes on this range's own base (D3). That is fixable by landing order or a ~6-line guard — both spelled out below — so this is not a rewrite-level objection. But the branch must not land on `feat/matter-capture` ahead of `fix/brief-short-section`, or it needs the guard. The handoff's evidence was verified against the code wherever checkable without commands; everything load-bearing traced clean.

## Findings

**P1 — A dotted short section now suffix-binds dot-stripped stored keys: new false passes on this range's own base.**
`matterkit/briefcheck.py:1156` (fallback), with `_norm_key` at `:169-170` stripping dots, `_statute_base` producing `sec` at `:230-233`, and the trigger surface at `matterkit/citations.py:61,117-121` (`_SEC_NUM` dot continuation + cross-ref pattern).

Mechanism: `_norm_key("5.1")` → `"51"`, so the fallback `k.endswith(_norm_key(sec))` treats a dotted draft section and a dot-free or differently-dotted stored section as the same suffix. At `c3402e1` the extractor cut "Section 5.1" to "Section 5", and `endswith("5")` accidentally missed these keys; now `endswith("51")` hits them.

Synthetic inputs → wrong results (each at `fda581a`, logged authority `[source-checked]`, single-cite draft):
- Logged `Tex. Bus. Orgs. Code § 51`; draft `Under Section 5.1, the name must differ.` → cite binds `statute:texbusorgscode51`, WARN only, exit 0. At base: BLOCK. `§ 5.1` ≠ `§ 51` reported as tied to a source.
- Logged `Tex. Bus. Orgs. Code § 15.1`; same draft → binds `statute:texbusorgscode151` (suffix `51`), exit 0.
- Logged `Tex. Gov't Code § 2552.101`; draft `Under Section 552.101, records are closed.` → binds `statute:texgov'tcode2552101`, exit 0.
- Logged `Tex. Gov't Code § 1552.102`; draft `Tex. Gov't Code § 552.101 applies. Sections 552.101 and 552.102 close them.` → member `Section 552.102` binds via the same fallback — the member path is equally exposed.
- `Id.` after any of these inherits the mis-bound target (`:1045-1054`, `_judge_authority` on `base.target`).
- Footnotes and full cites: no new surface — the `§` extraction path (`citations.py:128`) already took dots, and the full-cite `_norm_key` collision is the documented pre-existing issue, unchanged by this range.

Two mitigating facts, honestly stated: (a) the `§` sign form already had this exact weakness at the base (`§ 5.1` bound `§ 51`), so the range reaches parity with a pre-existing hole rather than inventing the mechanism; (b) the new-code direction is otherwise safe — the same change *removes* a real base false pass: `Under Section 552.101, 552 pages were withheld.` at `c3402e1` invented a `Section 552` member (head `552` shape 0 == tail `552` shape 0) and bound `statute:5usc552`, exit 0; the new `shape` check drops the count → BLOCK.

Remedy — either is sufficient:
- **(a) Sequencing (recommended).** Land only after or atomically with `fix/brief-short-section` — design B binds a short-form section only to a section a logged key was made from, which covers every vector above (word form, sign form, members, `Id.`). The handoff's merge evidence (all four drafts BLOCK; 451 tests OK on the merged tree) is consistent with the code I read: every new false pass flows through the one fallback design B replaces.
- **(b) In-range guard.** If this branch must stand alone, confirm the suffix candidate by comparing `sec` to the *stored row's own* section as written, at `briefcheck.py:1156`:
  ```python
  if len(cands) == 1:
      cand = next(iter(cands))
      if any(_statute_base(r["citation"])[1] == sec
             for r in self.auth_index.get(cand) or []):
          c.target = cand
  ```
  `§ 51` stores sec `"51"` ≠ `"5.1"` → BLOCK; `§ 5.1` → bind; `§ 2552.101` ≠ `"552.101"` → BLOCK. This also repairs the pre-existing `§`-form weakness, keeps every existing test green (`test_5_1_binds_5_1` and `test_plain_section_still_binds` bind on exact-`sec` equality), and becomes dead code once design B lands — a cheap, honest interim.

**P2 — The new false-pass class is unpinned by the 25 new tests.**
`tests/test_briefcheck_dotted_section.py:166-186` (`TestDecimalStyleNumbersStayDistinct`) covers 5.1↔5.10 but not the dot-stripped collision 5.1↔51 — the exact input this range makes dangerous. Also missing: a prefix-digit collision (`Section 552.101` vs `§ 2552.101`), a member-level collision (`§ 1552.102`), and `Id.` propagation. Required: at least the two head negatives (`5.1` vs `§ 51` → BLOCK; `552.101` vs `§ 2552.101` → BLOCK) must exist and pass in the merged suite — on this branch alone they fail today (that is the gate); if `fix/brief-short-section` lands first they pass and belong there or here.

**P3-1 — `full_statutes` poisoning via a shape-mismatched or/and composite (false BLOCK direction).**
`briefcheck.py:1162` + `_statute_base` at `:225-236` + `_xref_members` `[]` at `:218-222`. Draft: `Section 552.101 or 552 is unclear. Tex. Gov't Code § 552.101 applies. Section 552.101 governs.` → the whole-frag composite `statute:section552101or552` registers `full_statutes["552.101"]` (setdefault, so ordering matters — the good key loses only if the composite comes first) → the later legit `Section 552.101` binds the composite → BLOCK `authority-not-logged` though the section is logged. Pre-existing mechanism (identical for hyphen shapes at base); dotted heads make the mixed-shape trigger more natural. Safe direction; worth a line or a `sec`-validity check on registration.

**P3-2 — A comma-list silently truncates at a shape-mismatched member.**
`briefcheck.py:218-222`. `Sections 552.101, 552.102, and 552.103.5 closes them.` — extractor frag ends at the head (commas aren't in the cross-ref pattern); members `[552.101, 552.102]` pass `pos >= b` (b = head end), and `, and 552.103.5` is never claimed or judged → exit 0 if both members are logged. The *and/or* form of the same sentence stays whole and BLOCKs loudly (composite key), so the behavior differs by punctuation for the same words. Pre-existing for hyphenated heads; the docstring documents "the list stops," but a WARN on a dropped same-sentence tail would close the asymmetry.

**P3-3 — Cosmetics.** `briefcheck.py:13`: the `quotations` docstring line lost one space of column alignment vs the neighbors. `citations.py:41-43`: the `0.5` comment retro-covers the base's hyphen widening that shipped under `0.4` — fine as a capability statement, slightly revisionist as history. `briefcheck.py:220`: member display is always `Section {cite}` even under a `Sec.` head.

## Answers to the six points

1. **New false passes — CONFIRMED** (P1 above, traced line-by-line through `resolve_authority`). Judgment on remedy: landing after/with `fix/brief-short-section` is enough; an in-range guard is also feasible and small (sketch in P1) and additionally fixes the base's `§`-form hole. I found **no** new pass outside the fallback class: composite or/and frags key as full cites no store holds → BLOCK; ranges stay whole → BLOCK; nested rows are filtered by the `§|…|Section|Sec.` gate at `:338` and suppressed by `free()` after the whole frag claims its span (`:343-348`, stable `char_start` sort puts the whole row first); footnotes have no special path.
2. **Extractor pattern — sound.** `_SEC_NUM` (`citations.py:61`) requires a digit after every separator, so a sentence-final period can't be taken (pinned at test `:83-85`); it can't absorb a following cite's number (continuation is gated on `and|or|through|to` words) or an `of Title N` tail without the optional clause. Decimal-looking strings ("Sec. 107.5", "Section 3.2.2026") read whole — strictly safer than the old truncation, since a longer `sec` can only suffix-match a *longer* stored suffix. `_claimed` (`:160-170`) is safe: statute-range precedes cross-ref in `_PATTERNS`, so "Sections 605.0101 - 605.1108" (verified present at `evals/holdout_v1/docs/fl_stat_605.txt:20`) is claimed by the range pattern and the crossing "Sections 605.0101" match is refused. The nested split (`:200-203`) still yields head+tail rows; its single-paren vs pattern `*`-paren asymmetry is unchanged.
3. **Member splitting — C2 endorsed.** `shape()` (`:214-219`) is the right rule; it eliminates the count-invention false pass the base had. Residuals are P3-1/P3-2 plus one pre-existing word-boundary weakness: a same-shape count is still invented as a member (`Section 552.101, 552.102 pages` → `Section 552.102` member; the lookahead at `:197` only rejects a following capitalized dotted word) — unchanged by this range, and the invented member fails closed (BLOCK if unlogged).
4. **`PARSER_ID` — complete.** Bump at `citations.py:43`, test pin at `test_cite_v4_gaps.py:31`, scope comment accurate; holdout_v3's `kit-cite-0.4` acceptance records correctly left (they describe 0.4 runs). Nothing reads or compares parser IDs: `matter.py:375` prints it, `store.py:55` is the column, `record_extractions` writes it. RFC 0001 §2.2(a)'s "parser + version" is honored — re-extracting a text appends fresh-id rows, so old truncated 0.4 rows and new whole 0.5 rows coexist under distinct labels; `brief check` re-extracts live and reads none of them.
5. **Tests — good, one critical gap** (P2). 25 tests confirmed by count; negatives are discriminating (unlogged lists, target keys, exit codes, the no-`5usc552` assertions); the Florida-range and endpoint-range controls pin C4. Every asserted behavior traced to code paths that produce it.
6. **Gaps.** Beyond P3-3: commit messages were not readable under this run's constraints (diff, code and docstrings are consistent with the handoff's claims); the `_xref_members` and module docstrings are accurate post-edit; nothing the founder's request asked for is missing from the code — D1's eval-comparison step is a process requirement reported in evidence (56 docs / 570 rows identical), which is consistent with what the code permits (the only reachable behavioral deltas are `Section|Sec.` + dotted numbers, and the sole such string in the eval corpus is range-claimed).

**C1–C5:** C1 endorsed — as-written comparison is the only defensible choice and both directions are pinned. C2 endorsed — the shape rule is exactly what removes the base's count-invention false pass; the truncation cost is documented. C3 endorsed — sharing `citations._SEC_NUM` makes the two modules agree by construction; the private-name reuse is worth it and commented. C4 sound — verified against `_claimed` ordering and the real holdout text. C5 endorsed with the P1 caveat — deferring to `fix/brief-short-section` is right *if* sequencing holds; the ~6-line equality check is the alternative.

## Readiness

**Not ready to land on `feat/matter-capture` as an independent commit.** Required before landing, in one of two shapes:

- **Path A (recommended):** land `fix/brief-false-blocks` first (it is this range's base), then `fix/brief-short-section`, then this branch; ensure the P2 negatives exist and pass in the merged suite.
- **Path B:** apply the P1(b) guard at `briefcheck.py:1156` and add the P2 negatives to `test_briefcheck_dotted_section.py` now; then the branch can land in any order, and `fix/brief-short-section` removes the guard's need later.

The `scan_authorities` merge conflicts the handoff reports against `6ac2b01` lie between the base and `6ac2b01` — this range's hunks are at `:8-13`, `:188-236` and `citations.py` and do not touch `scan_authorities`'s body. The reported red-step (20 failures + 1 error → green) and the eval-extraction delta claim are consistent with what the code permits; I could not re-run them under this run's constraints, so the gate numbers are taken as reported.

## Model

SWE-2 Max (Devin, Cognition).
