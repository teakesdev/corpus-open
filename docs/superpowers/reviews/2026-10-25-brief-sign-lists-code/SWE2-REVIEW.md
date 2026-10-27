I'll start by reading the review diff and the code under review.Now let me read the rest of `scan_authorities` and the related functions.Now let me look at the checking logic — how `stops` become findings, and how short-form `§ N` cites bind to full statute keys.Let me check the test base fixture and the project's Python version requirements.The `SIGN_HEAD_RE` uses `\Z` with `search(s, a, b)` — I need to verify whether `\Z` respects `endpos` across Python versions.Let me verify the specific `\Z`/`endpos` interaction on older Python versions.Now let me check how `authority.find` and the ledger use `authority_keys`, and look at any other callers of `_xref_members` or `scan_authorities`.The SWE2-REVIEW.md placeholder confirms the launcher is capturing this stream. Now a few remaining checks: `_sentence_spans` (does it split a "§ N, M" list across units?), exit-code semantics for WARN, and `rule_prefixes`.Let me check the existing list-stops and list-members test files to verify the digit-guard change doesn't conflict with pinned behavior.Let me check the other pinned tests that exercise plain-head "Section" lists — the digit guard could change their expectations.The mechanism is clear. Let me check a few more pinned suites for behaviors the digit-guard or sign-head could change — paren cites, review fixes, and short-section tests.Two more test files to verify pinned behavior (`test_briefcheck_review_fixes.py`, `test_cite_state_ann_heads.py`), plus `rule_linebreaks`.I have full context now. Let me do a final mechanical re-verification of two subtle points in the diff before writing — the `taken`/`free` dedup path for the nested `§` row, and the `heads`/`lst` interplay — both confirmed by tracing. Writing the report.

**Verdict: APPROVE**

# SWE-2 review — `fix/brief-sign-lists` (`53f82ec..c6c1676`)

Scope read: `review.diff` (all 248 lines), `matterkit/briefcheck.py` (`SIGN_HEAD_RE`, `_xref_members`, `scan_authorities`, `_statute_base`, `authority_keys`, `statute_sections`, `Report`, `_Checker.unit`, `resolve_authority`, `_sentence_spans`), `matterkit/citations.py` (`_SEC_NUM`, `_PATTERNS`, `extract_citations`, xref sub-rows), `matterkit/authority.py` (`find`, `import_ledger`, `parse_ledger`), `tests/test_briefcheck_sign_lists.py`, and the pinned neighbors: `test_briefcheck_list_stops.py`, `test_briefcheck_list_members.py`, `test_briefcheck_statute_lists.py`, `test_briefcheck_draft_statute_lists.py`, `test_briefcheck_paren_cites.py`, `test_briefcheck_short_section.py`, `test_cite_state_ann_heads.py`, `test_briefcheck_review_fixes.py`, `test_briefcheck.py` helpers. Constraints honored: no exec, no git, no writes, nothing outside this worktree. All behavior claims below are from static tracing, not runs.

## Findings

### P3 — HTML-entity section signs don't head a list

`SIGN_HEAD_RE` (`briefcheck.py:242`) matches a literal `§` only, but the extractor accepts `&sect;`, `&#x00A7;`, `&#167;` (`citations.py:144-151`) and produces fragments like `"&sect; 1983"`. For `"&sect; 1983, 1985 also apply."` (a draft pasted from HTML), the head check fails, `_xref_members` returns `[]`, and `"1985"` is silently dropped — exactly the D1 defect, surviving on the entity surface. Same class as the P2 this branch answers, narrower reach. Not a regression; a follow-up could widen the head alternation (the `code`/member/display machinery needs no other change).

### P3 — the disclosed residual (C5): plain counts inside the ±1-digit band still member-ize and BLOCK

`member()` at `briefcheck.py:289-294` admits a plain member within one digit of a plain head's length, so:

- `"Under § 1983, 200 plaintiffs sued."` → member `"§ 200"` → `authority-not-logged` BLOCK, exit 1.
- `"§ 1983, 2019 saw amendments."` → `"§ 2019"` BLOCK.
- `"Section 502, 12 plaintiffs moved."` → `"Section 12"` BLOCK.

