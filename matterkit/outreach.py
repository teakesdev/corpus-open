"""Counsel-outreach MVP (Corpus Open).

Sourced shortlist → minimal individualized drafts → exact approval
manifest → no-send (or later real) transport → per-recipient ledger.

Rules (council 2026-09-09):
- No Corpus marketing in messages.
- No evidence dump / attachments by default.
- Unsolicited intake is not confidential.
- Recipient must have a published intake channel + source URL for the match.
- Auto-send of an *approved* batch from the user's account is allowed later;
  this module's default transport is `nosend` (writes an outbox, does not SMTP).
- Sending similar inquiries to several attorneys does not itself make them
  commercial email (FTC primary-purpose test). Still: no marketing, respect
  declines, assess content.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

from . import store
from .store import now_iso

FORBIDDEN_MARKETING = (
    "corpuslaw.us", "corpus open", "start your business", "form your llc",
)
FORBIDDEN_DUMP = (
    "see attached", "attached please find", "exhibit a", "affidavit excerpt",
    "i have strong evidence that", "the defendant did the following",
)

DRAFT_TEMPLATE = """\
Subject: Inquiry regarding possible representation — {jurisdiction} {case_type}

I am {role} in a {court} matter involving {issue_general}.
Next known deadline: {deadline} ({deadline_label}).
I am seeking {representation}. Fee arrangement I can consider: {fee}.

I am not attaching evidence or a factual narrative. This is an initial
availability/conflicts inquiry only. Please reply with whether you are
taking this kind of matter, any conflict check needed, and your intake
next step.

