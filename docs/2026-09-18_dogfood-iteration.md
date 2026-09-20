# Matter-kit iteration after live dogfood (2026-09-18)

**Status:** working product note. Not an RFC until the founder picks an increment.  
**Scope:** what broke or went unused while operating a real pending federal civil-rights matter with Hermes, then what to build next.  
**Rule:** no live-case files, names, or evidence quotes in this repo. Examples below are synthetic.

Corpus Open / matter-kit already does the hard thing well: hashed sources, five epistemic labels, `[UNSUPPORTED]` visibility, counsel packets with adverse facts, `nosend` outreach, cwd-independent verify as a *stated* rule. The gap is that **a full investigation-and-hold day can complete without touching the store.** That is the iteration target.

---

## 1. What actually happened

In one session the agent:

1. Loaded the `matter-kit` skill and treated its labels as binding.
2. Wrote a large correspondence set (litigation-hold letters + a shared ESI schedule) and an investigation work product (inventory, timeline, relationship map, MPRA drafts, discovery menu).
3. Archived outside web pages to disk (HTML + SHA-256 + a handwritten provenance table).
4. OCR’d screenshots because vision was unavailable.
5. Used a *second* citation ledger (`grounded-citations` / `sources.py`) for the cover memo.
6. **Did not** run `ingest`, `verify`, `fact add`, `deadline add`, `party add`, or any MCP write against the live store for that new work.

The store at session end still reported the previous census (documents / 24 assertions / 2 open deadlines). New correspondence, research notes, and captures sat on disk as ordinary files. `status` looked healthy. `verify` from a shell that had `cd`’d into the kit repo then printed a long MISSING list — including paths that exist under the matter root.

So the product failed in the way the skill already warns about, just not loudly enough to change the agent’s default loop.

---

## 2. Three taxonomies, one agent

The same afternoon used three label systems:

| System | Tokens | Used for |
|---|---|---|
| Matter-kit assertions | `DOCUMENTED FACT` / `ALLEGATION` / `INFERENCE` / `HYPOTHESIS` / `UNKNOWN` | skill + SQLite |
| Matter-kit document custody | `verified` / `needs-source` / `unverified` / `superseded` / `excluded` | files |
| Workstream prompt (and case-project-reconciliation) | `(A) Confirmed fact` / `(B) lead` / `(C) hypothesis` plus `verified`…`excluded` | the human-facing memo |

The agent spent tokens mapping (A)→DOCUMENTED FACT, (B)→INFERENCE or a new “lead” that the schema does not have, (C)→HYPOTHESIS. Custody `verified` (bytes present + hash matches) was at risk of being read as “the statement in the PNG is true.”

**Iteration:** one mapping table in the skill and in `packet.py`, printed at the top of every packet:

```
custody verified  ≠  assertion DOCUMENTED FACT
(A) Confirmed     =  DOCUMENTED FACT (needs a hashed source)
(B) Lead          =  INFERENCE or UNKNOWN + a next-ask, never a packet headline
(C) Hypothesis    =  HYPOTHESIS; omitted from outbound correspondence
```

Do not add a sixth assertion label. Add an optional `next_ask` field on UNKNOWN/INFERENCE rows so (B) has a home without pretending it is a fact.

---

## 3. Friction table (ranked by how much it cost today)

