I'll start by reading the handoff document to understand the scope.Now let me read the round-2 report and the fix diff in parallel.I have the handoff, my round-2 report, and the fix diff. Now let me read the working files at `eadb780` — starting with locating the source files.Now the `units()` method and surrounding `_Checker` code at `eadb780`.Now the rest of `unit()` — authorities, `Id.` binding via `last_target`, quotes, and the uncited-fact branch.`unit()` and `quotes()`/`facts()` confirmed. Now the `Report` class — `summary`, `add`, and `check()` plumbing.Let me verify `scan_authorities` handles §-less statutes like `99 U.S.C. 1983` — reading the scanner body.`scan_authorities` delegates statutes to `citations.extract_citations`. Let me verify the §-less statute pattern in `citations.py`, the test fixture, and README at `eadb780`.Let me check the `Base` fixture and `codes` helper in `test_briefcheck.py`.Let me see the rest of the fixture setup — which authorities are logged.A few final checks — other `Unit(` constructions or `.heading`/`fact_section` consumers, and confirming the diff range boundaries match what I read.All consumers confirmed. One last grep — `_heading(` call sites and a sanity check that nothing else reads `Unit` positionally in a way the new field could break.**Verdict: APPROVE WITH CHANGES**

All three round-2 findings are addressed — the fix checks cites inside headings in place rather than widening the veto, which is a better mechanism than the one I suggested — and nothing in the fix diff is new breakage above P3. It can stay on `feat/matter-capture` as is; the changes below are non-blocking follow-ups. Verified by pure reading against the worktree at `eadb780` (read-only; no shell), per the handoff constraints.

## Round-2 findings

### ADDRESSED — P2: rule cites and §-less statutes inside numbered title-case headings unchecked

Instead of widening `_carries_cite`, every heading now becomes a scanned unit: `units()` appends `Unit(hs, bm.end(), self.text[hs:bm.end()], False, heading=True)` for `lvl is not None` blocks (`briefcheck.py:1039-1040`), `run()` sends every unit through `unit()` (`:1136-1137`), and `unit()` runs the full scan — `RECORD_RE`/`PLEADING_RE`/`scan_authorities`/`ID_RE`/`quotes()`/`facts()` (`:1150-1212`). So `"1. Rule 99(z) Standard."` stays a level-3 heading but `BARE_RULE_RE` yields `rule:…99(z)` → `authority-not-logged` BLOCK; `"3. Claims Under 99 U.S.C. 1983."` is extracted via `citations.extract_citations` (§-less U.S.C. pattern, `citations.py:93`; CFR without § at `:88`) and kept by the frag filter at `briefcheck.py:371`. This covers *more* than my finding asked: roman/brief-lettered headings carrying `§` statutes or quotes (`"II. Claims Under § 1983"` — `§` is absent from the `quotes=False` veto at `:516-518`) and all-caps/markdown headings — which never had any veto — are now checked too. Section roles are untouched. Pinned by `test_p2_cites_inside_headings_are_checked` (5 heading shapes × both modes, `test_briefcheck_complaint_mode.py:180-185`) and `test_p2_headings_keep_their_section_role` (`:187-191`).

### ADDRESSED — P3: title-case numbered line closes a level-3 fact section

`sub` at `briefcheck.py:1033-1038`: a heading at `lvl == fact_level` whose block matches `\s*\d{1,3}\.\s` and doesn't hit `NONFACT_HEADING` (`:162-163`) no longer clears `fact_level`. `"2. Plaintiff Was Arrested."` under `"1. Statement of Facts."` keeps the section → the following sentence gets `uncited-fact` (pinned, `:199-203`); `"2. Argument."` still closes it (pinned, `:205-209`). The heading itself can't be flagged — it is always constructed `fact_section=False`, and the `… and not u.heading` guard at `:1215` adds a second layer. Residual edges are listed under new-breakage items 1 and 6; lettered and roman subheadings at the same level still close (the `\d` test is deliberately numbered-only), which is pre-fix behavior, not new.

