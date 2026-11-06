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
python3 /path/to/matter-kit/matter.py verify <case-dir>     # re-hash; cwd-independent; fails on drift
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
9. **`rel_path` is matter-relative.** Never store `os.path.relpath(fp, cwd)`. After writing a file under the matter, ingest **that path**, then `verify` from a cwd that is neither the matter nor the kit repo (e.g. `cd /tmp && python3 /path/to/matter-kit/matter.py verify <case-dir>`). A `MISSING` or `CHANGED` line is a **blocker** — do not continue until verify is clean from a foreign cwd. Existing stores with cwd-relative locators will report MISSING until re-ingest; do not silently rewrite sqlite.

## Capturing web pages (no curl + hand-hashed tables)

```bash
matter capture <dir> --url https://example.org/press/release --out Media/captures/2026-09-18_example.org/page.html
matter capture list <dir>          # provenance table: url | retrieved_at | sha256 | rel_path
```

`--out` is matter-relative. `capture` writes the bytes, registers them (`source_url`
set, `kind` = `media`, or `research` under `Research/` or with `--kind research`), and
records the retrieval in the `captures` table. **Do not keep a handwritten provenance
table** — paste `capture list`. It refuses to overwrite, refuses redirects (re-run with
the URL it reports, so the row names the page that actually served the bytes), and
refuses URLs with embedded credentials. Identical bytes already in the store record a
new retrieval row and write no second file. The text extract lands in
`.matter/out/captures/<sha256>.txt`: search it, but **cite the captured file**, never
the extract. A capture proves what a URL served at `retrieved_at` — custody, not truth;
what the page *says* is still an ALLEGATION until a record shows it. Rule 9 applies
after every capture: `verify` from a foreign cwd; MISSING/CHANGED is a blocker.

## Facts & deadlines

```bash
matter fact add <dir> --text "Property records show plaintiff owns both parcels" --status DOCUMENTED-FACT --source "Evidence/EVIDENCE_INVENTORY.md@Tier 1 #6"
matter fact list <dir> [--status ALLEGATION]     # unsupported rows render **[UNSUPPORTED]**
matter deadline add <dir> --label "Rule 4(m) service deadline" --due 2026-10-14 --rule "Fed. R. Civ. P. 4(m)" --confidence hard --verification secondary-source
matter trigger add <dir> --label "Reply brief" --days 7 --waits-on "the response is served" --rule "L.U.Civ.R. 7(b)(4)" --owner ours
matter trigger fire <dir> <t_id> --on 2026-11-02 --service ecf --source Filings/response.pdf@p.1 --reason "response served" [--dry-run]
matter trigger fire <dir> <t_id> --on 2026-12-01 --service none --dry-run   # what if the order comes Dec. 1? writes nothing
```

A deadline that starts when something happens is a trigger, not a hand-counted
row. When the event happens, `trigger fire` counts the days (Fed. R. Civ. P.
6(a)(1) and 6(d)) and adds a `working-estimate/unverified` deadline with every
step in its event. Show the user the steps and have a person check the date
before anyone marks it verified. Firing needs the year's state holidays recorded
(`holiday add`, then `holiday year`) from a registered source.

An assertion with no recognized source renders `[UNSUPPORTED]` everywhere. That
is deliberate: unsupported leaps should be visible to the user, the agent, and
any lawyer reading the output.

## Custodian correspondence (holds / records requests)

Use the correspondence ledger for custodian notices; counsel outreach recipients
are not custodians. Register the addressee with `party add --name NAME --role
agency --capacity custodian` (or `role=other`), then select that party ID:

```bash
matter correspondence add <dir> --kind hold --addressee p_ID --address "Example mailing address" --exhibit Correspondence/esi-schedule.txt --confirm-due 2026-10-01 --assertion a_ID
matter correspondence export <dir> cn_ID
matter correspondence list <dir>
```

Ingest the exhibit first. All document references are IDs, SHA-256 hashes, or
matter-relative paths. Select assertions explicitly with repeated `--assertion`;
the default selects none. Generated letter bodies only include DOCUMENTED FACT
and UNKNOWN rows, with provenance or `[UNSUPPORTED]`. HYPOTHESIS, INFERENCE, and
ALLEGATION rows cannot enter through assertion selection. Do not paste theories
into addressee metadata or exhibits to evade the filter. Humans review exhibits
as well as the letter; hashing proves custody, not truth.

Exports under `.matter/out/correspondence/<sha>/` contain the packet, exact
Exhibit A bytes, and a manifest binding the addressee and packet to their hash.
`confirm_due` creates an open working-estimate deadline; it is a requested date,
not an automatically verified legal deadline. The packet prints this mapping:

```text
custody verified ≠ assertion DOCUMENTED FACT
(A) Confirmed = DOCUMENTED FACT (needs a hashed source)
(B) Lead = INFERENCE or UNKNOWN + a next ask; never a packet headline
(C) Hypothesis = HYPOTHESIS; omitted from outbound correspondence
```

Human actions are recorded using `correspondence status <dir> cn_ID signed
--issuer NAME --sha SHA`, then `mailed` with the same flags plus `--receipt
Correspondence/mailing-receipt.pdf`. The human reviews/signs/sends outside the kit
and supplies evidence. Ingest receipts before recording them. Delivery
confirmation (`receipt-hashed`) and `answered|refused` require their own ingested
evidence via `--receipt` and a human issuer. Do not invent an issuer or infer
mailing from an export. Transport stays `nosend` throughout. A changed approved
packet must be reviewed again; an unmailed signed notice can return to `draft`.
Rule 9 applies after adding source/receipt files. Derived export bundles stay
under `.matter/` and are not ingested as sources or receipts.

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
`matter.deadline_list`, `matter.trigger_list`, `matter.trigger_fire`,
`matter.packet_render`, `matter.cite_extract`,
`matter.ingest_path`, `matter.verify`, `matter.hold_render`,
`matter.correspondence_list`, `matter.caselaw_status`, `matter.search`,
`matter.cite_lookup`. `hold_render` takes a `notice_id` and returns the
filtered packet without approving or sending it. `ingest_path` refuses path escape.
Read-mostly plus local ingest/verify; nothing files or contacts anyone.

## Case law lookups

`matter.search` (CourtListener opinion search) and `matter.cite_lookup` (are the
draft's cited cases real, and do the names match?) are how you keep up with the
case law. They send only search words and bare citations, never documents.

They work once the person has turned case law lookups on for the case. You
cannot turn them on, and must not try (no grant files, no `consent` commands).
If a lookup replies "Nothing was sent" because lookups are off or the key is
missing, tell the person in plain words, one time, what the reply says:

> To let me look up case law for this case, open Matter Kit in your browser,
> open the case, click "Case law lookups", then "Turn on". To check citations,
> also paste your free CourtListener key there (CourtListener's API page has
> it). Or run `python3 matter.py courtlistener on <case folder>` once.

Then carry on with the work that doesn't need lookups. Answers are saved in the
case: repeating a search or re-checking a draft reads them back without sending
anything, even with lookups off, so don't avoid repeating a lookup to save the
daily limit. Pass `fresh: true` only when the person wants a newer answer. A search result or a
FOUND cite is a lead to read, not good law: say so when you use one, and never
mark an authority `source-checked` from a lookup.