| # | Friction | What the kit did | What the agent did instead |
|---|---|---|---|
| 1 | **Ingest is not the write path** | `ingest` is a separate CLI; MCP has no `ingest` | `write_file` into Correspondence/ and Research/; store drifted immediately |
| 2 | **`verify` is still easy to mis-run** | Skill says “foreign cwd”; CLI takes `case_dir` | Shell cwd was the kit repo; MISSING spam; agent did not stop the workstream |
| 3 | **No capture primitive** | `source_url` column exists, unused | `curl` + hand SHA-256 + Markdown table |
| 4 | **No correspondence object** | `documents.kind=correspondence` is a file class | 17 letters × 1 exhibit × service plan as Markdown/DOCX; send state is a comment (`DRAFT — Human sends`) |
| 5 | **Counsel outreach ≠ custodian hold** | RFC 0002/0003 are attorney *intake* | Needed sheriff/FBI/EMA *preservation* letters with an ESI schedule, confirmation date, and a ban on HYPOTHESIS in the envelope |
| 6 | **No relationship edges** | `parties.role` enum is plaintiff/defendant/witness/counsel/court/agency/other | Hand-drawn map with verbs `is` / `appears with` / `participates in` / `publicly identified as sworn` / `republished attributed narrative of`. Causal verbs forbidden by prose, not by schema |
| 7 | **OCR/derived is tribal knowledge** | `.matter/out/` convention | Tesseract sidecars next to sources; easy to cite OCR as the original |
| 8 | **Two citation systems** | `authorities` + `cite extract` | `grounded-citations` ledger for the memo; store authorities unchanged |
| 9 | **Packets are counsel-only** | 30s / 3min / 15min diligence | Needed an *investigation* layer (A-facts + map + next ask) and a *hold* layer (addressee, exhibit hash, confirmation due) |
| 10 | **MCP cannot see new files** | read-mostly: status, fact_add, fact_list, deadline_list, packet_render, cite_extract | Agent never called MCP. `fact_add` refuses unknown sources — correctly — which means ingest must happen first, which MCP cannot do |
| 11 | **Draft vs sent** | none | ChatGPT motion drafts live under `Filings/drafts/`; hold DOCX looks mail-ready. Nothing in the store can say “not mailed” |
| 12 | **Third-party custodians vs defendants** | party `role` has `agency` | Easy to promote a sheriff who issued a press release into a defendant. Skill text exists; schema does not constrain it |
| 13 | **Contradiction is a TODO** | CANONICAL_TODO: “Contradiction scan v0” | A Facebook post said “three” deputies and then named four; an order date is internally impossible; an LLM chat claimed a roster the USAO page does not. All survived as prose |
| 14 | **Issue matrix is a TODO** | `issues.elements_json` exists; no `issue.map` renderer | Litigation-relevance table was written by hand (retaliation / color-of-law / Monell / spoliation) |

---

## 4. What not to build

- Auto-send of holds, FOIA, or counsel email. RFC 0002’s `nosend` is the product.
- Auto-upgrade of HYPOTHESIS because a second news page “confirms” it.
- A conspiracy graph. Edges must be a **closed verb list**. `directed` / `caused` / `coordinated` require a sourced assertion with that label, not a line in a map.
- Fetch-and-match attorney discovery (RFC 0003 already closed that as not-this-increment).
- Copying live case bytes into this git repo.
- A second epistemic vocabulary. Alias (A)(B)(C) onto the five labels.

---

## 5. Proposed increments (smallest that would have changed today)

### 0. Close the loop so the store cannot be skipped (skill + MCP, no new tables)

- MCP: `matter.ingest_path`, `matter.verify`, `matter.document_list`.
- Skill: after any write under the matter root, `ingest` that path, then `verify` from a cwd that is not the matter and not the kit. Treat a MISSING/CHANGED line as a **blocker**, not a log line.
- `verify` always prints `root=` as an absolute path resolved from `case_dir`, never from `os.getcwd()`. Non-zero exit if `case_dir` is missing. This is a one-function fix to the failure mode the skill already documents.

**Done when:** an agent can finish a research session only if new files are in `documents` and `verify` is clean from `/tmp`.

### 1. `matter capture` (uses existing `source_url`)

```
matter capture <case> --url URL --out Media/captures/YYYY-MM-DD_host/page.html
```

Writes bytes, sets `source_url`, `kind=media` or `research`, records `retrieved_at` in `notes` or a tiny `captures` table (`url, retrieved_at, sha256, rel_path`). Refuses to overwrite. Derived extracts go to `.matter/out/`.

**Done when:** the handwritten provenance Markdown table is a `matter capture list` render.

### 2. Correspondence ledger (RFC 0006 — not counsel outreach)

A row is a **custodian notice**, not a lawyer pitch.

```
kind        ∈ hold | mpra | foia | touhy | letter | other
status      ∈ draft | signed | mailed | receipt-hashed | answered | refused
addressee   party_id (role=agency|other, capacity=custodian — not defendant)
exhibit_sha256
confirm_due date → auto deadline (confidence=working-estimate until mailed)
```

Renderer: one packet per addressee = letter body + Exhibit A (hash) + “HYPOTHESIS rows omitted.”  
`send` stays `nosend` unless a human supplies a receipt file that hashes.

Reuse RFC 0002’s approval-manifest idea. Do **not** reuse RFC 0002 recipients (those are counsel intake URLs).

**Done when:** “human sends” is a store state, not a Markdown banner, and a HYPOTHESIS assertion cannot be pulled into the letter body.

### 3. Relationship edges (closed verbs)

```
src_party  →  verb  →  dst_party | dst_document
verb ∈ is | appears-with | participates-in | employed-by |
       publicly-identified-as | republished-attributed |
       communicated-with | same-system-as
```

`directed` / `caused` / `coordinated` are **not in the enum**. If someone needs them, they file an assertion with status HYPOTHESIS and a source; the map still does not draw that arrow.

