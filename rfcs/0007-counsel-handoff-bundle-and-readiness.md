# RFC 0007 — Counsel handoff bundle and mechanical readiness audit (Corpus Open)

**Status:** DRAFT — POST-DOGFOOD ROADMAP (gated behind Phase 0 dogfood completion; not scheduled for `v0.1`/`v0.2` implementation).  
**Date:** 2026-11-03 · **Author:** `agent:gemini-4-argon; directed-by:ty`  
**Parent / Related:** [Roadmap](../docs/2026-11-03_counsel-handoff-and-exchange-roadmap.md) · [RFC 0001](0001-provider-neutral-search-adapters.md) · [RFC 0002](0002-counsel-outreach.md) · [RFC 0006](0006-correspondence-ledger.md) · [Brief Check & Subcases Spec](../docs/superpowers/specs/2026-10-23-brief-check-and-subcases-design.md)  
**Repository rule:** Synthetic examples only. No live case data, captions, paths, or hashes.

---

## 1. Summary

Today, `matter.py packet` (`matterkit/packet.py`) renders a single Markdown file with a static placeholder under `## Adverse facts & open questions`, while `correspondence export` ([RFC 0006](0006-correspondence-ledger.md)) packages immutable, SHA-256-pinned bundles strictly for third-party record custodians. Neither produces a **self-contained, quarantined diligence archive** that a prospective attorney can inspect offline in three minutes and mathematically verify without seeing private investigation hypotheses.

This RFC specifies the **Counsel Handoff Bundle** (`matter.py handoff`) and its companion **Mechanical Readiness Audit** (`readiness.json`) inside `Corpus Open / matter-kit`:

1. **First-class adverse disclosures (`adverse_disclosures`):** Replaces the static placeholder in `packet.py` with structured rows recording unfavorable facts, procedural vulnerabilities, date/account discrepancies, and open questions.
2. **Procedural posture & element-source links (`matter_posture`, `issue_element_links`):** Records the matter's verifiable procedural stage (e.g., `rule_12_survived`, `mediation_scheduled`) backed by a registered docket/order source, and links `issues.elements_json` entries to supporting `assertions`.
3. **Fail-closed quarantine export (`.matter/out/handoff/<manifest-sha256>/`):** Packages the 30-second, 3-minute, and 15-minute counsel packets, `readiness.json`, deadline/docket ledgers, exact byte-verified copies of cited exhibits (resolving `parent:` locators for `subcase` spin-offs), and a canonical `approval.json` manifest (`transport='nosend'`).
4. **Hard exclusion of quarantined work product:** `HYPOTHESIS` assertions, unapproved `amendment_items`, `kind='research'` private memos, `.matter/runs/`, and `.matter/outreach/` are excluded by construction.
5. **Factual readiness receipts, kept apart from estimates:** `readiness.json` reports custody, citation, deadline and element-linkage counts only. It holds no case score, win probability or dollar value. *Amended 2026-11-06 (ty):* those are no longer banned from the project. Judge analytics and case-value estimates are in scope ([Roadmap §4.4](../docs/2026-11-03_counsel-handoff-and-exchange-roadmap.md)). They are separate, labeled reports, never `readiness.json` fields, so the audit stays something counsel can check mechanically. A bundle may carry them as their own files, each stating its source, sample size, method and uncertainty.

---

## 2. Schema Additions (Post-Dogfood Increment)

Opening an existing store adds four tables without mutating existing `documents`, `assertions`, `deadlines`, `authorities`, `correspondence_*`, `amendment_*`, or `outreach_*` rows:

```sql
CREATE TABLE IF NOT EXISTS adverse_disclosures (
  id TEXT PRIMARY KEY,
  category TEXT NOT NULL CHECK (category IN (
    'adverse_fact', 'procedural_risk', 'discrepancy', 'immunity_or_bar', 'open_question'
  )),
  text TEXT NOT NULL CHECK (length(trim(text)) > 0),
  source_document_id TEXT REFERENCES documents(id),
  locator TEXT,
  mitigation_note TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'resolved', 'superseded')),
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matter_posture (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  court_id TEXT NOT NULL,                 -- e.g. "uscourts:mad" or state court identifier
  jurisdiction TEXT NOT NULL,             -- e.g. "US-MA", "US-FED-CA1"
  stage TEXT NOT NULL CHECK (stage IN (
    'pre_suit',
    'filed_pre_service',
    'served_awaiting_response',
    'rule_12_pending',
    'rule_12_survived',
    'discovery_open',
    'rule_56_pending',
    'rule_56_survived',
    'mediation_scheduled',
    'trial_set',
    'post_judgment_or_appeal'
  )),
  operative_pleading_id TEXT REFERENCES documents(id),
  stage_order_document_id TEXT REFERENCES documents(id), -- order/docket proving stage when post-filing
  declared_fee_statutes_json TEXT NOT NULL DEFAULT '[]', -- JSON array of parsed statutory strings
  updated_by TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS issue_element_links (
  issue_id TEXT NOT NULL REFERENCES issues(id),
  element_index INTEGER NOT NULL CHECK (element_index >= 0),
  assertion_id TEXT NOT NULL REFERENCES assertions(id),
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (issue_id, element_index, assertion_id)
);

CREATE TABLE IF NOT EXISTS handoff_exports (
  id TEXT PRIMARY KEY,
  manifest_sha256 TEXT NOT NULL UNIQUE CHECK (length(manifest_sha256) = 64),
  readiness_sha256 TEXT NOT NULL CHECK (length(readiness_sha256) = 64),
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'superseded', 'revoked')),
  transport TEXT NOT NULL DEFAULT 'nosend' CHECK (transport = 'nosend'),
  manifest_json TEXT NOT NULL,
  empty_adverse_attested_by TEXT,         -- required if active adverse_disclosures count == 0
  approved_by TEXT,
  approved_at TEXT,
  created_at TEXT NOT NULL
);
```

