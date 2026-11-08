# RFC 0008 — Blind teaser manifest, conflict-gate envelope, and external exchange boundary

**Status:** DRAFT — POST-DOGFOOD ROADMAP (depends on [RFC 0007](0007-counsel-handoff-bundle-and-readiness.md) and shared-server authorization seams; not scheduled for `v0.1`/`v0.2`).  
**Date:** 2026-11-03 · **Author:** `agent:gemini-4-argon; directed-by:ty`  
**Parent / Related:** [Roadmap](../docs/2026-11-03_counsel-handoff-and-exchange-roadmap.md) · [RFC 0001](0001-provider-neutral-search-adapters.md) · [RFC 0002](0002-counsel-outreach.md) · [RFC 0003](0003-recipient-discovery.md) · [RFC 0007](0007-counsel-handoff-bundle-and-readiness.md) · [Local Frontend Design](../docs/superpowers/specs/2026-10-31-local-frontend-design.md)  
**Repository rule:** Synthetic examples only (`Roe v. City of Example`, `Smith & Associates LLP`, `example.org`).

---

## 1. Summary

Once a self-represented litigant organizes a matter locally in `matter-kit` and reaches a procedural inflection point (such as obtaining primary public records, surviving a Rule 12(b)(6) motion to dismiss, or receiving a court-ordered mediation referral), they need a safe way to surface the case to prospective attorneys—whether through a court pro bono mediation panel, a nonprofit civil-rights clinic, a bar-sponsored Lawyer Referral Service (LRS), or a commercial clearinghouse such as the **Corpus Exchange**.

Posting an unredacted *pro se* case file—or even a free-text case summary—to an online directory creates severe legal hazards:
1. **Work-product waiver & defense harvesting:** Publicly disclosing trial-preparation analysis or adverse-fact assessments risks waiving **Fed. R. Civ. P. 26(b)(3)** work-product protection and lets opposing counsel or defense insurers identify the case and inspect the plaintiff's vulnerabilities.
2. **Tainted conflict screens:** If an attorney reads a litigant's confidential adverse disclosures *before* discovering that their firm represents a named defendant or municipal insurer, both sides face disqualification and **ABA Model Rule 1.18** conflicts.
3. **Platform lock-in & ethics violations:** Hardcoding a single commercial marketplace or embedding contingent referral/fee-splitting hooks inside open-source software violates `Corpus Open` neutrality and conflicts with **ABA Model Rules 5.4 and 7.2(b)** in traditional U.S. jurisdictions.

This RFC specifies a **provider-neutral, three-stage progressive disclosure protocol** at the boundary of `matter-kit`:

- **Stage 1 — Zero-Prose Blind Teaser (`teaser.json`):** Closed-enum fields, date buckets, verified document/element counts, and SHA-256 commitments to an approved [RFC 0007](0007-counsel-handoff-bundle-and-readiness.md) bundle. **Contains zero free-text strings**, zero party names, zero docket numbers, and zero exact incident dates.
- **Stage 2 — Sealed Conflict-Check Envelope (`conflict_envelope.json`):** Contains strictly the structured `parties` roster (`name`, `role`, `capacity`) and known opposing counsel/entities needed for a bar-verified attorney or panel coordinator to run an adverse-party conflict check before seeing any substantive work product.
- **Stage 3 — Prospective-Client Gated Handoff Access:** Only after conflict clearance, prospective-client confidentiality acknowledgment (Model Rule 1.18), and litigant release does prospective counsel receive either the offline RFC 0007 bundle or a scoped `prospective_counsel_readonly` grant on a shared `matter.py ui` server ([Local Frontend Design §2](../docs/superpowers/specs/2026-10-31-local-frontend-design.md)).

---

## 2. Three-Stage Progressive Disclosure Schemas

### 2.1 Stage 1: `teaser.json` (Zero Free-Text by Construction)

Following the structural safety pattern of [RFC 0006](0006-correspondence-ledger.md) (no free-form letter body override) and `matterkit/citelookup.py` (strict citation-token whitelist), `teaser.json` **forbids arbitrary prose fields**. Every string field is validated against a closed vocabulary or normalized statutory citation parser before serialization:

```json
{
  "schema_version": "handoff-teaser-v1",
  "teaser_id": "ht_9f4a2c1e8b0d",
  "generated_at": "2026-11-03T19:00:00+00:00",
  "court_id": "uscourts:exd",
  "jurisdiction": "US-EX",
  "nature_of_suit_codes": ["440"],
  "claim_categories": ["civil_rights_1983", "state_public_records"],
  "procedural_stage": "rule_12_survived",
  "stage_verified_by_pinned_filing": true,
  "representation_scopes_sought": [
    "pre_appearance_settlement",
    "limited_mediation_adr",
    "full_appearance"
  ],
  "fee_structures_considered": [
    "statutory_fee_shifting",
    "contingency",
    "limited_scope_flat_fee"
  ],
  "declared_fee_statutes": ["42 U.S.C. § 1988"],
  "next_open_deadline_bucket": "31_to_60_days",
  "overdue_deadlines": false,
  "verified_evidence_classes": [
    "official_filing",
    "audio_video_media",
    "custodian_correspondence_receipt"
  ],
  "readiness_rollup": {
    "documents_verified_count": 48,
    "documented_facts_count": 31,
    "adverse_disclosures_logged": true,
    "briefcheck_clean": true,
    "elements_with_documented_fact_ratio": [6, 7]
  },
  "commitments": {
    "handoff_manifest_sha256": "a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3",
    "readiness_sha256": "b3a8e0e1f9ab1bfe3a36f231f676f78bb30a519d2b21e6c530c0eee8ebb4a5d0",
    "conflict_envelope_sha256": "35a9e381b1a27567549b5f8a6f783c167ebf809f1c4d6a9e367240484d8ce281"
  }
}
```

#### Structural Anti-Fingerprinting Rules

1. **No exact dates:** `next_open_deadline_bucket` uses coarse buckets (`none`, `under_14_days`, `14_to_30_days`, `31_to_60_days`, `over_60_days`) so opposing counsel cannot correlate an exact briefing date on PACER back to a blind listing.
2. **No exact byte sizes or file names:** Only coarse `verified_evidence_classes` and aggregate document counts appear in Stage 1.
3. **Cryptographic commitment without leakage:** `commitments` binds the blind teaser to a specific approved RFC 0007 bundle (`handoff_manifest_sha256`) and `conflict_envelope_sha256`. When Stage 3 is eventually unlocked, the attorney's client or `verify-bundle` command confirms that the unlocked case file is the exact file promised by the teaser—preventing bait-and-switch modifications.

---

### 2.2 Stage 2: `conflict_envelope.json` (Adverse-Party Screen Only)

Generated alongside `teaser.json` under `.matter/out/handoff/<manifest-sha256>/conflict_envelope.json`, but **never** bundled into Stage 1 egress:

```json
{
  "schema_version": "handoff-conflict-v1",
  "handoff_manifest_sha256": "a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3",
  "court_id": "uscourts:exd",
  "docket_number_optional": "1:26-cv-00100",
  "parties": [
    {"name": "Jane Roe", "role": "plaintiff", "capacity": "individual"},
    {"name": "City of Example", "role": "defendant", "capacity": "municipal"},
    {"name": "John Doe", "role": "defendant", "capacity": "individual_and_official"},
    {"name": "Smith & Associates LLP", "role": "counsel", "capacity": "defense_counsel"}
  ],
  "custodians_excluded": true,
  "notice": "Provided solely for conflict-of-interest screening by prospective counsel under ABA Model Rule 1.18."
}
```

- Third-party non-adverse records custodians (`role='agency'` with `capacity='custodian'`) and non-party witnesses may be excluded (`custodians_excluded: true`) unless the operator opts to include witnesses for comprehensive conflict screening.
- No assertions, adverse disclosures, research notes, or exhibits appear in `conflict_envelope.json`.

---

### 2.3 Stage 3: Scoped Read-Only Data Room or Offline Bundle Release

After a recipient clears conflicts against Stage 2 and accepts prospective-client confidentiality terms:

1. **Offline / Direct Path (Zero Server):** The litigant transmits the `.matter/out/handoff/<manifest-sha256>/` archive directly to the attorney (encrypted email, clinic upload portal, or physical media). Counsel runs `matter.py handoff verify-bundle` locally.
2. **Self-Hosted Private Server Path (`matter.py ui`):** Using the per-route `policy(identity, case_id, action)` seam in [`matterkit/webui.py`](../matterkit/webui.py), the litigant issues a time-bounded, revocable capability token scoped to `(case_id, role="prospective_counsel_readonly", pinned_manifest_sha256)`. That role permits viewing only the quarantined RFC 0007 views and exhibits—never raw `Research/` folders or unapproved drafts—and logs every access event.
3. **External Clearinghouse Adapter Path (Optional):** An external clearinghouse (court ADR panel, nonprofit intake system, or commercial `Corpus Exchange`) mediates the Stage 1 → Stage 2 → Stage 3 transitions only after explicit human consent grants at each gate.

---

## 3. Consent, Egress, and Provider-Neutral Adapter Contract

Extending [`matterkit/consent.py`](../matterkit/consent.py) ([RFC 0001 §2.4](0001-provider-neutral-search-adapters.md)):

