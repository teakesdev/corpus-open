#!/usr/bin/env python3
"""matter-mcp — zero-dependency stdio MCP server exposing a matter store to any MCP client.

Set MATTER_DIR to the case workspace root before launching. Tools are deliberately
read-mostly: agents can register facts, keep a case law research log and render packets,
but nothing here files or contacts anyone. The one thing that leaves the machine is a
case law lookup (matter.search, matter.cite_lookup) to CourtListener, and only after a
person has turned lookups on for the case; no tool here can turn them on.
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
    {"name": "matter.fact_add", "description": "Register an assertion with an epistemic label and optional source refs ('rel_path@locator'). Refuses sources not present in the store. In a subcase, a source may be 'parent:<rel_path>@locator': resolved against the parent matter, re-hashed, refused if it escapes the parent or no longer matches; the document is referenced, never copied.",
     "inputSchema": {"type": "object", "required": ["text", "status"],
                     "properties": {"text": {"type": "string"}, "status": {"enum": ["DOCUMENTED FACT", "ALLEGATION", "INFERENCE", "HYPOTHESIS", "UNKNOWN"]},
                                    "sources": {"type": "array", "items": {"type": "string"}}, "created_by": {"type": "string"},
                                    "directed_by": {"type": "string", "description": "Person who directed the agent to do this work (not an approval). Requires created_by 'agent:<model>'."}}}},
    {"name": "matter.fact_list", "description": "List assertions, optionally filtered by label, with their provenance.",
     "inputSchema": {"type": "object", "properties": {"status": {"type": "string"}}}},
    {"name": "matter.amend_add", "description": "Queue a change for the next version of a pleading (the amendment list): what to add or change, the paragraphs it touches, why, and the facts (assertion ids) and registered documents ('rel_path@page', split on the last '@') that support it. Starts as 'proposed'. Refuses empty text, an empty target, an unknown fact id and a document that is not registered; changes nothing when it refuses. Agents only propose: approving, dropping or marking a change as in the draft is a person's decision, made with `matter.py amend set` or in the browser UI.",
     "inputSchema": {"type": "object", "required": ["text"],
                     "properties": {"text": {"type": "string", "description": "what to add or change"},
                                    "paragraphs": {"type": "string", "description": "e.g. '¶¶ 37, 172'"},
                                    "reason": {"type": "string"},
                                    "target": {"type": "string", "description": "which pleading; default 'complaint'"},
                                    "facts": {"type": "array", "items": {"type": "string"}, "description": "assertion ids (a_...) in this case"},
                                    "sources": {"type": "array", "items": {"type": "string"}},
                                    "created_by": {"type": "string"},
                                    "directed_by": {"type": "string", "description": "Person who directed the agent to do this work (not an approval). Requires created_by 'agent:<model>'."}}}},
    {"name": "matter.amend_list", "description": "List the amendment list with each change's status, paragraphs, text, reason, linked facts and documents, and history. status: 'open' (default: proposed + approved, approved first), 'all', or one of proposed|approved|incorporated|dropped. target limits it to one pleading. Read-only.",
     "inputSchema": {"type": "object", "properties": {"status": {"type": "string"}, "target": {"type": "string"}}}},
    {"name": "matter.deadline_list", "description": "List deadlines (open only unless all=true) with confidence, verification status and owner (who must act: ours, theirs or court; null when not set). owner limits the list to one owner. Open deadlines dated before today carry overdue=true and marker '⚠ OVERDUE'.",
     "inputSchema": {"type": "object", "properties": {"all": {"type": "boolean"},
                                                      "owner": {"enum": ["ours", "theirs", "court"]}}}},
    {"name": "matter.deadline_update", "description": "Change a deadline's state (open|met|superseded|lapsed|vacated), verification, confidence, due date or owner (ours|theirs|court). A reason is required; each change appends an events row (old->new, reason, sources, actor). Sources are 'rel_path@locator' refs that must be registered documents. Refuses unknown ids, invalid values, no-op updates and unregistered sources, and changes nothing when it refuses.",
     "inputSchema": {"type": "object", "required": ["deadline_id", "reason"],
                     "properties": {"deadline_id": {"type": "string"}, "reason": {"type": "string"},
                                    "state": {"enum": ["open", "met", "superseded", "lapsed", "vacated"]},
                                    "verification": {"enum": ["verified-official", "secondary-source", "unverified"]},
                                    "confidence": {"enum": ["hard", "working-estimate"]},
                                    "due": {"type": "string", "description": "YYYY-MM-DD"},
                                    "owner": {"enum": ["ours", "theirs", "court"]},
                                    "sources": {"type": "array", "items": {"type": "string"}},
                                    "created_by": {"type": "string"},
                                    "directed_by": {"type": "string", "description": "Person who directed the agent to do this work (not an approval). Requires created_by 'agent:<model>'."}}}},
    {"name": "matter.trigger_list", "description": "List deadlines that wait on an event and have no date yet (waiting only unless all=true): label, the event, days, rule, owner, state, and for a fired trigger its date, service and the deadline it made. owner limits the list to one owner. Read-only.",
     "inputSchema": {"type": "object", "properties": {"all": {"type": "boolean"},
                                                      "owner": {"enum": ["ours", "theirs", "court"]}}}},
    {"name": "matter.trigger_fire", "description": "The event a waiting trigger was waiting on happened on `on` (YYYY-MM-DD, not after today unless dry_run), served as `service`. Counts the days under Fed. R. Civ. P. 6(a)(1) (exclude the trigger day, count every day, roll past a Saturday, Sunday or legal holiday) and 6(d) (3 more days only for mail, clerk or consented-other service, then roll again), and inserts one deadline, working-estimate/unverified, with every counting step in its event. Requires a reason and registered, unchanged source documents. Refuses a year whose state holidays are not recorded as complete, a fired or withdrawn trigger, and a date after today; changes nothing when it refuses. dry_run=true returns the count and writes nothing (no reason or sources needed), and takes a date after today to show what an event on that day would make due. The date is computed, not checked: a person verifies it.",
     "inputSchema": {"type": "object", "required": ["trigger_id", "on", "service"],
                     "properties": {"trigger_id": {"type": "string"},
                                    "on": {"type": "string", "description": "YYYY-MM-DD the event happened"},
                                    "service": {"enum": ["mail", "clerk", "consented-other", "email", "ecf", "hand",
                                                         "none"],
                                                "description": "how the paper was served; none: the period runs from the event itself"},
                                    "reason": {"type": "string"},
                                    "sources": {"type": "array", "items": {"type": "string"}},
                                    "dry_run": {"type": "boolean"},
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
    {"name": "matter.brief_check", "description": "Check a draft brief (Markdown, text, Word .docx or PDF inside MATTER_DIR; findings are located as para. N, fn. N or p. N:LINE for Word and PDF): every record cite (Doc. 16 at 2), pleading cite (Compl. ¶ 59), authority, quotation and fact tag is tied to a hash-verified source in the store or flagged. Read-only; the store is never written. Returns the report; first line gives exit=0 (all tied), 1 (blocking), 2 (needs a human). Tied to a source is not legal correctness or good law.",
     "inputSchema": {"type": "object", "required": ["path"],
                     "properties": {"path": {"type": "string"}, "pdftotext": {"type": "boolean"},
                                    "mode": {"type": "string", "enum": ["brief", "complaint"], "description": "complaint: allegations need no record cite"}}}},
    {"name": "matter.rules_show", "description": "Text of a rule or subsection from a rules pack attached to this matter (.matter/rules.json), with the pack id, version and current-as-of date. Cite as 'L.U.Civ.R. 7(b)(4)', 'Fed. R. Civ. P. 15(a)(2)', 'Local Rule 7(b)(4)' or an anchor id. A bare 'Rule 15' names no rule set: it lists every attached pack that has it and picks none unless `pack` is given. Derived text, not the official copy. Read-only. No network.",
     "inputSchema": {"type": "object", "required": ["cite"],
                     "properties": {"cite": {"type": "string"}, "pack": {"type": "string", "description": "pack id"}}}},
    {"name": "matter.correspondence_list", "description": "Read the custodian notice ledger, including status, exhibit hashes, confirmation dates, and receipt evidence. No network.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "matter.caselaw_status", "description": "Whether case law lookups (CourtListener search and cite checking) are on for this case, today's use against the daily limit, and whether a CourtListener key is saved. When they are off, the text says exactly how the person turns them on: tell them that in plain words. Read-only. No network.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "matter.search", "description": "Search CourtListener's case law (court opinions) for a question or phrase, e.g. 'inherent authority to manage docket'. Sends only the query text; returns case names, citations, CourtListener links and snippets. Results are leads, not good law and not source-checked: read the opinion before relying on one. Needs case law lookups turned on by the person (if off, the reply explains how; pass that on). Uses the saved CourtListener key when there is one. A search asked before is answered from the case's saved lookups: nothing is sent and it works even with lookups off.",
     "inputSchema": {"type": "object", "required": ["query"],
                     "properties": {"query": {"type": "string"},
                                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "default 5"},
                                    "fresh": {"type": "boolean", "description": "ask CourtListener again instead of using the answer saved in this case (default false)"}}}},
    {"name": "matter.cite_lookup", "description": "Check every case citation in a Markdown or text draft inside MATTER_DIR against CourtListener: is each cite real, and does the case name match? Sends only the bare citations (e.g. '550 U.S. 544'), never the draft's text. Returns the report; first line gives exit=0 (all found and matching), 1 (a cite not found or a name differs: check by hand), 2 (needs a human). record=true adds found cases to the matter's authorities as 'resolved' (not good law). Needs case law lookups turned on and a CourtListener key (if not, the reply explains how; pass that on). One request covers a whole short draft; max_requests above 1 waits about a minute between requests. Cites CourtListener answered before come from the case's saved lookups and are not sent again.",
     "inputSchema": {"type": "object", "required": ["path"],
                     "properties": {"path": {"type": "string"},
                                    "max_requests": {"type": "integer", "minimum": 1, "maximum": 3, "description": "default 1"},
                                    "record": {"type": "boolean"},
                                    "fresh": {"type": "boolean", "description": "ask CourtListener again instead of using the answer saved in this case (default false)"},
                                    "created_by": {"type": "string"},
                                    "directed_by": {"type": "string", "description": "Person who directed the agent to do this work (not an approval). Requires created_by 'agent:<model>'."}}}},
    {"name": "matter.research_list", "description": "The case law searches recorded in this case: ones logged by hand (Google Scholar, a court's website, a law library, ...) with the outcome of each (found, nothing, blocked, not-online, paywalled, error), plus the CourtListener searches the kit sent (source 'courtlistener'), oldest first. outcome and jurisdiction narrow the list. Read-only. No network.",
     "inputSchema": {"type": "object", "properties": {"outcome": {"enum": ["found", "nothing", "blocked", "not-online", "paywalled", "error"]},
                                                      "jurisdiction": {"type": "string", "description": "two-letter state code, DC, or US"}}}},
    {"name": "matter.research_leads", "description": "Cases saved as leads to look at again, each with the reason it was kept, its revisit date (soonest first, undated last) and status. status: 'open' (still saved, default), 'all', or one of saved|revisited|dropped|used. A lead is a note to self: it is not source-checked and not good law. Read-only. No network.",
     "inputSchema": {"type": "object", "properties": {"status": {"type": "string"}}}},
    {"name": "matter.research_due", "description": "Leads whose revisit date has come (on or before as_of, default today), with how many days ago, and separately the open leads that have no revisit date. A lead is a note to self, not good law. Read-only. No network.",
     "inputSchema": {"type": "object", "properties": {"as_of": {"type": "string", "description": "YYYY-MM-DD, default today"}}}},
    {"name": "matter.research_where", "description": "Where else to look for case law: for a state (two-letter code), DC, or US (federal), the places a court's opinions can be read for free, each with its link, the years it covers, whether it has full-text search, whether the survey could open it (status accessible|partial|blocked|unknown; blocked means the survey's automated check was refused and the page may still open in a browser) and whether that was observed or inferred. A CourtListener row also carries, measured from CourtListener's public bulk files (cl_snapshot in the reply names the snapshot), its opinion count, the earliest and the newest opinion's date (the cl field); its years are never earlier than the court's own start. With year (2003 or 1990-2005) each place is marked covers|partly|does-not-cover|unknown and covering places come first. level and court narrow it; there are many federal courts, so give court for US. The data is a dated survey (see checked_at in the reply) and court websites change: check the page itself. A place listed is a lead, not good law. Read-only. No network.",
     "inputSchema": {"type": "object", "required": ["jurisdiction"],
                     "properties": {"jurisdiction": {"type": "string", "description": "two-letter state code, DC, or US for the federal courts"},
                                    "level": {"enum": ["supreme", "appellate", "trial", "federal-appellate", "federal-district", "bankruptcy", "other"]},
                                    "year": {"type": "string", "description": "2003 or 1990-2005"},
                                    "court": {"type": "string", "description": "only courts whose name has these words, e.g. 'Maryland'"},
                                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "default 20"}}}},
    {"name": "matter.research_log", "description": "Record a case law search done anywhere (Google Scholar, a court's website, a law library, CourtListener by hand) and what came of it: found, nothing, blocked (the site refused), not-online, paywalled or error. Writes one line to this case's .matter/caselaw/research.jsonl and nothing else; no network, nothing is searched. Refuses an empty source or query, an unknown outcome or malformed years (YYYY or YYYY-YYYY), and writes nothing then. When the outcome is nothing, blocked, not-online or paywalled and a jurisdiction is given, the reply also lists other places to look from a dated survey (court websites change; check the page). Searches the kit sends to CourtListener are recorded already.",
     "inputSchema": {"type": "object", "required": ["source", "query", "outcome"],
                     "properties": {"source": {"type": "string", "description": "where you looked, e.g. 'google scholar', 'court website', 'law library'"},
                                    "query": {"type": "string"},
                                    "outcome": {"enum": ["found", "nothing", "blocked", "not-online", "paywalled", "error"]},
                                    "jurisdiction": {"type": "string", "description": "two-letter state code, DC, or US"},
                                    "court": {"type": "string"}, "years": {"type": "string", "description": "2003 or 1990-2005"},
                                    "url": {"type": "string"}, "note": {"type": "string"}, "created_by": {"type": "string"},
                                    "directed_by": {"type": "string", "description": "Person who directed the agent to do this work (not an approval). Requires created_by 'agent:<model>'."}}}},
    {"name": "matter.research_save", "description": "Keep a case that may matter later as a lead, with the reason (required: a lead without one is useless later) and optionally a revisit date (YYYY-MM-DD), citation, link and jurisdiction. Writes one line to this case's .matter/caselaw/research.jsonl and nothing else; no network. A lead is a note to self: it is not source-checked and not good law. Agents save leads; marking one revisited, dropped or used is a person's decision, made with `matter.py research set`. Refuses an empty title or reason and a bad date, and writes nothing then.",
     "inputSchema": {"type": "object", "required": ["title", "why"],
                     "properties": {"title": {"type": "string", "description": "the case, in plain words"},
                                    "why": {"type": "string", "description": "why it may matter"},
                                    "revisit": {"type": "string", "description": "YYYY-MM-DD"},
                                    "citation": {"type": "string"}, "url": {"type": "string"},
                                    "jurisdiction": {"type": "string", "description": "two-letter state code, DC, or US"},
                                    "created_by": {"type": "string"},
                                    "directed_by": {"type": "string", "description": "Person who directed the agent to do this work (not an approval). Requires created_by 'agent:<model>'."}}}},
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


def _off_reply(err) -> str:
    """Lookups refused (off, a limit reached, no key): say so in words the agent can pass on."""
    from matterkit import caselaw
    why = "" if not caselaw.is_on(MATTER_DIR) else f" {err}"
    return f"Nothing was sent.{why}\n\n{caselaw.describe(MATTER_DIR)}"


def _research(name: str, args: dict):
    """The case law research log (matterkit.research_log). Local only: it reads and appends to the
    case's .matter/caselaw/research.jsonl and reads the packaged survey; nothing is sent anywhere."""
    from matterkit import research_log as rl
    if name == "matter.research_list":
        return json.dumps(rl.list_searches(MATTER_DIR, args.get("outcome"), args.get("jurisdiction")), indent=1)
    if name == "matter.research_leads":
        return json.dumps(rl.leads(MATTER_DIR, args.get("status") or "open"), indent=1)
    if name == "matter.research_due":
        return json.dumps(rl.due(MATTER_DIR, args.get("as_of")), indent=1)
    if name == "matter.research_where":
        limit = max(1, min(int(args.get("limit") or 20), 100))
        return json.dumps(rl.where(args.get("jurisdiction"), args.get("level"), args.get("year"), args.get("court"),
                                   limit=limit), indent=1)
    by = store.attribution(args.get("created_by"), args.get("directed_by"), default="agent")  # raises -> isError
    if name == "matter.research_log":
        r = rl.log_search(MATTER_DIR, by, args.get("source"), args.get("query"), args.get("outcome"),
                          args.get("jurisdiction"), args.get("court"), args.get("years"), args.get("url"),
                          args.get("note") or "")  # raises ValueError -> isError, nothing written
        text = f"{r['id']} [{r['outcome']}] {r['source']}: {r['query']} (recorded by {by})"
        if r["outcome"] in rl.MISSES and r["jurisdiction"]:
            text += "\n\n" + rl.render_hints(rl.hints(r["jurisdiction"], r["source"], years=r["years"], court=r["court"]),
                                              r["outcome"])
        return text
    r = rl.save_lead(MATTER_DIR, by, args.get("title"), args.get("why"), args.get("revisit"), args.get("citation"),
                     args.get("url"), args.get("jurisdiction"))  # raises ValueError -> isError, nothing written
    return f"{r['id']} [{r['status']}] {r['title']} (saved by {by}; a lead, not good law)" + \
        (f"; look again {r['revisit']}" if r["revisit"] else "")