### 2.1 Invariants

1. **Posture proof rule:** Any post-filing `stage` that asserts a judicial milestone (`rule_12_survived`, `rule_56_survived`, `mediation_scheduled`, `trial_set`) requires a non-null `stage_order_document_id` pointing to an ingested `kind='filing'` source whose bytes pass `verify`. Without a pinned filing/order, `readiness.json` marks `stage_verification: "unverified-operator-claim"`.
2. **Adverse-fact honesty gate:** If `SELECT COUNT(*) FROM adverse_disclosures WHERE status='active'` is `0`, `handoff approve` refuses unless the human operator passes `--attest-no-known-adverse-facts --issuer <human>`, which stamps a prominent `⚠ NO ADVERSE FACTS LOGGED (OPERATOR ATTESTED)` warning banner at the top of every packet layer and inside `readiness.json`.
3. **Authorship attribution:** Every write to `adverse_disclosures`, `matter_posture`, and `issue_element_links` enforces `store.attribution()` (`human:<name>` or `agent:<model>; directed-by:<person>`). `handoff approve` rejects `agent:*` issuers.

---

## 3. Quarantine & Export Rules

`matter.py handoff export <matter_dir>` stages a new immutable directory under `.matter/out/handoff/<manifest-sha256>/` (refusing to overwrite existing bundles, matching RFC 0006):

```
.matter/out/handoff/<manifest-sha256>/
├── INDEX.md                  # 60-second orientation + verification command + boundary notice
├── packet-30s.md             # Posture, claims, fee statutes, next hard deadline, top adverse risks
├── packet-3min.md            # Parties, critical dates, element-to-fact matrix, adverse disclosures
├── packet-15min.md           # Full allowed assertion ledger with exact exhibit pinpoints + timeline
├── readiness.json            # Mechanical custody, briefcheck, authority, and deadline audit
├── docket_and_deadlines.json # Registered filings, open/met deadlines, trigger audit trail
├── exhibits/                 # Exact byte copies of referenced documents (`<doc_id>_<sanitized_name>`)
└── approval.json             # Canonical manifest payload + outer `sha256` (`transport: "nosend"`)
```

### 3.1 Hard Quarantine Filter (Enforced at Render & Export)

| Object / Path Class | Handoff Bundle Treatment | Rationale |
|---|---|---|
| `assertions` with `status='HYPOTHESIS'` | **Always omitted** | Prevents speculative research theories from tainting counsel diligence or waiving work product; header prints `HYPOTHESIS rows omitted (N)`. |
| `assertions` with `DOCUMENTED FACT`, `ALLEGATION`, `INFERENCE`, `UNKNOWN` | Included with explicit badge; sourceless rows render `[UNSUPPORTED]` | Preserves epistemic honesty; incoming counsel distinguishes proved facts from pleaded allegations. |
| `documents` with `kind='research'` | **Excluded by default** | Quarantines private strategy memos and LLM transcripts under `Research/`. |
| `documents` referenced by included `assertion_sources`, `adverse_disclosures`, or `matter_posture` (`kind ∈ {filing, evidence, correspondence, media, tracker}`) | Copied byte-for-byte into `exhibits/` after live SHA-256 re-verification | Any `CHANGED`, `MISSING`, or `ESCAPED` source aborts export with non-zero exit. |
| `Subcases/<slug>/` `parent:<rel_path>` sources | Resolved read-only via `store.connect_readonly(parent_dir)`, rehashed, and copied into `exhibits/` | Lets a spin-off subcase hand off cleanly to specialist counsel without exposing unrelated parent-matter files. |
| `amendment_items` (`proposed`/`dropped`), `.matter/runs/`, `.matter/outreach/`, `.matter/egress.jsonl` | **Never exported** | Internal drafting and outreach history stay strictly local to the litigant's store. |

---

## 4. Mechanical Readiness Audit (`readiness.json`)

