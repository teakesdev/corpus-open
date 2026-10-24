# PR (local): brief check survives hard-wrapped drafts and parentheticals inside "§§" lists

| | |
| --- | --- |
| Base | `feat/matter-capture` @ `11f607b` |
| Head | `fix/brief-rule-linebreaks` @ `16659e9`, plus the review bookkeeping commits above it |
| Where | Local only. The founder chose a local PR: the `origin` repository is public, and `feat/matter-capture` (unpushed) carries review transcripts that embed the operator's own agent instructions. |
| Worktree | `~/dev/.worktrees/matter-kit/brief-rule-linebreaks` |
| Diff | `pr.diff` in this folder: `git diff 11f607b 16659e9 -- matterkit tests`, 403 lines (code in `matterkit/briefcheck.py` only, plus three new test files) |
| Status | **Not independently reviewed** beyond SWE-2's round 1 on `bebaaac`. This PR is handed to SWE-2 as round 2. |

## Summary

All of these are pre-existing on `feat/matter-capture`, and all of them make `brief check` crash or pass a cite it never read.

- **A line break inside a rule cite crashed brief check.** "Fed. R. Civ. P.\n12", "Fed.\nR. Civ. P. 12" and "Local\nBankruptcy Rule 7" made a prefix match return `None`. `brief check` then died with an `AttributeError` traceback and wrote no report, exiting 1, which is also the code for blocking findings. A stored citation holding the same break crashed every check of the matter. Fix: `re.S` on both prefix matches (`bebaaac`, reviewed by SWE-2: APPROVE, `ef9b236`).
- **A parenthetical inside a "§§" list member crashed it too** (SWE-2's round-1 P3-1). "Miss. Code Ann. §§ 21-3-15 (2 and ), 21-4" was split inside "(2 and )". As a stored citation it crashed every check. "(2 and 3)" created a phantom member "§ 3", which BLOCKed. Fix: walk the list member by member with its own grammar (`2277e93`).
- **A section after subsections was dropped.** With only 21-3-15 logged, "§§ 21-3-15 (a) and (b), 21-4" exited 0: the list stopped at "(a)", so 21-4 was never checked. Fix: a bare subsection ("(b)") continues the section before it, and the list goes on (`2277e93`).
- **Trailing whitespace before a wrap hid the cite.** "Fed. \nR. Civ. P. 12", the Markdown hard break "Fed.  \nR.", and "100 F. \nSupp. 2d 200" matched nothing, so an unlogged cite passed with no finding. "100 F. Supp. \n2d 200" read as the wrong case. Fix: any whitespace run joins the parts of a reporter or rule abbreviation (`\s?` → `\s*`, `f38443b`).

## Commits

| Commit | What |
| --- | --- |
| `bebaaac` | `re.S` on the rule-prefix and Local Bankruptcy/Admiralty word matches; `tests/test_briefcheck_rule_linebreaks.py` (7 tests, all red at `e77da0c`). |
| `6e24db5`, `ef9b236` | SWE-2 round-1 handoff and record (APPROVE; P3-1/P3-2/P3-3 notes). |
| `2277e93` | "§§" list walked by `SEC_RE` + `LIST_MEMBER_RE`; `_SUBSEC` members; `tests/test_briefcheck_list_members.py` (12 tests, 9 red at `bebaaac`). |
| `f38443b` | `\s?` → `\s*` inside `_REPORTERS`, `RULE_RE` and `LOCAL_RE`; `tests/test_briefcheck_wrapped_cites.py` (8 tests, 7 red at `2277e93`). |
| `16659e9` | Merge of `feat/matter-capture@11f607b` (squash merges of false-blocks, short-section and dotted-section, landed by another session meanwhile). One conflict, at the rule-prefix line: their `prefix, num = …groups()` plus `re.S`. The list-test key literals move to `misscode…`, because false-blocks drops "Ann." from keys. |

## Plan, as approved by the founder

1. Land `fix/brief-rule-linebreaks` once it has passed SWE-2's review.
2. Fix SWE-2's P3-1 before landing.
3. Fix the two silent passes found while checking that review: the dropped list member, and trailing whitespace before a wrap.
4. Open a PR (local, by the founder's choice) and have it reviewed before landing.

**Deviations:**
- I merged the moved base instead of rebasing, so the SHAs SWE-2 reviewed still exist on the branch.
- P3-2 (raw newlines in terminal echoes) and P3-3 (an optional render test) were not done; SWE-2 marked both optional.
- Line breaks in displayed cites are not flattened: `Report.render` already escapes them (`briefcheck.py:887`).

## Test results

- `python3 -m unittest discover -s tests`:
  - base `11f607b`: `Ran 536 tests`, `OK`;
  - head `16659e9`: `Ran 563 tests in 45.539s`, `OK` (+27).
- **Wrap differential** (`wrap-diff.py` → `wrap-diff.txt`; 13 synthetic drafts, each space replaced by "\n", " \n" or "  \n", 618 variants):
  - at `11f607b`: 38 crashes, 32 lost cites, 2 wrong cites, and both "§§" parenthetical probes crash;
  - at `16659e9`: 0 crashes, 0 lost, 0 gained; the draft probe exits 1 (BLOCK on the unlogged 21-4) and the stored probe exits 0.
- **Eval docs:** every `evals/*/docs/*` file gives the same 366 authority rows at `11f607b` and `16659e9`, and the same 359 rows at `bebaaac` and `f38443b` (before the merge).

## Merge hazards

`git merge-tree --write-tree 16659e9 <tip>`:
- **Clean:** `feat/matter-capture@11f607b` (fast-forward base), `fix/brief-dotted-keys@57070d5`, `fix/brief-va-abbrev@43ef8ba`.
- **Already conflicting with the base `11f607b`,** unchanged by this PR: `fix/state-abbrev-cites@250c100`, `fix/brief-word-states@9a0ffa3` (both in `citations.py` / `tests/test_cite_v4_gaps.py`, the `kit-cite-0.5` collision) and `fix/brief-heading-sentences@bcbf173`.
- **`fix/state-rule-keys@1f22ab1`** also conflicts with the base, and this PR adds two hunks in `briefcheck.py`:
  1. The module docstring example lines. Keep both lists' additions.
  2. `RULE_RE`. Keep the state alternatives from state-rule-keys and make every `\s?` outside `_STATE_DOTTED` a `\s*`:
     `rf"(?P<base>(?:(?:Fed|(?<![\w.])(?:{citations._STATE_DOTTED})|[A-Z][a-z]{{1,5}})\.\s*"`
     `rf"|(?<![\w.])(?:{citations._STATE_UNDOTTED})\s+)"`
     `r"R\.\s*(?:(?:Civ|Crim|App)\.\s*P\.|Evid\.)\s*\d+(?:\.\d+)?)"`

## Landing

After the review passes, squash-merge into `feat/matter-capture`, matching the three squash merges that landed today (`merge(squash): … (fix/brief-rule-linebreaks)`). Then rerun the gate on the result. Nothing is pushed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
