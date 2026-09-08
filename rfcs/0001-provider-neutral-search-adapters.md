# RFC 0001 — Provider-neutral search adapters for matter-kit

**Status:** PROPOSED rev3 — design acked by council 2026-09-08; implementation (R1+) still not authorized; kit publish + R3 Corpus adapter founder-gated
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

### 2.2 Three stages, independently stored state (rev2)

Extraction, lookup, and verification are different facts about different objects at different times; they get **separate storage**, not three vocabularies sharing one status column. (Rev2 supersedes the earlier "maps onto the existing `authorities` table — no schema migration" claim, which was wrong: `status='unverified'` had no stage, offsets had no home, and nothing enforced stage ordering.)

**(a) Extraction — new table `citation_extractions`** (one row per extraction occurrence against an identified source version):

```sql
CREATE TABLE IF NOT EXISTS citation_extractions (
  id TEXT PRIMARY KEY,
  document_sha256 TEXT NOT NULL,      -- which exact source bytes were parsed
  parser TEXT NOT NULL,               -- parser + version (e.g. kit-statute-0.1, eyecite-2.7.8)
  char_start INTEGER, char_end INTEGER,
  raw_fragment TEXT NOT NULL,
  parsed_citation TEXT,               -- NULL when extraction failed
  status TEXT NOT NULL CHECK (status IN ('extracted','extraction-failed')),
  created_at TEXT NOT NULL
);
```

Failed extractions are first-class rows (`extraction-failed`, `parsed_citation` NULL, offsets present) — never folded into "research pending." Manually logged authorities get **no invented extraction history**: rows appear only when a parser actually runs over a sha256-pinned document version.

**(b) Lookup + verification — `authorities.status` keeps one column with a documented, collision-free vocabulary:**

- `unverified` — logged (typically by hand); lookup not yet performed. Existing v0.1 rows already mean exactly this and are preserved as-is — a **meaning migration** documented here, with no data migration and no backfilled extraction rows.
- `resolved` (with `checked_via` = provider + external id) — a lookup returned a concrete authority record.
- `source-checked` — a defined source check was performed against the resolved authority.
- `research-pending` — verification requested, not yet performed. **Legal only in this stage.**
- `stale-flagged` — a source check surfaced a supersession/currency question.

**Deliberate non-reuse:** the verification token is `source-checked`, not `verified-official` — `deadlines.verification_status` already uses `verified-official` to mean "checked against a rule source," and one token carrying two meanings across two tables is exactly the ambiguity that turns a diligence flag into an apparent legal conclusion. **On both tables, every such token means "a defined source was checked" — never "this proposition is correct" or "this case remains good law."**

**(c) Stage-order validator — app-level check in `store.py`.** SQLite CHECKs cannot see row history, so `connect()`-level helpers enforce the transition rule: writing a verification-stage status requires the row to already carry lookup evidence (non-null `checked_via`); `source-checked`/`research-pending` on an `unverified` row raises. An adapter bug that jumps straight to "verified" fails loudly. Extraction→lookup ordering is enforced by construction: lookups consume *parsed* citations from `citation_extractions`, and `extraction-failed` rows are not addressable by lookup at all.

### 2.3 Local extraction is connector-independent

The Corpus-side eyecite smoke (2026-09-08) showed case-cite strength but statutory gaps (`8 Del. C. § 141`, `15 U.S.C. § 1681a` unparsed; junk `UnknownCitation('§')` tokens; `Code Ann.` styles fine). The kit therefore owns a **local citation layer that never needs a key**:

