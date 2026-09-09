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
"""


def _ensure(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


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


def add_recipient(conn, *, name, org, jurisdiction, practice_area,
                  intake_channel, intake_url, match_reason, source_url) -> str:
    _ensure(conn)
    if not intake_url.startswith("http"):
        raise ValueError("intake_url must be an http(s) published channel — do not invent one")
    if not source_url.startswith("http"):
        raise ValueError("source_url required — why this recipient was selected")
    rid = store.new_id("or_")
    conn.execute(
        "INSERT INTO outreach_recipients "
        "(id,name,org,jurisdiction,practice_area,intake_channel,intake_url,"
        "match_reason,source_url,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (rid, name, org, jurisdiction, practice_area, intake_channel, intake_url,
         match_reason, source_url, "shortlisted", now_iso()))
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
             source_url="https://example.org/legal-aid/practice-areas"),
        dict(name="Jordan Kim, Esq.", org="Kim Unbundled Practice (SYNTHETIC)",
             jurisdiction="N.D. Example", practice_area="civil rights / unbundled",
             intake_channel="published email",
             intake_url="https://example.org/kim-law/contact",
             match_reason="Firm site lists limited-scope civil consults in this court",
             source_url="https://example.org/kim-law/limited-scope"),
        dict(name="Example Bar Lawyer Referral", org="Example State Bar LRS (SYNTHETIC)",
             jurisdiction="Example", practice_area="lawyer referral service",
             intake_channel="published referral form",
             intake_url="https://example.org/state-bar/lrs",
             match_reason="State bar LRS is the non-spam path to attorneys taking cases",
             source_url="https://example.org/state-bar/lrs-about"),
    ]
    return [add_recipient(conn, **s) for s in specs]


def draft_all(conn, posture: dict) -> list[str]:
    """Minimal individualized drafts. Individualization = name/org/intake only."""
    _ensure(conn)
    ids = []
    recips = conn.execute(
        "SELECT * FROM outreach_recipients WHERE status='shortlisted'").fetchall()
    for r in recips:
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
        body = (
            f"To: {r['name']} ({r['org']})\n"
            f"Intake: {r['intake_channel']} — {r['intake_url']}\n\n"
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
           WHERE r.status='shortlisted' ORDER BY r.name"""
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
    conn.commit()


def ledger(conn) -> str:
    _ensure(conn)
    lines = ["# Counsel-outreach ledger", ""]
    recips = conn.execute("SELECT * FROM outreach_recipients ORDER BY name").fetchall()
    if not recips:
        return "# Counsel-outreach ledger\n\n_(no recipients)_"
    lines += ["| Name | Org | Status | Intake | Why | Source |",
              "|---|---|---|---|---|---|"]
    for r in recips:
        lines.append(
            f"| {r['name']} | {r['org']} | {r['status']} | {r['intake_url']} | "
            f"{r['match_reason']} | {r['source_url']} |")
    items = conn.execute(
        "SELECT i.response_state, i.send_status, r.name FROM outreach_batch_items i "
        "JOIN outreach_recipients r ON r.id=i.recipient_id").fetchall()
    if items:
        lines += ["", "## Batch items", ""]
        for i in items:
            lines.append(f"- {i['name']}: send={i['send_status']} response={i['response_state']}")
    return "\n".join(lines) + "\n"
