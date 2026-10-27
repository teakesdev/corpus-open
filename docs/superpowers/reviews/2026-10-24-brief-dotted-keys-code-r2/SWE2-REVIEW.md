I'll start by reading the review materials: my round-1 report and the three diffs in the review folder.Now the branch diff and the code file at the relevant lines.The file and diff were both truncated. Continuing both reads.Now the key code region: `_authority_index`, `_lookup`, and `resolve_authority`.I have the core code. Now I'll verify: (a) the old suffix-fallback is gone, (b) sweep for hollow negatives and stale key literals in tests, (c) the `;`-continuation path in `authority_keys` — line 399-401 strips `\d+$` from a normalized rule key, which interacts with kept dots.The suffix fallback is confirmed gone — the short-form path now uses exact section-set membership (:1271). I found a suspect though: `authority_keys` line 399 strips `\d+$` off a normalized `rule:` key to continue a `;` segment — with dots kept, `rule:fedrcivp5.1` yields a `rule:fedrcivp5.` prefix. Let me read the rule-lists test file and the helpers to confirm coverage gaps, plus check where `statute_secs`/`full_statutes` are initialized.The negatives at `dotted_section.py:123,228,313` all name existing keys (the logged cites have undotted sections) — those are fine. But `test_briefcheck_short_section.py` has two more stale literals the sweep missed: `:98` (`statute:42usc22`, logged § 2.2 now keys `42usc2.2`) and `:127` (`statute:42usc101`, logged § 1.01 now keys `42usc1.01`). Let me enumerate all `_norm_key`/`authority_keys` callers and check `authority.py`.The grep output was truncated — let me read the overflow file for the remaining `_norm_key`/`authority_keys` references.I have everything I need. Let me do a final consistency check on the two suspected stale negatives and confirm no other test asserts on a key that can't exist.

The two stale literals are confirmed:

- `test_briefcheck_short_section.py:93-98` (`test_head_before_a_dot_does_not_bind`) logs `"42 U.S.C. § 2.2"` — which now keys `statute:42usc2.2` — but the negative names `statute:42usc22`, a key no code path can produce. Identical defect to `dotted_section.py:303`, which `57070d5` fixed; this one was missed in a file `57070d5` already touched.
- `test_briefcheck_short_section.py:125-127` (`test_section_a_dot_splits_does_not_bind`) logs `"42 U.S.C. § 1.01"` — keys `statute:42usc1.01` — while naming `statute:42usc101`. Same.

And the `;`-continuation bug is confirmed real by re-trace: `authority_keys("Fed. R. Civ. P. 5.1; 6")` — `inside[-1]` = `rule:fedrcivp5.1`; `re.sub(r"\d+$", "", ...)` strips only `1`, leaving `rule:fedrcivp5.`; segment `6` then keys `rule:fedrcivp5.6` — a phantom key matching an unlogged rule, while `rule:fedrcivp6` is never produced. At the old `_norm_key` the same code produced `rule:fedrcivp6` correctly.

Here is the report.

**Verdict: REQUEST CHANGES** — `88ec41b` and `57070d5` correctly answer all of round 1, and the rebase is clean; but the carried `_norm_key` change corrupts the stored-citation `;`-continuation in `authority_keys` (`briefcheck.py:399`), producing phantom `rule:` keys that a wrong draft cite can bind (exit 0) while the real continuation BLOCKs — plus two stale negative test literals the hollow-negative sweep missed.

# SWE-2 review, round 2: `fix/brief-dotted-keys` — `0a6c9c7..57070d5` plus the rebase onto `11f607b`

## Round-1 findings

**P1 — `endswith` suffix fallback binds a suffix-extension section: ADDRESSED.** The code I flagged no longer exists. `resolve_authority`'s statute branch (`briefcheck.py:1264-1277`) now binds a short form only through `full_statutes` (:1267, same raw section string) or design B: `cands = {k for k, secs in self.statute_secs.items() if sec and sec.lower() in secs}` (:1271). That is exact set membership against the section numbers recorded from the logged citation's own text — no string suffix test anywhere in the resolution path. "15.1" ∉ {"5.1"}, "51" ∉ {"5.1"}, "5.1" ∉ {"51"}. My preferred guard is moot; the two new tie tests pin the scenario (below). The pre-existing single-row half (`§ 15.1` alone vs "Section 5.1") is also gone — `dotted_section.py:294-297` covers it.

