I'll start by reading the handoff document.Handoff read. Now I'll read the review diff and my round-2 report in parallel.Now let me locate the code files and the key regexes/functions in the worktree.Now I'll read the key regions of `briefcheck.py` and `authority.py`.The fix at `briefcheck.py:400` is in place. Now let me sweep for any other code that assumes keys end in plain digits, and check `rule_prefixes` (which feeds `bare_rule`/`local_rule`).Now let me read the surrounding context: `rule_prefixes` (:785-800), `_authority_index`/`_lookup` (:1025-1070), and `resolve_authority` (:1245-1295).Now let me read the test files — the full dotted_keys file, plus the relevant regions of short_section and dotted_section.Both P3 literals are fixed (:98 `statute:42usc2.2`, :127 `statute:42usc1.01`) and the inherited-fixture negatives at `short_section.py:81,84` resolve to `Miss. Code Ann. § 21-23-8` logged in `ShortSectionBase.setUp` — real. Let me verify the third flagged key at `dotted_section.py:228` and check `test_briefcheck_rule_lists.py` for the undotted-head coverage.Let me see the top of `dotted_section.py` — its base class and fixture — to verify the `:228` flag is a real key.The `dotted_section.py:228` flag is real: `TestDottedCoordinatedMembers` extends `DottedBase`, whose `LOGGED` includes `"5 U.S.C. § 552"` → `statute:5usc552` is a producible key. All three sweep flags are false positives from inherited fixtures, as the handoff says.

Let me do a final check for other `rule:`-key manipulation anywhere (matter.py, matter-mcp.py, citations.py, evals) and any other `authority_keys` consumers.The grep was truncated. Let me read the overflow file for the remaining matches.All verification is complete. I've traced the fix against every `rule:`-key producer, all `authority_keys` consumers, the new tests, and the sweep's flagged keys. Writing the report.

**Verdict: APPROVE** — `dc3d7fb` correctly fixes the round-2 P2 (`;`-continuation phantom rule keys) and both P3 stale literals, with discriminating tests and no new breakage in the diff.

# SWE-2 re-review, round 3: `fix/brief-dotted-keys` — `cfb3e0f..dc3d7fb`

## Round-2 findings

**P2 — `;`-continuation derives a corrupt rule prefix from a dotted head's normalized key: ADDRESSED.**

`briefcheck.py:400` is now `re.sub(r"\d+(?:\.\d+)*$", "", inside[-1])`. Traced at `dc3d7fb`:

- `authority_keys("Fed. R. Civ. P. 5.1; 6")`: `inside[-1]` = `rule:fedrcivp5.1` → strip → `rule:fedrcivp`; segment `6` matches `RULE_CONT_RE` → `rule:fedrcivp6`. Keys `{rule:fedrcivp5.1, rule:fedrcivp6, full:…}` — the phantom `rule:fedrcivp5.6` is gone and the real continuation is present. The bug had both directions; the fix restores both.
- Every `rule:`-key producer (`scan_authorities` :314 `RULE_RE`, :319 `RULE_TAIL_RE`, :329/:334 `LOCAL_WORDS_RE`, :341 `BARE_RULE_RE`) emits `rule:<letters><\d+(?:\.\d+)?>` — normalized name part contains no digits, and the trailing `\d+(\.\d+)?` is exactly what the new strip removes. Subdivisions (`(b)`, `(a)(1)`) live in the separate `sub` group and never enter a key, so nothing but the number is stripped.
- Chained and mixed cases trace correctly: `5.1; 6; 7` → 5.1/6/7; `5.1; 6.1` → 5.1/6.1; `L.U.Civ.R. 7.2; 15` → `rule:lucivr7.2`/`rule:lucivr15`; `6; 5.1` → 6/5.1; `5(b); 6(a), (d); 15` → 5/6/15.
- Consumers get the corrected keys uniformly: `_authority_index` (:1034), `authority.find` (`authority.py:48-50`), `import_ledger` dedup (`authority.py:239-244`). Recorded red evidence (4 failures + 1 error at `57070d5`, including `rule:fedrcivp5.6` present / `rule:fedrcivp6` missing) is consistent with the defect.

**P3 — two stale negative literals: ADDRESSED.**

- `test_briefcheck_short_section.py:98` now names `statute:42usc2.2`; the logged `42 U.S.C. § 2.2` keys `statute:42usc2.2` — producible.
- `:127` now names `statute:42usc1.01`; logged `§ 1.01` → `statute:42usc1.01` — producible.

**Carried forward, unchanged:** round-1 P3 spacing (`_norm_key("§ 5. 1")` → `51`) and `TOOL_ID` — no founder decision, no diff hunk touches them.

## New breakage in the fix diff

**None found.** The code change is one line; the rest is test-only. Two observations, neither rising to a finding:

