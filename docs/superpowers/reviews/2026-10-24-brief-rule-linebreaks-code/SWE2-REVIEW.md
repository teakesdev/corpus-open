I'll start by reading the handoff document.Now let me read the review diff and fuzz evidence files in the same folder.Now let me read the main code file under review.Let me read the rest of the file to trace consumers of the scan results.Continuing through the rest of briefcheck.py.Now let me look at the test file in the worktree and the test helpers it uses.Let me examine the test helpers (`Base`, `codes`, `Matter`, `TestCli`) in test_briefcheck.py.Now let me check `matter.py` (cmd_brief error handling and authority commands) and `matterkit/authority.py`.Now let me check the remaining consumers: `matter-mcp.py` (MCP tool), `citations.py` (called in the scan path), and grep for other unguarded `.group()` dereferences in the check path.Let me check `matter-mcp.py` (the MCP consumer) and the `citations.py` xref dereferences.Let me check the spec doc and related test files for conventions and invariants.Let me sweep for any other unguarded `re.match/search` results in briefcheck.py and check the remaining test files.Let me quickly check `packet.py` for citation display, and look at the remaining referenced test file for conventions.Let me check the §§-list test file and the other statute test to see whether the unguarded `LISTED_SEC_RE.match` at line 314 has known coverage.Let me check the full contents of the review folder and the repo root for anything else referenced.A prior attempt left review artifacts. Let me read them (as data) plus a prior SWE-2 review for house format.Both prior-attempt files contain only progress chatter, no report. My analysis is complete — I've traced every match-dereference in the check path, the display consumers, and the test coverage. Writing the report now.

**Verdict: APPROVE**

# SWE-2 review — `fix/brief-rule-linebreaks` (`e77da0c..bebaaac`)

## Verdict: APPROVE

The fix is minimal, correct, and complete for its stated scope. `re.S` added to the two prefix-cutting matches in `scan_authorities` (`matterkit/briefcheck.py:261,274`) closes the only `\s`-vs-`.` gap in the check path. Every yield site, key, and display path was traced; no P1 or P2 findings.

## Findings

### P3-1 — Unguarded `LISTED_SEC_RE.match` can still crash `scan_authorities` (pre-existing, out of diff scope)

`matterkit/briefcheck.py:314` — `part = LISTED_SEC_RE.match(part).group(0).rstrip(".")`. This is the same bug class the change fixes (match result dereferenced unguarded on grammar-derived text), and it is reachable with or without a newline: `re.split(f"({_SEC_SEP})", lst["secs"])` splits on the *separator pattern* anywhere in `secs`, including inside a `_SEC` parenthetical (`[^)]{1,24}` admits `and`/`or`/`,` text). Failure scenarios:

- `"Miss. Code Ann. §§ 21-3-15 (2 and ), 21-4"` — `" and "` splits inside the parens → even-index part `")"` → `LISTED_SEC_RE.match(")")` is `None` → `AttributeError` → `brief check` traceback, rc 1, no report. `"§§ 21-3-15 (2 and , 3)"` yields an empty part `""` — same crash. A wrap variant `"(2 and\n)"` triggers it via the `\s+` separator.
- `"§§ 21-3-15 (2 and 3)"` (no trailing space) — no crash but a wrong result: part `"3)"` yields a phantom `statute:…3` cite → spurious `authority-not-logged` BLOCK.

Pre-existing at `e77da0c`, not worsened by this diff, and outside the review range — but worth a follow-up guard (`(m := LISTED_SEC_RE.match(part))` and skip/break on `None`) plus a fuzz draft containing a parenthesized connector. Does not block landing.

### P3-2 — Stored citations echo raw to the terminal (cosmetic)

`matter.py:238` (`authority add` echo), `matter.py:254,256` (`import` dry-run rows), `matterkit/authority.py:53` (`AuthorityError` message) interpolate `r["citation"]`/`target` unescaped. A stored citation holding `\n` prints a literal newline. Cosmetic only — the authoritative surface is `Report.render`, where `esc`/`short` (`briefcheck.py:801-802`) flatten `\n` in every cell that can carry a cite, and the MCP tool returns `render()` output verbatim (`matter-mcp.py:104`). No change needed.

### P3-3 — Optional test nit

No test pins that a broken cite's *display* is newline-free in the rendered report (C2's claim). One line — e.g. in `test_unlogged_rule_broken_across_lines_blocks`, render and assert the finding cell contains no `\n` — would pin it. Low value since `esc` is uniform; optional.

## Answers to the handoff's points 1–6

