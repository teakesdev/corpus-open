"""Source-backed recipient discovery/import. No outbound contact.

Stages (distinct; hashing is integrity, not independent verification):
  candidate              imported. Verbatim claims are evidence assertions.
  source-checked         NOT granted on import. Requires a future fetch-and-match
                         proving the exact intake channel belongs to the intended
                         org on the cited source. Generic “contact our firm”
                         matching is insufficient. There is no fetch path yet.
  shortlisted            user-approved as a research lead / for contact tracking.
                         Still unsendable until an approved message batch.
                         Directory/unknown shortlists do not become To: addresses.

destination_type ∈ {directory, portal, form, email}:
  directory / unknown — research leads; draft refuses
  portal / form       — draft is preparation only, never permission to submit
  email               — draftable (transport remains nosend)

Imported rows never skip to shortlisted or source-checked.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit, urlunsplit

from . import outreach, store
from .store import now_iso
from .outreach import DESTINATION_TYPES

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


def _validate_required(cands: list[dict]) -> None:
    """Pre-flight: every candidate carries the required fields, or the whole
    brief aborts BEFORE any write (named error, nonzero CLI exit — chair
    check, 2026-09-08). Required per the brief contract: name, intake_url,
    source_url, intake_channel, retrieved_at."""
    required = ("name", "intake_url", "source_url", "intake_channel",
                "retrieved_at", "destination_type")
    for i, cand in enumerate(cands):
        missing = [f for f in required if not str(cand.get(f) or "").strip()]
        if missing:
            raise ValueError(
                f"candidate {i} ({cand.get('name') or 'unnamed'}) missing required "
                f"field(s): {', '.join(missing)} — brief aborted, nothing imported")
        dest = str(cand.get("destination_type") or "").strip()
        if dest not in DESTINATION_TYPES:
            raise ValueError(
                f"candidate {i} ({cand.get('name') or 'unnamed'}) "
                f"destination_type={dest!r} not in {DESTINATION_TYPES} — "
                "brief aborted, nothing imported")


def import_brief(conn, path: str) -> dict:
    """Load a research brief JSON. No HTTP. Returns counts + ids."""
    outreach._ensure(conn)
    with open(path, encoding="utf-8") as f:
        brief = json.load(f)
    cands = brief.get("candidates") or []
    _validate_required(cands)
    result = {"imported": [], "reused": [], "rejected": [], "flagged": []}
    seen_in_brief = {}
    try:
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
    except Exception:
        # Unexpected error mid-brief: leave NO partial import behind. (Without
        # this, a long-lived caller's next commit() would silently persist a
        # half-imported brief.)
        conn.rollback()
        raise
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
    claimed = bool(cand.get("intake_verbatim_on_source"))
    extra = []
    if claimed:
        extra.append("verbatim-unverified")
    flags = _flags(retrieved, extra)
    # Nothing enters source-checked without a real fetch-and-match (not in
    # this increment). Keep the verbatim claim as evidence only.
    stage = "candidate"
    dest = str(cand.get("destination_type") or "").strip()
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
            old_dest = (existing["destination_type"] if "destination_type" in existing.keys()
                        else "") or "unknown"
            dest, flags = _merge_dest(old_dest, dest, flags)
            conn.execute(
                "UPDATE outreach_recipients SET flags_json=?, record_sha256=?, retrieved_at=?, "
                "destination_type=? WHERE id=?",
                (json.dumps(flags), sha, retrieved or existing["retrieved_at"], dest, rid))
            return ("reused", rid, bool(flags))
        old_dest = (existing["destination_type"] if "destination_type" in existing.keys()
                    else "") or "unknown"
        dest, flags = _merge_dest(old_dest, dest, flags)
        conn.execute(
            "UPDATE outreach_recipients SET name=?, org=?, jurisdiction=?, practice_area=?,"
            "intake_channel=?, match_reason=?, source_url=?, retrieved_at=?, flags_json=?,"
            "record_sha256=?, status=?, destination_type=? WHERE id=?",
            (cand.get("name"), cand.get("org"), cand.get("jurisdiction"),
             cand.get("practice_area"), cand.get("intake_channel"),
             cand.get("match_reason"), source, retrieved or None,
             json.dumps(flags), sha, stage, dest, rid))
        _add_evidence(conn, rid, cand)
        return ("reused", rid, bool(flags))
    status = stage
    if suppressed:
        status = suppressed["reason"]
    conn.execute(
        "INSERT INTO outreach_recipients "
        "(id,name,org,jurisdiction,practice_area,intake_channel,intake_url,"
        "match_reason,source_url,status,created_at,retrieved_at,flags_json,"
        "record_sha256,email,destination_type) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, cand.get("name"), cand.get("org"), cand.get("jurisdiction"),
         cand.get("practice_area"), cand.get("intake_channel"), key,
         cand.get("match_reason") or "", source, status, now_iso(),
         retrieved or None, json.dumps(flags), sha, cand.get("email") or None, dest))
    _add_evidence(conn, rid, cand)
    return ("imported", rid, bool(flags))


def _merge_dest(existing: str, incoming: str, flags: list[str]) -> tuple[str, list[str]]:
    existing = existing or "unknown"
    if existing in ("", "unknown"):
        return incoming, flags
    if incoming and existing != incoming:
        if "conflict:destination-mismatch" not in flags:
            flags = list(flags) + ["conflict:destination-mismatch"]
        return existing, flags
    return existing, flags


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
    """Mark as a research lead / approved for contact tracking. Does not send.

    Allowed from candidate. Does not earn source-checked. Directory shortlists
    remain non-draftable.
    """
    outreach._ensure(conn)
    row = conn.execute(
        "SELECT * FROM outreach_recipients WHERE id=?", (recipient_id,)).fetchone()
    if not row:
        raise ValueError(f"unknown recipient {recipient_id}")
    if row["status"] in SUPPRESSED or suppression_for(conn, row["intake_url"]):
        raise ValueError("suppressed — decline/opt-out is sticky; reimport cannot clear it")
    if row["status"] not in ("candidate", "source-checked"):
        raise ValueError(
            f"cannot shortlist from status={row['status']}")
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
        "| ID | Stage | Dest | Name | Org | Intake | Source | Retrieved | Flags |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        flags = r["flags_json"] if "flags_json" in r.keys() else "[]"
        retrieved = r["retrieved_at"] if "retrieved_at" in r.keys() else ""
        dest = r["destination_type"] if "destination_type" in r.keys() else "unknown"
        lines.append(
            f"| {r['id']} | {r['status']} | {dest or 'unknown'} | {r['name']} | {r['org']} | "
            f"{r['intake_url']} | {r['source_url']} | {retrieved or '—'} | {flags or '[]'} |")
    return "\n".join(lines) + "\n"


def load_bundled_brief() -> str:
    here = os.path.join(os.path.dirname(__file__), "..", "evals", "outreach_import", "brief.json")
    return os.path.abspath(here)
