# R3 read-only API discovery — Corpus public surface (2026-09-09)

Verified live against `https://corpuslaw.us/api/mcp` (server `corpus-legal` v1.2.1). No key used. This is discovery only — no adapter calls from the kit yet.

## Endpoints

- MCP: `https://corpuslaw.us/api/mcp` — Streamable HTTP, JSON-RPC, **no OAuth**, works anonymously. Initialize → `tools/list` → `tools/call`.
- REST v1: `https://corpuslaw.us/api/v1/…` — **Bearer key required** for every search (free key = 1,000/mo @ 30 req/min; anonymous REST is not offered).
- OpenAPI: `https://corpuslaw.us/api/v1/openapi.json`; discovery index at `/api/v1`.

## MCP tools actually present (8)

| tool | read-only | required args | notes |
|---|---|---|---|
| `law.search` | yes | `query` | modes: hybrid/semantic/keyword; `jurisdiction` optional; limit 1–25 (default 8). **1 credit/search** |
| `law.get_node` | yes | `id` | full text + citation + hierarchy by UUID |
| `law.list_coverage` | yes | — | jurisdiction inventory |
| `account.status` | — | — | free; requires Bearer key (anonymous gets told how to get one) |
| `formation.requirements` | yes | `entityType`, `state` | free |
| `formation.compare` | yes | — | free |
| `formation.lookup_naics` | yes | `businessDescription` | free |
| `formation.handoff` | yes | `entityType`, `state` | free |

## Capabilities vs the R2 pattern

- **Search: exists** — `law.search` + `law.get_node` map cleanly onto the R2 `SearchAdapter` contract (query → provenance-bearing results). Anonymous MCP: 100 searches/month @ 10 req/min per IP. Keyed: 1,000/mo @ 30/min. Same transport rules apply: no redirects, explicit auth mode, kit consent budget governs.
- **Citation verification: does NOT exist.** `law.verify_citation` is **absent** from the live tool list. The only verification-shaped surface is FLP's separate Citation Lookup API on CourtListener — not Corpus. The R3 adapter therefore ships **search/lookup only**, and `source-checked` stays unreachable from the kit by construction. No pretend-verify.
- **Auth model:** anonymous works for MCP (per-IP meter), keyed raises it. The adapter should treat key as `authenticated` mode exactly like R2 (`fail before egress` if mode says authenticated and no key), record `authenticated` in provenance.
- **Payload:** `query_text` only. `law.get_node` fetches by node id — no document text egress. Formation tools are irrelevant to the connector (money path stays in Corpus's own surfaces).

## Recommended R3 shape (pending council ack)

1. `corpus-search` adapter: `manual_search` against MCP `law.search` (+ `law.get_node` for the node a user names), manual citations/queries only.
2. Same transport discipline as R2: `_RefuseRedirects`, explicit `auth_mode`, key in `CORPUS_API_KEY` only if `authenticated`, `authenticated` recorded in provenance, kit consent budget authoritative.
3. Statuses: results land as `resolved` with provenance; **no path to `source-checked`** (no such tool exists server-side).
4. Credit safety: default anonymous (100/mo), or keyed with a small kit-side daily budget; adapter reports remaining allotment only via `account.status` (free, keyed).