The handoff's C5 wording ("3 or 4 digits after a 4-digit head") understates it slightly: the band is `abs(digits(num) - digits(h)) <= 1`, so a 2-digit count after a 3-digit head or a 5-digit count after a 4-digit head collides too. This is the same guess-that-BLOCKs the base retains and pins for `Section` lists (`test_briefcheck_paren_cites.py:117-120` — `"Section 1983 (550 U.S. 544), 555"` member-izes `"555"`). For `§` heads it is a *new* exposure (base dropped the tail silently, exit 0) — unavoidable if members are to be checked at all, since `"§ 1983, 1985"` and `"§ 1983, 200"` are indistinguishable without deeper NLP; a following-lowercase-word heuristic would misfire on `"1985 also apply"`. Disclosed, bounded, corpus-clean. If ty wants it narrower, the only honest lever is the band width (which already costs "Sections 9, 100"); I would not gate landing on it.

### P3 — new WARN surface on prose after a `§` cite (intended; quantify for the record)

Any `", N"` / `" and N"` / `" or N"` after a `§`-headed fragment that isn't member-shaped now warns `list-member-unchecked` (exit 0): counts (`"§ 1983, 12 plaintiffs"`), short counts with `and`/`or` (`"§ 1983 and 5 records"`, `"Section 552, 5 records"` — which was an invented `Section 5` BLOCK at base), vote splits (`"§ 1983, 5-4 decisions"`), and real members crossing two magnitudes (`"§ 2 and 105"` → WARN `"105"`, while the extractor-composite `"Sections 2 and 105"` still splits — the inside-fragment exemption at `briefcheck.py:299-301` makes the asymmetry). Every case lands on WARN, the safe side per D2 — but the message ("cite it with its code") is only sometimes actionable, since most of these are prose, not miscited sections.

### P3 — members followed by an undotted capitalized word still member-ize

