"""Local citation extraction and staged authority diligence (RFC 0001 rev3).

Zero network: this module's import graph is stdlib-only and must stay that way
(see tests/test_rfc0001.py::test_import_graph_isolation).

Extraction rows go to `citation_extractions`, keyed by the sha256 of the exact
text passed to the parser — the chair requirement: character offsets are
meaningful only against a known text representation; a document hash alone
does not define offsets once a container format (PDF/DOCX) extracts text.

Stage transitions on `authorities` are validated here:

    unverified        logged; lookup not yet performed (v0.1 meaning preserved)
    resolved          lookup returned a concrete authority (checked_via set)
    research-pending  verification requested; legal ONLY after `resolved`
    source-checked    a defined source check recorded (checked_via + evidence)
    stale-flagged     a source check surfaced a currency question

`verified-official` stays readable for legacy rows but is no longer written:
`deadlines.verification_status` already uses it to mean something else.
Every one of these tokens means "a defined source was checked" — never "this
proposition is correct" or "this case remains good law."
"""
import hashlib
import re
import secrets
from datetime import datetime, timezone

AUTHORITY_STAGES = {
    "unverified": "lookup",
    "resolved": "lookup",
    "research-pending": "verification",
    "source-checked": "verification",
    "stale-flagged": "verification",
}
LEGACY_VERIFICATION = {"verified-official"}  # readable, never written by kit code