- `\d+(?:\.\d+)*$` accepts deeper dot chains (`5.1.2`) than any producer emits (grammar caps at `\d+(?:\.\d+)?`). The extra `*` is unreachable generality, harmless.
- A docket-config `bare_rule`/`local_rule` ending in digits (`rule_prefixes`, :791-793, validates only `isinstance(str)`) would still strip into the name — but `\d+$` and `\d+(?:\.\d+)*$` behave identically for undotted names, and a name ending in `X.Y` (e.g. `"R. 5.1"`) was already broken under the old code, differently. No regression.
- `_ledger` moved verbatim from `TestStoreLookups` to `DottedKeyBase` (:73-80); both consumers inherit it; `os`/`tempfile`/`self.m.dir` all already available. Docstring failure-index item 10 (:28-29) matches the class name and count (5 tests, consistent with 576→581).

## Answers to the handoff's points

**1. Can the strip remove too much?** No for all real inputs. A `rule:` key's normalized name part is letters-only — `(Fed|[A-Z][a-z]{1,5}).R.(Civ|Crim|App).P.|Evid.` → `fedrcivp`/`fedrevid`/`fedrappp`/`missrcrimp`/`texrcivp`; `lucivr`/`lucrimr`; `local_rule`-derived `localbankruptcyrule`-style names; `bare_rule` config. Rule numbers are always `\d+(\.\d+)?` — no letters (the grammar admits none) and no subdivisions (`sub` never enters the key). `inside[-1]` is only ever a producer key — `keys.add` continuations never feed back into `found`. A segment whose last-positioned cite is a statute/case yields `prefix=None`, ending the run — unchanged semantics, pinned by `test_briefcheck_rule_lists.py:51,54`. The only leak is a digit-ending configured `bare_rule`/`local_rule`, which was equally broken before (identical for undotted names; differently-broken for `X.Y`-ending names — pathological either way).

**2. Other plain-digit-suffix assumptions?** None. Swept all `re.sub`/`endswith`/`rstrip`/`startswith`/`\d+$` sites in `matterkit/`: `briefcheck.py:311` splits the *raw* `m["base"]` with `\d+(?:\.\d+)?$` — already dot-aware, producer-side. `authority.py` does no key-string manipulation (`find`/`import_ledger` are `authority_keys` set operations). `_lookup` (:1050) is an exact dict get; `resolve_authority`'s `startswith` (:1260) is on `case:` keys (space-separated `vol rep page`, dots stripped by `_rep_key` — unaffected); `full_rules` (:1281) keys by the raw `num` regex group, not a normalized key; `statute_sections` reads section values, key-agnostic. :400 was the only site, as round 2 concluded.

**3. Are the 5 new tests discriminating, and is the sweep sound?** Yes on both.

- `test_each_segment_after_a_dotted_rule_keys_as_its_own_rule` (:316-324) pins exact key sets — four of six cases fail on the old code (5.6 vs 6, 7.15 vs 15); the undotted-head and subdivision cases are regression guards that passed before.
- `test_a_listed_rule_passes_and_an_invented_one_blocks` (:326-330) covers both directions of the defect — false BLOCK on the listed rule, silent pass on the invented one — plus the dotted head still binding.
- `test_find_answers_the_listed_rule_only` (:332-335) pins `find` both ways (hit on the listed rule, `None` on the invented one; the `None["citation"]` access is what produced the recorded error).
- Both ledger tests (:337-348) pin dedup in each direction — invented-rule row no longer eaten by a stored list, and list + invented row coexist within one ledger.
- The sweep method (name every key literal in negative assertions against the keys the fixture produces) is sound and caught both stale literals. Its three remaining flags are MRO-following gaps, not real staleness — I verified each: `dotted_section.py:228` `statute:5usc552` comes from `DottedBase.LOGGED` (:56, inherited by `TestDottedCoordinatedMembers`); `short_section.py:81,84` `statute:misscode21-23-8` comes from `ShortSectionBase.setUp` (:45). Both producible. I additionally re-checked every other negative key literal in the touched files (`dotted_keys.py:290,296,303,313`; all `assert_unbound` targets in `short_section.py`) — each names a key its own fixture produces.

## Readiness

Ready to land on `feat/matter-capture@48bf50a`. The diff's surface (`authority_keys` `;`-continuation, two test literals, a test-file refactor) is disjoint from the new base work, which is statute/extraction-side (state-abbrev-cites, word-states, heading-sentences, hyphen-members, list-stops, xref-paren-cite, sign-lists) — none of it produces `rule:` keys or reads their digit suffixes. The recorded scratch merge (`merge-tree`, no conflicts, 732 tests OK) is consistent with that; per normal process, re-run the suite on the real merge.

## Model

SWE-2 Max, as my runtime reports it.
