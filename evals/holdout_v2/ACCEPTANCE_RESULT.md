# holdout_v2 acceptance record

**Status:** SCORED ONCE. Not a pass against the ≥ 0.95 bar.

| | |
|---|---|
| kit commit | `f1fc325` |
| parser file | `matterkit/citations.py` sha256 `e098e9b56192c0e32190cf39e07ad4cbe28a1b09b50d5040cfaa8e692d2fe6eb` (same blob as `a331f31` v3) |
| gold | 52 spans (TOC `Sec. N` + `SECTION 1` dropped) |
| exact-span | **42/52 = 0.8077** |
| false positives | 59 |
| extraction-failed rows | 0 |

Import was from a clean detached worktree at `f1fc325`. Labels were second-reviewed before this run. holdout_v1 was not used.

See `ACCEPTANCE_RUN.json` for item-level matched/missed/FP lists.

This set is the acceptance record for the current parser. It does **not** authorize R2/R3.