`readiness.json` gives an evaluating attorney an instant, machine-verifiable structural audit of the file. Every field is a **count, boolean, hash, or closed enum computed deterministically from SQLite and on-disk bytes**:

```json
{
  "schema_version": "handoff-readiness-v1",
  "generated_at": "2026-11-03T19:00:00+00:00",
  "disclaimer": "Mechanical custody and citation audit of user-organized materials. Not a legal merits evaluation, valuation, or legal advice.",
  "store_integrity": {
    "verified_from_foreign_cwd": true,
    "documents_ok": 48,
    "documents_changed": 0,
    "documents_missing": 0
  },
  "posture": {
    "court_id": "uscourts:exd",
    "jurisdiction": "US-EX",
    "stage": "rule_12_survived",
    "stage_verification": "pinned-filing-verified",
    "operative_pleading_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "declared_fee_statutes": ["42 U.S.C. § 1988"]
  },
  "assertions_summary": {
    "documented_fact": 31,
    "allegation": 12,
    "inference": 4,
    "unknown": 2,
    "hypothesis_quarantined": 5,
    "unsupported_included": 0
  },
  "adverse_disclosures": {
    "active_count": 3,
    "categories": {
      "adverse_fact": 1,
      "procedural_risk": 1,
      "discrepancy": 1
    },
    "empty_operator_attested": false
  },
  "brief_and_authority_audit": {
    "operative_filings_checked": 2,
    "briefcheck_blocking_findings": 0,
    "briefcheck_manual_review_flags": 1,
    "authorities_total": 18,
    "authorities_by_status": {
      "source-checked": 14,
      "resolved": 4,
      "unverified": 0,
      "stale-flagged": 0
    }
  },
  "deadline_hygiene": {
    "open_deadlines": 2,
    "overdue_deadlines": 0,
    "verified_official_count": 2,
    "working_estimate_count": 0
  },
  "custodian_correspondence": {
    "mailed_or_later": 4,
    "receipt_hashed": 4,
    "answered": 2,
    "refused": 1
  },
  "element_coverage": {
    "issues_active": 2,
    "elements_total": 7,
    "elements_with_documented_fact": 6,
    "elements_allegation_only": 1,
    "elements_unlinked": 0
  }
}
```

### 4.1 Explicit Prohibition on Composite Scores

Neither `readiness.json` nor any readiness surface built on it (CLI, UI, MCP, packet header) may collapse these axes into a letter grade (`A+`), percentage (`92% ready`), traffic light, or dollar figure. This rule is narrower than it once was: since 2026-11-06, judge analytics and case-value estimates are allowed in `matter-kit` as separate, labeled reports under [Roadmap §4.4](../docs/2026-11-03_counsel-handoff-and-exchange-roadmap.md), never as readiness fields or a readiness rollup. Attorneys weight procedural posture, fee-shifting statutes, and adverse facts differently depending on their practice; flattening factual counts into an opaque score misleads self-represented users and invites unauthorized-practice-of-law challenges.

---

## 5. CLI, MCP, and Verification Contract

- **CLI:**
  - `matter.py adverse add|list|resolve <matter_dir> ...`
  - `matter.py posture set|show <matter_dir> ...`
  - `matter.py issue link-element <matter_dir> --issue <id> --element <idx> --assertion <a_id>`
  - `matter.py handoff render|readiness|export|approve|verify-bundle <matter_dir> ...`
- **Standalone bundle verification (`verify-bundle`):**
  - `python3 matter.py handoff verify-bundle /path/to/exported-bundle` recomputes the canonical JSON digest of `approval.json`, rehashes every file under `exhibits/` and `packet-*.md`, and exits `0` only if every byte matches the signed manifest—allowing incoming counsel to verify integrity without access to the litigant's `.matter/matter.db`.
- **MCP (`matter-mcp.py`):**
  - Read-mostly parity with RFC 0006: `matter.handoff_readiness` and `matter.packet_render` are exposed as read tools; `handoff approve` is **never** exposed over MCP so an agent cannot self-approve an outbound counsel bundle.

---

## 6. Acceptance Criteria (When Scheduled Post-Dogfood)

1. Synthetic unit suite (`tests/test_handoff_bundle.py`) proves:
   - `HYPOTHESIS` rows and `Research/` files never appear in `packet-*.md`, `readiness.json`, `exhibits/`, or `approval.json`, even if linked to an issue element;
   - any byte modification to an ingested exhibit causes `handoff export` to abort with non-zero exit and zero partial files left behind;
   - `Subcases/<slug>/` exports resolve `parent:` exhibits read-only without copying unreferenced parent documents;
   - `handoff approve` refuses `agent:*` issuers and refuses zero-adverse-disclosure bundles unless `--attest-no-known-adverse-facts` is explicitly supplied by a human issuer;
   - `handoff verify-bundle` succeeds on an untouched export from `/tmp` under `netblock_sitecustomize` (zero network calls).
