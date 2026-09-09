"""Source-backed recipient discovery/import. No outbound contact.

Stages (distinct; hashing is integrity, not independent verification):
  candidate              imported, not yet source-checked
  source-checked         source URL + retrieval date + intake claimed verbatim
                         on that source — does NOT mean available/willing
  shortlisted            user-approved for contact (still unsendable until
                         an approved message batch)

Imported rows never skip to shortlisted. Guessed emails are refused.
Reimport cannot clear declines/opt-outs or rewrite prior batches.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit, urlunsplit

from . import outreach, store
from .store import now_iso

STALE_AFTER = timedelta(days=90)
CONTACT_STATES = {
    "shortlisted", "simulated", "declined", "consultation",
    "conflict_check", "follow_up", "opted_out",
}
SUPPRESSED = {"declined", "opted_out"}


def normalize_intake(url: str) -> str:
    u = (url or "").strip()
    parts = urlsplit(u)
    if parts.scheme.lower() == "mailto" or "@" in parts.path and not parts.scheme.startswith("http"):
        raise ValueError("guessed email refused — intake must be a published http(s) channel")
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        raise ValueError("intake_url must be an http(s) published channel — do not invent one")
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def stable_id(intake_url: str) -> str:
    return "or_" + hashlib.sha256(normalize_intake(intake_url).encode()).hexdigest()[:12]


def record_sha(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def remember_suppression(conn, intake_url: str, reason: str, recipient_id: str) -> None:
    outreach._ensure(conn)
    key = normalize_intake(intake_url)
    existing = conn.execute(
        "SELECT created_at FROM outreach_suppression WHERE key=?", (key,)).fetchone()
    created = existing["created_at"] if existing else now_iso()
    conn.execute(
        "INSERT OR REPLACE INTO outreach_suppression (key, reason, recipient_id, created_at) "
        "VALUES (?,?,?,?)",
        (key, reason, recipient_id, created))


def suppression_for(conn, intake_url: str):
    outreach._ensure(conn)
    try:
        key = normalize_intake(intake_url)
    except ValueError:
        return None
    return conn.execute(
        "SELECT * FROM outreach_suppression WHERE key=?", (key,)).fetchone()


def _flags(retrieved_at: str, extra: list[str]) -> list[str]:
    flags = list(extra)
    try:
        ts = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
        # Date-only briefs (the documented format) parse naive — treat as UTC
        # midnight; a naive timestamp never means local time.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - ts > STALE_AFTER:
            flags.append("stale-evidence")
    except ValueError:
        flags.append("unparsed-retrieved-at")
    return flags


def _refuse_guessed_email(cand: dict) -> None:
    email = (cand.get("email") or "").strip()
    quoted = cand.get("quoted_from_source") or ""
    intake = cand.get("intake_url") or ""
    channel = (cand.get("intake_channel") or "").lower()
    if "mailto:" in intake.lower():
        raise ValueError("guessed email refused: mailto intake is not a published channel")
    if email:
        if email.lower() not in quoted.lower():
            raise ValueError(
                "guessed email refused: address is not quoted from the source page")
        if not cand.get("intake_verbatim_on_source"):
            raise ValueError("guessed email refused: email without verbatim-on-source flag")
    if "email" in channel and not cand.get("intake_verbatim_on_source"):
        raise ValueError("guessed email refused: email channel without verbatim-on-source")


def import_brief(conn, path: str) -> dict:
    """Load a research brief JSON. No HTTP. Returns counts + ids."""
    outreach._ensure(conn)
    with open(path, encoding="utf-8") as f:
        brief = json.load(f)
    cands = brief.get("candidates") or []
    result = {"imported": [], "reused": [], "rejected": [], "flagged": []}
    seen_in_brief = {}
    for cand in cands:
        try:
            rid = _import_one(conn, cand, seen_in_brief)
        except ValueError as e:
            result["rejected"].append({"name": cand.get("name"), "error": str(e)})
            continue
        kind = rid[0]
        result[kind].append(rid[1])
        if rid[2]:
            result["flagged"].append(rid[1])
    _flag_org_conflicts(conn)
    conn.commit()
    return result


def _import_one(conn, cand: dict, seen_in_brief: dict):
    _refuse_guessed_email(cand)
    intake = cand["intake_url"]
    source = cand["source_url"]
    if not source.startswith("http"):
        raise ValueError("source_url required — why this recipient was selected")
    key = normalize_intake(intake)
    if key in seen_in_brief:
        # duplicate channel in the same brief: one record, extra evidence
        rid = seen_in_brief[key]
        _add_evidence(conn, rid, cand)
        _add_flag(conn, rid, "duplicate-channel")
        return ("reused", rid, True)
    seen_in_brief[key] = None  # filled after insert
    rid = stable_id(intake)
    seen_in_brief[key] = rid
    retrieved = cand.get("retrieved_at") or ""
    verbatim = 1 if cand.get("intake_verbatim_on_source") else 0
    flags = _flags(retrieved, [])
    if not verbatim or not retrieved:
        flags.append("incomplete-source")
        stage = "candidate"
    else:
        stage = "source-checked"
    payload = {
        "name": cand.get("name"),
        "org": cand.get("org"),
        "intake_url": key,
        "source_url": source,
        "retrieved_at": retrieved,
        "match_reason": cand.get("match_reason"),
    }
    sha = record_sha(payload)
    suppressed = suppression_for(conn, intake)
    existing = conn.execute(
        "SELECT * FROM outreach_recipients WHERE id=?", (rid,)).fetchone()
    if existing:
        # never reset suppression or contact history
        if existing["status"] in SUPPRESSED or suppressed:
            _add_evidence(conn, rid, cand)
            return ("reused", rid, bool(flags))
        if existing["status"] in CONTACT_STATES:
            _add_evidence(conn, rid, cand)
            # refresh flags/sha only; keep status
            conn.execute(
                "UPDATE outreach_recipients SET flags_json=?, record_sha256=?, retrieved_at=? "
                "WHERE id=?",
                (json.dumps(flags), sha, retrieved or existing["retrieved_at"], rid))
            return ("reused", rid, bool(flags))
        conn.execute(
            "UPDATE outreach_recipients SET name=?, org=?, jurisdiction=?, practice_area=?,"
            "intake_channel=?, match_reason=?, source_url=?, retrieved_at=?, flags_json=?,"
            "record_sha256=?, status=? WHERE id=?",
            (cand.get("name"), cand.get("org"), cand.get("jurisdiction"),
             cand.get("practice_area"), cand.get("intake_channel"),
             cand.get("match_reason"), source, retrieved or None,
             json.dumps(flags), sha, stage, rid))
        _add_evidence(conn, rid, cand)
        return ("reused", rid, bool(flags))
    status = stage
    if suppressed:
        status = suppressed["reason"]
    conn.execute(
        "INSERT INTO outreach_recipients "
        "(id,name,org,jurisdiction,practice_area,intake_channel,intake_url,"
        "match_reason,source_url,status,created_at,retrieved_at,flags_json,"
        "record_sha256,email) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, cand.get("name"), cand.get("org"), cand.get("jurisdiction"),
         cand.get("practice_area"), cand.get("intake_channel"), key,
         cand.get("match_reason") or "", source, status, now_iso(),
         retrieved or None, json.dumps(flags), sha, cand.get("email") or None))
    _add_evidence(conn, rid, cand)
    return ("imported", rid, bool(flags))


def _add_evidence(conn, rid: str, cand: dict) -> None:
    eid = store.new_id("ev_")
    intake = normalize_intake(cand["intake_url"])
    retrieved = cand.get("retrieved_at") or now_iso()
    verbatim = 1 if cand.get("intake_verbatim_on_source") else 0
    sha = record_sha({
        "recipient_id": rid, "source_url": cand["source_url"],
        "intake_url": intake, "retrieved_at": retrieved,
    })
    conn.execute(
        "INSERT INTO outreach_evidence "
        "(id,recipient_id,source_url,intake_url,retrieved_at,verbatim,record_sha256,created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (eid, rid, cand["source_url"], intake, retrieved, verbatim, sha, now_iso()))


def _add_flag(conn, rid: str, flag: str) -> None:
    row = conn.execute(
        "SELECT flags_json FROM outreach_recipients WHERE id=?", (rid,)).fetchone()
    flags = json.loads(row["flags_json"] or "[]") if row else []
    if flag not in flags:
        flags.append(flag)
    conn.execute("UPDATE outreach_recipients SET flags_json=? WHERE id=?",
                 (json.dumps(flags), rid))


def _flag_org_conflicts(conn) -> None:
    rows = conn.execute(
        "SELECT id, org, intake_url FROM outreach_recipients WHERE org IS NOT NULL").fetchall()
    by_org = {}
    for r in rows:
        org = (r["org"] or "").strip().lower()
        if not org:
            continue
        by_org.setdefault(org, set()).add(r["intake_url"])
    conflicted = {org for org, urls in by_org.items() if len(urls) > 1}
    if not conflicted:
        return
    for r in rows:
        org = (r["org"] or "").strip().lower()
        if org in conflicted:
            _add_flag(conn, r["id"], "conflict:intake-mismatch")


def shortlist(conn, recipient_id: str) -> None:
    """User-approved for contact. Requires source-checked. Does not send."""
    outreach._ensure(conn)
    row = conn.execute(
        "SELECT * FROM outreach_recipients WHERE id=?", (recipient_id,)).fetchone()
    if not row:
        raise ValueError(f"unknown recipient {recipient_id}")
    if row["status"] in SUPPRESSED or suppression_for(conn, row["intake_url"]):
        raise ValueError("suppressed — decline/opt-out is sticky; reimport cannot clear it")
    if row["status"] != "source-checked":
        raise ValueError(
            f"cannot shortlist from status={row['status']}; "
            "need source-checked (user-approved-for-contact is a separate step)")
    conn.execute("UPDATE outreach_recipients SET status='shortlisted' WHERE id=?",
                 (recipient_id,))
    conn.commit()


def candidate_report(conn) -> str:
    outreach._ensure(conn)
    rows = conn.execute(
        "SELECT * FROM outreach_recipients ORDER BY status, name").fetchall()
    if not rows:
        return "# Outreach candidates\n\n_(none)_\n"
    lines = [
        "# Outreach candidates",
        "",
        "_Published channel ≠ availability or willingness to take a case. "
        "record_sha256 is integrity of the import, not independent verification._",
        "",
        "| ID | Stage | Name | Org | Intake | Source | Retrieved | Flags |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        flags = r["flags_json"] if "flags_json" in r.keys() else "[]"
        retrieved = r["retrieved_at"] if "retrieved_at" in r.keys() else ""
        lines.append(
            f"| {r['id']} | {r['status']} | {r['name']} | {r['org']} | "
            f"{r['intake_url']} | {r['source_url']} | {retrieved or '—'} | {flags or '[]'} |")
    return "\n".join(lines) + "\n"


def load_bundled_brief() -> str:
    here = os.path.join(os.path.dirname(__file__), "..", "evals", "outreach_import", "brief.json")
    return os.path.abspath(here)