class StageOrderError(ValueError):
    """A status transition skipped required prior stage evidence (RFC 0001 §2.2c)."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Extraction (stdlib patterns; order matters — most specific first, and a
# span already claimed by an earlier pattern is never re-claimed)
# --------------------------------------------------------------------------
_PATTERNS = [
    # U.S. Code:           15 U.S.C. § 1681a   /   15 USC § 1681a
    re.compile(r"\b\d+\s+U\.?S\.?C\.?\s*§+\s*[\dA-Za-z]+(?:\s*\([^)]{1,40}\))?"),
    # Code of Federal Regulations (full depth):  17 C.F.R. § 240.10b-5
    re.compile(r"\b\d+\s+C\.F\.R\.\s*§+\s*[\dA-Za-z.\-]+"),
    # Delaware:            8 Del. C. § 141
    re.compile(r"\b\d+\s+Del\.\s+C\.\s*§+\s*\d+[a-z]?"),
    # State Code Ann.:     Miss. Code Ann. § 57-1-319 / Wyo. Stat. Ann. § 17-29-802
    re.compile(r"\b[A-Z][A-Za-z.]*\.?\s+(?:Code|Stat\.)\s+Ann\.\s*§+\s*[\dA-Za-z.\-]+"),
    # Keyword-tail code names (v2): dotted words chain across legitimate
    # spaces; (?<![\w.]) forbids starting mid-acronym (the 'N.Y.' -> 'Y. …'
    # FP) and sentence prefixes can never match (no trailing dot).
    #   Cal. Civ. Code § 1542 · Cal. Bus. & Prof. Code § 17200 ·
    #   Tex. Bus. & Orgs. Code § 101.55 · N.Y. Bus. Corp. Law § 405
    re.compile(r"(?<![\w.])(?:[A-Z][\w'&]*\.(?:\s*&\s*)?\s*){1,4}(?:Code|Law)\s*§+\s*[\dA-Za-z.\-]+"),
    # Gov't-tail:          Tex. Gov't Code § 3.005
    re.compile(r"(?<![\w.])[A-Z][\w'.]*(?:\s*&\s*)?\s*Gov'?t\.?\s+(?:Code|Stat\.)\s*§+\s*[\dA-Za-z.\-]+"),
    # Dotted-pure stat:    Fla. Stat. § 605.100
    re.compile(r"(?<![\w.])(?:[A-Z][\w'&]*\.(?:\s*&\s*)?\s*){1,2}Stat\.\s*§+\s*[\dA-Za-z.\-]+"),
    # Multi-part compiled statutes:  805 Ill. Comp. Stat. 5/1.10
    re.compile(r"\b\d+\s+[A-Z][\w'.]*\.(?:\s*[A-Z][\w'.]*\.)+\s*\d+(?:\.\d+)?/\d+(?:\.\d+)?\b"),
    # Reporter cites:      347 U.S. 483   /   946 So. 2d 851
    re.compile(r"\b\d+\s+[A-Z][A-Za-z.]*(?:\.\s*\d+[a-z]*)?\s+\d+\b"),
    # Bare section fragment — kept and recorded as extraction-failure evidence,
    # never silently dropped (chair requirement, 2026-09-08):
    re.compile(r"§+\s*[\dA-Za-z.\-]+"),
]


def _looks_complete(fragment: str) -> bool:
    """Heuristic: does the fragment carry its citation context?

    A §-fragment is complete only when a code name/number precedes the sign;
    a bare `§ 1234` is a real citation attempt the parser could not resolve
    to a code, so it is recorded as extraction-failed with offsets.
    """
    if "§" in fragment:
        before = fragment.split("§", 1)[0]
        return bool(re.search(r"[A-Za-z]", before) or re.search(r"\d", before))
    return True


def extract_citations(text: str, document_sha256: str | None = None,
                      parser: str = "kit-cite-0.1") -> list[dict]:
    """Parse `text`; return rows ready for citation_extractions. Local-only."""
    text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    found: list[dict] = []
    claimed: list[tuple[int, int]] = []
    for pat in _PATTERNS:
        for m in pat.finditer(text):
            s, e = m.span()
            if any(s < ce and cs < e for cs, ce in claimed):
                continue
            claimed.append((s, e))
            frag = text[s:e]
            # A trailing period directly after the section/reporter number is
            # sentence punctuation, not citation content ('...§ 57-1-319.').
            if len(frag) > 2 and frag.endswith(".") and frag[-2].isdigit():
                frag = frag[:-1]
                e = s + len(frag)
            ok = _looks_complete(frag)
            found.append({
                "id": "cx_" + secrets.token_hex(6),
                "document_sha256": document_sha256,
                "text_sha256": text_sha,
                "parser": parser,
                "char_start": s,
                "char_end": e,
                "raw_fragment": frag,
                "parsed_citation": frag if ok else None,
                "status": "extracted" if ok else "extraction-failed",
                "created_at": now_iso(),
            })
    found.sort(key=lambda r: r["char_start"])
    return found


def record_extractions(conn, rows: list[dict]) -> int:
    cols = ("id", "document_sha256", "text_sha256", "parser", "char_start",
            "char_end", "raw_fragment", "parsed_citation", "status", "created_at")
    for r in rows:
        conn.execute(
            "INSERT OR REPLACE INTO citation_extractions ({}) VALUES ({})".format(
                ",".join(cols), ",".join("?" * len(cols))),
            tuple(r[c] for c in cols))
    conn.commit()
    return len(rows)


# --------------------------------------------------------------------------
# Stage transitions
# --------------------------------------------------------------------------
def transition_authority(conn, authority_id: str, new_status: str,
                         checked_via: str | None = None,
                         check_evidence: str | None = None) -> None:
    """Stage-validated status change on `authorities` (RFC 0001 §2.2c).

    - research-pending requires prior lookup: non-null checked_via.
    - source-checked / stale-flagged require prior lookup AND recorded check
      evidence — not merely a nonempty checked_via string (chair requirement).
    """
    row = conn.execute("SELECT * FROM authorities WHERE id=?",
                       (authority_id,)).fetchone()
    if row is None:
        raise StageOrderError(f"unknown authority {authority_id!r}")
    if new_status not in AUTHORITY_STAGES:
        raise StageOrderError(
            f"status {new_status!r} not writable (writable: {sorted(AUTHORITY_STAGES)}; "
            f"legacy {sorted(LEGACY_VERIFICATION)} stays readable)")
    stage = AUTHORITY_STAGES[new_status]
    if not (row["checked_via"] or checked_via):
        raise StageOrderError(
            f"{new_status!r} requires a prior resolved source (checked_via); "
            "see RFC 0001 §2.2c")
    if new_status in ("source-checked", "stale-flagged"):
        evidence = (check_evidence or "").strip()
        if not evidence:
            raise StageOrderError(
                f"{new_status!r} requires recorded check evidence — what source "
                "was checked and what it said, not just which provider")
    conn.execute(
        "UPDATE authorities SET status=?, checked_via=COALESCE(?, checked_via), "
        "check_evidence=COALESCE(?, check_evidence) WHERE id=?",
        (new_status, checked_via, check_evidence, authority_id))
    conn.commit()


def resolve_from_extraction(conn, extraction_id: str, checked_via: str) -> str:
    """Move a *parsed* citation to the authorities stage-2 flow.

    Failed extractions are not addressable by lookup — by construction (the
    citation string is NULL), and enforced here explicitly.
    """
    row = conn.execute("SELECT * FROM citation_extractions WHERE id=?",
                       (extraction_id,)).fetchone()
    if row is None:
        raise StageOrderError(f"unknown extraction {extraction_id!r}")
    if row["status"] != "extracted" or not row["parsed_citation"]:
        raise StageOrderError(
            "extraction-failed rows are not lookup-addressable; re-extract with "
            "a better parser or log the authority by hand")
    aid = "auth_" + secrets.token_hex(6)
    conn.execute(
        "INSERT INTO authorities (id, citation, status, checked_via, note) "
        "VALUES (?,?,?,?,?)",
        (aid, row["parsed_citation"], "resolved", checked_via,
         f"via extraction {row['id']} of text {row['text_sha256'][:12]}"))
    conn.commit()
    return aid
