# Quote guard: old-to-new commit ids (2026-11-07)

Before the first push, one commit message (round 1's review record) was reworded to drop a private path the pre-push hook refuses. Every commit from it on got a new id; the trees are unchanged. The review handoffs and reports in this folder and its `-r2`..`-r4` siblings name the old ids; read them through this map.

| Old | New | Commit |
| --- | --- | --- |
| `e71c6d7` | `05d3c83` | review: record SWE-2's round 1 REQUEST CHANGES of the quote guard |
| `5fb9bc6` | `a9b23ab` | fix(hermes): answer SWE-2's round 1 P1s and P2s in the quote guard |
| `23e1948` | `183f9fd` | review: hand the quote guard fix round to SWE-2 (round 2) |
| `d485978` | `c2289df` | review: record SWE-2's round 2 REQUEST CHANGES of the quote guard fix |
| `832365b` | `f784daf` | fix(hermes): answer SWE-2's round 2 in the quote guard (R2-1 and the P3s) |
| `c728875` | `31d2a73` | review: hand the quote guard round-2 fix to SWE-2 (round 3) |
| `786b9af` | `694c7a5` | review: record SWE-2's round 3 REQUEST CHANGES of the quote guard fix |
| `84cb11d` | `79a8566` | fix(hermes): answer SWE-2's round 3 P2s in the quote guard (R3-1, R3-2) |
| `da67155` | `08d6cd8` | review: hand the quote guard round-3 fix to SWE-2 (round 4) |
| `aab310d` | `f264b18` | review: record SWE-2's round 4 APPROVE of the quote guard |
| `1e36454` | `4cfecaf` | merge: bring main (source guard by file identity #4, Deadline Watch) into the quote guard |
| `84a5f2f` | `3b5af91` | fix(hermes): quote guard calls the source guard's _classify by file identity after merging main |