def call(name: str, args: dict):
    if name.startswith("matter.research_"):
        return _research(name, args)
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
    if name == "matter.brief_check":
        from matterkit import briefcheck
        report = briefcheck.check(MATTER_DIR, _resolve_in_matter(args["path"]),
                                  use_pdftotext=args.get("pdftotext", True), mode=args.get("mode", "brief"))
        return f"exit={report.exit_code} · {report.summary()}\n\n{report.render()}"
    if name == "matter.rules_show":
        from matterkit import rules
        code, text = rules.show(MATTER_DIR, args.get("cite", ""), args.get("pack"))
        if code == 1:
            raise ValueError(text)
        return text
    if name == "matter.caselaw_status":
        from matterkit import caselaw
        return caselaw.describe(MATTER_DIR)
    if name == "matter.search":
        from matterkit import caselaw
        from matterkit.consent import ConsentError
        query = (args.get("query") or "").strip()
        if not query:
            raise ValueError("query required")
        limit = max(1, min(int(args.get("limit") or 5), 20))
        try:
            rows = caselaw.search(MATTER_DIR, "agent", query, limit=limit, fresh=bool(args.get("fresh")))
        except ConsentError as e:
            return _off_reply(e)
        saved = rows and rows[0].provenance.get("saved")
        out = [f"{len(rows)} CourtListener result(s) for {query!r} (leads only: not source-checked, not good law)"
               + (f"; saved in this case {str(rows[0].provenance.get('retrieved_at'))[:10]}, nothing sent"
                  " (fresh=true asks again)" if saved else "")]
        for r in rows:
            out.append(f"- {r.name}, {r.citation} ({r.effective_date or 'date unknown'}) {r.provenance.get('url')}")
            if r.snippet:
                out.append(f"  {r.snippet[:300]}")
        return "\n".join(out)
    if name == "matter.cite_lookup":
        from matterkit import caselaw, citelookup
        from matterkit.consent import ConsentError
        by = store.attribution(args.get("created_by"), args.get("directed_by"), default="agent")  # raises -> isError
        path = _resolve_in_matter(args.get("path", ""))
        if not os.path.isfile(path):
            raise ValueError(f"no such draft: {args.get('path')}")
        max_requests = max(1, min(int(args.get("max_requests") or 1), 3))
        try:
            with contextlib.redirect_stdout(sys.stderr):
                report = caselaw.lookup_draft(MATTER_DIR, path, "agent", max_requests=max_requests,
                                             fresh=bool(args.get("fresh")))
        except ConsentError as e:
            return _off_reply(e)
        if args.get("record"):
            report.recorded = citelookup.record(store.connect(MATTER_DIR), report.results, by)
        return f"exit={report.exit_code} · {report.summary()}\n\n{report.render()}"
    conn = store.connect(MATTER_DIR)
    if name == "matter.status":
        return pk.status_report(conn, MATTER_DIR)
    if name == "matter.fact_add":
        from matterkit import facts
        by = store.attribution(args.get("created_by"), args.get("directed_by"), default="agent")  # raises -> isError
        # raises -> isError, nothing written: invalid label, empty text, an uncitable parent: ref
        r = facts.add(conn, MATTER_DIR, args["text"], args.get("status", "UNKNOWN"), args.get("sources", []), by)
        warn = "\n⚠ UNSUPPORTED — no recognized sources." if r["unsupported"] else ""
        bad = f"\n⚠ Unrecognized source refs ignored: {r['unrecognized']}" if r["unrecognized"] else ""
        return (f"{r['id']} [{r['status']}] by {r['created_by']}{warn}{bad}"
                + "".join(f"\n  ↳ {note}" for note in r["notes"]))
    if name == "matter.fact_list":
        from matterkit import facts
        out = [{"id": f["id"], "status": f["status"], "text": f["text"],
                "supported": not f["unsupported"],
                "sources": [x["rel_path"] + (f"@{x['locator']}" if x["locator"] else "") for x in f["sources"]]}
               for f in facts.list_(conn, args.get("status"))]
        return json.dumps(out, indent=1)
    if name == "matter.amend_add":
        from matterkit import amendments
        by = store.attribution(args.get("created_by"), args.get("directed_by"), default="agent")  # raises -> isError
        sources = args.get("sources") or []
        if not isinstance(sources, list):
            raise ValueError("sources must be a list of 'rel_path@page' strings")
        documents = [amendments.resolve_source(conn, ref) for ref in sources]  # raises -> isError, nothing written
        r = amendments.add(conn, args.get("text"), args.get("paragraphs"), args.get("reason"),
                           args.get("target", amendments.DEFAULT_TARGET), args.get("facts") or [], documents, by,
                           matter_dir=MATTER_DIR)  # raises SubcaseError (-> isError) if a parent: reference changed
        return f"{r['id']} [{r['status_label']}] by {r['created_by']}"
    if name == "matter.amend_list":
        from matterkit import amendments
        return json.dumps(amendments.list_(conn, args.get("status", "open"), args.get("target")), indent=1)
    if name == "matter.deadline_list":
        from matterkit import deadlines as dl
        rows = []
        for r in dl.select(conn, include_closed=bool(args.get("all")), owner=args.get("owner")):
            od = dl.is_overdue(r["due_date"], r["state"])
            rows.append(dict(r, overdue=od, marker=dl.OVERDUE_MARK if od else None))
        return json.dumps(rows, indent=1)
    if name == "matter.deadline_update":
        from matterkit import deadlines as dl
        by = store.attribution(args.get("created_by"), args.get("directed_by"), default="agent")  # raises -> isError
        dl.update(conn, MATTER_DIR, args.get("deadline_id"), args.get("reason"), by, state=args.get("state"),
                  verification=args.get("verification"), confidence=args.get("confidence"),
                  due=args.get("due"), sources=args.get("sources") or [], owner=args.get("owner"))
        return f"deadline {args['deadline_id']} updated by {by}; events row recorded"
    if name == "matter.trigger_list":
        from matterkit import triggers
        rows = triggers.select(conn, include_closed=bool(args.get("all")), owner=args.get("owner"))
        return json.dumps([dict(r) for r in rows], indent=1)
    if name == "matter.trigger_fire":
        from matterkit import triggers
        by = store.attribution(args.get("created_by"), args.get("directed_by"), default="agent")  # raises -> isError
        sources = args.get("sources") or []
        if not isinstance(sources, list):
            raise ValueError("sources must be a list of 'rel_path@locator' strings")
        did, due, steps = triggers.fire(conn, MATTER_DIR, args.get("trigger_id"), args.get("on"), args.get("service"),
                                        sources, args.get("reason"), by, dry_run=bool(args.get("dry_run")))
        head = (f"dry run: the last day would be {due.isoformat()}; nothing was written" if did is None else
                f"deadline {did} registered: {due.isoformat()} (working-estimate/unverified: computed, not checked)")
        return head + "\n" + "\n".join(f"- {s}" for s in steps)
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
