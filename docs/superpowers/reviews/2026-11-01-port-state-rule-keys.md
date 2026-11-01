# Port of fix/state-rule-keys onto the public line (2026-11-01)

`fix/state-rule-keys` (tip `1f22ab1`, forked from `b32d490`) was cut from the history that existed before the first push. That history carried SWE-2 transcripts and home paths, so it was not pushed. This commit applies the branch's net change to `feat/matter-capture` as one commit. The branch itself stays local.

**Reviews on the branch:** Round 1 APPROVE WITH CHANGES, round 2 APPROVE (SWE-2).

## What differs from the reviewed branch

- Two commits at the start of the branch (`eade672`, `b32d490`) belong to `fix/state-abbrev-cites`, which landed as a squash. They are not in this port; it starts after them.
- Home paths in the handoffs and prompts are shortened to `~`. The branch never committed its transcripts, so none are dropped.
- `matterkit/briefcheck.py` conflicted with later public changes in the docstring list of authorities and in `RULE_RE`. I kept the branch's state alternation and the public line's `\s*` whitespace handling (the hard-wrap fix).
- `tests/test_briefcheck_state_rules.py`: the expected key for `Iowa R. Civ. P. 1.421` is `rule:iowarcivp1.421`, not `rule:iowarcivp1421`. The public line now keeps a dot between digits in a key (the dotted-keys fix, SWE-2 APPROVE in round 3), and that fix landed after this branch was reviewed.

Everything else is the branch's net diff. Comparing the lines the branch adds and removes with the lines this commit adds and removes, file by file, found no other difference.

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