Party `capacity` gains `custodian` | `task-force` | `media` | `fusion-center` without changing `role=defendant`.

**Done when:** the investigation map is `matter map` output, and promoting a custodian to defendant is an explicit `party` update, not a side effect of drawing a line.

### 4. Packet flavors

Keep 30s/3min/15min. Add `--audience`:

| audience | Contents |
|---|---|
| `counsel` | current packet (adverse facts on) |
| `investigation` | only DOCUMENTED FACT + UNKNOWN-with-next_ask; map; no HYPOTHESIS |
| `hold` | correspondence ledger + exhibit hashes + confirm_due |

**Done when:** the investigation memo is generated, not authored from scratch, and HYPOTHESIS cannot leak into `hold`.

### 5. Contradiction scan v0 (already on the live matter’s product TODO)

Second pass over assertions + document extracts:

- same entity, conflicting counts or dates
- LLM-share claims vs official-page claims (shares stay `unverified` / workflow provenance)
- image-only PDF filename vs OCR text (already a skill rule)

Output: a defense-question list. No auto-resolution.

### 6. `issue.map` renderer (already on the product TODO)

Render `issues.elements_json` as a gap table: element → supporting assertion ids → `gap=true` if none are DOCUMENTED FACT. Today’s “litigation relevance” section was this table written in English.

### 7. `authority check` (already on the product TODO)

Wire RFC 0001 adapters behind consent.json. Until then, stop running a parallel `sources.py` ledger for matter prose — either ingest those URLs via `capture` or cite `authorities` only.

---

## 6. MCP surface after increment 0–2

Current: `status`, `fact_add`, `fact_list`, `deadline_list`, `packet_render`, `cite_extract`.

Add, still read-mostly except the two writes that already exist in spirit:

| Tool | Why |
|---|---|
| `matter.ingest_path` | otherwise fact_add cannot see new files |
| `matter.verify` | returns ok/changed/missing with absolute root |
| `matter.capture` | URL → hashed file |
| `matter.document_list` | `--kind correspondence --uningested` |
| `matter.hold_render` | increment 2; nosend |

Refuse: SMTP, browser-post, “mark mailed” without a receipt hash.

---

## 7. Skill patches (can ship before code)

1. **Default loop:** write → ingest path → verify from `/tmp` → fact add. Not optional.
2. **Print the taxonomy map** once per session that touches a matter.
3. **Outbound filter:** anything that leaves the matter (hold, MPRA, counsel packet) may include DOCUMENTED FACT and UNKNOWN. HYPOTHESIS stays in Research/.
4. **`verify` failure is a stop**, not a warning in the narrative.
5. **Media:** if `vision_analyze` is unavailable, OCR to `.matter/out/`, never next to the only copy, and label the locator `ocr-derived`.
6. **Do not load a parallel citation ledger** when `authorities` / `capture` can hold the URL.

These are skill_manage patches, not schema changes.

---

## 8. Suggested order

| Week | Increment | Unblocks |
|---|---|---|
| 0 | verify root print + MCP ingest/verify + skill loop | today’s silent drift |
| 1 | `matter capture` | provenance tables |
| 2 | RFC 0006 correspondence ledger (holds/MPRA, nosend) | 17-letter days |
| 3 | edge table + `matter map` | investigation work product |
| 4 | packet `--audience investigation\|hold` | stop hand-authoring the memo |
| later | contradiction scan, issue.map, authority check | already listed on the live matter’s product TODO |

Increments 5–7 are already named in the dogfood matter’s canonical todo. Do not start them before 0–2; they decorate an empty store.

---

## 9. Acceptance for “the kit was used”

A session that “used matter-kit” means all of:

- [ ] `verify` from a non-matter, non-kit cwd is `ok` (or every CHANGED/MISSING path is explained)
- [ ] every new file is in `documents`
- [ ] every load-bearing sentence in outbound drafts is an assertion id
- [ ] HYPOTHESIS ids are listed in Research/ and absent from hold/MPRA/counsel-outbox renders
- [ ] send_status is not `mailed` unless a receipt hash is in the store

If those boxes are empty, we wrote Markdown *near* a matter. That is not dogfood.

---

## 10. Pointers

- Skill discipline: `skills/matter-kit/SKILL.md`
- Counsel nosend: `rfcs/0002-counsel-outreach.md` (do not overload this for sheriffs)
- Recipient stages: `rfcs/0003-recipient-discovery.md`
- Schema: `schema/matter.schema.json` (`Document.source_url`, `Party.capacity`, `Issue.elements`)
- Live-matter product TODOs (element matrix, contradiction scan, authority check) stay in that matter’s tracker, not here
