# Second history rewrite: an address removed from v0.1 (2026-11-01)

The first commit in this repository (`matter-kit v0.1`, 2026-08-23) added an example line to
`skills/matter-kit/SKILL.md` that held private address details. A later commit ("public-release
hygiene before GitHub publish", 2026-09-11) removed the line from the file, but it stayed in history.
A case-pattern scan found it on 2026-11-01, and ty approved rewriting history to remove it.

One `git filter-repo` run over four branches (`main`, `feat/matter-capture`,
`feat/docx-structural-extraction` and `feat/cite-lookup`) made three changes:

- **The address details** now read `[address redacted]`.
- **An absolute home-folder path** in line 3 of RFC 0004 and RFC 0005 now starts with `~`. Those
  two files exist only on `feat/docx-structural-extraction`.
- **Two commit messages** that record the cite-lookup reviews name the external drive that holds
  the reviewer transcripts by description rather than by its volume name.

Because the address was in the first commit, every commit on these branches has a new hash. Nothing
else changed:

- 53 commits have a different tree (marked below): the 47 from v0.1 up to the hygiene commit,
  and 6 on `feat/docx-structural-extraction` that carry the two RFC lines.
- The tips of `main`, `feat/matter-capture` and `feat/cite-lookup` have exactly the trees they had
  before. The tip of `feat/docx-structural-extraction` differs only in those two RFC lines.
- Authors, dates and parents are unchanged. Messages are unchanged apart from the two above, except
  that a message naming another commit now gives that commit's new hash.

This map continues [the first rewrite's map](2026-11-01-history-rewrite.md). Review handoffs and
reports pin commits by older hashes: find a hash in **Before** (or, for a hash from before the first
rewrite, in **Pre-scrub**) and use **After**. **Tree** is "yes" where the commit's files changed.
**On** lists the branches that contain the commit (capture = `feat/matter-capture`, docx =
`feat/docx-structural-extraction`, cite = `feat/cite-lookup`). Rows are in history order.

| Pre-scrub | Before | After | Tree | On | Commit |
| --- | --- | --- | --- | --- | --- |
|  | `99652a4` | `e0cb25a` | yes | main capture docx cite | matter-kit v0.1: schema, CLI, zero-dep stdio MCP server, skill, synthetic demo |
|  | `f318c9a` | `878c176` | yes | main capture docx cite | add .gitignore; exclude derived stores and bytecode |
|  | `a094c91` | `e0c81f1` | yes | main capture docx cite | packet --out mkdirs; demo store excluded from git |
|  | `a8ec07c` | `f95c84b` | yes | main capture docx cite | rfc: provider-neutral search adapters — consent.json grants, 3-stage cite statuses, zero-adapter default |
|  | `8539de1` | `311fbe4` | yes | main capture docx cite | rfc: rev3 per council — blocking schema fix (citation_extractions table, stage-order validator, source-checked token), precise consent trust model (cooperative guardrail vs containment), rulings recorded |
|  | `19f5d4e` | `140f888` | yes | main capture docx cite | matter-kit v0.2 R1 (RFC 0001): local citation extraction w/ staged diligence, cooperative consent scaffolding, cite_extract MCP tool — zero adapters, zero network; 31/31 tests |
|  | `1400e76` | `98df7cc` | yes | main capture docx cite | matter-kit R1 rev4 per adversarial review: in-child Python-level network denial w/ negative controls; consent issuer/callers/scope model (human grants delegate to listed callers, agent grants inert); 35/35 tests |
|  | `05e74fb` | `242c128` | yes | main capture docx cite | rfc: pin Deepseek grant-CLI gap to R2 scope (consent authoring surface + caller-identity ergonomics) |
|  | `aed1217` | `59b0b64` | yes | main capture docx cite | rfc: record R1 closure (chair, 2026-09-08) and R2 consent-authoring constraint — agent-staged proposals inert until out-of-band human activation |
|  | `e51fbd4` | `58f595a` | yes | main capture docx cite | rfc: bump status header to rev4 — R1 closed, R2/R3 separately gated |
|  | `3c0a2cb` | `02ac207` | yes | main capture docx cite | evals: Set A/B/C v1 frozen gold + scored run — kit 1.000/0.583, eyecite 1.000/0.417, union 0.942, 0 FP both; complementary format families (pre-adversarial-review) |
|  | `0f9c190` | `2f74a1e` | yes | main capture docx cite | evals: corrected record rev2 per adversarial review — committed scorer, exact-match primary (kit 0.333 / eyecite 0.250 / union 0.417), pollution finding, v1 headline retracted |
|  | `3704e76` | `c88ab72` | yes | main capture docx cite | evals: canonical run rev3 per chair — exact source spans for both parsers, item-level matched/missed/FP sets, stored union, norm-vs-boundary separation, scorer self-tests 10/10 (caught double-consume matcher bug) |
|  | `b4bfb37` | `96fbb8e` | yes | main capture docx cite | citations: v2 parser — left-anchored dotted-chain patterns, Gov't/Comp. Stat./C.F.R. families, mid-acronym guard; dev-deck 180/180 @ 0 FP (regression coverage only, acceptance waits on frozen holdout) |
|  | `4af39d6` | `24ca865` | yes | main capture docx cite | evals: freeze acceptance holdout v1 — 5 public-domain statute/reg excerpts (USC x3, CFR, FL 605), UNLABELED, independent labeling by adversarial reviewer; no implementation-seat gold |
|  | `0730729` | `e14ffe5` | yes | main capture docx cite | evals: independent holdout acceptance — labels + result (kit 2/32, eyecite 2/32, FIX required) |
|  | `4f6bad1` | `761ddee` | yes | main capture docx cite | evals: correct holdout gold (remove nested-prefix artifacts, add U+202F heading) — kit 25/29 exact-span, eyecite 2/29 |
|  | `a331f31` | `f7daabc` | yes | main capture docx cite | citations: v3 — holdout acceptance families (short-form real, Pub. L./R.S./F.S., undotted CFR, spelled-out U.S. Code, xref word family incl. coordinated sub-spans, ranges, session laws, entity/Unicode tolerance); holdout 29/29 recall 1.0; suites green |
|  | `26947e4` | `f20d053` | yes | main capture docx cite | evals: taxonomy-corrected gold v3 (Stat.=session-law, coordinated-ref convention, +section 3) — kit 96fbb8e scores 35/35 as DEV-DATA only, acceptance still needs fresh holdout |
|  | `514188e` | `3681128` | yes | main capture docx cite | evals: version reconciliation — prior 96fbb8e scores were working-tree contamination (v3 parser); clean repro: 96fbb8e=0/29, f7daabc=29/29; gold-file drift recorded |
|  | `0e81501` | `2b508ad` | yes | main capture docx cite | evals: controlled parser comparison (same 35-span gold, pinned hashes) — v2 6/35, v3 35/35, FPs recorded |
|  | `375f8c1` | `a5a3629` | yes | main capture docx cite | evals: freeze unlabeled holdout_v2 for independent labeling |
|  | `3642132` | `e9cfbb1` | yes | main capture docx cite | evals: holdout_v2 independent labels (108 spans, 6 docs) — for second review, NOT scored; Python char offsets, raw entities preserved, session-law gold |
|  | `f1fc325` | `14c0366` | yes | main capture docx cite | evals: holdout_v2 labels — second review applied (drop plaw TOC chrome Sec. N + SECTION 1); 53 gold spans remain, still NOT scored |
|  | `4908110` | `bfa5ee6` | yes | main capture docx cite | evals: holdout_v2 one-shot acceptance run (42/52 exact-span) |
|  | `fd6a362` | `c5a9445` | yes | main capture docx cite | citations: v4 syntax gaps + pin holdout_v2 as development data |
|  | `e3b80c5` | `6aa9df7` | yes | main capture docx cite | evals: freeze unlabeled holdout_v3 with ignored_intervals |
|  | `b27e6ed` | `57c9179` | yes | main capture docx cite | evals: holdout_v3 independent labels (21 spans, 5 docs) — FOR SECOND REVIEW, NOT SCORED; kinds per taxonomy, plaw TOC ignored |
|  | `cbc23be` | `653a7c0` | yes | main capture docx cite | evals: holdout_v3 label-contract amendment per Astra — SECTION 1 to ignored_intervals [419,431]; out-of-scope references rule (neither gold nor FP) |
|  | `c3e3a5f` | `89eb607` | yes | main capture docx cite | evals: holdout_v3 contract patch per second review — SECTION 1 [419,428] exact; out-of-scope intervals for usc_11 (named act, structural refs) neither gold nor FP |
|  | `5bc4f50` | `f0223cb` | yes | main capture docx cite | evals: holdout_v3 one-shot acceptance (18/21; statutory 17/20) |
|  | `5c3d423` | `d38313d` | yes | main capture docx cite | R2: CourtListener manual search behind human TTY grant activation |
|  | `5193d92` | `6d3223c` | yes | main capture docx cite | R2: refuse HTTP redirects; explicit auth mode; no silent anonymous |
|  | `0ef6440` | `3a5dabc` | yes | main capture docx cite | docs: R3 read-only API discovery — law.verify_citation does not exist live |
|  | `f1905a1` | `864a3ce` | yes | main capture docx cite | R3: corpus-search MCP adapter — protocol errors vs empty results |
|  | `0ee63c0` | `b9ae7c7` | yes | main capture docx cite | Counsel-outreach MVP: sourced shortlist, sha-gated nosend, ledger |
|  | `890da07` | `fa22589` | yes | main capture docx cite | Outreach: refuse empty batches; replace drafts instead of duplicating |
|  | `e3e797e` | `c1d39ff` | yes | main capture docx cite | Outreach: pin stale-approval + historical-batch regressions; RFC 0002 CLOSED |
|  | `701f1e7` | `11d358c` | yes | main capture docx cite | RFC 0003: source-backed recipient import, no outbound contact |
|  | `3922f2c` | `f7100eb` | yes | main capture docx cite | discovery: fix naive-vs-aware datetime crash on date-only retrieved_at (every date-only brief import raised TypeError) |
|  | `4f61296` | `64c2587` | yes | main capture docx cite | tests: discovery import regressions — date-only retrieved_at, incomplete brief |
|  | `1bbcab9` | `082ac60` | yes | main capture docx cite | discovery: atomic brief import — pre-flight required-field validation (named errors), rollback guard; test_cannot_shortlist updated to v3 no-verbatim candidate path |
|  | `05b3c4c` | `5e2a70c` | yes | main capture docx cite | RFC 0003: destination_type + honest candidate (no unearned source-checked) |
|  | `999efa5` | `51a7d18` | yes | main capture docx cite | Outreach P3: no doubled To-line org; prep-only warning on CLI manifest |
|  | `2a74e8e` | `3c3b547` | yes | main capture docx cite | evals: reviewer-verified outreach candidate — MacArthur Justice MS (candidate only, no contact; practice evidence + intake form verified from org pages) |
|  | `d5c713a` | `2e3fe3b` | yes | main capture docx cite | Record RFC 0003 safeguard acceptance; MacArthur MS candidate stays candidate |
|  | `319e406` | `aa13543` | yes | main capture docx cite | RFC 0003: close bounded one-candidate proof; keep three gates separate |
|  | `f35efb2` | `8e1c3bb` |  | main capture docx cite | chore: public-release hygiene before GitHub publish |
|  | `52e1177` | `5626f4b` | yes | docx | feat(extract): stdlib PDF text extraction with page locators + document_pages table + matter pages CLI (RFC 0004); honest per-page failure for scanned/LZW/CID PDFs; 5 new tests, 93/93 green |
|  | `96e1306` | `59d1751` |  | main capture docx cite | docs: add Corpus Open Legal Kit hero to README (#1) |
|  | `ca9f0d6` | `66dc867` | yes | docx | Merge remote-tracking branch 'origin/main' |
|  | `24a8b85` | `5efcb1c` | yes | docx | feat(extract): stdlib DOCX text extraction with structural locators + document_blocks table (RFC 0005); zero fabricated pagination; 57 new tests, 150/150 green |
|  | `f538ac4` | `5c377ca` | yes | docx | fix(extract): record_blocks no longer commits; caller owns the transaction |
|  | `06c277f` | `d04fbe8` | yes | docx | fix(extract): archive resource bounds — pre-open member GUARD + post-open aggregate ACCEPTANCE LIMIT (RFC 0005 §4a) |
|  | `3f390e5` | `9e71bb8` | yes | docx | fix(extract): pre-open central-directory byte cap + parser-agreement tests (RFC 0005 §4a) |
|  | `db947bf` | `ba521ce` |  | capture cite | fix(ingest): store matter-relative rel_path; cwd-independent verify |
|  | `503cd4d` | `10acfc8` |  | capture cite | docs: write→ingest→verify-from-foreign-cwd; MCP ingest_path/verify |
|  | `58809a3` | `1486dce` |  | capture cite | docs: add dogfood-iteration product note (spec for §5.1 matter capture) |
|  | `1d2ca1a` | `de10144` |  | capture cite | feat(capture): matter capture + capture list (dogfood-iteration §5.1) |
|  | `550292c` | `b78d574` |  | capture cite | feat(correspondence): custodian notice ledger, nosend (RFC 0006) |
|  | `64f511f` | `399af02` |  | capture cite | feat(attribution): record agent authors and the person who directed them |
|  | `be38709` | `342e466` |  | capture cite | fix(consent): open /dev/tty with separate read and write handles |
|  | `0954f79` | `165da1a` |  | capture cite | docs: design for brief check, deadline lifecycle and subcases (approved by ty 2026-10-23) |
|  | `eee92c1` | `d701c5c` |  | capture cite | feat(store): connect_readonly for checkers and cross-matter lookups |
| `f114d3a` | `b0f3d59` | `b307410` |  | capture cite | feat(brief): brief check ties every cite, quote and fact in a draft to the store |
| `bff7589` | `261c683` | `aa3e5ec` |  | capture cite | feat(authority): authority check records source evidence; authority import reads ledgers |
| `6b4045c` | `bc79fda` | `ca405d2` |  | capture cite | fix(authority): conservative ledger mapping; dry-run import opens the store read-only |
| `08633de` | `3fd6f1e` | `59b3b1a` |  | capture cite | docs: README section on checking a brief before filing |
|  | `90000cc` | `fa778bb` |  | capture cite | feat(deadlines): update with reason and events trail; OVERDUE in list |
|  | `b230ee8` | `8cd77f0` |  | capture cite | feat(subcase): subcase init and nested-matter ingest boundary |
|  | `e767159` | `ba21313` |  | capture cite | feat(subcase): parent: references, hash-checked and never copied |
|  | `bcb9652` | `66b714b` |  | capture cite | feat(subcase): status lists a parent's subcases and names a subcase's parent |
| `450b023` | `3b1340d` | `ed0af05` |  | capture cite | merge: deadline lifecycle and subcases (feat/deadlines-subcases) |
| `3a8c78b` | `0d4f728` | `ae0388c` |  | capture cite | feat(brief): brief check follows a subcase's parent: references, read-only |
| `3d88bb6` | `03b1f48` | `fc65c70` |  | capture cite | docs: README section on deadline updates and subcases |
| `d64e6bc` | `6c7cd9d` | `b598d0d` |  | capture cite | review: hand brief check, authority checks, deadlines and subcases to SWE-2 |
| `cfdfc95` | `e81279d` | `391f2ce` |  | capture cite | review: record SWE-2's REQUEST CHANGES of brief check, authority checks, deadlines and subcases |
| `0791064` | `03f9f2d` | `38af383` |  | capture cite | fix(brief): answer SWE-2's review — Id. binding, altered quotes, exit contract and 17 P3s |
| `8bf2c5a` | `b53cd5b` | `1f1d217` |  | capture cite | review: hand the round-1 fix to SWE-2 for a scoped re-review |
| `81bb2da` | `7949264` | `a8be707` |  | capture cite | review: record SWE-2's round-2 APPROVE of the brief-check fix |
| `766440e` | `631d4a0` | `f51c894` |  | capture cite | fix(brief): SWE-2 round-2 follow-ups N1-N5 |
| `7b78d9b` | `883584a` | `49e0cdf` |  | capture cite | feat(brief): --mode complaint; numbered paragraphs are no longer skipped as headings |
| `15c2b7e` | `5bba2aa` | `5c21e6b` |  | capture cite | review: hand brief check complaint mode to SWE-2 |
| `3a63d41` | `2b5c557` | `b3dcb09` |  | capture cite | review: record SWE-2's APPROVE WITH CHANGES of brief check complaint mode |
| `3dceaa5` | `e023ceb` | `c15b464` |  | capture cite | fix(brief): key every rule in a stored "5(b); 6(a); 15" rule list |
| `c63d392` | `6deab52` | `3c92f7f` |  | capture cite | fix(brief): key every section of a stored "§§ 21-21-3, 21-3-15" statute list |
| `3b4906c` | `31294aa` | `4b9504c` |  | capture cite | fix(brief): split a draft's "§§ 21-21-3, 21-3-15" list into one cite per section |
| `af0a7dd` | `06e0bd0` | `5ab7acf` |  | capture cite | fix(brief): end a "§§" list where the next citation starts |
| `f0bf852` | `1ed7fe2` | `f74b0be` |  | capture cite | review: hand draft-side §§ statute lists to SWE-2 |
| `3aaab1f` | `33a7b1f` | `11ea243` |  | capture cite | review: record SWE-2's REQUEST CHANGES of draft-side §§ statute lists |
| `2b229b6` | `fae7e7d` | `7f52471` |  | capture cite | fix(brief): answer SWE-2 round 1 on draft "§§" lists (P1, P2, P3 tests) |
| `ca4829c` | `5cbc037` | `22d7b5f` |  | capture cite | review: hand the round-1 fix of draft-side §§ lists to SWE-2 for a scoped re-review |
| `e023540` | `7c613fd` | `3a6ef2e` |  | capture cite | review: record SWE-2's round-2 APPROVE of draft-side §§ lists |
| `6ac2b01` | `0afb7a0` | `a7ffdf3` |  | capture cite | merge: "§§" statute lists and stored rule lists in brief check (fix/brief-draft-statute-lists) |
| `4d21b9b` | `29fb90d` | `200ca19` |  | capture cite | merge: feat/matter-capture (a7ffdf3, §§ statute lists) into complaint-mode follow-up |
| `e77da0c` | `dd517cb` | `4d0b2e6` |  | capture cite | fix(brief): answer SWE-2's complaint-mode review — headings never swallow cites |
| `6209e76` | `6c11d8b` | `cc8321a` |  | capture cite | review: hand the complaint-mode fix to SWE-2 for a scoped re-review |
| `978dfc2` | `db4ba4e` | `e0da769` |  | capture cite | review: record SWE-2's round-2 APPROVE WITH CHANGES of the complaint-mode fix |
| `117e9bc` | `58f2e1d` | `ed7dc0e` |  | capture cite | merge(squash): brief check reads three cite styles it used to BLOCK (fix/brief-false-blocks) |
| `3da135f` | `443c43a` | `cca5064` |  | capture cite | merge(squash): a statute short form binds only to a logged key's own section (fix/brief-short-section) |
| `11f607b` | `2079832` | `7fb0da2` |  | capture cite | merge(squash): a dotted "Section 552.101" is one section, not "Section 552" (fix/brief-dotted-section) |
| `5c0a42c` | `495ef74` | `ae795de` |  | capture cite | merge(squash): brief check survives hard-wrapped drafts and parentheticals in "§§" lists (fix/brief-rule-linebreaks) |
| `3fa4e2c` | `dd2778f` | `7397bb8` |  | capture cite | merge(squash): state code heads start at the state; the splitter knows the 26 missing state abbreviations (fix/state-abbrev-cites) |
| `e4e4f0c` | `033755b` | `2645a4e` |  | capture cite | merge(squash): a word-like state abbreviation ends a sentence unless a cite goes on (fix/brief-word-states) |
| `69ab284` | `77ca8e5` | `d69628c` |  | capture cite | merge: feat/matter-capture into complaint-mode round-2 follow-up |
| `eadb780` | `007fed8` | `8fbee2d` |  | capture cite | fix(brief): headings are checked for cites and quotes; numbered fact subheadings keep the section |
| `16c8fa8` | `8affc46` | `e3d94f8` |  | capture cite | review: hand the complaint-mode round-2 fix to SWE-2 for a scoped re-review |
| `57c14dd` | `78c2dc6` | `847aa8c` |  | capture cite | review: record SWE-2's round-3 APPROVE WITH CHANGES of the complaint-mode fix |
| `f7119bd` | `96fb7c0` | `e10f532` |  | capture cite | merge(squash): a list member with another hyphen count no longer ends the list (fix/brief-hyphen-members) |
| `a613f06` | `e1b7f89` | `263e6fb` |  | capture cite | merge(squash): a section list that stops at a number says so (fix/brief-list-stops) |
| `53f82ec` | `dda8801` | `2b0631b` |  | capture cite | merge(squash): a cite in a parenthetical after a statute is checked on its own (fix/brief-xref-paren-cite) |
| `48bf50a` | `3a4783a` | `fc73c55` |  | capture cite | merge(squash): a "§"-led comma list is read member by member (fix/brief-sign-lists) |
| `f3045a9` | `b463ee3` | `43f289c` |  | capture cite | merge: feat/matter-capture into complaint-mode round-3 follow-up |
| `d89743f` | `f2d52e4` | `49c92db` |  | capture cite | fix(brief): more non-fact section names end a fact section; README lists every heading veto |
| `27ea7fb` | `05646ad` | `adfbb21` |  | capture cite | merge(squash): a dot between two digits stays in an authority key, so 5.1 is not 51 (fix/brief-dotted-keys) |
| `9fb77b4` | `6f06ac7` | `d74b056` |  | capture cite | feat(rules): attach a court's rules to a case as a verified rules pack |
| `664b68e` | `34fadd1` | `fb54331` |  | capture cite | review: hand the court rules pack to SWE-2 for a code review |
| `9d7359b` | `54a940a` | `041ab6e` |  | capture cite | review: record SWE-2's APPROVE WITH CHANGES of the court rules pack |
| `2d5fba6` | `17c1240` | `322658a` |  | capture cite | fix(rules): answer SWE-2's review of the rules pack (F1, F2, F4-F7, F9) |
| `3304ebd` | `e880891` | `db7ba99` |  | capture cite | spec: court rules pack revision 2 — answer SWE-2's code review (APPROVE WITH CHANGES) |
| `ae6ff62` | `a45feab` | `fd2898e` |  | capture cite | feat(ui): run the kit in a local browser for people who don't use a terminal |
| `0832eaa` | `5936dc6` | `1f42bea` |  | capture cite | feat(amend): a per-case amendment list, in the CLI, MCP and the local browser UI |
| `9f4c61e` | `a780c86` | `f153e74` |  | capture cite | review: hand the local frontend to Astra for a code review |
| `520b6e6` | `7d3a4bd` | `278e7be` |  | capture cite | review: record Astra's round 1 REQUEST CHANGES of the local frontend |
| `f27c766` | `754053a` | `5e54bbe` |  | capture cite | fix(ui): answer Astra's code review of the local frontend (F1-F9) |
| `b4de5b2` | `7dd1ff4` | `41fa00e` |  | capture cite | spec: local frontend revision 2 — answer Astra's code review (REQUEST CHANGES) |
| `063b0a4` | `7ce1311` | `9feb2d6` |  | capture cite | Merge feat/frontend (41fa00e) into feat/amend-list |
| `46d5e48` | `f5cddf3` | `08405bc` |  | capture cite | fix(amend): follow the fixed frontend's conventions (F4-F7) and add page tests |
| `6e9202e` | `1a7b56c` | `e2c7475` |  | capture cite | feat(rules): record a check of the court's website for an attached pack |
| `f28eba7` | `70f5d2e` | `cbd938e` |  | capture cite | feat(rules): build packs from outline-numbered sources (--markers dotted) |
| `a95dc7a` | `da1509d` | `ae998b5` |  | capture cite | fix(rules): a wrapped marker indented unlike its list yields to the real item |
| `43e39af` | `ce69b76` | `935fa11` |  | capture cite | feat(deadlines): record who must act by a deadline (--owner ours\|theirs\|court) |
| `6edf859` | `6f1837c` | `3c80eb9` |  | capture cite | fix(brief): read '7(b) and (c)' as 7(b) and 7(c) (SWE-2 F8) |
| `93d03a8` | `76c1f1b` | `d32c0eb` |  | capture cite | spec: design drafts for trigger deadlines (G5) and chambers contacts and judges' practices |
|  | `f1845e0` | `fe8cc15` |  | capture cite | docs: record the history rewrite made before the first push |
|  | `26b821c` | `ce88ee0` |  | capture | review: record SWE-2's round 2 APPROVE of the rules pack fix round |
|  | `dbdaf9f` | `8e3e143` |  | capture | fix(rules): answer SWE-2's four round-2 P3s |
|  | `a5c8efb` | `a6dd7e3` |  | capture | feat(rules): anchor a line that opens an item and its first child, '(A)(i) deliver ...' |
|  | `f38b4b5` | `61aba6e` |  | capture | feat(deadlines): trigger deadlines that count Fed. R. Civ. P. 6(a) and 6(d) days when an event happens |
|  | `6b445e3` | `33dbeb2` |  | capture | fix(brief): a sentence that opens with an initial is not a heading; headings' cites are checked |
|  | `2d4a535` | `aafb319` |  | capture | review: hand the port of brief heading sentences to SWE-2 |
|  | `34e80be` | `df25f4e` |  | capture | review: record SWE-2's APPROVE WITH CHANGES of the port of brief heading sentences |
|  | `67ed2d4` | `b301863` |  | capture | docs,test(brief): scope the fact-heading README claim, pin the round-3 P2 as a known limit, correct the port note |
|  | `015ba01` | `bb00c4e` |  | capture | merge: land the port of brief heading sentences (SWE-2 APPROVE WITH CHANGES, follow-up b301863) |
|  | `762a2d9` | `34770dd` |  | capture | fix(brief): a state rule cite keys on the whole state ("W. Va.", "N.D.", "Ohio") |
|  | `ac41b11` | `615863a` |  | capture | review: hand the port of state rule keys to SWE-2 |
|  | `c43a102` | `f3a09ee` |  | capture | review: record SWE-2's APPROVE WITH CHANGES of the port of state rule keys |
|  | `2d9e97c` | `dbd3a4c` |  | capture | test,docs(brief): pin line breaks inside a state rule cite; state the changed state patterns in the port note |
|  | `7a1933f` | `084900a` |  | capture | merge: land the port of state rule keys (SWE-2 APPROVE WITH CHANGES, follow-up dbd3a4c) |
|  | `3061058` | `a5d3448` |  | capture cite | spec: design for checking that cited cases exist (CourtListener citation lookup) |
|  | `e72359d` | `2fe3c75` |  | capture cite | feat(cite): cite lookup checks that a draft's case cites name real opinions |
|  | `9591685` | `c3a07dd` |  | capture cite | review: hand cite lookup to SWE-2 |
|  | `0ef6c6d` | `f62d912` |  | capture cite | review: record SWE-2's APPROVE WITH CHANGES of cite lookup |
|  | `69dd62e` | `92a3bb3` |  | capture cite | fix(cite): answer SWE-2's round-1 P2s in cite lookup |
|  | `b731515` | `2ec4dd1` |  | capture cite | review: hand the cite lookup P2 fix to SWE-2 (round 2) |
|  | `2d289dd` | `39e8d96` |  | capture cite | review: record SWE-2's round 2 APPROVE of the cite lookup P2 fix |
|  | `d2ea6ce` | `2fb13e6` |  | capture | merge: land cite lookup (SWE-2 round 2 APPROVE) |
|  | `d6f4fab` | `c6243f0` |  | capture | feat(brief): read .docx and .pdf drafts directly, with locations a person can find |
|  | `ef3da8d` | `0d99caa` |  | capture | review: hand brief-check docx/pdf drafts to SWE-2 |
|  | `9da21ac` | `5c45032` |  | capture | review: add the three priority areas to the SWE-2 handoff |
|  | `0758b62` | `98ed4ce` |  | capture | review: record SWE-2's APPROVE WITH CHANGES of brief-check docx/pdf drafts |
|  | `cb309e2` | `98681f6` |  | capture | fix(brief): close the silent drops SWE-2 found in the Word reader (round 1, 98ed4ce) |
|  | `c05795f` | `5073a13` |  | capture | review: hand the Word-reader fixes to SWE-2 for a scoped re-review (round 2) |
|  | `4f418f3` | `0800107` |  | capture | review: record SWE-2's round 2 APPROVE of the Word-reader fixes |
|  | `839333f` | `88b1658` |  | capture | merge: land brief check on Word and PDF drafts (SWE-2 round 2 APPROVE) |
|  | `81e8344` | `84bb720` |  | capture | feat(scripts): leak_scan finds home paths, personal emails, session transcripts and case patterns |
|  | `5044642` | `01817b2` |  | capture | review: hand the leak scanner, CI job and hook sample to SWE-2 |
|  | `5a5ca0d` | `94e27bb` |  | capture | review: record SWE-2's APPROVE WITH CHANGES of the leak scanner, CI job and hook sample |
|  | `a7e94dd` | `b64c402` |  | capture | fix(scripts): leak_scan answers SWE-2's review (F1-F9, record separators, hook stdin) |
|  | `808a6c1` | `710d8dc` |  | capture | fix(scripts): a top-level home/ directory is not a home path |
|  | `886b0d1` | `e68b006` |  | capture | review: hand the leak scanner fixes to SWE-2 for a scoped re-review (round 2) |
|  | `0491d57` | `d45fd3b` |  | capture | review: record SWE-2's round 2 APPROVE of the leak scanner fixes |
|  | `66ebd3a` | `804575e` |  | capture | merge: land the leak scanner and its CI step (SWE-2 round 2 APPROVE) |
|  | `47a8746` | `f953c9c` |  | capture | fix(brief): close the loose ends SWE-2's round 2 found (0800107) |
|  | `801f9b5` | `e5f93a0` |  | capture | merge: land the Word reader's round-2 P3 fixes (f953c9c, not re-reviewed) |
|  | `bb9ba9f` | `7729037` |  | cite | fix(cite): match case names by Bluebook abbreviation and distinctive word |
|  | `0f38256` | `5b7df98` |  | cite | fix(cite): close SWE-2's open P3s from rounds 1 and 2, and three more |
|  | `4f7cb89` | `e66f291` |  | cite | spec: cite lookup revision 2 — answer SWE-2's code review, rounds 1 and 2 |
|  | `8c4767d` | `2f4d61a` |  | cite | review: hand the cite lookup P3 fixes to SWE-2 (round 3) |
