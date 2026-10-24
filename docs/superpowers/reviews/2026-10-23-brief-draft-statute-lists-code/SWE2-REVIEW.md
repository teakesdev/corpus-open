I'll start by reading the handoff document.I have the handoff. Now let me read the review diff and the differential files, plus locate the code files under review.Now let me read the keys-differential evidence files and the main code file.Now I'll read the main file under review. Let me look at the key regions: the regexes (~133-135), `_statute_base` (~182), `scan_authorities` (~240-320), and `authority_keys` (~319-340).Now let me read the `_Checker.unit` and `resolve_authority` sections, plus the citations module.Let me now read `citations.py` to understand `extract_citations` and `_looks_complete`, plus the earlier part of `_Checker`.Now let me read `authority.py` (`find`, `import_ledger`, pin pick ~208) and the rest of `briefcheck.py` around `_Checker.__init__` (`full_statutes`, `line`, `units`).Let me check the existing statute-list tests (unchanged, must stay green), the `Base` helper, and where `scan_authorities`/`authority_keys` are used elsewhere.Let me read the rule-lists test, the relevant spec doc, and check `test_briefcheck.py` header for the fixture data (stored "42 U.S.C. § 1983" etc.).Let me check the spec doc for the list requirements and any wording on line numbers/pins.Let me verify all `scan_authorities`/`authority_keys` call sites across Python files only, and check `ingest`/`subcase` for any extraction usage.I have now traced the full code path. Let me do a final verification of a couple of subtle regex behaviors before writing the report — checking `_SEC`'s parenthetical clause and confirming line numbers for citations.All analysis complete. Writing the report.

**VERDICT: REQUEST CHANGES**

The core design is sound — one `§§`-list code path serving stored keys and draft scan, offset-preserving `§§`→`§ ` substitution, per-member spans, and the `heads` stop rule that fixes `3b4906c`'s comma-joined run-on. But the member-splitting loop introduces a regression that silently hides citations on plausible input, which is a false pass — the property this tool exists to prevent — plus one narrower hidden-cite edge. I would not land it as-is.

---

## Findings

### P1 — A `§§` member's `(...)` tail can swallow an independent citation (hidden cite / false pass)

**Where:** `matterkit/briefcheck.py:306–316`, enabled by `_SEC`'s `(?:\s*\([^)]{1,24}\))*` clause at `matterkit/briefcheck.py:133`.

**Mechanism.** A list member is the raw `_SEC` text, which includes up to 24-char parentheticals. Its claimed span is `[start, start + len(part))` (line 312–314). The `heads` break (line 310) only inspects the member's *start*, so a citation that begins *inside* a member's paren is not seen. Two failure shapes:

1. *Statute follower in parens.* Draft `… Miss. Code Ann. §§ 21-21-3, 21-3-15 (42 U.S.C. § 1983).` — the last member is `"21-3-15 (42 U.S.C. § 1983)"` (`"(42 U.S.C. § 1983)"` inner text is 16 chars ≤ 24). Its span claims the whole paren; the extractor's `"42 U.S.C. § 1983"` row is later `free()`-blocked at line 313 and never yielded. `42 U.S.C. § 1983` escapes `authority-not-logged` entirely. With only `MISS_LIST` stored, the draft exits 0 — a real cite unchecked. Also the member's display becomes `"Miss. Code Ann. § 21-3-15 (42 U.S.C. § 1983)"` (line 316), polluting the Authorities table.
2. *Case/WL follower in parens.* `… §§ 21-21-3, 21-3-15 (550 U.S. 544)` or `(2019 WL 1234567)` — the earlier `CASE_RE`/`WL_RE` passes claim the inner cite first, so the member's own span fails `free()` and the member `21-3-15` is dropped entirely. The cited section vanishes from the check; again a silent pass for that section (the case cite itself is still checked).

**Regression evidence.** At `c63d392` the draft scan yielded the paren cite: the outer `"§§"` row was `extraction-failed` and claimed no span, and the members were never yielded as cites, so `"42 U.S.C. § 1983"`/`"550 U.S. 544"` stood on their own rows. The new member spans are what hides them.

**Trigger realism.** `"(42 U.S.C. § 1983)"`, `"(see 42 U.S.C. § 1983)"` (20), `"(e.g., 28 U.S.C. § 1331)"` (21), `"(citing 42 U.S.C. § 1983)"` (23) all fit the ≤24 window — a bare parenthetical cite or short signal attached to a list's last (or any) member.

**Fix direction.** When a `heads` offset falls inside a member's `(...)` tail, cut the member's `part`/span before the enclosing `(` — the key is unchanged because `_statute_base` strips trailing parens anyway — so the inner cite's row stays `free()` and yields normally. Cheap localized fix inside the line 306–316 loop.

