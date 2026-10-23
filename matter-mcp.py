#!/usr/bin/env python3
"""matter-mcp — zero-dependency stdio MCP server exposing a matter store to any MCP client.

Set MATTER_DIR to the case workspace root before launching. Tools are deliberately
read-mostly: agents can register facts and render packets, but nothing here sends,
files, or contacts anyone.
"""
import contextlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from matterkit import ingest as ing, store, packet as pk  # noqa: E402

MATTER_DIR = os.environ.get("MATTER_DIR", os.getcwd())

TOOLS = [
    {"name": "matter.status", "description": "Counts of documents by kind, assertions by label, open deadlines, unverified authorities.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "matter.fact_add", "description": "Register an assertion with an epistemic label and optional source refs ('rel_path@locator'). Refuses sources not present in the store.",
     "inputSchema": {"type": "object", "required": ["text", "status"],
                     "properties": {"text": {"type": "string"}, "status": {"enum": ["DOCUMENTED FACT", "ALLEGATION", "INFERENCE", "HYPOTHESIS", "UNKNOWN"]},
                                    "sources": {"type": "array", "items": {"type": "string"}}, "created_by": {"type": "string"},
                                    "directed_by": {"type": "string", "description": "Person who directed the agent to do this work (not an approval). Requires created_by 'agent:<model>'."}}}},
    {"name": "matter.fact_list", "description": "List assertions, optionally filtered by label, with their provenance.",
     "inputSchema": {"type": "object", "properties": {"status": {"type": "string"}}}},
    {"name": "matter.deadline_list", "description": "List deadlines (open only unless all=true) with confidence and verification status. Open deadlines dated before today carry overdue=true and marker '⚠ OVERDUE'.",
     "inputSchema": {"type": "object", "properties": {"all": {"type": "boolean"}}}},
    {"name": "matter.deadline_update", "description": "Change a deadline's state (open|met|superseded|lapsed|vacated), verification, confidence or due date. A reason is required; each change appends an events row (old->new, reason, sources, actor). Sources are 'rel_path@locator' refs that must be registered documents. Refuses unknown ids, invalid values, no-op updates and unregistered sources, and changes nothing when it refuses.",
     "inputSchema": {"type": "object", "required": ["deadline_id", "reason"],
                     "properties": {"deadline_id": {"type": "string"}, "reason": {"type": "string"},
                                    "state": {"enum": ["open", "met", "superseded", "lapsed", "vacated"]},
                                    "verification": {"enum": ["verified-official", "secondary-source", "unverified"]},
                                    "confidence": {"enum": ["hard", "working-estimate"]},
                                    "due": {"type": "string", "description": "YYYY-MM-DD"},
                                    "sources": {"type": "array", "items": {"type": "string"}},
                                    "created_by": {"type": "string"},
                                    "directed_by": {"type": "string", "description": "Person who directed the agent to do this work (not an approval). Requires created_by 'agent:<model>'."}}}},
    {"name": "matter.packet_render", "description": "Render the counsel packet markdown at a given layer (30s | 3min | 15min). Read-only; returns text.",
     "inputSchema": {"type": "object", "properties": {"layer": {"type": "string"}}}},
    {"name": "matter.cite_extract", "description": "Local citation extraction (RFC 0001 §2.2/§2.3): stdlib parser, zero network, no adapter imports reachable; needs no consent grant. Records rows with character offsets keyed to the parsed text's sha256; unparsed section fragments are first-class extraction-failed rows, never dropped.",
     "inputSchema": {"type": "object", "required": ["text"],
                     "properties": {"text": {"type": "string"},
                                    "document_sha256": {"type": "string"}}}},
    {"name": "matter.ingest_path", "description": "Hash and register one path that must resolve inside MATTER_DIR. Refuses path escape. Returns added/skipped/excluded. No network.",
     "inputSchema": {"type": "object", "required": ["path"],
                     "properties": {"path": {"type": "string"}}}},
    {"name": "matter.verify", "description": "Re-hash the store against files under MATTER_DIR. Returns absolute root= plus ok/changed/missing. MISSING/CHANGED is a blocker. No network.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "matter.hold_render", "description": "Render one custodian notice packet by notice_id (all correspondence kinds). Only DOCUMENTED FACT and UNKNOWN assertion rows; HYPOTHESIS omitted. Validates source hashes. Read-only, nosend; no approval or mailing writes.",
     "inputSchema": {"type": "object", "required": ["notice_id"],
                     "properties": {"notice_id": {"type": "string"}}, "additionalProperties": False}},
    {"name": "matter.correspondence_list", "description": "Read the custodian notice ledger, including status, exhibit hashes, confirmation dates, and receipt evidence. No network.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def _resolve_in_matter(path: str) -> str:
    """Resolve path inside MATTER_DIR. Relative paths are joined to the matter root, not cwd."""
    if not path:
        raise ValueError("path required")
    abs_matter = os.path.abspath(MATTER_DIR)
    if os.path.isabs(path):
        candidate = os.path.abspath(path)
    else:
        candidate = os.path.abspath(os.path.join(abs_matter, path))
    if not ing.is_within_matter(candidate, abs_matter):
        raise ValueError("path escapes MATTER_DIR")
    try:
        ing.to_rel_path(candidate, abs_matter)
    except ValueError as e:
        raise ValueError("path escapes MATTER_DIR") from e
    return candidate


def call(name: str, args: dict):
    if name == "matter.verify":
        abs_matter = os.path.abspath(MATTER_DIR)
        if not os.path.isdir(abs_matter):
            return f"root={abs_matter}\nerror: MATTER_DIR is not a directory"
        conn = store.connect(MATTER_DIR)
        ok, bad, missing, failures = ing.verify_integrity(conn, MATTER_DIR)
        lines = [f"root={abs_matter}",
                 f"integrity: {ok} ok · {bad} changed · {missing} missing"]
        for kind, path in failures:
            lines.append(f"  {kind}: {path}")
        return "\n".join(lines)
    if name == "matter.ingest_path":
        candidate = _resolve_in_matter(args.get("path", ""))
        conn = store.connect(MATTER_DIR)
        with contextlib.redirect_stdout(sys.stderr):  # ingest prints "! ..." notes; stdout is the JSON-RPC channel
            added, skipped, excluded = ing.ingest_paths(conn, MATTER_DIR, [candidate])
        return json.dumps({"added": added, "skipped": skipped, "excluded": excluded})
    conn = store.connect(MATTER_DIR)
    if name == "matter.status":
        return pk.status_report(conn, MATTER_DIR)
    if name == "matter.fact_add":
        from matterkit.store import new_id, now_iso
        aid = new_id("a_")
        status = args.get("status", "UNKNOWN")
        by = store.attribution(args.get("created_by"), args.get("directed_by"), default="agent")  # raises -> isError
        conn.execute("INSERT INTO assertions (id,text,status,created_by,created_at) VALUES (?,?,?,?,?)",
                     (aid, args["text"], status, by, now_iso()))
        unsupported = []
        for s in args.get("sources", []):
            ref, _, loc = s.partition("@")
            row = conn.execute("SELECT id FROM documents WHERE rel_path=? OR id=?", (ref, ref)).fetchone()
            if not row:
                unsupported.append(ref)
                continue
            conn.execute("INSERT OR IGNORE INTO assertion_sources VALUES (?,?,?)", (aid, row["id"], loc or None))
        conn.commit()
        n = conn.execute("SELECT COUNT(*) c FROM assertion_sources WHERE assertion_id=?", (aid,)).fetchone()["c"]
        warn = f"\n⚠ UNSUPPORTED — no recognized sources." if n == 0 else ""
        bad = f"\n⚠ Unrecognized source refs ignored: {unsupported}" if unsupported else ""
        return f"{aid} [{status}] by {by}{warn}{bad}"
    if name == "matter.fact_list":
        st = args.get("status")
        q = "SELECT * FROM assertions" + (" WHERE status=?" if st else "") + " ORDER BY status"
        out = []
        for r in conn.execute(q, (st,) if st else ()):
            srcs = conn.execute(
                "SELECT d.rel_path, s.locator FROM assertion_sources s JOIN documents d ON d.id=s.document_id"
                " WHERE s.assertion_id=?", (r["id"],)).fetchall()
            out.append({"id": r["id"], "status": r["status"], "text": r["text"],
                        "supported": bool(srcs),
                        "sources": [f"{x['rel_path']}" + (f"@{x['locator']}" if x['locator'] else "") for x in srcs]})
        return json.dumps(out, indent=1)
    if name == "matter.deadline_list":
        q = "SELECT * FROM deadlines" + ("" if args.get("all") else " WHERE state='open'") + " ORDER BY due_date"
        from matterkit import deadlines as dl
        rows = []
        for r in conn.execute(q):
            od = dl.is_overdue(r["due_date"], r["state"])
            rows.append(dict(r, overdue=od, marker=dl.OVERDUE_MARK if od else None))
        return json.dumps(rows, indent=1)
    if name == "matter.deadline_update":
        from matterkit import deadlines as dl
        by = store.attribution(args.get("created_by"), args.get("directed_by"), default="agent")  # raises -> isError
        dl.update(conn, MATTER_DIR, args.get("deadline_id"), args.get("reason"), by, state=args.get("state"),
                  verification=args.get("verification"), confidence=args.get("confidence"),
                  due=args.get("due"), sources=args.get("sources") or [])
        return f"deadline {args['deadline_id']} updated by {by}; events row recorded"
    if name == "matter.packet_render":
        return pk.packet(conn, MATTER_DIR, layer=args.get("layer", "3min"))
    if name == "matter.hold_render":
        from matterkit import correspondence
        return correspondence.render_packet(conn, MATTER_DIR, args["notice_id"])
    if name == "matter.correspondence_list":
        from matterkit import correspondence
        return correspondence.ledger(conn)
    if name == "matter.cite_extract":
        from matterkit import citations
        text = args.get("text", "")
        rows = citations.extract_citations(
            text, document_sha256=args.get("document_sha256"))
        citations.record_extractions(conn, rows)
        return json.dumps(rows, indent=1)
    raise ValueError(f"unknown tool: {name}")


def handle(req: dict) -> dict:
    method = req.get("method")
    rid = req.get("id")
    def result(res): return {"jsonrpc": "2.0", "id": rid, "result": res}
    if method == "initialize":
        return result({"protocolVersion": req["params"].get("protocolVersion", "2025-06-18"),
                       "capabilities": {"tools": {}},
                       "serverInfo": {"name": "matter-kit", "version": "0.1.0"}})
    if method == "notifications/initialized":
        return {}
    if method == "tools/list":
        return result({"tools": TOOLS})
    if method == "tools/call":
        params = req.get("params", {})
        try:
            text = call(params["name"], params.get("arguments", {}))
            return result({"content": [{"type": "text", "text": str(text)}]})
        except Exception as e:  # noqa: BLE001
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}}
    if method == "ping":
        return result({})
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method: {method}"}}


if __name__ == "__main__":
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle(json.loads(line))
        except Exception as e:  # noqa: BLE001
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(e)}}
        if resp:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
