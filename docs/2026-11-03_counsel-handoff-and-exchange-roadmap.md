# Long-Term Roadmap — Pro Se Case Maturation, Late-Stage Counsel Handoff, and the Open/Exchange Boundary

**Status:** Canonical long-term architecture & product roadmap (post-dogfood). Not active `v0.1`/`v0.2` build scope, and not in scope for the Nous Research Hermes plugin hackathon (decided 2026-11-07, ty).  
**Date:** 2026-11-03 · **Author:** `agent:gemini-4-argon; directed-by:ty`  
**Related specs & RFCs:** [RFC 0001](../rfcs/0001-provider-neutral-search-adapters.md) · [RFC 0002](../rfcs/0002-counsel-outreach.md) · [RFC 0003](../rfcs/0003-recipient-discovery.md) · [RFC 0006](../rfcs/0006-correspondence-ledger.md) · [RFC 0007](../rfcs/0007-counsel-handoff-bundle-and-readiness.md) · [RFC 0008](../rfcs/0008-blind-teaser-and-exchange-boundary.md) · [Local Frontend Design](superpowers/specs/2026-10-31-local-frontend-design.md)  
**Repository rule:** Synthetic examples only (`Roe v. City of Example`, `example.org`). Live matter files, captions, paths, and hashes never enter this repository.

---

## 1. Thesis: Closing the "Missing Middle" Litigation Gap

Across civil rights, consumer protection, wage-and-hour, tenant/property, and public-records disputes, thousands of meritorious claims fall into a structural dead zone:

1. **Unviable at intake for full-scope contingency counsel:** Litigating a contested civil case from initial investigation through discovery and summary judgment routinely consumes \$40,000–\$150,000+ in attorney hours and hard costs. On a traditional 33%–40% contingency fee, a claim with \$15,000–\$60,000 in provable compensatory damages is negative-expected-value at day zero—especially when evaluating an unstructured *pro se* file requires 5–10 hours of unpaid diligence just to rule out missed notice windows, altered files, or procedural defaults.
2. **Asymmetric defense holdout against self-represented litigants:** Even when a self-represented plaintiff has primary documentary or audio/video evidence, institutional defendants routinely hold out rather than settle early. Two forces drive that holdout:
   - **Procedural attrition:** Defense counsel prices in a high probability that an unrepresented party will miss a deadline under Fed. R. Civ. P. 6, fail to preserve records, plead past a statutory immunity bar, or submit unverified AI-generated citations.
   - **Disabled statutory fee-shifting:** Under **_Kay v. Ehrler_, 499 U.S. 432 (1991)**, a *pro se* litigant cannot recover prevailing-party attorney's fees under 42 U.S.C. § 1988 (and analogous fee-shifting statutes), even if the litigant is a licensed lawyer. Without plaintiff's counsel on the docket, the defendant's downside exposure is capped near compensatory damages plus its own defense bills.
3. **Why mid-stream *pro se* cases are normally rejected anyway ("damaged goods" risk):** In principle, a case that already has authenticated public records, preserved video, and a complaint that survived Rule 12(b)(6) requires far fewer remaining hours—and an incoming attorney's appearance immediately turns **prospective** statutory fee-shifting back on while eliminating the defense's procedural-attrition discount. In practice, lawyers still reject late-stage *pro se* matters because most self-represented files arrive compromised by unlogged correspondence, silent file edits, blown deadlines, or hallucinated filings.

### Core Strategic Insight

Raw AI *drafting* makes mid-stream *pro se* files **harder** for counsel to adopt because it generates plausible-looking prose untethered from verifiable sources. **Provenance-enforced case organization** (`Corpus Open / matter-kit`) solves the opposite problem: it keeps a self-represented matter byte-verifiable, citation-checked, deadline-audited, and candid about adverse facts from day one so that **when the case reaches a procedural inflection point, an attorney can audit it in minutes and step in to resolve or try it.**

---

## 2. Strict Two-Layer Architecture: `Corpus Open` vs. External Exchanges

To preserve user trust, open-source neutrality, and legal-ethics compliance across U.S. jurisdictions, the long-term system is permanently divided into two layers with a hard firewall between them:

```
┌────────────────────────────────────────────────────────────────────────────┐
│ LAYER 1: Corpus Open / matter-kit ( THIS REPOSITORY — Apache-2.0 )         │
│ Free · Local-first · Stdlib-only · Zero network required · Non-commercial  │
│                                                                            │
│ • Immutable source registry + SHA-256 integrity (`ingest`, `verify`)       │
│ • Enforced 5-label epistemic ledger + `[UNSUPPORTED]` exposure (`facts`)   │
│ • First-class adverse disclosures + procedural posture + element mapping   │
│ • Mechanical brief & citation audits (`briefcheck`, `citelookup`, `rules`) │
│ • Rule 6 deadline & trigger engine + custodian hold ledger (RFC 0006)      │
│ • Offline Counsel Handoff Bundle + Readiness Audit (RFC 0007)              │
│ • Shared private case-room WSGI server (`matter.py ui` server mode)        │
│ • Provider-neutral Blind Teaser & Conflict Envelope export (RFC 0008)      │
└───────────────────────────────┬────────────────────────────────────────────┘
                                │  Explicit human-issued consent grant
                                │  Default `nosend` local bundle export
                                ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ LAYER 2: External Clearinghouses & Corpus Exchange ( OUTSIDE THIS REPO )   │
│ Court pro bono panels · Nonprofit clinics · Bar LRS · Corpus Exchange      │
│                                                                            │
│ • Consumes closed-enum Stage 1 Blind Teasers (`teaser.json`)               │
│ • Verifies attorney bar standing + runs Stage 2 Conflict Screen            │
│ • Enforces Model Rule 1.18 prospective-client confidentiality gate         │
│ • Unlocks Stage 3 read-only Handoff Data Room for settlement/trial review  │
└────────────────────────────────────────────────────────────────────────────┘
```

### Why `matter-kit` Must Never Contain Marketplace Logic

1. **Credibility of `verify` and `readiness.json`:** Incoming attorneys, legal-aid clinics, and court pro bono coordinators will only trust a `matter-kit` verification receipt if the underlying tool is an open, inspectable, vendor-neutral standard—not a lead-generation funnel optimizing for conversion.
2. **Universal utility without an account:** Every handoff feature in `matter-kit` (RFC 0007 bundles, private self-hosted `matter.py ui` sharing, RFC 0008 blind teasers) must work offline via email, USB drive, clinic intake portal, or court ADR coordinator without touching `corpuslaw.us`.
3. **Provider parity (RFC 0001 principle):** Just as search adapters treat CourtListener and `Corpus` through the same consent-gated interface, handoff exports treat a nonprofit legal clinic, a federal court pro bono mediation panel, and a commercial `Corpus` exchange identically.

---

## 3. High-Leverage Inflection Points for Counsel Handoff

Not every stage of a lawsuit is equally attractive to incoming counsel. The architecture models discrete procedural inflection points where litigation economics shift sharply:

| Inflection Point | What the Litigant Accomplished Locally in `matter-kit` | Why Attorney Economics Flip | Typical Engagement Scope |
|---|---|---|---|
| **A. Pre-Suit / Records-Backed** | Custodian holds (`hold`) and public-records requests (`mpra`/`foia`) answered and hashed; video/audio captured; statutory notice clocks tracked. | Eliminates unpaid pre-suit factual investigation; lawyer sees primary records and adverse facts in 3 minutes. | Full contingency, statutory fee-shifting, or pre-suit Rule 408 settlement demand. |
| **B. Post-Rule 12(b)(6) Survival** | Operative pleading survived dismissal (or qualified immunity at the pleading stage); docket and `briefcheck` clean. | Pleading-stage dismissal risk is retired; discovery opens; **prospective statutory fee-shifting** (e.g., 42 U.S.C. § 1988) activates upon counsel's appearance. | Full appearance, discovery + summary-judgment counsel, or settlement counsel. |
| **C. Court-Ordered Mediation / ADR** | Case referred to magistrate settlement conference or ADR panel; element-to-source matrix and damages documentation organized. | Bounded time commitment; opposing counsel faces an experienced negotiator backed by credible readiness to retain trial counsel. | **Limited-scope mediation / settlement counsel** under ABA Model Rule 1.2(c) (subject to local court rules). |
| **D. Post-Rule 56 Survival (Trial-Ready)** | Genuine disputes of material fact survived summary judgment; exhibits, witness pinpoints, and adverse facts indexed. | Highest settlement pressure point in civil litigation; defense faces immediate trial cost + prevailing-party fee exposure. | Trial counsel or co-counsel appearance. |
| **E. Modular Spin-Off (`subcase`)** | Discrete ancillary claim (e.g., open-records denial with statutory fee award, or standalone state tort) partitioned via `matter.py subcase`. | Specialist counsel can take a narrow, fast-resolving statutory claim without taking on a sprawling multi-defendant federal action. | Standalone representation on the spin-off matter only. |

