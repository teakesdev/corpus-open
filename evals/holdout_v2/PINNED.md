# holdout_v2 pin (immutable acceptance record)

This file pins the 42/52 run. Do not rewrite `ACCEPTANCE_RUN.json`.

| artifact | value |
|---|---|
| score commit | `4908110` |
| labels commit | `f1fc325` |
| gold file sha256 | `63c6083eaf03274485b17696de1c4b97a57c92b8ef544168daa3008ed0e53881` |
| run file sha256 | `089fc95f6c3bb3e40d12eb408d81d9bc506bca8f4376a145038d55833b220d8b` |
| parser blob sha256 | `e098e9b56192c0e32190cf39e07ad4cbe28a1b09b50d5040cfaa8e692d2fe6eb` (`a331f31` / v3) |
| exact-span | 42/52 = 0.8077 |
| missed_ids | H2-002, H2-004, H2-007, H2-019, H2-025, H2-040, H2-044, H2-045, H2-048, H2-049 |

**After the 2026-09-09 syntax-gap fix lane, holdout_v2 is development/regression data.** It must not be re-used as independent acceptance evidence. Fresh acceptance requires holdout_v3 with `ignored_intervals` declared at freeze time.
