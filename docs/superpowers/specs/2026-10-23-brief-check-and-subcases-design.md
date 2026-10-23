# Brief check, deadline lifecycle and subcases: design

**Date:** 2026-10-23 · **Author:** agent:claude-opus-5-5, directed by ty · **Tier:** Large because it adds a parent/subcase file contract and changes which folders `ingest` registers. Part A alone would be Medium.

## 1. Problem and intent

Dogfooding on a real federal matter showed that Matter Kit keeps evidence but the work that carries risk happens outside it: brief citations and facts are checked by hand in Markdown ledgers, the store's deadlines go stale (passed dates stay `open`, a court-granted date stays "contingent"), a key filing can be missing from the store with nothing saying so, and a related second matter has no separate home. ty's intent: **before an upcoming filing, a draft brief can be checked mechanically so that every record cite, legal citation, quotation and factual sentence is either tied to a source in the store or visibly flagged.** Then fix the deadline and missing-document gaps, and let a spin-off matter live as a subcase of its parent.

## 2. Decision

**A. `matter.py brief check <dir> <draft.md> [--out REPORT.md]`**: new module `matterkit/briefcheck.py`, stdlib only, read-only on the store. It emits a proposition-to-source report and exits non-zero on any blocking finding. It checks:
1. **Record cites** (`Doc. 16`, `Docs. 16 and 18`, `[Dkt. 27 at 2]`, `ECF No. 19`): each must resolve to a registered document. Pinpoint pages are checked against the page count when it is known. The docket map is read from `.matter/docket.json` (`{"16": "Filings/…pdf"}`), and suggested candidates are derived from filenames it can see.
2. **Pleading cites** (`Compl. ¶ 59`, `¶¶ 67–69`): checked against the operative pleading named in `docket.json` (`"operative_pleading"`). If that pleading is absent, the check says so.
3. **Legal authorities**: the existing `kit-cite` extractor, plus new patterns for case names (`X v. Y, 579 U.S. 40, 45 (2016)`), `Fed. R. Civ. P.`, `Rule 15(a)(2)`, `L.U.Civ.R.` and `Id. at`, the last bound to the preceding cite. Each is matched to `authorities` and its stage (`source-checked`, `resolved` or `unverified`) is shown. A cite that is not logged or is `unverified` blocks.
4. **Quotations** inside a cited sentence are looked for in the cited source's text: UTF-8 text documents, or `document_pages` rows when present. A PDF with no extracted text gets "quote not checked". It never counts as a pass.
5. **Factual sentences**: a sentence with no cite inside a section headed Facts, Background or Statement is reported as `UNCITED`. An explicit `{fact:a_…}` tag is checked against that assertion's label, and an `ALLEGATION` or `HYPOTHESIS` presented as fact is flagged, as is an unsupported assertion.
6. **Docket coverage** (the "missing key document" check): every docket number cited in the draft, or in the text of a registered filing, that is not in the store is listed with the cite that revealed it.

The report's own wording is fixed. "Tied to a source in the store" never means "legally correct" or "good law".

**A2. `authority check <dir> <id|citation> --source-url U [--artifact PATH] --pin P [--quote Q] --by agent:X|human`** records a source check as JSON in the existing `check_evidence` column through `transition_authority`. **`authority import <ledger.md>`** proposes rows from an existing ledger table as a dry run. It writes only with `--apply`.

**A3. Attribution**: carry the uncommitted `fact add --author/--directed-by` work from the primary checkout as its own commit, unchanged, and use the same `attribution()` helper in every new write path.

**B. Deadline lifecycle**: `deadline update <dir> <id> --state open|met|superseded|lapsed|vacated [--verification …] [--confidence …] --reason R [--source rel@loc] --by …`. Each change appends an `events` row (old→new, reason, source, actor). `deadline list` marks open dates in the past as `OVERDUE`. There is no schema change: `state` and `events` already exist.

**C. Subcases**: a subcase is its own matter at `<parent>/Subcases/<slug>/` with its own `.matter/` and a `matter.json` (`{"parent": "../..", "relation": "subcase", "title": …}`). `ingest` and `verify` treat any subfolder holding its own `.matter/` as a boundary and skip it. Today a parent ingest would register the child's files, so this is the behavior change. A subcase cites parent documents in place with `parent:<rel_path>@<loc>`, resolved against the parent store and checked by hash. Files are never copied or moved, so the parent's integrity record is untouched. `status` on a parent lists its subcases with their open and overdue deadlines.

## 3. Options considered