`XREF_TAIL_RE`'s lookahead (`briefcheck.py:246`) only excludes a *dotted* capitalized word (`\s+[A-Z][\w'&]*\.`), so `"§ 1983, 2000 AMC 1"` reads `"2000"` as member `"§ 2000"` (BLOCK) and orphans `"AMC 1"`; same for `"4 Veterans L. Rev."`-style reporters. This blind spot is identical for `Section` lists at the base — not a regression, same narrow class as the C5 residual.

### P3 — test gaps and a doc nit

- No test pins a parenthetical between a `§` head and its member: `"42 U.S.C. § 1983 (2018), 1985"` (mark kept), `"§ 1983 (550 U.S. 544), 1985"` (non-mark cut, cite stays free), `"Miss. Code Ann. § 21-23-7 (Rev. 2019), 21-23-8"` — all traced correct via the tail's `[^)]{1,24}` consumption, but unpinned.
- No test pins the `_ANNOTATED_HEAD` path: `"Miss. Code Annotated § 21-23-7, 21-23-8"` → member keys `statute:misscode21-23-8` via `_ANNOTATED.sub` — traced correct.
- C5 residual and the `"§ 2 and 105"` drop are unpinned (pinning known-odd behavior is a judgment call; noting so the choice is deliberate).
- `scan_authorities`' docstring (`briefcheck.py:362-364`) still says only `"a coordinated 'Section X, Y' list"` for `stops` — now also `§`-led lists.

## Answers to the handoff's points

**1. False passes (most important).** I could not construct one. Every handoff scenario resolves correctly:

- `"42 U.S.C. § 1983, 28 U.S.C. § 1331"`, `"15 U.S.C. § 78j(b), 17 C.F.R. § 240.10b-5"`, `"§ 1983, 550 U.S. 544"`, `"§ 1983, 17 Stat. 13"`, `"§ 1983, 1985 U.S. 1"` — the `\s+[A-Z][\w'&]*\.` lookahead kills the tail before `member()` runs; where a cite doesn't carry a dot the member's cite span is in `taken` (the extracted row starting outside the fragment, e.g. `"17 C.F.R. § 5"`, or the case/WL span in `spans`), so the while-condition exits — no member, no stop, no swallow.
- `"§ 1983, Id. at 5"`, `"§ 1983, Fed. R. Civ. P. 12"` — `num` requires a digit; no match, no stop.
- `code` can never carry non-code text: it is `s[a:head.start()]`, strictly inside one extractor fragment, and every fragment ending in `"§ N"` is a code-named cite by construction (`42 U.S.C. `, `Miss. Code Ann. `, `R.S. `, `8 Del. C. `, `Fla. Stat. `, `17 C.F.R. `, `N.Y. Bus. Corp. Law `, `W. Va. Code Ann. ` …) or `""` for a short form. Signals like `"See"` are never inside a fragment. The `_ANNOTATED_HEAD` expansion (`:436-437`) supplies `"Miss. Code Annotated "` — a real code name, correctly keyed via `_ANNOTATED.sub`.
- A member gets the head's code only when it matches the head's shape (hyphen count or `lead`) — `"Miss. Code Ann. § 21-23-7, 11-46"` stops rather than mis-keying (`"Miss. Code Ann. § 11-46"` is what the drafter plausibly meant, but WARN is the honest answer per the member rule; test pinned). `"§ 1983, 1331"` keys `statute:42usc1331` — the standard reading of a bare number after a `§` cite.

**2. False BLOCKs / the C2 guard.** The ±1-digit band is a reasonable guard: it converts the base's worst false-BLOCK (`"Section 552, 5 records"` → invented `Section 5`, exit 1) into a WARN while keeping pinned real members (`"Sections 9, 10, and 11"`, `"Section 1983 (…), 555"`). Costs: the C5 residual (P3 above — it does still BLOCK on a guess, in tension with D2's letter, but it is the same residual the founder's suite already pins for `Section` lists, narrowed by the band) and real members crossing two magnitudes now WARN instead of checking (`"Sections 9, 100"`; `"§ 2 and 105"` vs the pinned composite). Both land on the visible side.

**3. Interaction with `53f82ec`'s parenthetical walk.** Correct on all three handoff examples: `"42 U.S.C. § 1983 (2018), 1985"` keeps `(2018)` as a `_MARKS` mark and walks to `§ 1985`; `"§ 1983 (550 U.S. 544), 1985"` trims via `_without_parentheticals`, the tail's paren-prefix passes the case, `§ 1985` members, `550 U.S. 544` stays a free case cite; `"Miss. Code Ann. § 21-23-7 (Rev. 2019), 21-23-8"` same shape, member keys right. `taken` correctly excludes the extractor's inside-fragment rows (the nested `"§ 1983"`) while including outside ones; the per-member claim is the cite span only, so parenthetical cites stay free. One asymmetry worth noting: `"§ 21-23-7 or 11-46"` warns on `"11-46"` and checks the head (exit 0), while the composite `"Section 21-23-7 or 11-46"` still BLOCKs whole — both visible, different severities; defensible since the `§` head is independently a cite.

**4. C3 and C4.** `stops if free(a, b) else None` (`:443`) is exactly right: the nested `"§ 1983"` row's span is contained in the outer row's head-member claim `(a, head.end())` — rows sort by `char_start` so the outer always yields first — hence `free` fails, and both walks are byte-identical (same `SIGN_HEAD_RE` hit at the same `§`), so nothing the nested row could produce is free anyway. The gate suppresses stops for any partly-claimed fragment — conservative (at most a lost duplicate WARN), and pinned implicitly by `test_count_after_a_sign_head` (`["12"]`, not `["12","12"]`). C4 is consistent with `§§`-list semantics since `c63d392`: `authority_keys("42 U.S.C. § 1983, 1985")` ⊇ `{statute:42usc1983, statute:42usc1985}` plus the `full:` key, `statute_sections` records each, `authority.find` resolves a member to the list row, and a ledger row listing a member separately triggers `authority-duplicate` — same as the pinned `test_list_row_and_single_row_for_one_section_warn_duplicate`.

**5. Tests and gaps.** 16 discriminating tests; the header table is accurate; `SignBase`'s fixture additions are exactly what the assertions need. Docstrings at `:249-267` and comments at `:239-242`, `:441-442` are accurate — the one drift is `scan_authorities`'s own docstring still saying `"Section X, Y"`. Gaps listed above.

**View on C1–C5.** C1 is sound: the `search(s, a, b)` + `\Z` + `(?<!§)` head is the minimal correct shape (endpos semantics confirmed — `search(s, a, b)` behaves as if the string ends at `b`), and `code`-carrying mirrors the `§§` path's `f"{code}§ {part}"` display exactly. C2 is the right direction of trade; the band is a disclosed approximation, and putting it *only* after the fragment correctly preserves the composite exemption. C3 is clean and verified (the `free(a,b)` gate exists because the nested row's would-be stop is never pre-claimed — unlike the `sections 502` composite case the base deduped via `taken`). C4 is right for the ledger and `find`, with the duplicate consequence identical to `§§` lists. C5 is honestly disclosed; I recorded it as P3 rather than a blocker because it is the base's own pinned residual shape, applied to a new head.

## Readiness

Ready to land on `feat/matter-capture`. The fix closes the reviewed P2 (silent `§`-list tails) without introducing new false passes, keeps every pinned `Section`-list/`§§`-list behavior I could trace, and the new failure modes are all on the WARN side except the disclosed C5 residual.

## Model

SWE-2 Max, as reported by the runtime system prompt ("powered by SWE-2 Max").