### ADDRESSED — P3: README over-claim + dropped quote-only lettered/roman headings

`README.md:176-184` now says headings are still read for cites and quotations and states the actual numbered-heading and subheading rules — the earlier "never a heading when it contains a cite or a quotation" over-claim is gone. A brief-mode `'A. He said "the file is closed"'` is a heading (`quotes=False` veto) whose unit reaches `quotes()` → `quote-uncited` (`:1349`); in complaint mode the same line is still demoted to text by the full veto (`:510`) and lands in `notes` (`:1347`). Pinned by `test_p3_quote_only_lettered_heading_reaches_quote_checks` (`:193-197`) and the updated README assertion (`:172-174`). One residual doc nit, item 5 below.

The changed `test_p3_roman_heading_with_rule_stays_a_heading` (`:144-151`) is now non-vacuous in both directions: the roman heading still ends the facts section (only the facts sentence gets `uncited-fact`) *and* its unlogged `Rule 99(z)` blocks `authority-not-logged` — it would fail at `e77da0c` on the second assert, which is the correct red premise.

## New breakage in the fix diff (`69ab284..eadb780`)

### P3 — Non-fact section names missing from `NONFACT_HEADING` now keep the fact section open (over-flagging, new)
`briefcheck.py:1033-1034`. A same-level numbered title that is neither `FACT_HEADING` nor `NONFACT_HEADING` is a `sub`, so `fact_level` survives it — at `e77da0c` it closed. Synthetic: `"1. Statement of Facts.\n\n2. Issues Presented.\n\nWhether the court erred is plain."` → "Whether the court erred is plain." now draws `uncited-fact` (brief mode). Realistic leaking names: `"2. Issues/Questions Presented."`, `"2. Procedural Posture."`, `"2. Nature Of The Case."`, `"2. Merits."`, `"2. Introduction."`, `"2. Summary."`. UNCHECKED noise, brief-mode + numbered fact section only — extend `NONFACT_HEADING` or accept.

### P3 — Scare-quoted phrases in headings now reach the quote checks
`briefcheck.py:1040` feeding `:1335-1350`. `II. The "Objective Reasonableness" Standard` → `quote-uncited` UNCHECKED (brief) / a `notes` row (complaint); the same heading with a cite, `II. "Knock-And-Announce" Under Rule 41`, → `quote-unchecked`. Before: silent. Same judgment the tool already makes for quoted phrases in body text, so consistent — but headings are the most idiomatic place for doctrinal scare quotes, and complaints will accumulate `notes` for them. Consider gating heading quotes or accept as noise.

### P3 — `Id.` now binds through headings
`briefcheck.py:1208-1210` fires on heading units, so a cite-bearing heading becomes the `Id.` antecedent and a multi-cite heading arms `id-ambiguous` (`:1192-1194`). `"Text. Doc. 16.\n\nII. Argument Under Rule 56\n\nId."` → `Id.` resolves against Rule 56, not Doc. 16. That matches reading order ("Id." = nearest preceding cite), so defensible — but it's a silent binding change, and a heading like `II. Rule 56 And § 1983` now makes the next `Id.` warn `id-ambiguous`. Document or accept.

### P3 — `not u.heading` is inert today
`briefcheck.py:1215`. Heading units are always `Unit(..., False, heading=True)` (`:1040`) — `fact_section` is never mutated — so the guard can never fire; `test_heading_is_never_flagged_as_an_uncited_fact` (`:211-213`) would pass without it. Harmless defensive code; keep or drop.

### P3 — README still under-lists the veto
`README.md:178-179` says a numbered heading carries "no record, ¶ or case cite, statute sign or quotation" — the actual veto (`briefcheck.py:480-483`) also covers short-form cites (`579 U.S. at 45`), WL cites, and `Id.` `"12. Id. At 5."` stays text contrary to the "only when" wording. One clause fixes it.