1. **Default transport is `nosend`:** `matter.py handoff teaser <matter_dir>` writes `teaser.json` and `conflict_envelope.json` locally under `.matter/out/handoff/<manifest-sha256>/` and makes **zero network calls**.
2. **Distinct consent payload classes:** `SENSITIVITY` in `consent.py` NEVER treats a `query_text` or `citation_strings` grant as permission to publish a handoff teaser. Handoff egress requires dedicated, human-issued (`kind: "human"`), manifest-pinned grants:
   - `handoff_blind_teaser` — authorizes transmitting one exact `teaser.json` (matched by SHA-256) to one named provider endpoint;
   - `handoff_conflict_envelope` — authorizes transmitting one exact `conflict_envelope.json` to a specific recipient/clearinghouse after human approval.
   - Full Stage 3 exhibit bundles are **never** transmitted automatically by a directory adapter; Stage 3 release requires an explicit per-attorney human action.
3. **Provider neutrality & anti-marketing parity ([RFC 0002](0002-counsel-outreach.md)):**
   - `matter-kit` ships **zero default exchange endpoints** and **zero commercial upsells** in CLI, MCP, or Web UI output.
   - Any adapter implementing `HandoffDirectoryAdapter` (whether targeting a court pro bono portal on `.uscourts.gov`, a legal-aid intake endpoint, or `corpuslaw.us`) uses the identical JSON contract and `.matter/egress.jsonl` hash audit log.

---

## 4. Representation Scope & Local-Rule Disclosure

Because federal district courts frequently restrict attorney withdrawal once a general appearance is entered, every `representation_scopes_sought` entry maps to explicit expectations disclosed in both Stage 1 (`teaser.json`) and the Stage 3 `INDEX.md`:

| Token | Meaning | Appearance / Withdrawal Note |
|---|---|---|
| `pre_appearance_settlement` | Evaluate file, draft Rule 408 demand, negotiate directly with defense counsel prior to entering an ECF appearance. | Terminates cleanly under retention agreement if settlement fails prior to court appearance. |
| `limited_mediation_adr` | Represent litigant solely for court-ordered mediation / settlement conference (ABA Model Rule 1.2(c)). | Requires checking compiled court rules pack (`matterkit/rules.py`) for local limited-appearance permission. |
| `discrete_task_unbundled` | Ghostwriting/reviewing a specific brief or taking/defending specified depositions where local rules permit. | Subject to district ghostwriting/disclosure rules in the active `rules` pack. |
| `subcase_only` | Full representation strictly on a partitioned `Subcases/<slug>/` spin-off claim (e.g., statutory public-records suit). | Does not enter an appearance in the parent litigation. |
| `full_appearance` | General entry of appearance through summary judgment, settlement, or trial. | Prospective statutory fee-shifting (`declared_fee_statutes`) and *Evans v. Jeff D.* fee-waiver terms addressed in counsel's retainer. |

---

## 5. Durable Ethics & Regulatory Guardrails

1. **No fee-splitting or contingent referral hooks (Model Rules 5.4 & 7.2(b)):** Neither `teaser.json` nor any `matter-kit` module records platform commissions, referral percentages, or settlement splits.
2. **No auctioning of claims or champerty:** `teaser.json` is an inquiry for licensed legal representation by the party holding the claim—never an assignment or sale of a cause of action to third-party speculators.
3. **No per-match pricing in any adapter contract:** `HandoffDirectoryAdapter` carries no price, fee or commission field. Per-matter "marketing fees" are the model state bar opinions rejected for Avvo Legal Services (see [Roadmap §4.5](../docs/2026-11-03_counsel-handoff-and-exchange-roadmap.md)).
4. **Revocation and takedown:** `matter.py handoff revoke <export_id>` marks the local `handoff_exports` row `revoked`, revokes any local `webui` capability tokens pinned to that manifest, and (when an adapter grant exists) emits a signed revocation request for the `teaser_id`.

---

## 6. Acceptance Criteria (When Scheduled Post-Dogfood)

1. Schema validator test (`tests/test_blind_teaser.py`) feeds a synthetic matter containing distinctive party names, docket numbers, phone numbers, emails, street addresses, and `HYPOTHESIS` strings into `handoff teaser` and asserts **zero substring occurrences** anywhere in `teaser.json`.
2. Any unexpected key or non-enum string in `teaser.json` raises `ValueError` prior to file write (fail-closed whitelist serialization).
3. `consent.py` tests confirm that an active `query_text` or `document_text` search grant raises `ConsentError` if used to transmit `handoff_blind_teaser` or `handoff_conflict_envelope`, and that `kind: "agent"` grants remain inert proposals.
4. End-to-end offline workflow (`handoff export` → `handoff teaser` → `handoff verify-bundle`) executes with zero network access under `tests/netblock_sitecustomize`.
