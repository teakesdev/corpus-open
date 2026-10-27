I'll review this per the handoff. Reading the diff and the relevant source files in parallel.Diff and tests are in hand. Now reading `briefcheck.py` for full context — the xref machinery, `scan_authorities`, and the `unit` loop.Now the `_Checker.unit` region and the report/severity plumbing.Now checking the supporting pieces: `_SEC_NUM`/`SECTION_LIST_RE`/`RULE_TAIL_RE` in citations.py, all callers of `scan_authorities`, and the report severity/exit-code plumbing.The grep output was truncated — reading the overflow file for the remaining `scan_authorities` callers.Checking the other callers the handoff names — `authority.py:208`, MCP/CLI entry points — plus the report/exit-code semantics.Nearly complete. Verifying the test helpers (`Base`, `codes`), `rule_prefixes`, and the `Finding` dataclass fields.One more check on the extractor side — how `citations.py` delimits fragments for `Section X, Y` vs `§ X, Y`, since that determines where the stop-warning can and cannot fire.The extractor side confirms the fragment boundaries. Now checking the existing list-member tests pinned before this change — anything asserting a member the new `lead` would reject.Let me check the remaining pinned tests that exercise `scan_authorities` — statute_lists and state_abbrev — for member expectations the new `lead` could change.The picture is nearly complete. Checking the review folder for a commit message or other pinned artifacts.One last look at the `check()` entry point to confirm `unit()` is the only authority-scanning path.Analysis complete. Writing the report.

**Verdict: APPROVE**

# SWE-2 review — `fix/brief-list-stops` (`f7119bd..49ac7e2`)

Scope read: `review.diff` (all 271 lines), `matterkit/briefcheck.py` (`_xref_members`, `scan_authorities`, `authority_keys`, `statute_sections`, `Report`, `_Checker.unit`, `resolve_authority`), `matterkit/citations.py` (`_SEC_NUM`, extractor patterns, xref sub-rows), `matterkit/authority.py:208`, `tests/test_briefcheck_list_stops.py` and the pinned neighbor tests (`test_briefcheck_list_members.py`, `test_briefcheck_draft_statute_lists.py`, `test_briefcheck_statute_lists.py`, `test_briefcheck_state_abbrev.py`, `test_briefcheck.py` helpers). Constraints honored: no exec, no git, no writes; files treated as data. The commit message itself was not readable under those constraints; docstrings verified against behavior instead.

## Findings

### P2 — residual silent drop in the `§`-fragment path (pre-existing, out of scope, now conspicuous)

`_xref_members` returns `[]` at the head check (`briefcheck.py:240-242`), so the stop-warning only exists for `Section`-worded lists. A code-led list drops its tail exactly the way this commit set out to make visible:

- `"Miss. Code Ann. § 21-23-7, 11-46 governs."` → one cite (`statute:misscode21-23-7`); `"11-46"` is neither cited nor warned; exit 0. Bare `"11-46"` matches no extractor pattern, so nothing else sees it either.
- `"42 U.S.C. § 1983, 1985 also apply."` → same: `"1985"` silently dropped.

Not a regression — `f7119bd` did the same — and the single-§ comma list is less idiomatic than `§§`. But it is the identical defect class one branch over, and the feature's one-line description ("a section list that stops at a number says so") now reads broader than what ships. Worth a follow-up: either extend `_xref_members`'s head to `§`-fragments, or note the limitation where the WARN is documented.

Adjacent same-class gap in the `§§` path: `SECTION_LIST_RE`/`LIST_MEMBER_RE` (`briefcheck.py:139-141`) truncate at a member their grammar doesn't take — `"Miss. Code Ann. §§ 21-21-3, Appendix A"` silently drops `Appendix A`. Note the deliberate counter-example pinned in `test_briefcheck_draft_statute_lists.py:163`: `"... and 5 others"` *does* become member `§ 5` and BLOCKs — the `§§` path member-izes aggressively where the xref path drops. Third branch of the same class.

### P3 — lettered-lead members at different hyphen depth now WARN instead of being checked