This message is from the prospective client, not a law-firm advertisement.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS outreach_recipients (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  org TEXT,
  jurisdiction TEXT,
  practice_area TEXT,
  intake_channel TEXT NOT NULL,
  intake_url TEXT NOT NULL,
  match_reason TEXT NOT NULL,
  source_url TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'shortlisted',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outreach_drafts (
  id TEXT PRIMARY KEY,
  recipient_id TEXT NOT NULL REFERENCES outreach_recipients(id),
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outreach_batches (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'pending_approval',
  transport TEXT NOT NULL DEFAULT 'nosend',
  manifest_sha256 TEXT NOT NULL,
  approved_by TEXT,
  approved_at TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outreach_batch_items (
  batch_id TEXT NOT NULL REFERENCES outreach_batches(id),
  recipient_id TEXT NOT NULL,
  draft_id TEXT NOT NULL,
  send_status TEXT NOT NULL DEFAULT 'pending',
  response_state TEXT DEFAULT 'none',
  note TEXT,
  PRIMARY KEY (batch_id, recipient_id)
);
CREATE TABLE IF NOT EXISTS outreach_evidence (
  id TEXT PRIMARY KEY,
  recipient_id TEXT NOT NULL,
  source_url TEXT NOT NULL,
  intake_url TEXT NOT NULL,
  retrieved_at TEXT NOT NULL,
  verbatim INTEGER NOT NULL DEFAULT 0,
  record_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outreach_suppression (
  key TEXT PRIMARY KEY,
  reason TEXT NOT NULL,
  recipient_id TEXT,
  created_at TEXT NOT NULL
);
"""


def _ensure(conn) -> None:
    conn.executescript(SCHEMA)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(outreach_recipients)")]
    if cols:
        if "retrieved_at" not in cols:
            conn.execute("ALTER TABLE outreach_recipients ADD COLUMN retrieved_at TEXT")
        if "flags_json" not in cols:
            conn.execute("ALTER TABLE outreach_recipients ADD COLUMN flags_json TEXT DEFAULT '[]'")
        if "record_sha256" not in cols:
            conn.execute("ALTER TABLE outreach_recipients ADD COLUMN record_sha256 TEXT")
        if "email" not in cols:
            conn.execute("ALTER TABLE outreach_recipients ADD COLUMN email TEXT")
        if "destination_type" not in cols:
            conn.execute(
                "ALTER TABLE outreach_recipients ADD COLUMN destination_type TEXT "
                "DEFAULT 'unknown'")
    _downgrade_unearned_source_checked(conn)
    conn.commit()


def _downgrade_unearned_source_checked(conn) -> None:
    """source-checked is fetch-gated. Self-asserted imports must not keep it.

    Idempotent: rows already candidate, or marked fetch-matched, are left alone.
    Evidence rows are not deleted.
    """
    cols = [r[1] for r in conn.execute("PRAGMA table_info(outreach_recipients)")]
    if "status" not in cols:
        return
    rows = conn.execute("SELECT * FROM outreach_recipients").fetchall()
    for r in rows:
        flags = json.loads(r["flags_json"] or "[]") if "flags_json" in r.keys() else []
        dest = ""
        if "destination_type" in r.keys():
            dest = (r["destination_type"] or "").strip()
        if not dest:
            dest = "unknown"
            flags = list(flags)
            if "destination-unclassified" not in flags:
                flags.append("destination-unclassified")
            conn.execute(
                "UPDATE outreach_recipients SET destination_type=?, flags_json=? WHERE id=?",
                (dest, json.dumps(flags), r["id"]))
        if r["status"] == "source-checked" and "fetch-matched" not in flags:
            flags = list(flags)
            if "source-checked-revoked:unverified" not in flags:
                flags.append("source-checked-revoked:unverified")
            if "verbatim-unverified" not in flags:
                flags.append("verbatim-unverified")
            conn.execute(
                "UPDATE outreach_recipients SET status='candidate', flags_json=? WHERE id=?",
                (json.dumps(flags), r["id"]))


def refuse_if_unsafe(subject: str, body: str) -> None:
    blob = (subject + "\n" + body).lower()
    for w in FORBIDDEN_MARKETING:
        if w in blob:
            raise ValueError(f"draft refused: Corpus marketing '{w}' is not allowed")
    for w in FORBIDDEN_DUMP:
        if w in blob:
            raise ValueError(f"draft refused: looks like an evidence dump ('{w}')")
    if re.search(r"\bexhibit\s+\d+\b", blob):
        raise ValueError("draft refused: exhibit references are not allowed in initial inquiry")


NON_DRAFTABLE = {"directory", "unknown"}
PREP_ONLY = {"portal", "form"}
DESTINATION_TYPES = ("directory", "portal", "form", "email")
PREP_BANNER = (
    "PREPARATION ONLY — this is not permission to submit a form or portal. "
    "A published channel is not availability or willingness to take a case.\n\n"
)


def destination_type_of(row) -> str:
    if "destination_type" not in row.keys() or not row["destination_type"]:
        return "unknown"
    return row["destination_type"]


def add_recipient(conn, *, name, org, jurisdiction, practice_area,
                  intake_channel, intake_url, match_reason, source_url,
                  destination_type: str = "unknown") -> str:
    _ensure(conn)
    if not intake_url.startswith("http"):
        raise ValueError("intake_url must be an http(s) published channel — do not invent one")
    if not source_url.startswith("http"):
        raise ValueError("source_url required — why this recipient was selected")
    if destination_type not in DESTINATION_TYPES and destination_type != "unknown":
        raise ValueError(
            f"destination_type must be one of {DESTINATION_TYPES} or unknown")
    rid = store.new_id("or_")
    conn.execute(
        "INSERT INTO outreach_recipients "
        "(id,name,org,jurisdiction,practice_area,intake_channel,intake_url,"
        "match_reason,source_url,status,created_at,destination_type) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, name, org, jurisdiction, practice_area, intake_channel, intake_url,
         match_reason, source_url, "shortlisted", now_iso(), destination_type))
    conn.commit()
    return rid


def seed_synthetic(conn) -> list[str]:
    """Demo recipients — clearly labeled synthetic; published-looking URLs on example.org."""
    specs = [
        dict(name="Pat Rivera, Esq.", org="Example County Legal Aid (SYNTHETIC)",
             jurisdiction="N.D. Example", practice_area="civil / limited-scope",
             intake_channel="published web form",
             intake_url="https://example.org/legal-aid/intake",
             match_reason="Legal-aid clinic listing civil intake for this district",
             source_url="https://example.org/legal-aid/practice-areas",
             destination_type="form"),
        dict(name="Jordan Kim, Esq.", org="Kim Unbundled Practice (SYNTHETIC)",
             jurisdiction="N.D. Example", practice_area="civil rights / unbundled",
             intake_channel="published email",
             intake_url="https://example.org/kim-law/contact",
             match_reason="Firm site lists limited-scope civil consults in this court",
             source_url="https://example.org/kim-law/limited-scope",
             destination_type="form"),
        dict(name="Example Bar Lawyer Referral", org="Example State Bar LRS (SYNTHETIC)",
             jurisdiction="Example", practice_area="lawyer referral service",
             intake_channel="published referral form",
             intake_url="https://example.org/state-bar/lrs",
             match_reason="State bar LRS is the non-spam path to attorneys taking cases",
             source_url="https://example.org/state-bar/lrs-about",
             destination_type="directory"),
    ]
    return [add_recipient(conn, **s) for s in specs]


def draft_all(conn, posture: dict) -> list[str]:
    """Minimal individualized drafts. Individualization = name/org/intake only.

    Replaces any prior *pending* draft for each shortlisted recipient so a
    posture typo-fix cannot put two emails to the same intake on the manifest.
    """
    _ensure(conn)
    recips = conn.execute(
        "SELECT * FROM outreach_recipients WHERE status='shortlisted'").fetchall()
    if not recips:
        raise ValueError("no shortlisted recipients — seed or add before drafting")
    draftable = [r for r in recips if destination_type_of(r) not in NON_DRAFTABLE]
    skipped = [r for r in recips if destination_type_of(r) in NON_DRAFTABLE]
    if not draftable:
        kinds = sorted({destination_type_of(r) for r in skipped}) or ["unknown"]
        raise ValueError(
            "no draftable recipients — "
            f"{', '.join(kinds)} destinations are research leads, not deliverable "
            "addresses (directory/unknown cannot become a To: or form submit)")
    conn.execute(
        "DELETE FROM outreach_drafts WHERE recipient_id IN "
        "(SELECT id FROM outreach_recipients WHERE status='shortlisted')")
    ids = []
    for r in draftable:
        filled = DRAFT_TEMPLATE.format(
            representation=posture.get(
                "representation", "a consultation / limited-scope representation"),
            jurisdiction=posture.get("jurisdiction", r["jurisdiction"] or "this court"),
            case_type=posture.get("case_type", "a civil matter"),
            role=posture.get("role", "a party"),
            court=posture.get("court", "a U.S. district court"),
            issue_general=posture.get("issue_general", "a civil dispute"),
            deadline=posture.get("deadline", "unknown"),
            deadline_label=posture.get("deadline_label", "next known date"),
            fee=posture.get("fee", "to be discussed"),
        )
        first, _, rest = filled.partition("\n")
        subject = first.replace("Subject:", "", 1).strip()
        dest = destination_type_of(r)
        banner = PREP_BANNER if dest in PREP_ONLY else ""
        body = (
            f"To: {r['name']} ({r['org']})\n"
            f"Intake: {r['intake_channel']} — {r['intake_url']}\n"
            f"Destination-Type: {dest}\n\n"
            + banner
            + rest.strip() + "\n"
        )
        refuse_if_unsafe(subject, body)
        did = store.new_id("od_")
        conn.execute(
            "INSERT INTO outreach_drafts (id,recipient_id,subject,body,created_at) "
            "VALUES (?,?,?,?,?)",
            (did, r["id"], subject, body, now_iso()))
        ids.append(did)
    conn.commit()
    return ids


def build_manifest(conn) -> dict:
    _ensure(conn)
    items = []
    q = """SELECT d.id AS draft_id, d.subject, d.body, r.id AS recipient_id,
                  r.name, r.org, r.intake_url, r.intake_channel, r.source_url, r.match_reason
           FROM outreach_drafts d JOIN outreach_recipients r ON r.id=d.recipient_id
           WHERE r.status='shortlisted'
             AND d.created_at = (
               SELECT MAX(d2.created_at) FROM outreach_drafts d2
               WHERE d2.recipient_id = d.recipient_id)
           ORDER BY r.name"""
    for row in conn.execute(q):
        items.append({
            "recipient_id": row["recipient_id"],
            "name": row["name"],
            "org": row["org"],
            "intake_channel": row["intake_channel"],
            "intake_url": row["intake_url"],
            "source_url": row["source_url"],
            "match_reason": row["match_reason"],
            "draft_id": row["draft_id"],
            "subject": row["subject"],
            "body": row["body"],
        })
    raw = json.dumps(items, sort_keys=True, ensure_ascii=False).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    return {"items": items, "sha256": sha, "n": len(items)}


def write_manifest(matter_dir: str, manifest: dict) -> str:
    path = os.path.join(matter_dir, ".matter", "outreach", "approval.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def approve_and_simulate(conn, matter_dir: str, *, issuer: str, expected_sha: str,
                         transport: str = "nosend") -> str:
    """Record an approved batch. `nosend` writes outbox files; never SMTP."""
    man = build_manifest(conn)
    if man["n"] == 0:
        raise ValueError(
            "empty manifest — no shortlisted recipients with drafts; "
            "outreach did not happen. Seed/add recipients, then draft, then approve.")
    if man["sha256"] != expected_sha:
        raise ValueError(
            f"manifest sha mismatch: got {man['sha256'][:12]}… wanted {expected_sha[:12]}… "
            "re-print the exact recipients/messages before approving")
    if transport != "nosend":
        raise ValueError("only transport=nosend is implemented — real send needs a later go")
    bid = store.new_id("ob_")
    conn.execute(
        "INSERT INTO outreach_batches "
        "(id,status,transport,manifest_sha256,approved_by,approved_at,created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (bid, "simulated", "nosend", man["sha256"], issuer, now_iso(), now_iso()))
    outbox = os.path.join(matter_dir, ".matter", "outreach", "outbox")
    os.makedirs(outbox, exist_ok=True)
    for it in man["items"]:
        conn.execute(
            "INSERT INTO outreach_batch_items "
            "(batch_id,recipient_id,draft_id,send_status,response_state) "
            "VALUES (?,?,?,?,?)",
            (bid, it["recipient_id"], it["draft_id"], "simulated", "none"))
        conn.execute("UPDATE outreach_recipients SET status='simulated' WHERE id=?",
                     (it["recipient_id"],))
        eml = os.path.join(outbox, f"{it['recipient_id']}.txt")
        with open(eml, "w", encoding="utf-8") as f:
            f.write(f"To: {it['name']} <{it['intake_url']}>\n")
            f.write(f"Subject: {it['subject']}\n")
            f.write("X-Transport: nosend (not delivered)\n\n")
            f.write(it["body"])
    conn.commit()
    return bid


def set_response(conn, recipient_id: str, state: str, note: str = "") -> None:
    allowed = {"none", "declined", "consultation", "conflict_check", "follow_up", "opted_out"}
    if state not in allowed:
        raise ValueError(f"state must be one of {sorted(allowed)}")
    conn.execute(
        "UPDATE outreach_batch_items SET response_state=?, note=? "
        "WHERE recipient_id=? AND batch_id=(SELECT id FROM outreach_batches "
        "ORDER BY created_at DESC LIMIT 1)",
        (state, note, recipient_id))
    conn.execute("UPDATE outreach_recipients SET status=? WHERE id=?",
                 (state if state != "none" else "simulated", recipient_id))
    if state in ("declined", "opted_out"):
        row = conn.execute(
            "SELECT intake_url FROM outreach_recipients WHERE id=?",
            (recipient_id,)).fetchone()
        if row:
            from .discovery import remember_suppression
            remember_suppression(conn, row["intake_url"], state, recipient_id)
    conn.commit()


def ledger(conn) -> str:
    _ensure(conn)
    lines = ["# Counsel-outreach ledger", ""]
    recips = conn.execute("SELECT * FROM outreach_recipients ORDER BY name").fetchall()
    if not recips:
        return "# Counsel-outreach ledger\n\n_(no recipients)_"
    lines += ["| ID | Name | Org | Status | Intake | Why | Source |",
              "|---|---|---|---|---|---|---|"]
    for r in recips:
        lines.append(
            f"| {r['id']} | {r['name']} | {r['org']} | {r['status']} | {r['intake_url']} | "
            f"{r['match_reason']} | {r['source_url']} |")
    items = conn.execute(
        "SELECT i.response_state, i.send_status, r.name FROM outreach_batch_items i "
        "JOIN outreach_recipients r ON r.id=i.recipient_id").fetchall()
    if items:
        lines += ["", "## Batch items", ""]
        for i in items:
            lines.append(f"- {i['name']}: send={i['send_status']} response={i['response_state']}")
    return "\n".join(lines) + "\n"