- case cites: the kit's own stdlib parser is the **default** (acceptance targets below); `eyecite` may later become an optional extra behind the Corpus eval's hybrid decision rule and a BSD-2 license pin — never a default dependency;
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
  "kind": "human",
  "authorized_callers": ["matter-agent"],
  "created_at": "2026-09-08T22:00:00+00:00",
  "expires": null
}
```

- **Issuer / callers / scope (rev4):** a grant names its issuer (`created_by`), its `kind` (`human` — authorizing the issuer and exactly the listed `authorized_callers`; `agent` — the conservative default when absent — an **inert proposal** that authorizes no one, including its author), and a scope (endpoints × payload types × expiry × budget). Human authorship neither authorizes every agent implicitly nor blocks explicit delegation; the headless workflow is a human-issued grant listing the agent.

- **Payload sensitivity ladder:** `query_text` < `passage_text` < `document_text`. Query text is itself matter-derived ("may contain case facts" is part of any `query_text` grant's meaning); `passage_text`/`document_text` are excluded from any default grant and require explicit logged per-call escalation.
- **Fail-closed baseline:** adapter construction refuses without a matching unexpired grant, and every call re-checks its payload type against the grant — escalation refuses at call time, not just construction time.
- **Trust model (rev2 — precise claims):** a `consent.json` that an agent can write is a **cooperative-agent guardrail, not compromised-agent containment**. When the adversary is the runtime itself, the file, its `created_by` field, and any in-matter audit log are all inside the adversary's write authority and can be forged or rewritten. What the kit honestly provides: (i) **no accidental egress** — absent, expired, or out-of-scope grants are refused by the kit's own code at construction and at call time; (ii) **no legitimate self-expansion** — a grant whose `created_by` matches the calling agent identity and lacks a human-held preauthorization record is treated as unauthorized (R1 tests must cover self-expansion, cross-agent grant reuse, expired grants, and payload-type escalation, not just missing grants); (iii) **compromised-agent containment requires a trust anchor outside the agent's write authority** — a separate broker process/user enforcing preauthorized scope, or an OS-user-owned grant store the agent cannot write. (iii) is optional deployment hardening the kit does not ship; without it, the kit's promise is "the kit's own call graph refuses," never "the system cannot egress" — an agent retaining unrestricted network tools is a runtime property outside kit control.
- **Authorization provenance:** `egress.jsonl` proves *what the kit sent* (asserted digest), never *who authorized it* or an independent view of the wire; authorization proof lives in the grant's `created_by` plus preauthorization records, which must be append-only or human-held to mean anything in a compromised-agent scenario.
- **Egress audit mechanics:** every adapter call appends one JSON line to `.matter/egress.jsonl`: timestamp, provider, endpoint, payload type, byte size, sha256 of outbound text (hash only — the log must never become a second copy of matter content).
- **Headless-safe:** agents/CLI may write the grant before a run; it is an inspectable repo-diffable file, not an interactive prompt.

### 2.5 Degradation contract

No adapter configured: init, ingest, verify, facts, deadlines, authorities, events, parties, issues, packets, and **local citation extraction** all work exactly as today. Only lookup/verification stages surface `research-pending`. Nothing about the kit's current zero-network behavior changes unless a user writes a grant.

### 2.6 Adapters in scope (later commits, each opt-in)

1. `courtlistener-free` — queries only; honors the free-tier budgets measured in the FLP spike (5/min·50/hr·125/day; per-scope via their api-usage endpoint; citation-lookup scope 60 valid citations/min); per-citation local cache to stay inside daily budgets on a single matter.
2. `corpus` — keyed HTTPS calls to the **public** `law.*` MCP, the same surface any external user gets; no private endpoints, no backdoor. Founder-gated (it is the paid connector).

## 3. Non-goals

No Mike integration (bake-off remains conditional on a named workflow beating the kit on non-sensitive docs). No citator/"good law" claims. No document-text egress by default. No change to the MCP tool surface beyond the one addition below.

## 4. MCP impact

One new read-mostly tool: `matter.cite_extract` — local extraction + stage statuses only. **Isolation claim is PROPOSED until executed:** R1 must land with a proven network-free call graph (stdlib-only import graph, no adapter/urllib/socket modules reachable) plus an isolation test pinning that (e.g., running `cite_extract` under a network-denied environment passes, and an import scan fails CI if `matterkit.search.adapters` is ever reachable from the extraction path). Until then it is a design property, not a fact.

## 5. Rulings recorded (council 2026-09-08) and what remains open

1. `consent.json` **ACK** — supersedes the Corpus spec's `consent.yaml` (naming drift reconciled in the parent docs at Corpus `0324d0e1`; the kit's JSON is authority).
2. `matter.cite_extract` as the only v0.2 MCP addition **ACK, conditional** — isolation stays PROPOSED until the network-free call graph and isolation test exist (§4). No search tools in v0.2.
3. eyecite — **not a parser verdict**: the default install stays stdlib-pure (the kit's own parser must hit Set-B USC/DE/`Code Ann.` acceptance without eyecite); eyecite is eligible as an optional extra only after Set A/B/C and a BSD-2 license pin. R1 does not wait on that eval.
4. Schema finding **BLOCKING, resolved in rev2 (§2.2)**: independent storage per stage; documented meaning migration of `unverified`; `source-checked` deliberately not reusing `deadlines.verified-official`; app-level stage-order validator.
5. Consent trust model **resolved in rev2 (§2.4)**: cooperative-agent guardrail vs compromised-agent containment stated precisely; R1 tests must cover self-expansion, cross-agent grant reuse, expiry, and payload-type escalation; broker/OS-owned trust anchor is optional deployment hardening, not a shipped claim.

Still founder-gated: publishing the kit remote; R3 Corpus adapter authorization (after Set A/B/C and the DE Title 8 delivery proof).

## 6. Rollout (small, separate commits)

- R1: **LANDED 2026-09-08, rev4 after adversarial review** — `matterkit/citations.py` (stdlib parser, extraction-failure rows with offsets keyed to `text_sha256`, stage-order validator incl. evidence requirement), `matterkit/consent.py` (issuer/authorized_callers/scope model: human grants delegate to *listed* callers only; agent-authored grants are inert proposals; fail-closed missing/corrupt/expired/escalating/unlisted cases), `matterkit/search.py` (interface + hash-only egress + **zero adapters**), `citation_extractions` table + `check_evidence` column + backward-compatible migration, `matter cite extract|list` + `matter stage` CLI, `matter.cite_extract` MCP tool. Evidence: `tests/test_rfc0001.py` **35/35** — legacy-store migration, CLI/MCP regression, offsets-vs-text-sha, **Python-level network denial inside the MCP child** (`tests/netblock_sitecustomize/sitecustomize.py` via PYTHONPATH) with **two negative controls** (socket probe fails with the harness, succeeds without it — this is Python-level denial, *not* OS-level containment), AST import-graph scan, consent matrix (listed/unlisted caller, scope overflow, inert agent grants, expiry, escalation, budget). Packet count updated for the new vocabulary (legacy `verified-official` counts as checked). Adversarial corrections folded: Deepseek (subprocess isolation; guard gap), chair (denial inside the child; issuer/callers/scope).
- R2: `courtlistener-free` adapter behind a written grant. *(not started; separate go)*
- R3: `corpus` adapter (founder-gated, keyed, after the Set A/B/C eval and DE Title 8 proof). *(not started)*