- **Brief check as an LLM reviewer.** Rejected: it would be one more unverifiable opinion. Mechanical resolution against the store is the point.
- **Subcase as a `subcase` column in one store** (simpler queries). Rejected: the spin-off has a different court, parties and deadlines, and has to be handed to a different lawyer without the parent's private research. Mixed rows make that export risky.
- **Moving the spin-off's files into the subcase.** Rejected: registered paths would go "missing" in the parent, and the matter's rules forbid rebaselining.

## 4. Failure modes

| What could go wrong | How a test catches it |
|---|---|
| A cite to an unregistered docket entry passes | Fixture draft cites `Doc. 1`, which is absent; expect a blocking `NOT IN STORE` and exit 1 |
| `Id. at 45` binds to the wrong authority | Draft with two cases and `Id.` after the second; expect it bound to the second |
| A quote not in the source is reported as found, or an unreadable PDF counts as a pass | Text source missing the quote gives `QUOTE NOT FOUND`; a PDF without text gives `not checked`, and neither exits 0 |
| An `ALLEGATION` cited as fact passes silently | A `{fact:a_x}` tag on an `ALLEGATION` produces a flag |
| A parent ingest registers subcase files, or a subcase `parent:` ref escapes the parent | Nested-matter fixture: parent ingest count excludes the child; `parent:../x` is refused; a hash mismatch is flagged |
| A deadline state change leaves no trail, or a bad state is accepted | `update` writes an `events` row; an unknown state or missing reason exits non-zero |
| Agent work is stored as `human` | The new write paths require or default to a validated `by`; test that the `agent:` value is recorded |
| The checker writes to the store | Store file hash is identical before and after `brief check` |

## 5. Cut line

Out of scope: DOCX or PDF text extraction (other branches; PDFs report "not checked"), citator or good-law checks, network lookups, schema migrations, Cursus pin changes, porting to the schema-v1 branches, and any filing or sending. Live-store changes (deadline fixes and creating a subcase) are a separate, backed-up case run after the tool lands. They are not part of this branch.

## 6. Tasks

1. Carry the primary checkout's uncommitted attribution and consent-TTY diffs as two commits, unchanged. Proof: their existing tests pass.
2. `briefcheck.py`: record-cite and docket-map resolution, plus docket coverage. Tests: absent `Doc. 1`, pin beyond page count, multi-cite `Docs. 16 and 18`.
3. Pleading ¶ cites, legal-authority matching with new patterns and `Id.` binding. Tests: case-name cite, `Id.` binding, unlogged authority blocks.
4. Quote and factual-sentence checks, `{fact:}` tags. Tests per the failure table, plus the read-only hash test.
5. CLI `brief check` and MCP `matter.brief_check`; report renderer. Test: end-to-end on a synthetic matter.
6. `authority check` / `authority import --apply`. Tests: evidence JSON is stored, the dry run writes nothing.
7. `deadline update` + `OVERDUE` in `list`. Tests: events row, invalid state refused.
8. Subcases: ingest/verify boundary, `parent:` refs, parent `status` listing, `matter.py subcase init`. Tests per the failure table.
9. Case run (separate, after approval): back up the store, fix the stale deadlines with sources, create `Subcases/Related-Claim`, move that track's open tasks into its own todo, run `brief check` on the current drafts.

Gate: `python3 -m unittest discover -s tests -v` (ty to confirm; the repo has no AGENTS.md).

## 7. Revision 2 (2026-10-23): SWE-2's code review

The founder assigned `550292c..3d88bb6` to SWE-2 (Devin, `swe-2-max`, read-only). The verdict was **REQUEST CHANGES**, recorded in [the review](../reviews/2026-10-23-brief-check-code/SWE2-REVIEW.md). Every finding that changes behavior was reproduced with synthetic probes before fixing, and the founder approved fixing all of them.

| Finding | Amendment here |
| --- | --- |
| **P2** uncited facts and quotes were warnings, so exit 0 could claim "all tied" | §2.A5: an uncited factual sentence and a quotation with no cite are `UNCHECKED` (exit 2). An exit-0 report that has warnings says "with warnings to read". |
| **D6** pdftotext deviation | §5: confirmed as built. When `pdftotext` is on PATH, PDF text is read transiently and never stored; `--no-pdftotext` turns it off. Without it, a PDF with no text copy stays "not checked". |
| Docket-map shape (doc nit) | §2.A1: the map is `{"entries": {"16": …, "23-1": …}, "pleadings": {…}, "text_copies": {…}, "bare_rule": …, "local_rule": …}`. A map of any other shape is refused. |
| Local rules (P3) | §2.A3: "Local [Uniform] Civil Rule N" is read under `local_rule` (default `L.U.Civ.R.`), never as a bare federal rule. |

Found while fixing: a sentence split inside a multi-sentence quotation meant that quotation was never checked. The segmenter no longer splits inside quotation marks.

Revision 2 has not been re-reviewed.
