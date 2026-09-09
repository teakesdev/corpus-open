# holdout_v3 acceptance record

**Status:** SCORED ONCE after second-review approval. Not a pass against ≥ 0.95.

| | |
|---|---|
| kit commit | `c3e3a5f` |
| parser | `kit-cite-0.4` experimental, sha256 `03c69676a1d8…` (`fd6a362` blob) |
| gold | 21 = 20 statutory + 1 identifier |
| exact-span all | **18/21 = 0.857** |
| exact-span statutory | **17/20 = 0.850** |
| identifier (`H.R. 3590`) | 1/1 |
| FP (unignored, in-scope) | 7 (nested inner spans) |
| preds in ignored TOC/SECTION 1 | 14 (excluded from FP by freeze contract) |
| preds in out-of-scope intervals | 0 |

Misses: two `section 301, 302, or 303` coordinated lists; `§ 255.2(a) and (b)` coordinated short-form.

See `ACCEPTANCE_RUN.json`. This gold is the v4 acceptance record. It does **not** authorize treating the parser as accepted, and does **not** retune on this set.