---

## 4. Hard Legal, Ethical, and Procedural Guardrails

Any system connecting self-represented litigants with attorneys mid-case will fail—or violate bar rules—unless four constraints are treated as hard architectural invariants:

### 4.1 Fee-Splitting and Paid-Referral Prohibitions (ABA Model Rules 5.4 & 7.2(b))

- **Constraint:** In the vast majority of U.S. jurisdictions following ABA Model Rule 5.4 (barring fee-sharing with nonlawyers) and Model Rule 7.2(b) (barring giving anything of value for recommending a lawyer's services outside bar-approved/nonprofit Lawyer Referral Services), a software platform **cannot** take a percentage of a contingency recovery or charge a contingent per-case placement/referral fee. *(Arizona's Alternative Business Structure [ABS] framework under Ariz. Sup. Ct. R. 33.1 and narrow state regulatory sandboxes are jurisdiction-specific exceptions, not the national rule.)*
- **Architectural rule:**
  - `Corpus Open / matter-kit` is 100% free Apache-2.0 software with zero fees of any kind.
  - Any hosted commercial `Corpus` layer must monetize strictly through **outcome-independent software/infrastructure value** (e.g., hosted private data-room workspaces, flat-rate directory/platform subscriptions decoupled from case outcomes, or optional research/verification compute)—or operate strictly through a jurisdictionally licensed entity where applicable—never through unlawful fee splits on client recoveries.

### 4.2 Work-Product Protection, Non-Privileged Pro Se Files, and Defense Anti-Snooping

- **Constraint:** Materials created by a *pro se* litigant prior to retaining counsel are **not** protected by attorney-client privilege. While trial-preparation materials created by a party in anticipation of litigation qualify for work-product protection under **Fed. R. Civ. P. 26(b)(3)(A)**, **publicly posting** strategy memos, adverse facts, or witness assessments on an open marketplace waives work-product protection and allows defense firms or municipal insurers to harvest the plaintiff's roadmap.
- **Architectural rule (RFC 0007 & RFC 0008):**
  1. **Strict Quarantine on Export (RFC 0007):** `HYPOTHESIS` rows, unapproved `amendment_items`, internal `Research/` strategy notes, and agent run checkpoints are excluded by code from handoff bundles.
  2. **Zero-Prose Blind Teasers (RFC 0008 Stage 1):** Public or directory-visible listings contain **closed-enum metadata, counts, date buckets, and SHA-256 digests only**—never party names, docket numbers, exact incident dates, or free-text summaries.
  3. **Conflict Screen Before Merits Disclosure (RFC 0008 Stage 2 → Stage 3):** Prospective counsel must establish bar credentials, clear an adverse-party conflict check (`conflict_envelope.json`), and acknowledge prospective-client confidentiality duties (**ABA Model Rule 1.18**) *before* seeing assertions, adverse disclosures, or exhibits.

### 4.3 Federal Withdrawal Lock-In ("Hotel California" Appearance Risk)

- **Constraint:** In most federal district courts, once an attorney files a general Notice of Appearance, local civil rules prohibit withdrawal without **leave of court**. If an attorney enters a general appearance expecting an immediate settlement and the defendant refuses to settle, the court may deny withdrawal and force the attorney through an uncompensated trial. Also, under **_Evans v. Jeff D._, 475 U.S. 717 (1986)**, defendants in § 1983 actions may tender lump-sum Rule 68 or global settlement offers conditioned on waiving statutory attorney's fees, creating potential client-counsel tension unless addressed clearly at engagement.
- **Architectural rule:**
  - Handoff metadata ([RFC 0008](../rfcs/0008-blind-teaser-and-exchange-boundary.md)) explicitly distinguishes **representation scopes** (`pre_appearance_settlement`, `limited_mediation_adr`, `discrete_motion_or_deposition`, `subcase_only`, `full_appearance`) and surfaces compiled local court rules (`matterkit/rules.py`) regarding limited appearances and withdrawal so attorneys never step into an ambiguous commitment.

### 4.4 Analytics, Estimates, and Epistemic Honesty