### P2 — `heads` break drops a legitimate member that starts a non-statute extractor row

**Where:** `matterkit/briefcheck.py:310` (`if i and start in heads: break`) and `matterkit/briefcheck.py:292` (`heads` built from *every* extractor row).

**Mechanism.** `heads` includes row starts the statute pass itself never yields — e.g. the extractor's statute-range pattern `\d+\.\d{3,5}\s*[-–—]\s*\d+\.\d{3,5}`, whose fragment fails the keyword filter at line 295 and is skipped. Draft `Fla. Stat. §§ 605.0101, 605.0102 - 605.1108` → the `605.0102 - 605.1108` row starts exactly at member `605.0102` → break → `605.0102` never yields *and* nothing else yields it. Two consequences:

- **Draft side (hidden cite):** the section is silently unchecked; if unlogged, no `authority-not-logged` fires.
- **Stored side (false block, regression vs base):** `c63d392`'s `authority_keys` loop keyed every `secs` member with no `heads` rule, so `statute:flastat6050102` existed; now it doesn't, so a draft `Fla. Stat. § 605.0102` BLOCKs against a stored list that names it. The differential corpus missed this class — it contains no list member that starts a non-statute extractor row.

**Fix direction.** Restrict `heads` to rows that look like *independent* cites (e.g. fragment contains a letter or `§`; a bare `N.NNN - N.NNN` range row has neither), or keep the member when its own span is `free()` and the head row is not claimable by any pass. If deliberately left as a known limit, add it to C7 with a pinned test.

### P3 — Test coverage gaps

`tests/test_briefcheck_draft_statute_lists.py` discriminates well for D1/D3/C2–C6, but:

- No case for a `(...)`-attached follower (would have caught P1) or a `… - …` range member (P2).
- The `af0a7dd` run-on fix is pinned only for a *statute* follower; no draft-side test pins a list followed by `, <case cite>` or `, <WL cite>` (those exist only in the differential evidence, not the suite).
- No test for two `§§` lists in one unit, a list across a line break, `et seq.`, or C7's `"§§ 21-21-3, 21-3-15 and 5 others"` spurious-member negative (a fail-safe worth pinning, not just a comment).
- No `authority-duplicate` interplay test (stored list + stored single sharing a section key now warns on draft lists).

### P3 — `3b4906c` commit message claims stored keys unchanged

False for `", <cite>"` inputs (the `keys-differential.txt` bogus keys: `statute:misscodeann42`, `statute:42usc28`, etc.). Documented in the handoff and `af0a7dd`'s work, but history still carries the claim; worth a line in the landing note.

### P3 — Minor cosmetic

For a letter-bearing later member (e.g. `…, 1985a`), `u.text[a:b]` contains `[A-Za-z]` so the `raw` heuristic at `matterkit/briefcheck.py:991` shows `"1985a"` rather than the reconstructed `"42 U.S.C. § 1985a"` — consistent with the existing `", and 15"` convention; cosmetic only.

---

## Answers to the handoff's points 1–6

**1. False passes.** The serious vector is P1: `heads` checks only member *starts* and cannot see a cite inside a member's paren tail; and a member's own span can be killed by an earlier-claimed (case/WL) paren cite, silently dropping the member. Everything else I traced is safe: separators (`,` `&` `and` `or`, `, and`) split cleanly and identically between match and `re.split`; a following cite the extractor can't row is absorbed as a spurious member → bogus key → **BLOCK (fail-safe, C7's documented class)**; nested extractor rows are suppressed by char-start order + `free()`; two `§§` lists in one unit expand independently via their own rows; a `§§` inside a row's span is always that row's own list (extractor frags end at the first section, and `_claimed` forbids crossing rows). One asymmetry to note: the stored/draft differential cannot see P1 at all — it is a *span-claim* bug, not a key bug; `authority_keys` on the same input produces identical keys at base and at `af0a7dd`.

