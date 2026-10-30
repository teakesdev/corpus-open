# Court rules pack: design

**Date:** 2026-10-30 · **Author:** agent:claude-opus-5-5, directed by ty · **Tier:** Large because it adds a subsystem: a rules module, a CLI command and an on-disk pack format that brief check, deadlines and future forecasts will depend on.

Draft. Not reviewed. ty decides whether a reviewer seat sees it.

## 1. Problem and intent

A matter has no attached copy of the rules that govern it: the Federal Rules, the district's local rules, standing orders and judges' practices. Every agent run re-finds them. A private dogfood run on 2026-10-30 found:

- the only copy of a district's local rules was a PDF inside an old run folder;
- the default `grep` treated its text extraction as binary (form feeds);
- the response and rebuttal clocks had to be read from page text by hand;
- the copy's "current as of" date was flagged nowhere;
- brief check already recognizes local-rule cites (`briefcheck.LOCAL_RE`) but cannot resolve them to text;
- deadline rows keep their rule as free text (`deadlines.add(rule=...)` → `rule_source`).

**ty's intent** (2026-10-30, 10:23, verbatim): "We should have some sort of local rules .md file or some way to easily attach the ruleset of a court to a case." Scope beyond that sentence has **not** been confirmed with ty.

## 2. Decision

