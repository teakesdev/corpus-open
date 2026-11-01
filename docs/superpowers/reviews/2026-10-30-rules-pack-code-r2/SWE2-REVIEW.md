I'll start by reading the handoff document and my round-1 report in parallel.Now let me read the review.diff file containing the complete diff.I have the full diff. Now let me read the actual code at 3304ebd to verify context — starting with `matterkit/rules.py`.Now let me check `LOCAL_WORDS_RE` in briefcheck.py and look at relevant parts of the code.The fix code is clear. Now let me verify a few supporting details: `ingest.ingest_paths` transaction behavior (for the atomicity claim), `store.connect`, and how `cmd_rules` handles `stale`/`list`.`ingest_paths` commits internally at line 178 — important for the atomicity analysis. Let me read it and `store.connect`.`ingest_paths` commits internally — the atomic block covers the event + rules.json only; file copy/registration is idempotent. Now let me check the CLI wiring for `stale`/`list`/`attach` and the test helpers.The CLI is `matter.py` at the repo root. Let me check the `rules` subcommand wiring there.Now let me look at the test files to verify helper functions used by the new tests.Let me check how briefcheck renders pack state (`[STALE]`/`[UNKNOWN]` in the header).Let me verify `.matter` is excluded from document registration (the README/comment claim that rules.json isn't a registered doc) and check `test_briefcheck.py`'s `Base`/`codes` helpers.`.matter` is in `SKIP_DIRS` — the "rules.json is not registered" premise holds. Final checks: dangling references to the removed `LOCAL_WORDS`, the MCP handler, and `load_docket` signature.All verification is complete. I have everything needed for the report. One note: the grep output shows `SWE2-REVIEW.md` in the r2 folder already accumulating my session's text — that's the launcher's output channel, not something I need to touch; my final message is the report.

Here is my review.

---

**Verdict: APPROVE**

All seven founder-approved fixes (F1, F2, F4, F5, F6, F7, F9) are implemented correctly at `3304ebd`, each pinned by at least one discriminating test. I found no new P1 or P2 breakage in the fix diff; only P3-grade residuals on contrived inputs, listed below. F3 and F8 are unchanged per the founder's decision and the diff does not make them worse.

## Round-1 findings