*Amended 2026-11-06 (ty):* judge analytics and case-value estimates are in scope. This replaces the earlier rule that no schema, CLI, UI or RFC may emit a merits score, win probability or estimated settlement value.

- **Constraint:** An estimate that reads like a verdict misleads both litigants and counsel. Examples are a win probability, a settlement range, or a judge's grant rate. It misleads when its data, sample size and method are hidden, or when it is mixed in with verified facts.
- **Architectural rule:**
  - **Verified facts stay separate.** Readiness reports ([RFC 0007](../rfcs/0007-counsel-handoff-bundle-and-readiness.md)) stay a factual audit: custody, citation and completeness counts such as `documents_verified: 142/142`, `briefcheck_blocking_findings: 0`, `overdue_deadlines: 0`, `adverse_disclosures_count: 4`. Analytics and estimates are their own outputs, never fields in `readiness.json`.
  - **Every analytic and estimate shows where it came from.** That means its data source and retrieval date, the number of cases it rests on, the date range, the method, and its uncertainty, such as a range or an interval. When the sample is too small, it says so instead of producing a number.
  - **Estimates are labeled as estimates.** That holds in the CLI, the UI, MCP replies and anything exported. An agent repeating one must keep the label and the sample size.
  - **Estimates bring back unauthorized-practice risk.** Labeling handles honesty, not UPL. A figure computed for *this* user's claim ("your case is worth $X", "accept this offer") is closer to legal advice than a count or a published statistic. Until a lawyer has reviewed the line, estimates describe a population of public cases (a judge's ruling history, outcomes for a claim type in a district) and never recommend a settlement number or a course of action to the user.

### 4.5 Precedents and Fee Reasonableness for Any Exchange

These bind the commercial layer (Layer 2), not `matter-kit`, but they decide whether the exchange can exist at all:

- **Avvo Legal Services is the closest precedent.** Its per-matter "marketing fee," taken from a fixed-price legal service, was found to be improper fee-sharing or paid referral by several state bar ethics opinions in 2016–2017 (New Jersey's joint advisory opinion among them), and the service closed in 2018. Any Corpus Exchange pricing must be checked against those opinions, state by state, before launch. A flat subscription that does not vary with matters taken is the safest shape; per-case or per-match pricing is the one that failed.
- **A contingency on value the litigant already built may be unreasonable.** Model Rule 1.5(a) requires a reasonable fee. A standard 33–40% contingency taken by counsel who enters a case days before settlement, on value the self-represented litigant created, invites a reasonableness challenge. The exchange should expect and display reduced or staged contingencies, fee-shifting-only engagements, or hourly limited-scope terms, and should never present a standard full-case percentage as the default.
- **Status:** these points come from an agent review on 2026-11-07, not from a lawyer. A licensed ethics lawyer reviews the Layer 2 business model before any build.

---

## 5. Sequenced Roadmap (Gated Behind Active Lawsuit Dogfooding)

**Hard Gate:** Phases 1–3 below are **post-dogfood** milestones. Nothing in Phases 1–3 preempts Phase 0 fixes discovered while running live litigation in `matter-kit`. A handoff exchange is useless if the underlying local case store still has friction in daily litigation work.

### Phase 0 — Core Matter Store & Litigation Loop Hardening (Current Focus)

Complete and stabilize the local toolkit through real litigation dogfooding:

- **Version-aware document registration (`MK-01` / schema v1):** Allow living files (trackers, evolving drafts) to advance cleanly along version chains without triggering false `CHANGED` integrity failures on `verify`.
- **UI parity for `.docx` and `.pdf` `briefcheck`:** Wire structural Word/PDF extraction into `matter.py ui` (`webui.py`) so non-CLI users can audit court-formatted drafts directly.
- **First-class Adverse Disclosures:** Replace the static prose placeholder in `packet.py` (`_(fill deliberately...)_`) with structured, source-linked adverse-fact / open-question records in SQLite.
- **Issue-to-Element Mapping (`issue.map`):** Operationalize `issues.elements_json` (`store.py`) so assertions map cleanly onto statutory/common-law claim elements and reveal unsupported element gaps early.
- **Contradiction & Date-Discrepancy Surfacing:** Flag conflicting timestamps/accounts across assertions and OCR/ASR derivatives rather than allowing silent prose reconciliation.
- **Court Rules & Chambers Practices:** Stabilize `rules` packs and judge-specific procedural rules (`2026-10-30-court-rules-pack-design.md`, `2026-11-01-chambers-and-judge-practices-design.md`).

**Exit gate to Phase 1:** A complex multi-track litigation workspace runs end-to-end through motion practice and public-records correspondence with `verify` exiting `0` from `/tmp`, `briefcheck` exiting `0` on filed papers, and counsel packets rendering complete adverse facts from the database without manual Markdown patching.

---

### Phase 1 — Offline Counsel Handoff Bundle & Mechanical Readiness Audit ([RFC 0007](../rfcs/0007-counsel-handoff-bundle-and-readiness.md))

Turn any clean `matter-kit` folder (or `Subcases/<slug>/` spin-off) into a self-contained, tamper-evident archive a prospective lawyer can inspect in 3 minutes offline:

- `matter.py handoff render|manifest|export` producing `.matter/out/handoff/<manifest-sha256>/`.
- Automatic quarantine filter stripping `HYPOTHESIS` rows, unapproved `amendment_items`, and private `Research/` memos.
- `readiness.json` factual audit manifest (integrity, posture, briefcheck receipts, citation lookup stages, deadline hygiene, custodian hold receipts, element-source coverage, declared fee-shifting statutes, adverse disclosure count).
- Standalone offline HTML/Markdown viewer inside the exported archive so an attorney with zero Python tools installed can click from any assertion in the 3-minute packet directly to the exact exhibit page/timestamp and verify hashes.

---

### Phase 2 — Shared Private Counsel Data Room (`matter.py ui` Server Mode)

Activate the multi-user seams already built into [`matterkit/webui.py`](../matterkit/webui.py) ([Local Frontend Design §2](superpowers/specs/2026-10-31-local-frontend-design.md)):

- Production WSGI deployment profile behind a TLS reverse proxy (self-hosted by a clinic, firm, or litigant—never mandatory third-party hosting).
- Scoped per-case access tokens / sign-in enforcing the per-route `policy(identity, case_id, action)` seam with a dedicated **`prospective_counsel_readonly`** role (can view quarantined handoff view, packets, exhibits, and run `verify`/`briefcheck`; cannot see private `Research/` or mutate store) and **`retained_counsel`** role (co-editing facts, deadlines, and drafts with distinct `human:<counsel>` attribution).
- Immutable read-access audit log recording which credentialed identity accessed which case view and when.

---

### Phase 3 — Blind Teaser Protocol & Conflict-Gated Exchange Boundary ([RFC 0008](../rfcs/0008-blind-teaser-and-exchange-boundary.md))

Connect matured local matters to external attorney clearinghouses without sacrificing privacy, work-product protection, or open-source neutrality:

- Closed-enum Stage 1 `teaser.json` generator and deterministic identifier-leakage refusal checks in `matter-kit`.
- Stage 2 `conflict_envelope.json` derived from `parties` for adverse-party screening prior to merits access.
- Consent-gated egress (`consent.py` extension, default `nosend`) compatible with court pro bono panels, legal-aid intake portals, bar referral services, and the commercial **Corpus Exchange**.
- Commercial **Corpus** side (outside this repository): bar-credential verification, automated conflict-screen workflow, ABA Model Rule 1.18 prospective-client gate, saved attorney alerts by district / fee-shifting statute / procedural posture, and one-click unlock of the Stage 3 read-only data room.

---

## 6. Durable Non-Goals ("What Never to Build")

1. **No contingency fee-splitting or pay-per-settlement brokerage** inside `matter-kit` or violating Model Rules 5.4 / 7.2(b).
2. **No public unredacted case listings** where narratives, party names, docket numbers, or adverse facts are browseable by opposing counsel or data brokers.
3. ~~No AI "Case Value Estimator" or "Win-Rate Score."~~ *Withdrawn 2026-11-06 (ty):* judge analytics and case-value estimates are wanted. They follow §4.4: shown apart from verified facts, with their source, sample size, method and uncertainty.
4. **No silent hiding of bad facts.** Handoff bundles require explicit adverse-fact rows (or a conspicuous `ADVERSE_DISCLOSURE_EMPTY` warning banner) because attorney trust depends on seeing the worst facts in the first three minutes.
5. **No mandatory cloud dependency.** Every local organization, verification, and offline handoff workflow in `Corpus Open` remains 100% functional forever without an internet connection or a `Corpus` account.
