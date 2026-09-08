# RFC 0001 — Provider-neutral search adapters for matter-kit

**Status:** PROPOSED — not an implementation order; awaits council/founder ack
**Date:** 2026-09-08 · **Author:** @flash (Hermes seat)
**Provenance:** Council 2026-09-08 founder clarification (OSS toolset / optional paid Corpus connector / private matter data); design source: Corpus `docs/superpowers/specs/2026-09-08-oss-toolset-eyecite-eval-and-connector-design.md` rev2 (@ `08c75306`); grok-4-6 directed this RFC into the kit repo.

## 1. Summary

Give matter-kit one internal search interface with pluggable providers, so that:

- the kit is **fully useful with zero providers configured** (its current state);
- free lookups (CourtListener) and the paid Corpus connector are **both just adapters** — no kit code knows which is which, and no Corpus code ever imports kit matter data;
- every byte that leaves the machine does so under an **inspectable, fail-closed consent grant**, and every egress is auditable;
- citation handling reports **three separate stages** (extraction / lookup / verification) so a missing provider can never disguise a parsing failure as "research pending."

## 2. Design

### 2.1 Interface (new `matterkit/search.py`, stdlib-only, keeps the zero-dep rule)

```
SearchQuery:   text, jurisdiction=None, source_type in {statute, regulation, case, local, auto},
               as_of_date=None, limit=10
SearchResult:  authority_id (provider-scoped), citation, name, snippet,
               jurisdiction, effective_date=None,
               provenance {provider, url, retrieved_at}
SearchAdapter: search(query: SearchQuery) -> list[SearchResult]     # pure function of inputs
```

Adapters are constructed only from an explicit grant (§2.4). The kit ships **zero adapters and zero grants**.

### 2.2 Three-stage citation status (maps onto the existing `authorities` table)

The store's `authorities` row (`citation`, `status`, `checked_via`, `note`) already models staged diligence. Formalize `status` vocabulary as the three stages, machine-set:

| stage | status value | set by | requires network |
|---|---|---|---|
| extraction | `extracted` / `EXTRACTION_FAILED (format unsupported)` | local parser (§2.3) | no |
| lookup | `unresolved` / `resolved <provider>` | adapter or free/local resolver | adapter only |
| verification | `verified-official` / `stale?` / `research-pending` | adapter | adapter only |

Rule: `research-pending` is legal **only** at the verification stage. An unparsed cite renders `EXTRACTION_FAILED` with the character offsets of the unparsed fragment — never silently, never as resolved. `matter.fact` provenance and counsel packets display the stage, so a reader can always tell *which* layer is missing.

### 2.3 Local extraction is connector-independent

The Corpus-side eyecite smoke (2026-09-08) showed case-cite strength but statutory gaps (`8 Del. C. § 141`, `15 U.S.C. § 1681a` unparsed; junk `UnknownCitation('§')` tokens; `Code Ann.` styles fine). The kit therefore owns a **local citation layer that never needs a key**:

- case cites: adopt `eyecite` behind the Corpus eval's hybrid decision rule (Apache/BSD license to be verified; version-pinned) — or keep kit-native parsing if the eval favors it;
- statutory cites: extend the kit's own parser (stdlib regex over `reporters_db`-style patterns is acceptable) targeting **Set-B acceptance: recall ≥ 0.95, zero junk tokens** on USC-style (`15 U.S.C. § 1681a`), Delaware (`8 Del. C. § 141`), and `Code Ann.` formats;
- the Corpus connector's paid value starts at **resolution/verification, never parsing** — this RFC makes that structural.

### 2.4 Consent: `.matter/consent.json` (deviation from the Corpus spec, deliberate)

The Corpus spec said `consent.yaml`; the kit's zero-dependency rule (design rule #1: plain files + stdlib) means no YAML parser — this RFC amends the format to **JSON**, same semantics, still human-diffable. One grant per provider:

```json
{
  "provider": "courtlistener",
  "endpoints": ["www.courtlistener.com"],
  "payload_types": ["query_text"],
  "daily_budget": 100,
  "created_by": "ty",
  "created_at": "2026-09-08T22:00:00+00:00",
  "expires": null
}
```

- **Payload sensitivity ladder:** `query_text` < `passage_text` < `document_text`. Query text is itself matter-derived ("may contain case facts" is part of any `query_text` grant's meaning); `passage_text`/`document_text` are excluded from any default grant and require explicit logged per-call escalation.
- **Fail closed twice:** adapter construction refuses without a matching unexpired grant; each call re-checks its payload type against the grant (escalation refuses at call time, not just construction time).
- **Headless-safe:** agents/CLI may write the grant before a run; it is an inspectable repo-diffable file, not an interactive prompt.
- **Egress audit:** every adapter call appends one JSON line to `.matter/egress.jsonl`: timestamp, provider, endpoint, payload type, byte size, sha256 of outbound text (hash only — the log must never become a second copy of matter content).

### 2.5 Degradation contract

No adapter configured: init, ingest, verify, facts, deadlines, authorities, events, parties, issues, packets, and **local citation extraction** all work exactly as today. Only lookup/verification stages surface `research-pending`. Nothing about the kit's current zero-network behavior changes unless a user writes a grant.

### 2.6 Adapters in scope (later commits, each opt-in)

1. `courtlistener-free` — queries only; honors the free-tier budgets measured in the FLP spike (5/min·50/hr·125/day; per-scope via their api-usage endpoint; citation-lookup scope 60 valid citations/min); per-citation local cache to stay inside daily budgets on a single matter.
2. `corpus` — keyed HTTPS calls to the **public** `law.*` MCP, the same surface any external user gets; no private endpoints, no backdoor. Founder-gated (it is the paid connector).

## 3. Non-goals

No Mike integration (bake-off remains conditional on a named workflow beating the kit on non-sensitive docs). No citator/"good law" claims. No document-text egress by default. No change to the MCP tool surface beyond the one addition below.

## 4. MCP impact

One new read-mostly tool: `matter.cite_extract` — local extraction + stage statuses only; **no network path exists in its call graph**, so it needs no grant. Lookup/verification stay adapter-side (CLI/agent), not MCP tools, until a real workflow demands otherwise.

## 5. Open questions (council/founder)

1. Confirm `consent.json` amendment (§2.4) supersedes the spec's `consent.yaml`.
2. Confirm `matter.cite_extract` as the only MCP addition for v0.2.
3. Is the eyecite dependency acceptable for the OSS kit (pulls lxml et al.) if the eval favors it, or is "stdlib-only kit, eyecite optional extra" the right packaging?

## 6. Rollout (small, separate commits)

- R1: `search.py` interface + consent/egress scaffolding + authorities stage statuses + `matter.cite_extract` (zero network).
- R2: `courtlistener-free` adapter behind a written grant.
- R3: `corpus` adapter (founder-gated, keyed, after the Set A/B/C eval and DE Title 8 proof).
