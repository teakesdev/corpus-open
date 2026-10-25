I'll start by reading the handoff file that defines this re-review.Now let me read the round-1 review and the fix diff in parallel.Now let me read the working files at e77da0c. Starting with `briefcheck.py` — the regexes, `_heading`, and related helpers.Continuing through the file — `units()`, `unit()`, `quotes()`, `facts()`, `check()`, and `Report`.Reading the rest of the file — `_Checker`, `units()`, `unit()`, `quotes()`, `facts()`, `check()`.Now the test file to see the Base fixture and full test suite.Let me look at the `Base` fixture and `codes` helper in the main test file.The core fix is in `briefcheck.py:364–413`. Now let me verify a few details: call sites of `_heading` and `summary`, the MCP error path, and whether `citations.extract_citations` would pull a statute cite without a `§` (affects whether `_carries_cite`'s pattern list has a gap).`citations.py:74` confirms `42 U.S.C. 1983` (no §) is a checkable statute cite — but `_carries_cite` wouldn't catch it. Let me check the MCP error path and the CLI `--out`/summary path.Let me verify the review folder contents and check `git log`-equivalent info — actually, no git/shell allowed. Let me list the review directories via glob, and double-check one edge: whether `_carries_cite`'s pattern list covers the rule/statute patterns that `scan_authorities` handles.One more check — the PROMPT.txt in the review folder for any additional scope instructions.I have everything needed. I've traced `_heading`, `_carries_cite`, `_title_case`, `units`, `unit`, `quotes`, `facts`, `Report.summary`, the CLI/MCP plumbing, the test fixture, and `citations.py`'s extraction patterns. Writing the report.

**Verdict: APPROVE WITH CHANGES**

Every round-1 finding is addressed, the fix mechanism is sound, and the test additions pin the right behavior. One new coverage hole in the fix diff — the `_carries_cite` veto does not cover rule cites or statutes without a section sign, so a numbered title-case heading ending in `.`/`:` can again hide an unchecked authority — plus one narrow fact-tracking regression and a residual README over-claim. None of it blocks the merge; it can stay on `feat/matter-capture`.

## Round-1 findings

### ADDRESSED — P2: short numbered lines with a cite, quote or parenthesis swallowed as headings
`briefcheck.py:374–377`, `:403–405`. `_carries_cite(line)` vetoes a numbered heading on `RECORD_RE`, `PLEADING_RE`, `CASE_RE`, `SHORT_RE`, `WL_RE`, `QUOTE_RE`, `ID_RE`, or a literal `§`, and runs before the punctuation/title-case check (line 404 precedes 406–408), so precedence is correct. Every round-1 shape now reads as text: `"12. See Doc. 99 at 3"` and `"105. See Doc. 99 at 3"` (the 100–999 regression window included), `"5. Statement (Doc. 99)"`, and `'3. The captain said "stand down"'`, which reaches `notes` in complaint mode and `quote-uncited` in brief mode (`briefcheck.py:1207–1211`). Pinned by `test_p2_short_numbered_lines_with_checkable_content_are_text` (both modes, 2- and 3-digit, paren-ended, and `"1. Doc. 99."`) and `test_p2_quote_ended_numbered_line_reaches_the_quote_checks`.

### ADDRESSED — P2: punctuated numbered headings demoted, brief-mode fact sections never start
`briefcheck.py:406–409`. Solved differently than my suggested mode switch but equivalently for the failing case: a numbered line ending in `.` or `:` with no comma and `_title_case(words[1:])` is a heading again, so `"1. Statement of Facts."` → level 3 → `FACT_HEADING` match → `fact_level` set → the following uncited sentence gets `uncited-fact` (`:918–924`, `:1090–1092`). Pinned by `test_p2_punctuated_title_case_numbered_heading_starts_fact_section_in_brief_mode` for `.`-ended, `:`-ended, and unpunctuated headings. Sentence-case numbered paragraphs still demote (`test_sentence_numbered_paragraph_is_still_text`).

### ADDRESSED — P3: summary omits listed quotations
`briefcheck.py:791–796`. `"; N alleged quotation(s) listed for the pleader to confirm"` is appended when `mode == "complaint" and self.notes`; singular/plural handled via `{'s' if k != 1 else ''}`. Both summary-only surfaces pick it up: the `--out` console line (`matter.py:290`) and the MCP first line (`matter-mcp.py:104`). Pinned by `test_p3_summary_mentions_listed_quotations`.