**P2 — fallback path untested: ADDRESSED.** `TestShortFormKeysStayDistinct` (`test_briefcheck_dotted_keys.py:258-303`) exercises both paths this commit touches: the `statute-short:` key path (:262-281) and the design-B candidate path under a split key space (:283-301), including both P1 tie scenarios. Recorded red evidence is consistent: at `303948c` (new `_norm_key`, old fallback) the two tie cases fail; at `11f607b` (design B, old `_norm_key`) the three `statute-short:` negatives fail on the old key collision.

**P3 — spacing typos (`_norm_key("§ 5. 1")` → "51"): NOT ADDRESSED (deliberate).** Handoff states the founder has not decided; `authority find` semantics shift as recorded. Carried forward unchanged — I verified `_norm_key("§ 5. 1")` still produces `51` (dot dropped by `\.(?!\d)`).

**P3 — `TOOL_ID` unchanged: NOT ADDRESSED.** Unchanged; same defensibility argument as round 1.

## New breakage (range + rebase)

### P2 — `briefcheck.py:399-401`: `;`-continuation derives a corrupt rule prefix from a dotted head's normalized key

```python
prefix = re.sub(r"\d+$", "", inside[-1]) if inside[-1].startswith("rule:") else None
elif prefix and (m := RULE_CONT_RE.fullmatch(seg.strip().rstrip("."))):
    keys.add(prefix + _norm_key(m["num"]))
```

