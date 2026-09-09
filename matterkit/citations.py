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

# Capability-level label (chair 2026-09-09): the extractor is experimental until
# a fresh holdout_v3 acceptance pass. Do NOT mark every row extraction-failed.
PARSER_CAPABILITY = "experimental"
PARSER_ID = "kit-cite-0.4"


class StageOrderError(ValueError):
    """A status transition skipped required prior stage evidence (RFC 0001 §2.2c)."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Extraction (stdlib patterns; order matters — most specific first, and a
# span already claimed by an earlier pattern is never re-claimed)
# --------------------------------------------------------------------------
_PATTERNS = [
    # Public laws spelled out:           Public Law 107-204
    re.compile(r"\bPublic\s+Law\s+\d+\s*[–\-—]\s*\d+\b"),
    # Public laws abbreviated:           Pub. L. 96–170 / Pub. L. 104-317
    re.compile(r"\bPub\.\s*L\.\s*\d+\s*[–\-—]\s*\d+\b"),
    # House bills (identifier, not statute): H.R. 3763
    re.compile(r"\bH\.R\.\s*\d+\b"),
    # Revised Statutes:                  R.S. § 1979 (Unicode space tolerated by \s)
    re.compile(r"\bR\.S\.\s*§+\s*[\dA-Za-z.\-]+"),
    # Florida short:                     F.S. 605.0101 / F.S. § 605.0101
    re.compile(r"\bF\.S\.\s*(?:§+\s*)?[\dA-Za-z.\-]+"),
    # Code of Federal Regulations — dotted or undotted, § optional:
    #   17 C.F.R. § 240.10b-5 / 12 CFR 1026.1
    re.compile(r"\b\d+\s+C\.?F\.?R\.?\s*(?:§+\s*)?[\dA-Za-z.\-]+"),
    # U.S. Code — abbreviated or spelled out, with section sign:
    #   15 U.S.C. § 1681a / 42 U.S. Code § 1983
    re.compile(r"\b\d+\s+(?:U\.?S\.?C\.?|U\.?S\.?\s+Code)\s*§+\s*[\dA-Za-z.\-]+(?:\s*\([^)]{1,24}\))*"),
    # U.S. Code without section sign:    15 U.S.C. 78j
    re.compile(r"\b\d+\s+U\.?S\.?C\.?\s+(?!§)[\dA-Za-z][\dA-Za-z.\-]*\b"),
    # Code of Federal Regulations (full depth):  17 C.F.R. § 240.10b-5
    re.compile(r"\b\d+\s+C\.F\.R\.\s*§+\s*[\dA-Za-z.\-]+"),
    # Delaware:            8 Del. C. § 141
    re.compile(r"\b\d+\s+Del\.\s+C\.\s*§+\s*\d+[a-z]?(?:\s*\([^)]{1,24}\))?"),
    # State Code Ann.:     Miss. Code Ann. § 57-1-319 / Wyo. Stat. Ann. § 17-29-802
    re.compile(r"\b[A-Z][A-Za-z.]*\.?\s+(?:Code|Stat\.)\s+Ann\.\s*§+\s*[\dA-Za-z.\-]+(?:\s*\([^)]{1,24}\))?"),
    # Keyword-tail code names (v2): dotted words chain across legitimate
    # spaces; (?<![\w.]) forbids starting mid-acronym (the 'N.Y.' -> 'Y. …'
    # FP) and sentence prefixes can never match (no trailing dot).
    #   Cal. Civ. Code § 1542 · Cal. Bus. & Prof. Code § 17200 ·
    #   Tex. Bus. & Orgs. Code § 101.55 · N.Y. Bus. Corp. Law § 405
    re.compile(r"(?<![\w.])(?:[A-Z][\w'&]*\.(?:\s*&\s*)?\s*){1,4}(?:Code|Law)\s*§+\s*[\dA-Za-z.\-]+(?:\s*\([^)]{1,24}\))?"),
    # Gov't-tail:          Tex. Gov't Code § 3.005
    re.compile(r"(?<![\w.])[A-Z][\w'.]*(?:\s*&\s*)?\s*Gov'?t\.?\s+(?:Code|Stat\.)\s*§+\s*[\dA-Za-z.\-]+(?:\s*\([^)]{1,24}\))?"),
    # Dotted-pure stat:    Fla. Stat. § 605.100
    re.compile(r"(?<![\w.])(?:[A-Z][\w'&]*\.(?:\s*&\s*)?\s*){1,2}Stat\.\s*§+\s*[\dA-Za-z.\-]+(?:\s*\([^)]{1,24}\))?"),
    # Multi-part compiled statutes:  805 Ill. Comp. Stat. 5/1.10
    re.compile(r"\b\d+\s+[A-Z][\w'.]*\.(?:\s*[A-Z][\w'.]*\.)+\s*\d+(?:\.\d+)?/\d+(?:\.\d+)?\b"),
    # Reporter cites:      347 U.S. 483   /   946 So. 2d 851
    re.compile(r"\b\d+\s+[A-Z][A-Za-z.]*(?:\.\s*\d+[a-z]*)?\s+\d+\b"),
    # Statute-range cite:                605.0101 - 605.1108
    re.compile(r"\b\d+\.\d{3,5}\s*[-–—]\s*\d+\.\d{3,5}\b"),
    # Florida session-law cite:          s. 2, ch. 2013-180 / s. 1, ch. 89-154
    re.compile(r"\bs\.\s*\d+\s*,\s*ch\.\s*\d{2,4}\s*[–\-—]\s*\d+\b"),
    # Coordinated chapter lists:         chapter 109A, 109B, 110, or 117
    re.compile(
        r"\b[Cc]hapters?\s+\d+[A-Za-z]?"
        r"(?:\s*,\s*\d+[A-Za-z]?)+(?:\s*,?\s+(?:and|or)\s+\d+[A-Za-z]?)?"
    ),
    # Chapter abbreviation:              ch. 645
    re.compile(r"\bch\.\s+\d+[A-Za-z]?\b"),
    # Cross-reference word family: section 43 of Title 8 · Section 80 ·
    # sections 106 and 106A · section 401(a) · Sec. 107
    re.compile(
        r"\b(?:[Ss]ections?|Sec\.)\s+\d+[A-Za-z]?"
        r"(?:\s*\([^)]{1,24}\))*(?:\s+(?:and|or|through|to)\s+\d+[A-Za-z]?"
        r"(?:\s*\([^)]{1,24}\))*)*(?:\s+of\s+Title\s+\d+)?"
    ),
    # Short-form section cite: § 1983 / § 309(c) / § 330016(1)(L). Digit-start
    # is mandatory. Multiple parentheticals allowed. HTML entities / Unicode
    # spaces in the gap are tolerated so offsets never shift.
    re.compile(
        r"(?:§|&sect;|&#x00A7;|&#167;)\s*"
        r"(?:&nbsp;|&#x2003;|&#8194;|&#8195;|&#8239;|\s)*"
        r"\d[\dA-Za-z.\-]*(?:\s*\([^)]{1,24}\))*"
    ),
    # Bare § followed by a NON-number: recorded as extraction-failed evidence
    # (honest-failure path preserved for genuinely unparsable fragments).
    re.compile(r"(?:§|&sect;|&#x00A7;|&#167;)\s*(?:&nbsp;|&#x2003;|\s)*[A-Za-z][\dA-Za-z.\-]*"),
]


def _looks_complete(fragment: str) -> bool:
    """Heuristic: does the fragment carry its citation context?

    v3: a short-form `§ 1983` IS a complete citation (holdout labels
    established this); bare-§ failure rows now only arise when an entity
    decode failed or the fragment is otherwise non-citation noise. Entity
    forms (&sect; etc.) count as complete when they carry a section number.
    """
    core = fragment.replace("&sect;", "§").replace("&#x00A7;", "§").replace("&#167;", "§")
    if "§" in core:
        after = core.split("§", 1)[1].lstrip()
        # complete iff a DIGIT follows the sign ('§ for' is not a citation;
        # '§ 1983' and '§ 309(c)' are — holdout labels, 2026-09-08)
        return bool(after[:1].isdigit())
    return True


def extract_citations(text: str, document_sha256: str | None = None,
                      parser: str = PARSER_ID) -> list[dict]:
    """Parse `text`; return rows ready for citation_extractions. Local-only."""
    text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    found: list[dict] = []
    matches: list[tuple[int, int]] = []

    def _claimed(s: int, e: int) -> bool:
        """Exact-duplicate or boundary-crossing overlap blocks a match.
        Full containment is ALLOWED: real corpora legitimately yield nested
        predictions (full cite + its tail short-form), and the holdout gold
        labels score them as separate items."""
        for cs, ce in matches:
            if (s, e) == (cs, ce):
                return True
            if s < ce and cs < e and not (cs <= s and e <= ce) and not (s <= cs and ce <= e):
                return True  # partial/crossing overlap
        return False

    for pat in _PATTERNS:
        for m in pat.finditer(text):
            s, e = m.span()
            if _claimed(s, e):
                continue
            matches.append((s, e))
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
            # Coordinated cross-reference (holdout labels score each member as
            # its own item): 'section 502 or 503' also yields 'section 502'
            # and the trailing member as separate nested rows.
            xref = re.match(
                r"((?:sections?|Sec\.)\s+\d+[A-Za-z]?(?:\s*\([^)]{1,24}\))?)"
                r"(\s+(?:and|or|through|to)\s+)(\d+[A-Za-z]?(?:\s*\([^)]{1,24}\))?)",
                frag)
            if xref:
                head_s = s + xref.start(1)
                head_e = head_s + len(xref.group(1))
                tail_s = s + xref.start(3)
                tail_e = tail_s + len(xref.group(3))
                for sub_s, sub_e in ((head_s, head_e), (tail_s, tail_e)):
                    if not _claimed(sub_s, sub_e):
                        matches.append((sub_s, sub_e))
                        found.append({
                            "id": "cx_" + secrets.token_hex(6),
                            "document_sha256": document_sha256,
                            "text_sha256": text_sha,
                            "parser": parser,
                            "char_start": sub_s,
                            "char_end": sub_e,
                            "raw_fragment": text[sub_s:sub_e],
                            "parsed_citation": text[sub_s:sub_e],
                            "status": "extracted",
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