### ADDRESSED — P3: letter tokens
`briefcheck.py:403`, `:409–412`. In complaint mode, lettered lines take the numbered rule (`mode == "complaint" and not roman`, returning level 2 at line 409), so `"A. Plaintiff lives in Exampleton. Doc. 99."` is text and checked. In brief mode, lettered — and now roman — lines get a narrower `_carries_cite(line, quotes=False)` veto covering record, pleading, case, short-form and WL cites (the roman veto is new hardening beyond what round 1 asked). Residual, by design per the docstring (`:386–387`): a brief-mode lettered or roman line carrying *only* a quotation, `Id.`, or a rule/§-statute cite still stays a heading — see new-breakage P3 and answer 3. Pinned by `test_p3_letter_numbered_allegation_with_cite_is_checked` (both modes) and `test_p3_roman_heading_with_rule_stays_a_heading`, which is non-vacuous this time (`Rule 99(z)` is unlogged; if the line were text, `authority-not-logged` would block).

### ADDRESSED — P3: MCP `mode` type
`matter-mcp.py:58`: `"mode": {"type": "string", "enum": ["brief", "complaint"], ...}`. Invalid values still reach `check()`, raise `ValueError` (`briefcheck.py:866–867`), and `handle()` wraps as `isError` (`matter-mcp.py:182–187`).

### ADDRESSED — P3: test gaps
`tests/test_briefcheck_complaint_mode.py:114–170`. `TestSwe2ComplaintModeReview` adds the ≤8-word-class boundary (`"1. Doc. 99."`, which also pins veto-over-title-case precedence), paren/quote endings in both modes, the punctuated title-case heading in brief mode, a non-vacuous roman-heading test, an end-to-end MCP invalid-mode `isError` test, and a README assertion. The one soft spot: `test_p3_mcp_mode_schema_is_typed_and_invalid_mode_is_an_error` string-matches the schema rather than parsing `tools/list` output — acceptable, since it also exercises the runtime path.

### ADDRESSED — P3: README wording
`README.md:176–181` now states the actual rule (cite/quotation veto, short + unpunctuated-or-title-case). One residual over-claim, noted below.

## New breakage in the fix diff (`4d21b9b..e77da0c`)

### P2 — Rule cites and statutes without `§` inside numbered title-case headings are unchecked again
`briefcheck.py:374–377`, `:404–408`.

`_carries_cite` omits the entire rule family — `RULE_RE`, `LOCAL_RE`, `BARE_RULE_RE`, `LOCAL_WORDS_RE` (`:127–141`) — and statutes lacking a literal `§`. `citations.extract_citations` does pull those: `15 U.S.C. 78j` without § (citations.py:74), `12 CFR 1026.1` (citations.py:69), `Pub. L.` etc. Because the new title-case exception at line 407 makes punctuated numbered lines headings again, and the word cap rose from 8 to 12, a checkable authority in such a line is silently skipped — a regression versus `7b78d9b`, where the same line was demoted to text and scanned:

- `"1. Rule 99(z) Standard."` (brief or complaint): 4 words, no veto, ends `.`, `words[1:]` = `Rule/99(z)/Standard` all pass `_title_case` → heading 3 → `Rule 99(z)` never scanned. At `7b78d9b` → text → `authority-not-logged` BLOCK.
- `"3. Claims Under 42 U.S.C. 1983."`: `CASE_RE` can't match (`U.S.C.` isn't a reporter and `42 U.S.` lacks a page number), no `§` → heading → the statute cite is invisible. At `7b78d9b` it extracted to `statute:42usc1983` and resolved against the logged `42 U.S.C. § 1983`.
- `"2. Facts Under Local Rule 7."`: same via `LOCAL_WORDS_RE`. `§`/`§§` cites *are* caught (the literal `§` check covers `SECTION_LIST_RE` lines too).

Suggested edit: extend the veto for the `tok.isdigit()` / complaint-letter branch so it covers the rule family and non-`§` statutes — either add `RULE_RE, LOCAL_RE, BARE_RULE_RE, LOCAL_WORDS_RE` to `pats` plus a statute probe such as `re.search(r"§|\d+\s+U\.?S\.?C|\d+\s+C\.?F\.?R|\b(?:Code|Stat\.|Pub\.|Sec(?:tion)?\.?)\b", line)`, or (simpler and future-proof) treat the line as cite-carrying when `scan_authorities(line)` yields anything. Whether to widen the roman/brief-letter branch (`quotes=False` at `:410`) is a design call — its docstring already scopes it to record/pleading/case — but leaving rules out there too keeps `"II. Argument Under Rule 99."`-style headings unchecked.

### P3 — A title-case numbered line inside a level-3 fact section now closes the section and skips itself
`briefcheck.py:406–409` interacting with `:918–924`.

`"1. Statement of Facts."` → `fact_level = 3`. A following `"2. Plaintiff Was Arrested."` (or `"2. The Arrest."`) is now a level-3 heading → `lvl <= fact_level` → `fact_level = None` → it is skipped itself and every later uncited sentence goes unflagged. At `7b78d9b` that line was text and drew `uncited-fact`. This restores the pre-`7b78d9b` heading semantics exactly (numbered subheadings always closed numbered fact sections), so it is a revived limitation rather than a fresh bug — but title-cased numbered *sentences* inside a numbered fact section are the regression surface. Narrow; document or accept.