`lead` (`briefcheck.py:249-255`) is `\d+[-.]?`; for `"106A-1"` it yields `"106"` (the letter isn't a separator), so against head `"106-2-3"` (`lead "106-"`) it is not a member — under `f7119bd` (`lead "106" == "106"`) it was checked.

- `"Sections 106-2-3, 106A-1 govern."` → was: `Section 106A-1` member cite, resolved/BLOCKed; now: WARN `list-member-unchecked` on `"106A-1"`.

Defensible — the letter plausibly means a different family, and the WARN's advice ("cite it with its code") is the right fix — and consistent with C1's report-don't-guess. But it is exactly the handoff's challenge case ("a member whose lead is followed by a letter"), it has no pinning test, and it is a real narrowing: a checked member can now degrade to a warning. If a jurisdiction's codes interleave lettered siblings (`"21-23-7, 21A-23"`), they'll warn. Acceptable under D2; pin the intent.

### P3 — test gaps (the suite is discriminating; these are what's missing)

`tests/test_briefcheck_list_stops.py`:

- No test for `"2-15"` after `"2-1-5"` — the handoff asserts it stays a member (`lead "2-" == "2-"`); unverified by suite.
- No test for the lettered-lead case above.
- No test pinning that one WARN stands for the whole unread tail: `"Section 21-23-7, 11-46, 21-23-8"` warns on `"11-46"` and silently drops `"21-23-8"` (a member shape). The message ("a section list ends here") honestly covers it — worth pinning so a future "walk past the stop" change is deliberate.
- `free(a,b)` at `briefcheck.py:1203` is effectively unreachable (a stop span can't overlap a record/pleading claim — a stop requires `Section X,` before the number, which no record/pleading pattern spans). Harmless defense-in-depth; fine untested.
- `TestStoredCitationsUnchanged` overclaims by name: the `stops` opt-in is unchanged, but the `lead` tightening does change stored-cite keys for phantom-member inputs — `authority_keys("Section 552.101, 552-553")` loses `statute-short:section552-553`. Intended (that member was a phantom), but "Unchanged" describes only the plumbing. Rename or pin a case like `"Section 552.101, 552-553"` explicitly.

### P3 — WARN noise surface (as designed; quantify for the record)

A stop fires on `", N"` / `" and N"` after a `Section` head when the number isn't a member, isn't claimed, and isn't followed by a dotted word. Correct prose that warns: counts (`"552 pages"`), vote splits (`"5-4 splits"`), percentages (`"50%"`), `"and 12 others"`, bare-year tails not caught by the lookahead (`", 2024 Term"` — `"1972 Miss. Laws"`, `"42 U.S.C."` are already excluded by `XREF_TAIL_RE`'s `\s+[A-Z][\w'&]*\.` lookahead), en-dash page ranges (`"11–46 pp."`). WARN is the only right severity: nothing can be dismissed, so a guess must keep exit 0. The handoff's 0/366-cite corpus result is consistent; real briefs will produce some of these — that is the cost C1 accepted, and the message is clear and actionable.

### P3 — range connectors the tail can't see

`"Section 21-23-7, 21-23-8–21-23-9"` cites `21-23-8` and silently drops `21-23-9` — the en dash isn't a tail separator and `_SEC_NUM` only takes ASCII `[-.]`. Pre-existing; the WARN can't fire because no tail match occurs. Same class, smallest instance.

## Answers to the handoff's points

**1. False passes / new BLOCKs.** The loop reorder is safe: `f7119bd` evaluated `member()` before `taken`; `49ac7e2` evaluates `taken` in the while condition and `member()` inside. Both orders exit the loop in the same cases — the new order additionally suppresses the stop when the number is claimed (correct per C4). Membership strictly narrows: `lead` is only consulted when hyphen counts differ and plainness matches, and the separator requirement can only reject. Verified members kept: `"21-23"` after `"21-23-7"`, `"34-13-3"` after `"34-13-3-3"`, `"552.102-1"`/`"552.103.5"` after `"552.101"`, `"11-46-11.1"` after `"21-23-7"`, `"2-15"` after `"2-1-5"` (`lead "2-" == "2-"`), `"106A-1"` after `"106-2"` (same hyphen count — `lead` never consulted). Rejected (all → WARN, not silence): `"552-553"` after `"552.101"`, `"5-4"` after `"5.1"`, `"21.23"` after `"21-23-7"`, lettered-lead cases (P3 above). No new invented members → no new BLOCKs. One composite nuance: `"Section 5.1 or 5-4"` was split into two cites before (`"5-4"` a guessed member) and now stays one composite cite — it BLOCKs as `authority-not-logged` either way, visible under both.

**2. The warning.** The condition is right: `t.start("cite") >= b` correctly separates comma-fragment tails (always ≥ b, since the fragment ends at the head) from `"X or Y"` composite tails (always < b, where the composite BLOCKs whole — verified `"Section 21-23-7 or 21-23-8, 11-46"` cites both members and warns on `"11-46"`). WARN is the right severity under D2. Message is clear and actionable. Noise enumerated above. Line attribution (unit's first line) matches all other findings.

**3. Isolation.** Verified completely. `scan_authorities(..., stops=None)` — `authority_keys` (:437), `statute_sections` (:463), `authority.py:208` all call without it; `_Checker._scan_authorities` passes it through and only `unit` (:1194) supplies a list. Generator protocol holds: the authorities loop in `unit` has no `break`/`return` (only `continue`), so the generator is exhausted and `stops` is fully populated before the WARN loop at :1202 runs. `taken` inside `_xref_members` already excludes every extracted row, case, WL, rule and earlier statute span — so a stop can only collide with record/pleading claims, which `free(a,b)` at :1203 covers. The stop span is correctly not added to `claimed`.

**4. Tests.** Fifteen discriminating tests; each maps to a real failure mode (the header table is accurate). Gaps listed in findings. The `TestStoredCitationsUnchanged` pinning of `authority_keys("Section 21-23-7, 11-46")` is a good anchor.

**5. Gaps.** Both docstrings are accurate — `:226-239` correctly documents the separator-inclusive lead, the kept/rejected examples, and the `stops` contract; `:321-323` correctly notes stored citations scan without one. Commit message unverifiable under the no-git constraint; the diff is the pinned artifact.

**View on C1–C4.** C1 is the right call — converting silent drops to WARNs rather than deeper guessing honors D2 and the no-dismiss constraint. C2 is right in substance; the lettered-lead collateral is an acceptable, warn-visible narrowing. C3 is clean — the opt-in collector keeps stored-citation keying byte-identical (the only key changes come from C2's intended phantom removal, not from `stops`). C4 is accurate; the `free(a,b)` guard is nearly unreachable in practice but correct.

## Readiness

Ready to land on `feat/matter-capture`. No new false passes; every path that silently dropped a tail number either still does (pre-existing `§`/`§§`-path cases, out of this commit's scope) or now warns. The P2 item is a follow-up, not a blocker — the commit is strictly better than its base.

## Model

SWE-2 Max, as reported by the runtime system prompt ("powered by SWE-2 Max").
