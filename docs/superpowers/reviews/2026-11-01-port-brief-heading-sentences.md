# Port of fix/brief-heading-sentences onto the public line (2026-11-01)

`fix/brief-heading-sentences` (tip `05c66f4`, forked from `eadb780`) was cut from the history that existed before the first push. That history carried SWE-2 transcripts and home paths, so it was not pushed. This commit applies the branch's net change to `feat/matter-capture` as one commit. The branch itself stays local.

**Reviews on the branch:** Round 1 APPROVE WITH CHANGES, round 2 APPROVE WITH CHANGES, round 3 APPROVE WITH CHANGES (all SWE-2). The open P3s in the round 3 report are not addressed by this port.

## What differs from the reviewed branch

- The three SWE-2 transcripts (one per round) are not included. They are kept outside the repository.
- Home paths in the handoffs and prompts are shortened to `~`.
- `README.md` conflicted with later public changes. I kept the branch's heading paragraph and added the public line's "short-form or Westlaw cite, Id." to its list of what a heading may not carry.

Everything else is the branch's net diff. Comparing the lines the branch adds and removes with the lines this commit adds and removes, file by file, found no other difference.

## Not re-reviewed

SWE-2 reviewed the branch on its own base. The public line has changed since, so the combined result has run the full test suite but has not been reviewed again.

## Old hashes the review handoffs pin

The handoffs and reports in the review folders name these commits. None exists on the public line; each is part of this commit's net diff.

| Old hash | Commit |
| --- | --- |
| `e50de74` | fix(brief): a sentence that opens with an initial is not a heading; headings' cites are checked |
| `d9ae415` | review: hand headings in brief check to SWE-2 |
| `70dbea1` | review: record SWE-2's APPROVE WITH CHANGES of headings in brief check |
| `bcbf173` | fix(brief): answer SWE-2 round 1 on headings (P1-1, P2-1, P3-3, P3-4, P3-5; a second P1) |
| `817dde4` | review: hand the round-1 fix of headings in brief check to SWE-2 for a scoped re-review |
| `3112a51` | review: record SWE-2's round-2 APPROVE WITH CHANGES of headings in brief check |
| `00e49b5` | fix(brief): answer SWE-2 round 2 on headings (never narrow a fact section; lone dotted fragments) |
| `6fad277` | merge: feat/matter-capture (e4e4f0c) into fix/brief-heading-sentences; one heading rule |
| `88b732f` | merge: feat/matter-capture (eadb780) into fix/brief-heading-sentences; combine checked headings |
| `f2a8c9e` | review: hand the round-2 fix and base integration of headings in brief check to SWE-2 (round 3) |
| `05c66f4` | review: record SWE-2's round-3 APPROVE WITH CHANGES of headings in brief check |
