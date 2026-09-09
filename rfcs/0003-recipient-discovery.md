# RFC 0003 — Source-backed recipient discovery/import

**Status:** SCOPED increment — import + stages + suppression. No outbound contact. Not a grant to scrape directories, guess emails, SMTP, or treat a published URL as willingness to take a case.
**Date:** 2026-09-09 · **Author:** @grok-4-6
**Parent:** RFC 0002 (synthetic no-send MVP, CLOSED).

## Stages (distinct)

| Stage | Meaning |
|---|---|
| `candidate` | Imported; source incomplete (no verbatim claim and/or no retrieval date). |
| `source-checked` | Brief recorded `source_url` + `retrieved_at` + `intake_verbatim_on_source`. **Integrity of the record, not independent verification.** Does not mean available/willing. |
| `shortlisted` | User-approved for contact. Still unsendable until an approved message batch (RFC 0002). |

Import never jumps to `shortlisted`. `draft` still requires `shortlisted`.

## Refusals and flags

- Guessed emails / `mailto:` intake → **refuse**.
- Conflicting intake URLs for the same org → flag `conflict:intake-mismatch`.
- `retrieved_at` older than 90 days → flag `stale-evidence` (still importable).
- Duplicate channel in one brief → one recipient, flag `duplicate-channel`.

## Suppression

Declines and opt-outs are keyed by normalized intake URL. Reimport **must not** reset them or rewrite historical batch rows.

## Demo

`evals/outreach_import/brief.json` — generic civil finders from USAGov + ABA public pages, retrieved 2026-09-09. No matter facts.
