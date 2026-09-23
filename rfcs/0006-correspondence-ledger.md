# RFC 0006 — Custodian correspondence ledger

**Status:** implemented, local `nosend` increment.  
**Date:** 2026-09-20.  
**Source:** [dogfood increment 2](../docs/2026-09-18_dogfood-iteration.md#2-correspondence-ledger-rfc-0006--not-counsel-outreach).

## Notice and custody

`correspondence_notices` records one notice to one addressee. Kinds are `hold`,
`mpra`, `foia`, `touhy`, `letter`, and `other`. The addressee references `parties`,
with `role=agency|other` and `capacity=custodian`; a separate address records the
operator's chosen mailing destination. Creation and rendering validate that
capacity. Nothing creates an outreach recipient or promotes a custodian to a
defendant. `party add|list` provides the small CLI needed to select the addressee.

Each notice pins one ingested Exhibit A by document ID and SHA-256. References
accept document IDs, hashes, or matter-relative paths. Paths resolve from the
matter root, including from a foreign cwd. Missing, changed, escaped, and derived
`.matter/` sources are refused. Every included assertion source is rehashed too.
Exhibits remain opaque bytes; hashing does not establish their truth or suitability.

`confirm_due`, when provided, atomically creates one linked open deadline with
`confidence=working-estimate` and `verification_status=unverified`. It is a
requested confirmation date, not a calculated statutory deadline. Mailing does
not establish a legal deadline or automatically promote its confidence. A reply
does not automatically close the deadline: it may be incomplete or a refusal.

## Rendering and approval

`correspondence add --assertion ID` explicitly selects rows for that notice;
an empty selection renders no factual claims. The letter body consists of a fixed
request for the notice kind plus selected `DOCUMENTED FACT` and `UNKNOWN` rows.
No free-form letter-body override is provided. `ALLEGATION`, `INFERENCE`,
`HYPOTHESIS`, and unrecognized labels are omitted at every render, even if
explicitly selected or sourced. Unsupported allowed rows remain visibly
`[UNSUPPORTED]`. Source locators and hashes accompany included assertions.
Exhibit contents and operator-supplied metadata require human review; this filter
is a guarantee about assertion rows, not a semantic classifier for arbitrary text.

The packet prints the taxonomy mapping, “HYPOTHESIS rows omitted,” the
addressee, the requested confirmation date, and the Exhibit A hash.
`correspondence export` writes a review bundle under
`.matter/out/correspondence/<manifest-sha256>/`:

- `packet.md`: the letter and Exhibit A reference/hash.
- `Exhibit-A.<source-extension>`: an exact copy of the registered exhibit.
- `approval.json`: the canonical payload and its SHA-256.

The digest covers version, notice ID/kind, `nosend`, addressee identity/capacity/
address, confirmation date, allowed assertion text/labels/sources, exhibit
identity/hash, and exact packet text. Canonical JSON uses sorted keys, UTF-8,
unescaped Unicode, and compact separators. The payload is hashed without its
outer `sha256` field. Exports stage all files and refuse to overwrite an existing
bundle. Neither rendering nor export changes the notice status.

Signing records the exact manifest, its hash, operator identity, and recording
time in `correspondence_approvals`. History preserves prior approvals. If the
addressee, allowed assertions, or other packet inputs change, signed packet
render/export and mailing are refused. An unmailed signed notice can return to
`draft` for review against current inputs, then receive a new approval. Notices
have no address/exhibit editing command in this increment; create a new notice
for those corrections. Historical bundles and approvals are retained.

## State and evidence

| Transition | Required evidence |
|---|---|
| creation → `draft` | Valid custodian and hashed exhibit |
| `draft` → `signed` | Explicit human issuer and reviewed manifest SHA-256 |
| `signed` → `draft` | Explicit human issuer; clears active approval, retains history |
| `signed` → `mailed` | Same approved manifest SHA-256, human issuer, and ingested mailing receipt that rehashes |
| `mailed` → `receipt-hashed` | Human issuer and ingested delivery confirmation that rehashes |
| `mailed` or `receipt-hashed` → `answered` / `refused` | Human issuer and ingested response/refusal evidence that rehashes |

`answered` and `refused` are terminal for this increment. `receipt_sha256` on the
notice retains the initial mailing evidence; delivery and response hashes are
separate history rows. Later states revalidate the initial mailing receipt.
Timestamps record when an operator recorded the action. They do not infer when
delivery occurred. Every transition, approval, and history insertion commits
atomically or rolls back. SQL constraints reject mailed-or-later state without
mailing evidence and reject any transport other than `nosend`.

`--issuer` is an explicit operator attestation, following RFC 0002's local
manifest model. It is not identity authentication, signature verification, or
proof that receipt contents are genuine. Humans inspect, sign, and send using
their own tools; the CLI records those actions and custody of their evidence.
There is no SMTP, browser submission, or actual `send` implementation, even after
a receipt is supplied. A generated outbox file cannot serve as receipt evidence.

## Surfaces and compatibility

CLI: `correspondence add|list|render|manifest|export|status`.
MCP adds only read operations: `matter.hold_render` (any notice kind, selected by
`notice_id`) and `matter.correspondence_list`. Approval, mailing, and response
transitions are not exposed over MCP. `matter.status` includes counts by notice
state. Counsel outreach and counsel packet layers remain separate.

Opening an existing store adds four tables without changing prior document,
assertion, party, deadline, or outreach records. Tests use synthetic temporary
matters and cover lifecycle gates, stale approvals, filtering across packet and
manifest artifacts, hash drift, path escape, atomic failures, existing stores,
CLI foreign-cwd operation, and MCP rendering.