### Not ranked
- `NONFACT_HEADING` false closes — fact-y titles containing `claims`, `standard`, `discussion`, `analysis`, `jurisdiction`, `venue`, `damages`, `remed*`, `legal` (`"2. The Claims Process At The Jail."`, `"2. Evidence Of Jurisdiction."`, `"2. The Remedial Steps Taken."`) still close the section — under-flagging, same conservative direction as before, strictly narrower than at `e77da0c`. `FACT_HEADING` wins ordering (`:1035`), so `"2. Facts About The Claims."` correctly keeps the section.
- Lettered (complaint-mode) and roman headings at the same level still close fact sections — residual edge of the numbered-only `sub` test.
- Summary counts heading rows as "cited or tagged sentences" (`:904-905`, `:1213-1214`) — cosmetic.
- All-caps and markdown headings were never vetoed and are now fully checked: `## Order Re Doc. 99` → `record-not-in-store`; `VIOLATIONS OF 42 U.S.C. § 1983` → statute check. Intended per the fix's design, but it is new blocking surface — worth a line in the commit/changelog if anyone trips on it.

## Answers to points 1–4

**1. Headings as units.** Neighbouring effects are bounded. `Id.`/`last_target`: heading cites update it — the nearest antecedent is now the heading's cite (P3 above; direction is correct, change is silent). Fact-tag hosting: `host[-1]` is the last unit with `u.start <= off` (`:1050-1053`); a freestanding tag line after a heading now hosts on the heading unit instead of the previous text unit — the tag is checked identically and the reported line is *closer* to the tag. Citation-only merge: `merged` is rebuilt per block (`:1043-1048`) and heading blocks `continue` before it, so a heading can never merge with a citation-only sentence in either direction — confirmed clean. Rows/summary: heading units append `Row`s only when they carry cites/quotes/facts; findings report the heading's own line.

**2. Noise from scanned headings.** Lower than feared because the cite patterns are case-sensitive: all-caps `DOC. NO.`, `CIVIL ACTION NO.`, `RULE 56`, `FED. R. CIV. P.` match nothing (`:148-151`, `:130-147`, `:91-93`). Caption lines (`UNITED STATES DISTRICT COURT`, `CIVIL ACTION NO. 3:26-CV-1234`, party names, signature labels) produce no rows. What does fire in titles — `§`/code-name statutes, `Rule N`, `Local Rule N`, `Doc. N`, `Compl. ¶ N`, case cites — are real cites; checking them is the fix's purpose. The one genuine new noise class is scare-quoted phrases (P3 #2).

**3. `NONFACT_HEADING` and the `sub` test.** `re.match(r"\s*\d{1,3}\.\s", block)` is the right test: `lvl == fact_level` can only be 3 for a numbered heading, but markdown `###` also yields level 3, so re-deriving "numbered" from the line is necessary and sufficient. False closes (`claims`/`standard`/`remed*`/`jurisdiction`/etc. in a fact-y title) persist but only shrink versus `e77da0c`. False keeps are the new direction — non-fact names absent from `NONFACT_HEADING` keep the section and over-flag (P3 #1). Roman, all-caps, and complaint-mode lettered headings at the same level always close, exactly as before.

**4. Complaint mode.** `uncited-fact` is `mode == "brief"` gated anyway (`:1215`), so heading units change nothing there. Quotations in headings reach `notes` (`:1346-1347`) and the complaint summary count (`:900-903`); a lettered allegation with a quote is still demoted to text and lands in `notes` identically. Fact-tag hosting and `facts()` (`:1405-1421`) are untouched — `{fact:…}` inside or beside a heading is checked on the heading's line.

## Readiness

It can stay on `feat/matter-capture` as is. All three round-2 findings are resolved at `eadb780`, the check-in-place design closes the coverage hole without the false-demotion trade of widening the veto, and the recorded evidence (618 tests OK; the 6 new + 2 changed tests red/green exactly where the new code paths differ) is consistent with everything I read. One small follow-up — extend `NONFACT_HEADING` (issues/questions presented, posture, nature of the case, merits, introduction, summary), add short-form/WL/`Id.` to the README veto list — clears every item above except the scare-quote and `Id.` trade-offs, which are accept-or-document calls.

## Reviewing model

SWE-2 Max.
