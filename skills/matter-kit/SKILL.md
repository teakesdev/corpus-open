---
name: matter-kit
description: Organize litigation case files into a provenance-enforced matter store (hashes, labeled facts, deadlines, counsel packets) that any AI agent can operate over. Use when working with legal case files, court filings, evidence, records requests, or when the user asks to organize/index/audit a matter.
---

# Matter Kit — organizing a case any agent can be trusted with

A matter is a directory of source documents plus a derived `.matter/` store.
**Sources are immutable; everything derivable lives in `.matter/`.** The store
makes every substantive claim either traceable to a hashed document or visibly
unsupported — that visibility is the product.

## Setup

```bash
python3 /path/to/matter-kit/matter.py init <case-dir>       # creates .matter/ + standard folders
python3 /path/to/matter-kit/matter.py ingest <case-dir>     # hash+register all files (idempotent)
python3 /path/to/matter-kit/matter.py verify <case-dir>     # re-hash everything; fails on drift
python3 /path/to/matter-kit/matter.py status <case-dir>
```

Standard folders: `Filings/ Evidence/ Correspondence/ Case_Tracking/ Research/ Media/`.
Existing nonstandard layouts are fine — `ingest` classifies by directory hints
(`filings`, `evidence`, `correspondence|records_requests`, `case_tracking`,
`research`, `media/source_orders`) then by extension.

## The labeling discipline (non-negotiable)

Every substantive assertion gets exactly one epistemic label:

- `DOCUMENTED FACT` — a dated, identifiable record in the store actually shows it. Cite `rel_path@locator` (page ¶, timecode).
- `ALLEGATION` — asserted by a party/complaint/narrative but not independently evidenced.
- `INFERENCE` — you derived it from documented facts. Say from what.
- `HYPOTHESIS` — motive/conspiracy/causation theories. These stay quarantined until a named actor + admissible record exist.
- `UNKNOWN` — explicitly unknown beats silently assumed.

Agents may NOT upgrade a label without adding a new source. Custody status for
documents uses the same five words:
`verified / needs-source / unverified / superseded / excluded`.

## Working rules learned the hard way (follow these)

1. **Inspect media before writing anything about it.** Never characterize audio/video from its filename or another memo. Transcribe locally, log duration + timecodes + speakers, quote verbatim. A memo's characterization of a recording is an ALLEGATION until you have listened.
2. **Never silently correct a date discrepancy** in a court order or record. Preserve it, flag it, work around it. (A July 16 order once demanded compliance by June 27.)
3. **Verify every citation before it enters any outbound artifact.** `matter authority check "<cite>"` queries the hosted Corpus law API; record what verified and what didn't. An inflated or unconfirmable case cite destroys credibility on everything else in the file.
4. **Fee estimates and totals across requests do not sum.** Reconcile originals first; overlapping/duplicate-looking fields are common.
5. **Keep speculative theories out of anything that leaves the matter** (packets, filings, outreach). They live in Research/ under HYPOTHESIS until proven.
6. **Never expose raw IPs, emails, phone numbers, account identifiers** in anything public-facing.
7. **Secondary docket indexes are leads, not records.** Mark deadlines from them `working-estimate` + `secondary-source` until an official document lands in the store.
8. **Wrong-entity naming kills claims.** Naming "X Police Department" instead of "City of X" has gotten municipal defendants dismissed unopposed. Check party capacity carefully.

## Facts & deadlines

```bash
matter fact add <dir> --text "Property records show plaintiff owns both parcels" --status DOCUMENTED-FACT --source "Evidence/EVIDENCE_INVENTORY.md@Tier 1 #6"
matter fact list <dir> [--status ALLEGATION]     # unsupported rows render **[UNSUPPORTED]**
matter deadline add <dir> --label "Rule 4(m) service deadline" --due 2026-10-14 --rule "Fed. R. Civ. P. 4(m)" --confidence hard --verification secondary-source
```

An assertion with no recognized source renders `[UNSUPPORTED]` everywhere. That
is deliberate: unsupported leaps should be visible to the user, the agent, and
any lawyer reading the output.

## Counsel packets & MCP

```bash
matter packet <dir> --layer 3min --out COUNSEL_PACKET.md   # 30s | 3min | 15min layers
MATTER_DIR=<case-dir> python3 /path/to/matter-kit/matter-mcp.py   # stdio MCP server
```

Packets always carry adverse-fact sections and a not-legal-advice boundary.
Outreach drafts may be prepared, but nothing in matter-kit ever sends — humans send.

## MCP client config (Claude Desktop / Hermes / Codex-compatible)

```json
{ "mcpServers": { "matter": { "command": "python3",
  "args": ["/path/to/matter-kit/matter-mcp.py"],
  "env": { "MATTER_DIR": "/absolute/path/to/case-dir" } } } }
```

Tools: `matter.status`, `matter.fact_add`, `matter.fact_list`,
`matter.deadline_list`, `matter.packet_render`. Read-mostly by design.
