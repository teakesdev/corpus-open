"""Authority source checks and ledger import (RFC 0001 stages, recorded evidence).

`check` records one source check on an authority: what was opened (URL and/or
a hashed artifact), which pinpoints were confirmed, an optional quotation that
must actually appear in the artifact's text, and who did it. Evidence is JSON
in `authorities.check_evidence`:

    {"pins": ["45-47", "52"], "checks": [{checked_by, checked_at, source_url,
     artifact_sha256, artifact_path, registered_document, pins, quote,
     quote_found_in_artifact, note, stale}], ...}

`import_ledger` proposes rows from a Markdown authority ledger (tables with an
Authority/Case/Citation column and a Status column). Nothing is written unless
apply=True. Ledger statuses map conservatively:

    V-LEAD   -> source-checked   (pins confirmed only for a bare V-LEAD or "pins confirmed";
                                  any qualifier such as "slip" or "pins per W4" leaves them unconfirmed)
    V-WORKER -> resolved         (located; no source check by the lead)
    PARTIAL  -> research-pending
    UNVERIFIED -> unverified; a cell naming several takes the weakest; a cell naming
    none of these ("NO REPORT", "NOT FOUND") is not an authority row and is skipped

A recorded check means "this source was opened and these pins were seen". It
never means the proposition is correct or the authority is still good law.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess

from . import briefcheck, citations, ingest, store


class AuthorityError(ValueError):
    pass


DEFAULT_RULES = ("Fed. R. Civ. P.", "L.U.Civ.R.")  # briefcheck.rule_prefixes of an empty docket map


def find(conn, ref: str, rules: tuple = DEFAULT_RULES):
    """The authorities row for an id or a citation (matched by reporter/statute/rule key).
    `rules` is briefcheck.rule_prefixes(docket) so keys agree with `brief check`."""
    if re.fullmatch(r"auth_[0-9a-f]+", ref):
        return conn.execute("SELECT * FROM authorities WHERE id=?", (ref,)).fetchone()
    keys = briefcheck.authority_keys(ref, *rules)
    hits = [r for r in conn.execute("SELECT * FROM authorities ORDER BY id")
            if briefcheck.authority_keys(r["citation"], *rules) & keys]
    if len(hits) > 1:
        raise AuthorityError("citation matches several authorities: " +
                             ", ".join(f"{r['id']} ({r['citation']})" for r in hits) + "; pass the id")
    return hits[0] if hits else None


def _evidence(raw: str | None) -> dict:
    if not raw:
        return {"pins": [], "checks": []}
    try:
        ev = json.loads(raw)
    except ValueError:
        return {"pins": [], "checks": [], "legacy_evidence": raw}
    if not isinstance(ev, dict):
        return {"pins": [], "checks": [], "legacy_evidence": raw}
    ev.setdefault("pins", [])
    if "checks" not in ev:
        prior = {k: v for k, v in ev.items() if k != "pins"}
        ev["checks"] = [prior] if prior else []
    return ev


def _artifact_text(path: str, use_pdftotext: bool) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".md", ".txt", ".html", ".htm", ".markdown", ".text"):
        with open(path, "rb") as f:
            return f.read().decode("utf-8", "replace")
    exe = shutil.which("pdftotext") if use_pdftotext and ext == ".pdf" else None
    if exe:
        p = subprocess.run([exe, "-enc", "UTF-8", path, "-"], capture_output=True, timeout=120)
        if p.returncode == 0:
            return p.stdout.decode("utf-8", "replace")
    return None


def check(conn, matter_dir: str, ref: str, *, source_url: str | None = None, artifact: str | None = None,
          pins: list | None = None, quote: str | None = None, note: str | None = None,
          stale: str | None = None, by: str = "human", use_pdftotext: bool = True,
          rules: tuple = DEFAULT_RULES) -> tuple[str, str]:
    """Record one source check. Returns (authority_id, new_status). Writes nothing on refusal."""
    if not (source_url or artifact):
        raise AuthorityError("a source check needs --source-url and/or --artifact (what was opened)")
    record = {"checked_by": by, "checked_at": citations.now_iso(), "source_url": source_url,
              "pins": list(pins or []), "note": note}
    if artifact:
        apath = os.path.abspath(artifact)
        if not os.path.isfile(apath):
            raise AuthorityError(f"artifact not found: {artifact}")
        sha = ingest.sha256_file(apath)
        doc = conn.execute("SELECT id, rel_path FROM documents WHERE sha256=?", (sha,)).fetchone()
        try:
            shown = ingest.to_rel_path(apath, matter_dir)
        except ValueError:
            shown = apath
        record.update(artifact_sha256=sha, artifact_path=shown,
                      registered_document=dict(doc) if doc else None)
        if quote:
            text = _artifact_text(apath, use_pdftotext)
            if text is None:
                record["quote_found_in_artifact"] = None  # not machine-checkable
            elif briefcheck._find(briefcheck._fragments(quote), text) is None:
                raise AuthorityError("the quotation does not appear in the artifact's text; nothing recorded")
            else:
                record["quote_found_in_artifact"] = True
    if quote:
        record["quote"] = quote
    if stale:
        record["stale"] = stale
    row = find(conn, ref, rules)
    try:
        if row is None:
            aid = store.new_id("auth_")
            conn.execute("INSERT INTO authorities (id,citation,status,note) VALUES (?,?,?,?)",
                         (aid, ref, "unverified", f"added by authority check ({by})"))
            row = conn.execute("SELECT * FROM authorities WHERE id=?", (aid,)).fetchone()
        ev = _evidence(row["check_evidence"])
        ev["checks"].append(record)
        for p in record["pins"]:
            if p not in ev["pins"]:
                ev["pins"].append(p)
        status = "stale-flagged" if stale else "source-checked"
        # transition_authority validates the stage, then commits; that one commit also lands the
        # INSERT above. A refusal raises before its UPDATE, and the rollback below drops the INSERT.
        citations.transition_authority(conn, row["id"], status,
                                       checked_via=source_url or f"artifact sha256:{record['artifact_sha256']}",
                                       check_evidence=json.dumps(ev, ensure_ascii=False))
        conn.commit()  # explicit, so the contract does not rest on transition_authority alone
    except Exception:
        conn.rollback()
        raise
    return row["id"], status


# --------------------------------------------------------------------------
# Ledger import
# --------------------------------------------------------------------------
def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _col(headers, pattern):
    for i, h in enumerate(headers):
        if re.search(pattern, h, re.I):
            return i
    return None


def _clean(cell: str) -> str:
    s = re.sub(r"(?<!\w)[*_]+|[*_]+(?!\w)", "", cell).replace("`", "")
    return " ".join(s.split())


_LEDGER_STATUS = [("UNVERIFIED", "unverified"), ("PARTIAL", "research-pending"),
                  ("V-WORKER", "resolved"), ("V-LEAD", "source-checked")]  # weakest first


def _map_status(cell: str, has_source: bool) -> str | None:
    """Weakest status named in the cell; None when the cell names no ledger status (not an authority row)."""
    st = cell.upper()
    named = [mapped for token, mapped in _LEDGER_STATUS if token in st]
    if not named:
        return None
    return named[0] if has_source else "unverified"


def parse_ledger(text: str) -> list[dict]:
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines) - 1:
        if lines[i].lstrip().startswith("|") and re.match(r"\s*\|?\s*:?-{3,}", lines[i + 1]):
            headers = _cells(lines[i])
            ci = _col(headers, r"authority|case|citation")
            si = _col(headers, r"status")
            if ci is None or si is None:
                i += 1
                continue
            src_i = _col(headers, r"source")
            prop_i = _col(headers, r"proposition|used for|language|holding")
            note_i = _col(headers, r"note|treatment|adverse|limit")
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                c = _cells(lines[i])
                get = lambda k: _clean(c[k]) if k is not None and k < len(c) else ""  # noqa: E731
                cite, st_cell = get(ci), get(si)
                i += 1
                if not cite or cite in ("—", "-"):
                    continue
                source = get(src_i)
                has_source = bool(source) and source not in ("—", "-")
                status = _map_status(st_cell, has_source)
                if status is None:
                    out.append({"citation": cite, "ledger_status": st_cell, "status": None})
                    continue
                # pins count as confirmed only for a bare V-LEAD or an explicit "pins confirmed"
                confirmed = status == "source-checked" and (
                    "pins confirmed" in st_cell.lower() or st_cell.strip().upper() == "V-LEAD")
                pins = [p for _, _, p, *_ in briefcheck.scan_authorities(cite) if p][:1] if confirmed else []
                out.append({"citation": cite, "ledger_status": st_cell, "status": status,
                            "source": source if has_source else None, "proposition": get(prop_i),
                            "notes": get(note_i), "pins": pins,
                            "pins_unconfirmed": status == "source-checked" and not confirmed})
            continue
        i += 1
    return out


def import_ledger(conn, matter_dir: str, ledger_path: str, *, apply: bool = False, by: str = "human",
                  rules: tuple = DEFAULT_RULES):
    """Returns (planned, skipped) lists of dicts; writes only when apply=True."""
    with open(ledger_path, "rb") as f:
        raw = f.read()
    try:
        shown = ingest.to_rel_path(os.path.abspath(ledger_path), matter_dir)
    except ValueError:
        shown = os.path.abspath(ledger_path)
    origin = {"ledger": shown, "sha256": hashlib.sha256(raw).hexdigest()}
    planned, skipped = [], []
    seen: set = set()
    for row in parse_ledger(raw.decode("utf-8", "replace")):
        if row["status"] is None:
            skipped.append({**row, "reason": f"status '{row['ledger_status']}' is not an authority status"})
            continue
        try:
            existing = find(conn, row["citation"], rules)
        except AuthorityError as e:
            skipped.append({**row, "reason": str(e)})
            continue
        keys = briefcheck.authority_keys(row["citation"], *rules)
        if existing is not None or keys & seen:
            skipped.append({**row, "reason": f"already in store as {existing['id']} [{existing['status']}]"
                            if existing else "duplicate within the ledger"})
            continue
        seen |= keys
        planned.append(row)
    if apply and planned:
        now = citations.now_iso()
        try:
            for row in planned:
                ev = {"pins": row["pins"], "pins_unconfirmed": row["pins_unconfirmed"], "checks": [],
                      "imported_from": origin, "imported_by": by, "imported_at": now,
                      "ledger_status": row["ledger_status"], "source": row["source"],
                      "proposition": row["proposition"], "notes": row["notes"]}
                conn.execute("INSERT INTO authorities (id,citation,status,checked_via,note,check_evidence)"
                             " VALUES (?,?,?,?,?,?)",
                             (store.new_id("auth_"), row["citation"], row["status"], row["source"],
                              f"imported from {shown} ({row['ledger_status'] or 'no status'})",
                              json.dumps(ev, ensure_ascii=False)))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return planned, skipped