### P3 — README still over-claims at the edges
`README.md:176–177`: "a numbered or lettered line is never a heading when it contains a cite or a quotation." Two leaks remain: (a) "cite" doesn't include rule cites or non-`§` statutes (the P2 above) — `"1. Rule 15 Standard."` is a heading while containing what the tool itself treats as a cite; (b) in brief mode a lettered or roman line containing *only* a quotation stays a heading (`:410`, `quotes=False`), so `"A. He said \"the file is closed\""` drops the quote with no row, finding, or note. Suggest narrowing the sentence to the patterns the veto actually checks, or fixing the code per the P2.

Also noted, not ranked: numbered headings ending in `?`/`!`/`;` can never be headings (`:406–408` only admits `.`/`:`), and a comma anywhere disqualifies the title-case exception (`"1. Facts, Part One."` stays text) — both conservative demotions, same behavior as `7b78d9b` for those shapes.

## Answers to the five points

**1. The `_carries_cite` veto in headings.** Real headings *are* demoted when they carry a vetoed pattern — that's the intended trade, and what follows is mild. `"2. The § 1983 Claims"` → text → the statute is scanned; you get an authority row and possibly a `short-form-without-full-cite` WARN, never a spurious `uncited-fact` (the line has a cite) and never a fact-section-that-never-ends (demoted lines are text; text doesn't touch `fact_level` — the only loss is a would-be fact heading like `"1. Facts Under § 1983"` can't open a section). `"3. The 'Holiday' Excuse"` is unaffected — `QUOTE_RE` only matches `“…”`/`"…"`, not `'…'`. `Id.` look-alikes: a contrived heading like `"4. Id. Est. Cases"` demotes and can produce a spurious `id-unbound` BLOCK, but `"12. Id. at 3"` was *fully skipped* at `7b78d9b` and is now resolved against `last_target` — a net win. The real demotion risk is the P2 above: cites the veto doesn't know about.

**2. Title case in both modes.** Acceptable. In complaint mode a misjudged title-case sentence (`"4. Plaintiff Sued Defendants."` → heading) costs nothing: no cite/quote means nothing was checkable, and `uncited-fact` is off in this mode. In brief mode the cost is the P3 section-closing case. `_SMALL_WORDS`/`_title_case` behave on the stress cases: `"1. On January 1, officers …"` fails on lowercase `officers` *and* carries a comma; `McDonald's`, `FDA`, and `§`-leading tokens pass; lowercase-initial brands (`eBay`, `von`, `iPhone`) fail title-case and stay text — same as `7b78d9b`, not a regression. The comma-anywhere rule is a conservative pin; it only blocks heading status for punctuated lines, which were text at `7b78d9b` anyway.

**3. Roman and brief-mode letter lines with `quotes=False`.** Ordering is correct for the asked case: `quotes=False` still includes `RECORD_RE`/`PLEADING_RE`/`CASE_RE`/`SHORT_RE`/`WL_RE`, so `"A. Counsel said \"x.\" Doc. 99."` is text and the quotation is checked against the cite — a lettered brief paragraph can't smuggle a cited quote past. The deliberate residue: quotation-*only* lettered/roman lines in brief mode stay headings and the quote is silently unlisted (no `notes` mechanism exists in brief mode). Same for `Id.` and `§`/rule cites in those lines. Consistent with the docstring; contradicts the README sentence (P3 above).

**4. Summary wording.** `briefcheck.py:791–796`: singular `"1 alleged quotation listed for the pleader to confirm"`, plural `"N alleged quotations listed…"`, gated on `self.mode == "complaint" and self.notes`. `notes` is only ever appended in complaint mode (`:1208–1209`), and the string lands at the end of the one-line summary used by both `matter.py:290` and `matter-mcp.py:104`. Correct.

**5. Fact-section tracking.** A demoted numbered line inside a fact section still produces the right `uncited-fact` behavior: demotion yields a text `Unit` with `fact_section=True` inherited from `fact_level` (`:932`), and `unit()` flags it when it's ≥4 words with no cites/quotes/facts (`:1090–1092`). `test_sentence_numbered_paragraph_is_still_text` pins this. The complementary hole — a numbered line classified as a *heading* at the same level closing the section — is the P3 above.

## Readiness

It can stay on `feat/matter-capture` as is. All seven round-1 findings are resolved, the recorded test evidence (`438 tests OK`; 7 of 9 new tests red pre-fix, the 2 guards passing both ways) is consistent with the code I read — verified by pure reading per the constraints — and the new P2 requires a specific shape (rule or non-`§` statute cite inside a title-case `.`/`:`-ended numbered heading) that is rarer than the round-1 holes. One small follow-up commit — widen `_carries_cite` to the rule family and non-`§` statutes for the numbered/complaint-letter branch, plus a README clause tightening — clears this review entirely.

## Reviewing model

SWE-2 Max.