**2. Stored keys (C1).** Moving expansion into `scan_authorities` is sound — one path, and I verified each consumer: `authority.find` (same key-intersection semantics; the dropped bogus keys were latent false matches there too — `find("42 U.S.C. § 28")` could hit `§§ 1983 and 1988, 28 U.S.C. § 1331` at base), `import_ledger` dedup (same `keys & seen` logic; dropping bogus keys fixes the symmetric false-duplicate), the `;` rule-continuation pass (list members' starts stay inside their segment; `inside[-1]` is still a `statute:` key so `prefix=None` exactly as at base — e.g. `"…; 11"` after a list stays unkeyed on both sides), and the pin pick at `authority.py:208` (statute pins are `None` before and after). Dropping the bogus keys is right — they produced real false passes in both `find` and brief check. The differential is convincing *for its input class*; it misses (a) members that start non-statute extractor rows (P2), (b) paren-embedded cites (P1, invisible at key level), (c) cite families the extractor doesn't row (absorbed → spurious member → BLOCK, safe direction).

**3. Brief-check behaviour (D3).** Traced end-to-end: `_authority_index` keys each stored member; a draft list yields the same keys → pass; single-stored → the unlogged member BLOCKs with the unit's line and the reconstructed cite text (the `raw` fallback at line 991 gives `"Miss. Code Ann. § 21-3-15"` for the digit-only member — good). `full_statutes` is filled per-section via `setdefault` — a later `§ 21-3-15` binds correctly (test-pinned). `short-form-without-full-cite` paths unchanged. `Id.` after a list binds to the last member by `max(start)` — semantically correct. Two `AuthorityUse` rows sharing one `store_citation` is faithful (two distinct keys resolving to one source row) and test-pinned — acceptable. `authority-duplicate` can now fire on draft *list* cites where a stored list and stored single share a section key — same warning a draft single cite already produced at base; defensible, deduped via `_dup_warned`.

**4. Spans and display (C2–C4, C8).** First-section span `[a, hi0)` covering code name + `§§` + section keeps the draft's `§§` text as `raw` — right. Member spans cover own text; `raw` falls back to display for digit-only members — finding text reads as the full single cite — right. Subsection parens inside the member's span is the correct intent *and* the P1 attack surface. Trailing `.` stripping is correct (`rstrip` only removes the final dot; `pos` uses the un-stripped length — verified the tuple ordering). A list across a line break works (`\s` in `_SEC_SEP`); the finding lands on the unit's start line. On C8: D1 does **not** need per-cite line numbers — every finding kind (record, case, quote) is unit-grained, and statute `pin=None` is by design; the spans' real jobs (free claims, `Id.` binding, cite order, `raw`) are all implemented.

**5. Tests.** The 16 tests pin D1 (per-section yields, spans, display, sec), D3 (stored-list pass, unlogged-member BLOCK with exact line/cite, cite-after-list), and C2–C6 (subsection spans, display form, heads-break, no-code short forms, stored-key symmetry). Negatives are discriminating (neighbouring section, code-name-doesn't-carry). Missing cases listed under P3 above — the two most important being the paren-follower and the range member.

**6. Gaps.** Docstrings/comments are accurate (287–288, 307, 310, 326–328). The `3b4906c` "unchanged" claim is handled above. On the `fix/brief-false-blocks` conflict: this shape is a **sound base** for `_ANNOTATED_HEAD` — the extension pulls `a` back over an `<…> Annotated` head, and since `code = s[a:lst.start()]` it flows to every member key automatically, applied once rather than per-member. That is arguably a *better* base for that fix than `c63d392`'s split loop was.

### On C1–C8

- **C1** justified — single source of truth; the only stored-side changes are the bogus-key drops plus the P2 range-member class (unintended).
- **C2–C5** correct as described and verified by trace.
- **C6** correct for every extractor-rowed following cite; two blind spots: non-rowed cite families (absorbed → spurious member → BLOCK — fail-safe, belongs in C7) and non-statute numeric rows (P2).
- **C7** should grow two entries: `;`-joined members are unkeyed, and spaced-dash range members break the list (P2, if not fixed).
- **C8** right — D1 is satisfied; per-cite lines are not what D1 needs.

## Readiness

Not ready as-is: P1 is a false-pass regression on realistic brief text and is a small, localized fix. The recorded gate (`unittest discover` 401 OK at `af0a7dd`; merge-tree with `feat/matter-capture` clean, 413 OK) supports a mechanically clean land on the target once P1 is addressed. The live conflicts are with `fix/brief-false-blocks` and `fix/brief-short-section` in the same statute-pass hunk — whoever lands second rebases; this shape gives the false-blocks `_ANNOTATED_HEAD` work a better base (extend `a`, `code` follows). One scope note: `matter.py`/`matter-mcp.py` ingestion still calls `extract_citations` on raw text, so stored-document `§§` rows remain `extraction-failed` — unchanged and consistent with the extractor's `experimental` status, but the fix is briefcheck-local only.

## Model

SWE-2 Max (as reported by my runtime).

---

*Reviewed `c63d392..af0a7dd` via `review.diff`, `matterkit/briefcheck.py`, `matterkit/citations.py`, `matterkit/authority.py`, the new and existing list tests, and `keys-differential.{py,txt}`. Read-only; no code executed — all behaviors traced from source. Evidence for the recorded test runs and the base-commit behavior is taken from the handoff and is consistent with the code.*