`inside[-1]` is a *normalized* key. With dots kept, a stored citation whose last rule key ends in a dotted number leaves the dot-plus-head-digits in the "prefix": `re.sub(r"\d+$", "", "rule:fedrcivp5.1")` → `"rule:fedrcivp5."`. Every following `;` segment then keys under it. This is in `authority_keys`, so it feeds `_authority_index` (:1033), `authority.find` (`authority.py:48-50`), and `import_ledger` dedup (:239-244). (The `;` feature predates the rebase — `RULE_CONT_RE` and this block were in the pre-restack stack — but the regression is caused by the replayed `_norm_key` change, `d780615`, which is inside this review's rebase scope; I missed it in round 1. `88ec41b`/`57070d5` themselves are test-only.)

**Reproduction at `57070d5`:** store `Fed. R. Civ. P. 5.1; 6` (source-checked).

- `authority_keys` yields `{rule:fedrcivp5.1, rule:fedrcivp5.6, full:…}` — `rule:fedrcivp6` is missing; `rule:fedrcivp5.6` is phantom.
- Draft `"The deadline passed. Fed. R. Civ. P. 6."` → `rule:fedrcivp6` unbound → `authority-not-logged` BLOCK. Base: passed. **False BLOCK.**
- Draft `"The standard differs. Fed. R. Civ. P. 5.6."` → `rule:fedrcivp5.6` hits the stored row → exit 0 on a rule never logged. Base: `fedrcivp56` ≠ `fedrcivp6` → BLOCK. **Silent wrong-authority pass** — the same consequence class as round-1 P1.
- `authority find "Fed. R. Civ. P. 5.6"` returns the "5.1; 6" row; a ledger row `Fed. R. Civ. P. 5.6` after `5.1; 6` is skipped as a duplicate.
- Chains compound: `5.1; 6; 7` → `5.6`, `5.7`; `5.1; 6.1` → `5.6.1`. Local-rule lists do the same (`L.U.Civ.R. 7.2; 15` → `rule:lucivr7.15`, not `rule:lucivr15`). Undotted heads and dotted *continuations* of undotted heads are unaffected, which is why `test_briefcheck_rule_lists.py` (all-undotted heads: `5`, `7`, `4`, `56`, `6`) still passes — no test covers a dotted `;` head.

**Fix:** strip the numeric tail including dotted parts — `re.sub(r"\d+(?:\.\d+)*$", "", inside[-1])` — matching the rule-number grammar `\d+(?:\.\d+)?` used by `RULE_TAIL_RE`/`RULE_CONT_RE`, or derive the prefix from the raw `m["base"]` split instead of the normalized key. Add `rule_keys("Fed. R. Civ. P. 5.1; 6") == {"rule:fedrcivp5.1", "rule:fedrcivp6"}` plus a `TestRuleListsInBriefCheck` bind case; ideally a negative for `Fed. R. Civ. P. 5.6`.

### P3 — two stale negative literals the hollow-negative sweep missed

`57070d5` fixed `dotted_section.py:303` for exactly this defect but missed two siblings in `test_briefcheck_short_section.py`, a file both fix commits already touched:

- `:98` (`test_head_before_a_dot_does_not_bind`, :93-98): logs `42 U.S.C. § 2.2` → keys `statute:42usc2.2`; the `assert_unbound` target names `statute:42usc22` — a key no code path can now produce, so the named-key negative can never fail. Should be `statute:42usc2.2`.
- `:127` (`test_section_a_dot_splits_does_not_bind`, :125-127): logs `42 U.S.C. § 1.01` → `statute:42usc1.01`; names `statute:42usc101`. Should be `statute:42usc1.01`.

The handoff's sweep claim ("the rest name logged sections with no dot") is wrong for these two: both names *are* dot-stripped forms of dotted logged sections. Impact is minor — each test still discriminates via `assert_unbound`'s `unlogged(r) == [raw]`/BLOCK assertions — but they should be corrected under the same standard the commit applied to `:303`. My sweep covered every `assertFalse`/`assertNotIn`/`assertNotEqual`/`assert_unbound`/`assertIsNone` naming a key string in `tests/`; all others resolve to keys their fixtures actually produce (`42usc1983`, `misscode21-23-8`, `5usc552`, `8usc1108`, `misscode41-1983`, `5usc552a`, `42usc2000e-2`, `5usc1983`, `texgov'tcode2552.101`, `texbusorgscode51`, `texbusorgscode15.1`/`151`, `texgov'tcode5.1`/`51`).

## Answers to the handoff's points

**1. P1 by rebase — clean.** No short form can bind a wrong section through :1264-1277: candidates require the draft's exact section string inside the logged key's recorded section set. A dotted key maps to its own sections because `statute_sections` (:411-424) keys by the same `scan_authorities`/`_statute_base` extraction the draft side uses (`:200-220` of `test_briefcheck_short_section.py` pins key-set coverage and values). The split cannot strand a should-bind section: `statute_secs` is queried by section value, not key, and every `statute:` key carries its own cite's sections — where base merged `{5.1, 51}` under one key and let `_lookup` ranking pick a possibly-wrong row, tip now binds each section to its own row. Strictly better.

**2. The `statute-short:` path — no cross-section collision.** With dots kept, `5.1`/`51`, `1.01`/`101`, `5.1`/`5-1`, `5.1`/`5.10` are distinct keys and `_lookup` is an exact dict hit — collision requires identical normalized text, i.e. the same section. Answering a draft "§ 5.1" from a logged bare "§ 5.1" by key is consistent with the design: the store row answers to every key its citation produces, the `short-form-without-full-cite` WARN still fires, and there is no more authoritative interpretation available for a bare short form. (`§ 5.1` vs `Section 5.1` stay different keys — `statute-short:5.1` vs `section5.1` — pre-existing and unchanged.)

**3. The six strings — yes, and the sweep missed two.** The four `88ec41b` lines are exactly the forced updates: they are the three failing test methods recorded post-replay (`dotted_section.py:257`, `draft_statute_lists.py:215,217` — two asserts, `short_section.py:213`); any other stale *positive* literal would have shown as a fourth failure. Stale *negatives* fail silently — `:303` was caught and fixed; `:98` and `:127` were not (P3 above).

**4. The new tests are discriminating.** The three `statute-short:` negatives each name a real logged-key/draft-key pair that collided only at the old `_norm_key`; the control pins `statute-short:5.1` exactly; the two tie cases reproduce P1 verbatim and the recorded red run at `303948c` proves they fail on the old fallback; the bind case pins the design-B target. The tie cases belong here: they exercise the interaction unique to this change (key split × `len(cands)==1`), which the pre-split short-section tests could not express.

**5. The rebase — `range-diff.txt` supports "unchanged"; one base interaction carries a bug.** All three commits show `=` (identical patches) — nothing dropped, nothing altered. New-base readers of `_norm_key` keys: design B `statute_secs` reads section *values*, key-agnostic — safe; `§§` lists build keys via `_statute_base` symmetrically on store and draft sides — safe; complaint mode touches headings/quotes/facts only, no key path — safe. **Not safe:** the `;` rule-continuation in `authority_keys` reads the normalized key and strips `\d+$` — the P2 above. It is the one piece of `_norm_key`-reading code that assumed keys end in bare digits.

## Readiness

Not yet — fix the P2 continuation strip and the two P3 literals, then land. On `feat/matter-capture@5c0a42c`: the recorded `merge-tree` (603 tests, OK, no conflicts) is plausible — `fix/brief-rule-linebreaks` touches rule-extraction while this range touches keys and tests — but it is the handoff's evidence, not something I could re-verify read-only; re-run the suite after the real merge per normal process.

## Model

SWE-2 Max, as my runtime reports it.