A **rules pack** is one rule set at one version (e.g. a district's local rules current as of a date, or the FRCP as amended on a date). It is a folder holding:

- `manifest.json`: `id`, `court`, `title`, `cite_prefix` (e.g. `L.U.Civ.R.`), `current_as_of`, `source_url`, `source_sha256`, `retrieved_at`, `extractor`, `token_count`.
- `rules.md`: one heading per rule, an `<a id>` anchor and a bracketed label per subsection, and the source words unchanged.

A case attaches packs in `<case>/.matter/rules.json`. Each entry gives the pack id, version and path; the pack itself is copied into the case and ingested, so `verify` covers it. New module `matterkit/rules.py`; CLI `matter rules attach|list|show|stale`; MCP tool `rules_show`.

**There is no SQLite schema change and no edit to `versions.py` or to the `verify` count axes,** because the Cursus observer pins both.

**Contracts created:** the pack manifest (v0.1); the anchor grammar `<prefix>-<rule>-<sub>…` (L.U.Civ.R. 7(b)(4) → `lucivr-7-b-4`; a list nested under a capital item → `…-B-1`); and the per-case `rules.json`.

## 3. Options considered

- **A. File packs in the case, text verified against the source (recommended).** Works offline, is auditable, and reuses ingest and verify. A private prototype on 2026-10-30 converted a 164-page local-rules PDF: 33 rules, 416 anchors, word sequence identical to the source text layer.
- **B. A rules table in `matter.db`.** Rejected: a schema change for unversioned cases plus the Cursus pin, with no gain over files.
- **C. Fetch from court websites on demand.** Rejected: not deterministic, it is egress under `consent.py`, and an offline copy with provenance would still be needed.

## 4. Failure modes

| What could go wrong | How a test catches it |
|---|---|
| Extraction alters or drops words (columns, glyph loss, hyphenation) | The build fails unless the Markdown's words, with the added markup removed, equal the source text exactly. Synthetic fixtures: two-column page, missing-glyph font |
| An anchor is mislabeled, e.g. a numbered list under a capital-letter item | A fixture with (e)(3)(B)(1)–(3); assert unique ids and in-order labels under each parent |
| A real one-word line is dropped as a page number | Assert that the dropped lines are exactly the page-number sequence |
| A stale pack is cited as current | `rules stale` against a manifest older than its stated amendment cycle; brief-check output shows the pack version |
| `show` resolves to the wrong rule set (FRCP 15 vs. L.U.Civ.R. 15) | Lookup is scoped by prefix; a bare "Rule 15" lists every candidate and never guesses |
| An attached pack drifts from its registered bytes | Packs are ingested; `verify` reports CHANGED |

## 5. Cut line

Out of scope:

- scraping court websites;
- parsing every court's layout (start with one synthetic local-rules layout and a public FRCP text);
- computing deadlines automatically from rule text;
- judge-practice collection;
- the opposition-forecast feature, which gets its own design.

## 6. Tasks

1. **Builder:** `matterkit/rules.py` (text → `rules.md` + manifest, word-identity check, anchor assignment including nested lists). Tests: `tests/test_rules_build.py` with synthetic fixtures (form feeds, page numbers, nested list, two columns).
2. **Attach / list / show:** `matter.py rules …`, `.matter/rules.json`, ingest on attach. Test: a synthetic case attaches a pack and `show "L.U.Civ.R. 7(b)(4)"` prints verbatim text plus the version.
3. **Staleness:** `current_as_of` in the manifest; `rules stale`. Test with a dated manifest.
4. **Brief check:** resolve `LOCAL_RE`/`RULE_RE` cites against attached packs; WARN on a subsection that no pack contains; BLOCK semantics unchanged. Tests: synthetic briefs.
5. **Deadlines:** `deadline add --rule <anchor>` records the anchor and pack version in `rule_source`. Test.
6. **MCP:** `rules_show` in `matter-mcp.py`. Test.

Gate: `python3 -m unittest discover -s tests -v` (ty to confirm; the repo has no AGENTS.md).

## 7. As built (2026-10-30, branch `feat/rules-pack`, not reviewed)

ty approved building (2026-10-30, 10:53). Tasks 1–6 are done in `matterkit/rules.py`, `matter.py`, `matter-mcp.py` and `matterkit/briefcheck.py`. Tests are in `tests/test_rules_build.py`, `tests/test_rules_attach.py` and `tests/test_briefcheck_rules_pack.py`, all synthetic. Deviations from the plan above:

- **`matter rules build`** is a CLI command as well as a module function. It takes a `.txt`, or a `.pdf` through `pdftotext -layout`.
- **Manifest.** It adds `format`, `anchor_prefix`, `review_after_days`, `region`, `words` (where every source word went), `notes` and `warnings`. `token_count` became `words.body`, and `extractor` moved under `source`. `retrieved_at` is not recorded.
- **Subsection levels follow the marker sequence.** A marker that continues no list is kept as text and listed in `notes`, which stops a cross-reference wrapped onto a new line from becoming a subsection. When `(i)` could be a letter or a roman numeral, the next marker decides. When a number could continue two lists, indentation decides; failing that, the build takes the deeper list and says so in `notes`.
- **Page numbers.** Only a lone number on the first or last line of a page is dropped, and the dropped numbers must follow the page sequence (a page may lack one). Otherwise the build stops.
- **Columns and lost glyphs** are reported as `warnings`, not failures, because the word check compares against the text layer and cannot see them. The § 4 table above implied the word check would catch them.
- **`--start-at` / `--stop-at`** match the first line that begins with the text, skipping contents entries (dot leaders plus a page number).
- **Attach** refuses a second pack that claims the same cite prefix. A pack already inside the matter is registered where it is, not copied.
- **Brief check.** New WARN codes: `rule-not-in-pack`, `rule-subsection-not-in-pack` and `rules-pack-unreadable`. The header line and the "Rule text in attached packs" section appear only when a pack is attached, so other reports are unchanged.
- **Deadlines.** A `--rule` that does not resolve is stored as the free text it was, with a note. Before this change every rule was stored as free text, so existing callers see no change.
- **MCP.** The tool is `matter.rules_show`, matching the existing `matter.*` names.
- **No public FRCP text in the fixtures.** Both test rule sets are synthetic, so nothing is fetched.

**Real-text trial (private, outside the repo).** I built a pack from the 164-page local-rules PDF in a scratch folder with `--start-at PREAMBLE --stop-at "ADMIRALTY AND MARITIME RULES"`. It gave 33 rules, 416 anchors, 0 notes and one two-column warning. The anchors are the same set, in the same order, as the hand-checked prototype's. The trial found the two region-matching bugs that the contents-entry tests now cover. The admiralty rules use their own prefix (L.A.R.) and headings, so they need a pack of their own.

## 8. Revision 2 (2026-10-30): SWE-2's code review

The founder assigned the build (`27ea7fb..9fb77b4`) to SWE-2 (Devin `swe-2-max`, read-only). The verdict was **APPROVE WITH CHANGES**, recorded in [the review](../reviews/2026-10-30-rules-pack-code/SWE2-REVIEW.md). Before amending, I probed each finding with synthetic text against `9fb77b4`. The founder approved fixing every finding except F3 and F8.

| Finding | Amendment here |
| --- | --- |
| **P2 F1** A source line shaped like `<a id="…"></a>` became an anchor | An anchor is the tag *and* a bracketed label (`ANCHOR_LINE`); a tag alone stays source text, and `show` prints it as it is |
| **P2 F2** Dropped page numbers were unrecorded; "- 3 -" was sorted with the roman numerals | The manifest's `dropped` lists each page number left out. Roman or arabic is decided by the number's value, so "- 3 -" and "Page 3 of 40" are arabic. A number that is the only one of its kind gets a warning, because no sequence checks it |
| **P2 F3** Levels decided by the next marker or by indentation leave no note | Not changed (founder). § 7 keeps notes for guesses. SWE-2's example, (h)(1)(A) then (i) then (1), gives the right anchors |
| **P2 F4** A wrapped "(b) of this rule" took the 7(b) anchor | When a marker repeats one already anchored, the note names that anchor and its line |
| **P3 F5** "Local Rules 7" was not read | `show` uses brief check's `LOCAL_WORDS_RE`, so both read the same words |
| **P3 F6** A non-numeric rule number in `--heading-regex` crashed | The build refuses it with a message |
| **P3 F7** Two packs could share an anchor prefix; the clash check trusted `rules.json` | Attach refuses a shared anchor prefix and checks prefixes from each pack's manifest. Entries in `rules.json` need `cite_prefix` and `anchor_prefix`, and a hand-edited entry that shares a prefix is not used |
| **P3 F8** "7(b) and (c)" leaves (c) unchecked | Not changed (founder); a brief-check grammar issue for its own branch |
| **P3 F9** Future dates read as ok; `stale` trusted a changed pack; attaching the matter folder crashed; `rules.json` was written before the event | A date after today is UNKNOWN. `stale` reports a pack it cannot use as NOT USABLE and exits 1. The matter folder is refused as a pack. The event and `rules.json` are written in one transaction. `rules.json` entries are checked for `review_after_days` and `current_as_of` |

The real-text trial, rebuilt after the fixes, gave the same 416 anchors in the same order and a byte-identical body, with 50 page numbers now listed under `dropped`.

Revision 2 has not been re-reviewed.
