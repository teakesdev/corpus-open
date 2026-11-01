# History rewrite before the first push (2026-11-01)

`feat/matter-capture` had 79 commits that had never been pushed. Before its first push to this public repository, they were rewritten with `git filter-repo`, over `96e1306..feat/matter-capture` only. `main` and everything before `96e1306` are unchanged.

What changed in those commits:
- **Reviewer transcripts removed.** The 22 SWE-2 transcripts (`swe2-transcript*.md`) recorded the reviewer's whole session environment, not just the review. They are kept outside the repository. Each review folder keeps its report (`SWE2-REVIEW.md`), handoff, prompt and log.
- **Home paths shortened.** Paths in the handoffs, prompts, one PR note, one review and one spec now start with `~`.
- **One fixture renamed.** A file name in a `tests/test_briefcheck.py` fixture changed.

Nothing else changed. Each commit keeps its author, dates, parents and message, except that commit messages naming another commit in this range now give its new hash. A script compared every old commit with its new one and found no other difference.

Review handoffs and reports pin commits by their old hashes. This table gives each commit's new hash, in history order.

| Before | After | Commit |
| --- | --- | --- |
| `db947bf` | `db947bf` | fix(ingest): store matter-relative rel_path; cwd-independent verify |
| `503cd4d` | `503cd4d` | docs: write→ingest→verify-from-foreign-cwd; MCP ingest_path/verify |
| `58809a3` | `58809a3` | docs: add dogfood-iteration product note (spec for §5.1 matter capture) |
| `1d2ca1a` | `1d2ca1a` | feat(capture): matter capture + capture list (dogfood-iteration §5.1) |
| `550292c` | `550292c` | feat(correspondence): custodian notice ledger, nosend (RFC 0006) |
| `64f511f` | `64f511f` | feat(attribution): record agent authors and the person who directed them |
| `be38709` | `be38709` | fix(consent): open /dev/tty with separate read and write handles |
| `0954f79` | `0954f79` | docs: design for brief check, deadline lifecycle and subcases (approved by ty 2026-10-23) |
| `eee92c1` | `eee92c1` | feat(store): connect_readonly for checkers and cross-matter lookups |
| `f114d3a` | `b0f3d59` | feat(brief): brief check ties every cite, quote and fact in a draft to the store |
| `bff7589` | `261c683` | feat(authority): authority check records source evidence; authority import reads ledgers |
| `6b4045c` | `bc79fda` | fix(authority): conservative ledger mapping; dry-run import opens the store read-only |
| `08633de` | `3fd6f1e` | docs: README section on checking a brief before filing |
| `90000cc` | `90000cc` | feat(deadlines): update with reason and events trail; OVERDUE in list |
| `b230ee8` | `b230ee8` | feat(subcase): subcase init and nested-matter ingest boundary |
| `e767159` | `e767159` | feat(subcase): parent: references, hash-checked and never copied |
| `bcb9652` | `bcb9652` | feat(subcase): status lists a parent's subcases and names a subcase's parent |
| `450b023` | `3b1340d` | merge: deadline lifecycle and subcases (feat/deadlines-subcases) |
| `3a8c78b` | `0d4f728` | feat(brief): brief check follows a subcase's parent: references, read-only |
| `3d88bb6` | `03b1f48` | docs: README section on deadline updates and subcases |
| `d64e6bc` | `6c7cd9d` | review: hand brief check, authority checks, deadlines and subcases to SWE-2 |
| `cfdfc95` | `e81279d` | review: record SWE-2's REQUEST CHANGES of brief check, authority checks, deadlines and subcases |
| `0791064` | `03f9f2d` | fix(brief): answer SWE-2's review — Id. binding, altered quotes, exit contract and 17 P3s |
| `8bf2c5a` | `b53cd5b` | review: hand the round-1 fix to SWE-2 for a scoped re-review |
| `81bb2da` | `7949264` | review: record SWE-2's round-2 APPROVE of the brief-check fix |
| `766440e` | `631d4a0` | fix(brief): SWE-2 round-2 follow-ups N1-N5 |
| `7b78d9b` | `883584a` | feat(brief): --mode complaint; numbered paragraphs are no longer skipped as headings |
| `15c2b7e` | `5bba2aa` | review: hand brief check complaint mode to SWE-2 |
| `3a63d41` | `2b5c557` | review: record SWE-2's APPROVE WITH CHANGES of brief check complaint mode |
| `3dceaa5` | `e023ceb` | fix(brief): key every rule in a stored "5(b); 6(a); 15" rule list |
| `c63d392` | `6deab52` | fix(brief): key every section of a stored "§§ 21-21-3, 21-3-15" statute list |
| `3b4906c` | `31294aa` | fix(brief): split a draft's "§§ 21-21-3, 21-3-15" list into one cite per section |
| `af0a7dd` | `06e0bd0` | fix(brief): end a "§§" list where the next citation starts |
| `f0bf852` | `1ed7fe2` | review: hand draft-side §§ statute lists to SWE-2 |
| `3aaab1f` | `33a7b1f` | review: record SWE-2's REQUEST CHANGES of draft-side §§ statute lists |
| `2b229b6` | `fae7e7d` | fix(brief): answer SWE-2 round 1 on draft "§§" lists (P1, P2, P3 tests) |
| `ca4829c` | `5cbc037` | review: hand the round-1 fix of draft-side §§ lists to SWE-2 for a scoped re-review |
| `e023540` | `7c613fd` | review: record SWE-2's round-2 APPROVE of draft-side §§ lists |
| `6ac2b01` | `0afb7a0` | merge: "§§" statute lists and stored rule lists in brief check (fix/brief-draft-statute-lists) |
| `4d21b9b` | `29fb90d` | merge: feat/matter-capture (0afb7a0, §§ statute lists) into complaint-mode follow-up |
| `e77da0c` | `dd517cb` | fix(brief): answer SWE-2's complaint-mode review — headings never swallow cites |
| `6209e76` | `6c11d8b` | review: hand the complaint-mode fix to SWE-2 for a scoped re-review |
| `978dfc2` | `db4ba4e` | review: record SWE-2's round-2 APPROVE WITH CHANGES of the complaint-mode fix |
| `117e9bc` | `58f2e1d` | merge(squash): brief check reads three cite styles it used to BLOCK (fix/brief-false-blocks) |
| `3da135f` | `443c43a` | merge(squash): a statute short form binds only to a logged key's own section (fix/brief-short-section) |
| `11f607b` | `2079832` | merge(squash): a dotted "Section 552.101" is one section, not "Section 552" (fix/brief-dotted-section) |
| `5c0a42c` | `495ef74` | merge(squash): brief check survives hard-wrapped drafts and parentheticals in "§§" lists (fix/brief-rule-linebreaks) |
| `3fa4e2c` | `dd2778f` | merge(squash): state code heads start at the state; the splitter knows the 26 missing state abbreviations (fix/state-abbrev-cites) |
| `e4e4f0c` | `033755b` | merge(squash): a word-like state abbreviation ends a sentence unless a cite goes on (fix/brief-word-states) |
| `69ab284` | `77ca8e5` | merge: feat/matter-capture into complaint-mode round-2 follow-up |
| `eadb780` | `007fed8` | fix(brief): headings are checked for cites and quotes; numbered fact subheadings keep the section |
| `16c8fa8` | `8affc46` | review: hand the complaint-mode round-2 fix to SWE-2 for a scoped re-review |
| `57c14dd` | `78c2dc6` | review: record SWE-2's round-3 APPROVE WITH CHANGES of the complaint-mode fix |
| `f7119bd` | `96fb7c0` | merge(squash): a list member with another hyphen count no longer ends the list (fix/brief-hyphen-members) |
| `a613f06` | `e1b7f89` | merge(squash): a section list that stops at a number says so (fix/brief-list-stops) |
| `53f82ec` | `dda8801` | merge(squash): a cite in a parenthetical after a statute is checked on its own (fix/brief-xref-paren-cite) |
| `48bf50a` | `3a4783a` | merge(squash): a "§"-led comma list is read member by member (fix/brief-sign-lists) |
| `f3045a9` | `b463ee3` | merge: feat/matter-capture into complaint-mode round-3 follow-up |
| `d89743f` | `f2d52e4` | fix(brief): more non-fact section names end a fact section; README lists every heading veto |
| `27ea7fb` | `05646ad` | merge(squash): a dot between two digits stays in an authority key, so 5.1 is not 51 (fix/brief-dotted-keys) |
| `9fb77b4` | `6f06ac7` | feat(rules): attach a court's rules to a case as a verified rules pack |
| `664b68e` | `34fadd1` | review: hand the court rules pack to SWE-2 for a code review |
| `9d7359b` | `54a940a` | review: record SWE-2's APPROVE WITH CHANGES of the court rules pack |
| `2d5fba6` | `17c1240` | fix(rules): answer SWE-2's review of the rules pack (F1, F2, F4-F7, F9) |
| `3304ebd` | `e880891` | spec: court rules pack revision 2 — answer SWE-2's code review (APPROVE WITH CHANGES) |
| `ae6ff62` | `a45feab` | feat(ui): run the kit in a local browser for people who don't use a terminal |
| `0832eaa` | `5936dc6` | feat(amend): a per-case amendment list, in the CLI, MCP and the local browser UI |
| `9f4c61e` | `a780c86` | review: hand the local frontend to Astra for a code review |
| `520b6e6` | `7d3a4bd` | review: record Astra's round 1 REQUEST CHANGES of the local frontend |
| `f27c766` | `754053a` | fix(ui): answer Astra's code review of the local frontend (F1-F9) |
| `b4de5b2` | `7dd1ff4` | spec: local frontend revision 2 — answer Astra's code review (REQUEST CHANGES) |
| `063b0a4` | `7ce1311` | Merge feat/frontend (7dd1ff4) into feat/amend-list |
| `46d5e48` | `f5cddf3` | fix(amend): follow the fixed frontend's conventions (F4-F7) and add page tests |
| `6e9202e` | `1a7b56c` | feat(rules): record a check of the court's website for an attached pack |
| `f28eba7` | `70f5d2e` | feat(rules): build packs from outline-numbered sources (--markers dotted) |
| `a95dc7a` | `da1509d` | fix(rules): a wrapped marker indented unlike its list yields to the real item |
| `43e39af` | `ce69b76` | feat(deadlines): record who must act by a deadline (--owner ours\|theirs\|court) |
| `6edf859` | `6f1837c` | fix(brief): read '7(b) and (c)' as 7(b) and 7(c) (SWE-2 F8) |
| `93d03a8` | `76c1f1b` | spec: design drafts for trigger deadlines (G5) and chambers contacts and judges' practices |