**1. Completeness.** Exhaustively swept every `re.match/search/fullmatch` result used in the check path (`briefcheck.py`, `authority.py`, `citations.py:191-199` xref, `matter-mcp.py`). After this fix, the only unguarded dereferences on text-derived input are the two fixed sites and the pre-existing P3-1 at :314. Critically, `re.S` is *provably* sufficient for the whitespace class: for `str` patterns, `.` already matches every character `\s` admits (`\t`, `\r`, `\f`, `\v`, `\x1c–\x1e`, `\x85`, NBSP, U+2028/2029, …) — the *only* gap was `\n`, and `re.S` adds exactly that. Both sites are grammar-guaranteed to match post-fix (`m["base"]` always ends `\d+(?:\.\d+)?`; `words` always contains `Rules?\s` before the number), so the unguarded `.group(1)` is now safe by construction. The `\n\n` stored-citation case at the `\s*` slot (e.g. `"Fed. R. Civ. P.\n\n12"`) also works — `_norm_key` strips all `\s` so keys equal the unbroken cite. Rule tails, `;` continuations (`RULE_CONT_RE` is walrus-guarded), `_statute_base`, `_heading`, `_case_name`, `authority.find`/`import_ledger`/`_authority_index`: all checked, all fine.

**2. Prefix correctness under `re.S`.** Correct. `(.*?)\d+(?:\.\d+)?$` is lazy-leftmost: for `"Fed. R. Civ. P.\n6.1"` the earliest position where `\d+(?:\.\d+)?$` matches to end is the `6` (with `(?:\.\d+)?` taking `.1`), giving prefix `"Fed. R. Civ. P.\n"`. No ambiguity is possible — the grammar's name part `(?:Fed|[A-Z][a-z]{1,5})` admits no digits, so `base` holds exactly one digit cluster. Tail keys (`prefix + t["num"]`) and displays (`prefix + t["cite"]`) verified for every break position, including `12 and\n15` and `P.\n12 and 15`.

**3. Displays (C2).** Agree with C2. Every consumer escapes: finding cites and messages via `short`/`esc` (`:819`), row propositions already whitespace-collapsed (`:1090`), cite cells via `esc(_cite_cell(...))` (`:832`), authority display/pins/via (`:840-841`), complaint notes (`:827`), MCP tool returns `render()` (`matter-mcp.py:104`). Only raw echoes are the terminal prints in P3-2 — faithful echoes of stored data, not a defect.

**4. C1 and C3.** Agree with both. Refusing `\n` in `RULE_RE`/`LOCAL_RE` would silently un-see a wrapped cite — converting a crash into a false pass, strictly worse. Normalising `m["base"]` before cutting would work but loses display fidelity for no benefit, since `_norm_key` already erases the difference in keys. `re.S` is the minimal correct fix. On C3: a stored cite with a blank line at a `\s*` slot keys fine post-fix; the residual limitation is a blank line (or doubled whitespace) at a single-`\s?` slot — `Fed.\n\nR. Civ. P. 12` or `Fed. \nR. Civ. P. 12` — which scans as nothing (miss, not crash). Only reachable via stored citations since `units()` (`:915`) splits drafts at `\n\n` before scanning; pre-existing, reasonable to leave.

**5. Tests.** They pin D2 well: all 10 break positions across both crash sites (incl. `\r\n`, `Evid.`, `L.U.Civ.R.`, both spelled-out local paths, `\n` before/after `and`), each asserted against the unbroken cite's key — discriminating, since crash or wrong key both fail. The stored-citation, `find`, `authority_keys` `;`-continuation, and CLI tests cover the poisoning path (`_authority_index` runs per check — a stored broken cite crashed *every* check of that matter, the highest-severity aspect). The CLI test's `PATH=""` matches `TestCli` convention (`test_briefcheck.py:335`). D1 (base choice) isn't testable and is a founder call — fine. Optional nit in P3-3.

**6. Accuracy.** The `:260` comment, test docstring (exit-1-also-blocking-findings claim matches `cmd_brief`'s narrow `except (ValueError, FileNotFoundError)` at `matter.py:283`), and the merge-hazard description are all accurate. The `ff4a364`/`f523e2d` resolution ("their line plus `re.S`") is precisely specified and verified by the recorded scratch run; `:274` merges cleanly as claimed.

## Readiness

Ready to land on `feat/matter-capture`. The content conflict with whichever of `fix/brief-false-blocks`/`fix/brief-short-section` lands second is fully specified (their `prefix, num = ... .groups()` refactor plus `re.S`) and evidence-backed. The `bcbf173`/`8f3339f`/`f2d665e` conflicts pre-exist on `e77da0c` and are not this branch's problem.

## Model

`swe-2-max` (SWE-2 Max, Devin CLI).
