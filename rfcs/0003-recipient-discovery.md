# RFC 0003 — Source-backed recipient discovery/import

**Status:** Import safeguards accepted. **Bounded one-candidate proof CLOSED** (chair 2026-09-09): MacArthur Justice MS, two independent source reviews, import stayed `candidate`. Not automated discovery. Not suitability for any live matter. Generic §1983/unlawful-fines brief must **not** be assumed to be the founder’s case. No outbound contact. No fetch-and-match path.
**Date:** 2026-09-09 · **Author:** @grok-4-6
**Parent:** RFC 0002 (synthetic no-send MVP, CLOSED).

## Stages (distinct)

| Stage | Meaning |
|---|---|
| `candidate` | Imported. Verbatim quotes in the brief are **evidence assertions**, not verification. |
| `source-checked` | **Not granted on import.** Requires a future fetch-and-match that the *exact* intake channel belongs to the intended org on the cited source. Generic “contact our firm” matching is insufficient. Until that path exists, nothing enters this stage. |
| `shortlisted` | User-approved as a research lead / for contact tracking. Still unsendable until an approved message batch. Does not earn `source-checked`. |

Legacy unearned `source-checked` rows are downgraded to `candidate` (`source-checked-revoked:unverified`); evidence/history is preserved. No grandfathering.

## Destination types

`destination_type ∈ {directory, portal, form, email}`. Legacy/unclassified = `unknown`.

| Type | Shortlist | Draft |
|---|---|---|
| `directory`, `unknown` | research lead only | **refuse** — URL is not a To: or form-submit |
| `portal`, `form` | yes | preparation-only banner; **not** permission to submit |
| `email` | yes | draftable; transport remains `nosend` |

## Refusals and flags

- Guessed emails / `mailto:` intake → **refuse**.
- Conflicting intake URLs for the same org → `conflict:intake-mismatch`.
- `retrieved_at` older than 90 days → `stale-evidence` (still importable as `candidate`).
- Duplicate channel in one brief → one recipient, `duplicate-channel`.
- Verbatim claim without fetch → `verbatim-unverified`.

## Suppression

Declines and opt-outs are keyed by normalized intake URL. Reimport **must not** reset them or rewrite historical batch rows.

## Three gates (keep separate)

1. **Source evidence** — published practice match + published intake route. Does not mean the office is accepting new matters.
2. **Shortlist approval** — user marks a research lead. Does not authorize a message.
3. **Message/batch approval** — authorizes *contact* (or a nosend simulation). Does **not** verify the recipient or establish availability.

A published form proves a contact route only.

## Demo

`evals/outreach_import/brief.json` — generic civil **finders** (USAGov/ABA). Directories/portal. All `candidate`. Not an attorney.

`evals/outreach_import/generic_1983_ms_brief.json` + `verified_candidate_macarthur.md` — one org-owned **form** (MacArthur Justice Center, Mississippi office) matched to an explicit generic §1983 brief. Reviewers independently fetched the org pages. Status remains **`candidate`**. Published intake ≠ willingness to take the case. No contact.