| Finding | Status | Evidence at `3304ebd` |
|---|---|---|
| **P2 F1** anchor-shaped source line mints an anchor | **ADDRESSED** | `ANCHOR_LINE` (rules.py:50) now requires `**[` immediately after the tag (`(?=\*\*\[)`), so registration needs the emitted tag+label shape. A bare-tag source line stays text, prints verbatim in `show` (rules.py:493-499 strips nothing it doesn't match), and the full emitted shape in source still fails the word check because `ADDED` (51) strips it → mismatch → refuse (rules.py:324-328). |
| **P2 F2** dropped page numbers unrecorded; decorated arabic bucketed as roman | **ADDRESSED** | Bucketing is by captured value: `PAGE_NO.fullmatch(...)["n"].isdigit() != roman` (rules.py:163-164) puts `- 3 -`/`Page 3 of 40` in the arabic sequence. Singletons per bucket emit a warning (rules.py:165-167). `manifest["dropped"]` lists `{page, text}` for each (rules.py:445). |
| **P2 F4** wrapped cross-ref takes anchor; note pointed at the victim | **ADDRESSED** (as scoped) | `cur["at"]` records (label, line) per level (rules.py:285, 312, reset 310); an out-of-sequence repeat names the held anchor and its line (rules.py:299-304). The wrong anchor still persists — that was inherent; the approved edit was the named note. |
| **P3 F5** `LOCAL_WORDS` diverged from `LOCAL_WORDS_RE` | **ADDRESSED** | `resolve` uses `briefcheck.LOCAL_WORDS_RE.fullmatch(s.rstrip("."))` (rules.py:771); plural "Rules" and Civil/Criminal/Bankruptcy/Admiralty/Court now parse. |
| **P3 F6** non-digit `num` in `--heading-regex` crashed | **ADDRESSED** | `re.fullmatch(_NUM, m["num"])` → `RulesError` (rules.py:264-266); also refuses `7.1.2`-style nums that built unresolvable anchors. |
| **P3 F7** prefix checks bypassable/missed | **ADDRESSED** | `read_attachments` requires `cite_prefix`/`anchor_prefix` per entry plus type-checked `review_after_days`/`current_as_of` (rules.py:578-586). `load_attached` marks prefix-sharing packs NOT USABLE from manifest data (rules.py:615-622). `attach` refuses cite- and anchor-prefix clashes using manifest prefixes where readable (rules.py:643-653). |
| **P3 F9** robustness bundle | **ADDRESSED** | Future date → UNKNOWN (rules.py:549-550). `stale_report` loads packs, reports NOT USABLE, exits 1 for any non-ok (rules.py:847-862). `pack_dir == case_dir` refused (rules.py:632-633). Event insert and `rules.json` write are inside one `with conn:` block (rules.py:693-698). |

## New breakage in the fix diff

**P1 / P2:** none.

**P3 — `ANCHOR_LINE`'s lookahead accepts `**[` without a closed label (rules.py:50).** The emitted shape is `**[label]** `, but the lookahead only checks `\*\*\[`. A source line `<a id="lucivr-9"></a>**[unclosed` survives the word check (`ADDED` requires `]**`), then registers `lucivr-9` as an anchor; a colliding id still refuses the pack. Scenario: adversarial/patological source text — strictly narrower than F1 but the same channel. Exact change: extend the lookahead to `(?=\*\*\[[^\]\n]*\]\*\*)`.

**P3 — `LOCAL_WORDS_RE` is tighter than the grammar it replaced (rules.py:771 vs briefcheck.py:159-161).** Its sub group allows no whitespace and max 4 chars: "Local Rule 7 (b)", "Local Rule 7 ." and "Local Rule 7(iiiii)" resolved at `9fb77b4` and now report "not a rule cite", while prefix cites still accept the space via `_SUB`. Marginal; real cites don't space that way.

**P3 — `list` still renders staleness from entry fields (rules.py:838)** while `stale` correctly reads the manifest. On a hand-edited entry the display can diverge from the manifest date; cosmetic only — `stale` (the gated command) and `_pack_cell` in brief check both read the manifest.

**P3 — optional `num` group still crashes (rules.py:264).** A custom `--heading-regex` whose `(?P<num>)` doesn't participate yields `m["num"] is None` → `TypeError` from `re.fullmatch` — same uncaught-crash class as before (was `AttributeError` in `_num_key`). Trivial change: `m["num"] or ""`.

## Answers to points 1–5

**1. F1 — can a source line still produce a usable anchor?** Only via the residual above: it needs `<a id="X"></a>**[` with no `]**` — anything closer to the real shape is stripped by `ADDED` and fails C1 (refuse, correct direction). A tag alone, a tag mid-line, a tag split across lines, or two tags on one line all stay text (lookahead fails → no registration; `body_words` keeps them verbatim → words match). `ADDED` is unchanged and still strips exactly the emitted markup. The F1 channel is closed for every realistic input; the contrived unclosed-label case remains.

**2. F2 — is a real one-word line dropped?** Yes, still — that's inherent to edge-candidate dropping, but it is no longer silent: every dropped line lands in `manifest["dropped"]`, and singletons get a warning. A lowercase roman word ("i", "v", "c") at a page edge in an arabic-numbered doc is the lone roman → dropped + warned + listed — auditable, the designed prompt to a human. A stray `- 99 -` among arabic pages now fails closed inside the arabic bucket (named in the error) instead of dropping silently as a one-member roman bucket. The lone-number warning fires exactly once per singleton bucket and nowhere else.

**3. F7 — can two packs still answer the same cite or anchor?** No path I can construct: `attach` refuses both cite-prefix (normalized) and anchor-prefix clashes; `load_attached` catches hand-edited `rules.json` collisions from the sha-verified manifests and marks the later entry NOT USABLE everywhere (`list`, `stale`, `resolve`, brief check, `deadline add`). A hand-edited entry whose prefixes differ from its manifest is harmless — the manifest wins, and sha fields gate file substitution. `lucivr` vs `lucivr2` (prefix-of) is safe with exact-match checks: anchor ids are exact dict keys — `lucivr-7` can only live in one pack's dict. `--replace` skips the pack's own old entry, so a new prefix on replace works, and history entries aren't re-checked. `rules.json` written by the round-1 code already contained both keys, so no self-incompatibility.

**4. F9 — is the attach transaction really atomic?** The attach *record* is: the event INSERT and `_write_atomic(rules.json)` sit in one `with conn:` block — a failure before commit leaves neither (pinned by `test_a_failed_event_leaves_the_pack_unattached`). Two honest residuals: (a) the copied files and `documents` registrations persist — `ingest_paths` commits internally (ingest.py:178) — but that state is idempotent and self-heals on retry, matching the test's intent; (b) if the DB commit itself fails *after* `os.replace` wrote `rules.json`, the rules.json-without-event half-state remains — commit-failure-only, unavoidable without 2PC, and far rarer than the fixed path. `stale` exits 1 for STALE, UNKNOWN (including future dates), and NOT USABLE.

**5. Tests — discriminating?** Yes, all 15 pin their finding: under `9d7359b`'s code each fails or errors for the right reason (e.g. rules.json present after a failed event → `assertFalse` fails; fake anchors register/refuse → `test_anchor_shaped_source_text_is_not_an_anchor` errors; `- 1 -` mis-bucketed → build refuses). The reworked `test_a_stale_pack_says_so_in_the_header` uses a genuinely old pack — it would also pass at `9fb77b4` (STALE predates the fix), so it's a strengthening of an existing pin rather than a discriminator; its companion (`test_a_date_after_today_is_not_called_current`) is the discriminating one for F9.

## Readiness

The fix round is ready to stay landed on `feat/matter-capture`. The recorded gate (811 tests OK at `2d5fba6`/`3304ebd`) is consistent with my static reading. The P3 residuals are optional hardening for a later pass — none affects honest input or blocks the merge.

## Model serving me

SWE-2 Max (Devin).
