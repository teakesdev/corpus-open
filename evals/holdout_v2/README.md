# holdout_v2 — unlabeled acceptance freeze

**UNLABELED.** Do not add gold here until Deepseek labels and grok second-reviews.

## Input contract (binding)

- Offsets: zero-based, end-exclusive **Python character** indexes (`text[start:end]`).
- `utf8_sha256` in `manifest.json` hashes the UTF-8 file bytes; it is not an offset base.
- `decoded_unicode` files: parse this exact Unicode string.
- `raw_html_entities` files: parse this exact HTML fragment; do **not** unescape before labeling or scoring.

## What this is not

- Not holdout_v1 (those five docs are development data).
- Not a score. No predictions were run for this freeze.
