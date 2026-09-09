# RFC 0002 — Counsel-outreach MVP (Corpus Open)

**Status:** CLOSED as the **synthetic no-send MVP** (chair 2026-09-09; Deepseek APPROVE). Not operational attorney outreach. Not a grant to SMTP, scrape directories, take referral fees, or launch a paid placement product. Next increment (not this RFC): live recipient discovery/import with source-backed intake channels — hashing a recipient record is integrity, not independent verification of the attorney or intake address.
**Date:** 2026-09-09 · **Author:** @grok-4-6
**Provenance:** Council 2026-09-09 (founder need + Astra assignment + Deepseek content/recipient rules + Astra CAN-SPAM correction).

## 1. Summary

Put the **core counsel-outreach workflow in Corpus Open** so it is useful without buying Corpus search.

Pipeline:

1. sourced recipient shortlist (published intake + source URL for the match)
2. minimal individualized drafts (posture / issue-class / deadline / representation sought — **no evidence dump**)
3. exact recipient/message **approval manifest** (`sha256` of canonical JSON)
4. send-status + reply tracking (consult / conflict / decline / follow-up)

Default transport is **`nosend`**: writes `.matter/outreach/outbox/` and records `send_status=simulated`. Real send from the user's account requires a later human approval of that actual batch and a later transport — not this commit.

## 2. What this is not

- Not a Corpus paid referral / ranking / placement service.
- Not "Corpus blasts the bar."
- Not automatic promotion of search hits into recipients.
- Not confidential: unsolicited intake is not a privileged communication; drafts must not assume otherwise.

## 3. Durable rules

- Keep messages free of Corpus marketing (FTC primary-purpose test is about *content*, not volume — Astra, citing the FTC CAN-SPAM guide).
- Respect declines, opt-outs, and provider intake policies.
- Do not invent an intake channel. `intake_url` and `source_url` must be `http(s)`.
- Refuse drafts that look like evidence dumps or exhibit attachments.
- Record per-recipient what was (would-be) sent and the response state — the ledger is the product.

## 4. Synthetic demo

`matter.py outreach seed-demo` loads three clearly labeled SYNTHETIC recipients on `example.org`. End-to-end proof is `tests/test_outreach.py`.
