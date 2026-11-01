# Port of fix/state-rule-keys onto the public line (2026-11-01)

`fix/state-rule-keys` (tip `1f22ab1`, forked from `b32d490`) was cut from the history that existed before the first push. That history carried SWE-2 transcripts and home paths, so it was not pushed. This commit applies the branch's net change to `feat/matter-capture` as one commit. The branch itself stays local.

**Reviews on the branch:** Round 1 APPROVE WITH CHANGES, round 2 APPROVE (SWE-2).

## What differs from the reviewed branch

- Two commits at the start of the branch (`eade672`, `b32d490`) belong to `fix/state-abbrev-cites`, which landed as a squash. They are not in this port; it starts after them.
- Home paths in the handoffs and prompts are shortened to `~`. The branch never committed its transcripts, so none are dropped.
- `matterkit/briefcheck.py` conflicted with later public changes in the docstring list of authorities and in `RULE_RE`. I kept the branch's state alternation and the public line's `\s*` whitespace handling (the hard-wrap fix).
- `tests/test_briefcheck_state_rules.py`: the expected key for `Iowa R. Civ. P. 1.421` is `rule:iowarcivp1.421`, not `rule:iowarcivp1421`. The public line now keeps a dot between digits in a key (the dotted-keys fix, SWE-2 APPROVE in round 3), and that fix landed after this branch was reviewed.
- The state patterns in `matterkit/citations.py` changed under the port, because `fix/state-abbrev-cites` landed after the branch was reviewed: a two-part state now allows any whitespace inside the abbreviation, and `Am. Samoa` moved to the undotted list. `RULE_RE` reads both lists through `citations._STATE_DOTTED` and `_STATE_UNDOTTED`, so the port also keys `W.  Va.`, `N.  D.` and `Am. Samoa R. Civ. P. 12`, forms the reviewed branch did not.
- The `AttributeError` that round 2 found on `Ohio\nR. Civ. P. 12` at the old base cannot occur here: the prefix split in `scan_authorities` uses `re.S`.

Everything else is the branch's net diff. Comparing the lines the branch adds and removes with the lines this commit adds and removes, file by file, found no other difference.

## Changes after review

SWE-2 reviewed this port (APPROVE WITH CHANGES, `docs/superpowers/reviews/2026-11-01-port-state-rule-keys-code/SWE2-REVIEW.md`). The founder approved these follow-ups, which change tests and this note only, no code:

- `test_a_line_break_inside_the_state_rule_still_keys_the_whole_state` pins a line break inside a dotted, two-part and undotted state rule cite, with indented rows. It fails if the undotted separator goes back to `[ ]+` (the `Ohio` rows), if the dotted separator goes back to one whitespace character (the indented rows), or if the prefix split loses `re.S` (all rows). I checked each by making that change in a scratch copy.
- `test_extra_spaces_inside_a_two_part_state_and_the_territory_that_moved_still_key` pins `W.  Va.`, `N.  D.` and `Am. Samoa`.
- This note states the changed state patterns and the dead `AttributeError` class (the two bullets above).

These follow-ups were not re-reviewed.

## Not re-reviewed

SWE-2 reviewed the branch on its own base. The public line has changed since, so the combined result has run the full test suite but has not been reviewed again.

## Old hashes the review handoffs pin

The handoffs and reports in the review folders name these commits. None exists on the public line; each is part of this commit's net diff.

| Old hash | Commit |
| --- | --- |
| `8bbd9f6` | fix(brief): a state rule cite keys on the whole state ("W. Va.", "N.D.", "Ohio") |
| `522e2bc` | review: hand state rule keys in brief check to SWE-2 |
| `06e8dbf` | review: record SWE-2's APPROVE WITH CHANGES of state rule keys in brief check |
| `cbf2ede` | fix(brief): answer SWE-2 on state rule keys (P2-1 ledger test, P3-1 pins, P3-6 docstring) |
| `789c4df` | review: hand the round-1 fix of state rule keys to SWE-2 for a scoped re-review |
| `1f22ab1` | review: record SWE-2's round-2 APPROVE of state rule keys in brief check |
