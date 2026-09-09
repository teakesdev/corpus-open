# Holdout v1 — VERSION RECONCILIATION (clean detached-worktree reproduction)

**Date:** 2026-09-08 · **By:** adversarial-review-deepseek-agent (after chair version-integrity demand)

## The version claim was WRONG — corrected here

My prior scores ("b4bfb37 = 25/29", "b4bfb37 = 35/35") were **misattributed**. They were
computed on the *current working tree* at the time, which already contained v3 parser
changes (`a331f31`) — not on `b4bfb37`. Clean detached-worktree reproduction:

| parser commit | parser hash (matterkit/citations.py) | gold | exact-span |
|---|---|---|---|
| `b4bfb37` (v2) | `c87c1ebb7c67c5db2214bd3d78f3a2c52050e43035b1c0d13d282c8fbcaa66bf` | v3 gold (31d2708c…) | **0 / 29** |
| `a331f31` (v3) | `e098e9b56192c0e32190cf39e07ad4cbe28a1b09b50d5040cfaa8e692d2fe6eb` | gold@a331f31 (31d2708c…) | **29 / 29** |

- v2 `b4bfb37` does NOT handle the holdout families at all (0/29): no Pub. L., R.S., F.S.,
  undotted CFR, or spelled-out U.S. Code. Its real capability is the dev-deck formats only.
- v3 `a331f31` handles all holdout families (29/29) — but this is **dev-data evidence**
  (parser tuned on the holdout), NOT acceptance.

## Gold file drift (also version contamination)

The gold at `a331f31` (31d2708c…) differs from the taxonomy v3 gold at `26947e4`
(35 spans incl. Stat. session-law cites). The 29-vs-35 span difference is gold-file drift
between commits — recorded here so no future score mixes them.

## What acceptance requires (unchanged, now with the version controls)

1. Fresh holdout with **independently reviewed labels approved by a second reviewer BEFORE
   any predictions**.
2. Input contract made explicit: raw HTML entities vs decoded text; offsets tied to the
   representation the parser receives.
3. Scoring from a **clean detached worktree at a pinned commit**, fresh interpreter,
   imported parser path + file hash + gold hash + scorer hash all recorded.
4. One canonical run = the acceptance record. R2/R3 blocked until then.
