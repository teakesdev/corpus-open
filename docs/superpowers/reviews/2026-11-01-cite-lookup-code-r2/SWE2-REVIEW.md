I'll start by reading the handoff document.I'll read the review diff, my round-1 report, and the main source file in parallel.I have the current `citelookup.py` at `69dd62e`. Now let me see the rest of the diff — the actual code and test changes.The diff is fully read. The fix touches two hunks in `citelookup.py` plus two new tests. Now I need to verify: (1) the test file at `69dd62e` actually contains these tests and check the `result`/`cluster` helpers, and (2) `briefcheck._case_name` — whether extracted draft names can retain trailing periods, which decides if the "full word + period" residual in `_cut` is exploitable in practice.Now let me check `briefcheck._case_name` — whether extracted names can end in a period, which determines if the "full word + period" residual in `_cut` is reachable in practice.`_case_name` keeps verbatim tokens — a trailing-period word like "Marsh." stays in the extracted name (the sentence-boundary break only fires when scanning back across a non-name word; a period-final name token at the boundary is kept). That's relevant to the P2-2 residual. Let me confirm all callers of `_same_case` and `interpret`, and check for any other readers of `normalized_citations`.Let me verify the `NAME_CONNECT` contents to confirm my `_case_name` trace, and look at the `matter.py` call site.Let me look at `ABBREV` and the surrounding constants to complete the `_case_name` trace.All callers confirmed. Let me do a final consistency check on the design doc's NAME DIFFERS contract and confirm the diff's code hunks are exactly the two shown.Verdict: **APPROVE** — both round-1 P2s are addressed with discriminating tests; the diff introduces no P1/P2 breakage. It is ready for the founder's first live call and to land on `feat/matter-capture`.

# SWE-2 re-review, round 2: `e72359d..69dd62e`

Scope: `review.diff` (306 lines) plus the working files at `69dd62e`. The code change is exactly two hunks in `matterkit/citelookup.py` (`_fold`/`_cut`/`_same_case` at 150–178, `interpret` binding at 252–264) and one hunk of two tests in `tests/test_cite_lookup.py` (208–240). Everything else in the range is round-1 review paperwork. Constraints honored: read/grep/glob only; no shell, no network.

## Round-1 findings

### P2-1 — `interpret` binding a line by `start_index` alone: **ADDRESSED**

`matterkit/citelookup.py:252–264`. Each item still binds via `starts.get(item["start_index"])` with the `citation`-echo fallback (257–258), and the first well-formed answer for a line still wins. The new gate at 261–263 drops the item — leaving the line NOT LOOKED UP — when the echoed `citation` is a string that differs from the bound line, or when `normalized_citations` contains a string equal to a *different* payload line (`others`, 261).

My round-1 scenario verified by tracing: payload `"1 F.3d 1\n2 F.3d 2"`, item `{start_index: 9, citation: "1 F.3d 1"}` → `starts[9]` is `"2 F.3d 2"`, `echo != line` → dropped → both lines NOT LOOKED UP instead of line 2 judged with line 1's cluster. `test_an_answer_whose_index_and_echo_disagree_is_dropped` (`tests/test_cite_lookup.py:208`) covers index/echo disagreement, `normalized_citations` naming another line, a non-payload echo, no-shadowing by a dropped item, and the index-miss echo fallback — all discriminating.

### P2-2 — bare 4-char prefix conflating surnames: **ADDRESSED**

`matterkit/citelookup.py:150–178`. `_fold` is the old fold shared by `_words` (154) and the new `_cut` (158), which returns the words a name terminates with a period. In `_same_case` (163–178) a prefix match now requires the shorter word to appear period-cut **in its own name** — `a in my_cut` for draft words, `b in their_cut` for CourtListener's — with the same ≥4 minimum; otherwise whole-token equality. The rule is a strict subset of the old one (every new-rule match was also an old-rule match), so nothing new can match; all lost matches move toward NAME DIFFERS/AMBIGUOUS, the safe direction.

`test_only_a_word_cut_short_with_a_period_matches_by_prefix` (228) pins `Roger`/`Rogers`, `Marsh`/`Marshall`, `Hill`/`Hills` → NAME DIFFERS, and `Bd. of Educ.`/`Board of Education` in **both** directions → FOUND. Consistent with the design's §3 wording ("shares no significant word").

## New breakage in the fix diff

None at P1/P2. Two P3 observations:

- **`citelookup.py:201` — dropped answers report the wrong reason.** An item dropped as inconsistent lands in `_judge(cite, None)`, producing the note "CourtListener did not read this line as a citation" — accurate for genuinely unanswered lines, misleading for an answer dropped as malformed; in a real upstream-inconsistency incident the report would obscure what happened. Outcome and exit code are still correct. A distinct note ("answer dropped: index and echoed citation disagree") would fix it. Scenario: `{start_index: 9, citation: "1 F.3d 1"}` on the two-line payload → line 2's row reads "did not read this line" though an answer existed and was distrusted.
- **No new crash surface.** `others` (261) only admits elements equal to a payload-line string — hence hashable strings; `n in lines` filters non-strings before they enter the set. `echo in lines` is list membership, which cannot raise on JSON types. A dict `citation` or non-string entries in `normalized_citations` are ignored cleanly.

## Answers to points 1–3

**1. P2-1 binding.** The remaining ways a malformed answer could misjudge a line are the unverifiable ones: an item omitting `citation` (or with a non-string `citation`) binds on `start_index` alone — absence cannot contradict, and a fully consistent lie is beyond any cross-check. Everything contradictory is caught: disagreeing string echo, `normalized_citations` naming another payload line, duplicate bindings. A dropped item never enters `got`, so it cannot shadow a later good answer (tested, 223–226). Two disclosed edge notes: `start_index` as an unhashable type still raises `TypeError` at `starts.get` (256) — pre-existing at `e72359d`, out of scope; `start_index: false` resolves to offset 0 (`False == 0`) — also pre-existing. The strict-echo judgment call is correct: documented-shape answers echo the sent text verbatim, so the only cost of strictness is a false NOT LOOKED UP (exit 2) if the live API ever echoes a re-spelled form — e.g., a `normalized_citations` collision when a draft cites parallel reporters of the same case. That fails toward human review and the first live call would show it.

**2. P2-2 matching.** The prefix rule is now limited to period-cut words on **both** sides, verified against the code and tests. The disclosed residual is real and reachable: `_cut` cannot distinguish an abbreviation from a full word followed by a period, and `_case_name` (`briefcheck.py:394–409`) keeps capitalized period-final tokens verbatim — "Alpha v. Marsh.," extracts `"Marsh."`, so `"Marsh."` still prefix-matches `"Marshall"`. Confirmed by tracing the extractor (the sentence-boundary break at 400–402 requires `name[0]` uppercase, which `v.` isn't). `Jr.`/`Sr.`/`III.` cannot reopen it (len < 4). This narrows the surname hole from "any bare surname" to "surname written with a trailing period" — unusual drafting; I'd rate it P3, consistent with the handoff's own disclosure. Consonant abbreviations (`Natl.`, `Assn.`, `Mfrs.`) still don't match — known safe-direction noise.

**3. Nothing else moved.** `_judge`'s multi-opinion pick (218) uses the same `_same_case`, narrowed identically — a pick formerly made via a bare prefix now yields AMBIGUOUS instead of FOUND, the safe direction. `record` (344–385), `Report`/`render_dry_run`, `draft_cites`/`payloads`/`validate_payload`, `_cluster`/`_clean`, the adapter, `matter.py` and `consent.py` are untouched by the diff; `interpret`'s signature and one-Result-per-cite contract are unchanged, so the call site at `matter.py:548` is unaffected.

## Readiness

Ready. Both required changes are in, tested, and fail in the safe direction wherever residual risk remains (absent-echo misbinding, period-final surnames, echo/normalized false-drops). The two P3s above are optional polish, not blockers. Test evidence is the recorded 50/50 file run and 1227-test gate; my independent trace of the new tests and code paths agrees with those results.

## Model

SWE-2 Max.
